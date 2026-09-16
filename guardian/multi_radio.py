"""Station-level routing across independently owned radio channels."""
from __future__ import annotations

import time
from dataclasses import replace

from .i18n import dual
from .message import Folder, Status
from .protocol import ControlFrame
from .session import SessionState


class CombinedHeard:
    """UI view; protocol engines deliberately retain their per-radio registry."""

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def max_age(self):
        return self.coordinator.radios[0].heard.max_age

    def active(self, now):
        return sorted((replace(station, radio_id=radio.radio_id)
                       for radio in self.coordinator.radios
                       for station in radio.heard.active(now)),
                      key=lambda station: station.last_heard, reverse=True)

    def known(self):
        return sorted((replace(station, radio_id=radio.radio_id)
                       for radio in self.coordinator.radios
                       for station in radio.heard.known()),
                      key=lambda station: station.last_heard, reverse=True)

    def get(self, callsign):
        return next((s for s in self.known() if s.callsign == callsign.strip().upper()), None)


class RadioCoordinator:
    """Select one egress and preserve the ingress for end-to-end receipts."""

    def __init__(self, primary):
        self.radios = [primary]
        self.mailstore = primary.mailstore
        self.heard = CombinedHeard(self)
        self._receipt_queue = []
        self.attach(primary, 1)
        for meta in self.mailstore.list():
            pending = self.mailstore.radio_path(meta["msg_id"]).get("pending_receipt")
            if pending:
                try:
                    self._receipt_queue.append((int(pending["radio_id"]),
                        ControlFrame.decode(bytes.fromhex(pending["frame"]))))
                except (KeyError, TypeError, ValueError):
                    pass

    def attach(self, radio, radio_id):
        radio.radio_id = radio_id
        radio.coordinator = self
        primary = self.radios[0]
        radio._mail_mutation_lock = primary._mail_mutation_lock
        radio._mail_preparing = primary._mail_preparing
        radio._auto_delivery_attempted = primary._auto_delivery_attempted
        if radio not in self.radios:
            self.radios.append(radio)

    def busy(self):
        return any(r.network_settings_busy() or r.payload_handoff_pending()
                   or r._calibration_active() or r._mail_preparing
                   or any(r.workers.is_active(name) for name in
                          ("radio-control", "vara-control", "alert-sweep", "scanner-tune", "scanner-home"))
                   for r in self.radios)

    def active_message(self, message_id):
        return any((m := r.net.sessions.get(message_id)) is not None
                   and not m.state.terminal for r in self.radios)

    def _candidate(self, radio, destination, now, exclude):
        if radio.audio_transport is None or radio._closing.is_set():
            return None
        destination = destination.strip().upper()
        hop, _ = radio.net._resolve_next_hop(destination, exclude_hops=exclude)
        if (hop or destination) in exclude:
            return None
        station = radio.heard.get(hop or destination)
        frequency = radio.current_frequency()
        if station and station.age(now) <= radio.heard.max_age:
            if (not frequency or not station.last_freq_hz
                    or abs(frequency - station.last_freq_hz) <= 100):
                return (3 if station.callsign == destination else 2,
                        station.last_heard, -radio.radio_id)
        route = radio.routes.lookup(destination)
        if route and route.freq_hz and frequency and abs(route.freq_hz - frequency) <= 100:
            if (route.preferred or destination) not in exclude:
                return (1, 0, -radio.radio_id)
        return None

    def select(self, destination, *, exclude=(), fallback=False):
        now = time.monotonic()
        candidates = [(rank, r) for r in self.radios
                      if (rank := self._candidate(r, destination, now, set(exclude))) is not None]
        if candidates:
            return max(candidates, key=lambda item: item[0])[1]
        if fallback:
            return next((r for r in self.radios if r.audio_transport is not None), None)
        return None

    def send_queued(self, message_id, *, automatic=False):
        lock = self.radios[0]._mail_mutation_lock
        if not lock.acquire(blocking=False):
            return False
        try:
            if self.active_message(message_id) or message_id in self.radios[0]._mail_preparing:
                return False
            mail = self.mailstore.get(message_id)
            if mail is None:
                return False
            path = self.mailstore.radio_path(message_id)
            if mail.folder == Folder.TRANSIT and path.get("ttl", 1) < 1:
                return False
            exclude = {path["previous_hop"]} if path.get("previous_hop") else set()
            radio = self.select(mail.final_dest, exclude=exclude, fallback=not automatic)
            if radio is None or not radio._net_idle():
                return False
            if radio.config.payload_backend == "vara_p2p" and not radio.vara.connected:
                if not automatic:
                    radio._log(dual("Connect this radio's VARA before sending; mail remains queued.",
                                    "Připojte VARA tohoto rádia; zpráva zůstává ve frontě."), source="mail")
                return False
            self.mailstore.radio_path(message_id, outbound_radio=radio.radio_id)
            return radio.send_queued(message_id, _selected=True)
        finally:
            lock.release()

    def accept_relay(self, radio, message):
        # The normal incoming callback has already durably stored the bundle.
        if self.mailstore.get(message.msg_id) is None:
            return False
        self.mailstore.radio_path(message.msg_id, inbound_radio=radio.radio_id,
                                  previous_hop=message.source, ttl=message.ttl - 1)
        # Custody is now in the durable station queue. Release this ingress
        # session while retaining it to re-ack upstream retransmissions.
        radio.net._enter(message, SessionState.FORWARDED)
        radio._log(dual("Message TTL expired; retained in transit.", "TTL zprávy vypršelo; zůstává v předávané poště.")
                   if message.ttl <= 1 else
                   dual("Message queued for routing across both radios.",
                        "Zpráva zařazena ke směrování přes obě rádia."), source="mail")
        return True

    def transmit_receipt(self, radio, frame):
        path = self.mailstore.radio_path(frame.message_id)
        ingress = path.get("inbound_radio", radio.radio_id)
        # Only a reverse relay hop crosses back to the original channel.
        if path.get("previous_hop") != frame.next_hop:
            ingress = radio.radio_id
        self._receipt_queue.append((ingress, frame))
        self.mailstore.radio_path(frame.message_id, pending_receipt={
            "radio_id": ingress, "frame": frame.encode().hex()
        })

    def flush_receipts(self):
        for radio_id, frame in list(self._receipt_queue):
            radio = next((r for r in self.radios if r.radio_id == radio_id), None)
            if radio is not None and radio.audio_transport is not None and not radio.payload_active():
                radio.net.transport.send(frame)
                self._receipt_queue.remove((radio_id, frame))
                self.mailstore.radio_path(frame.message_id, pending_receipt=None)

    def tick(self):
        self.flush_receipts()
        now = time.monotonic()
        primary = self.radios[0]
        if not primary.config.auto_deliver:
            return
        for folder in (Folder.OUTBOX, Folder.TRANSIT):
            for meta in self.mailstore.list(folder):
                if meta.get("status") == Status.FAILED:
                    continue
                message_id = meta["msg_id"]
                last = primary._auto_delivery_attempted.get(message_id)
                if last is not None and now - last < primary._AUTO_DELIVER_RETRY:
                    continue
                if self.send_queued(message_id, automatic=True):
                    primary._auto_delivery_attempted[message_id] = now
