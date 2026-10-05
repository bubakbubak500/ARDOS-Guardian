"""A child process hosting the actual production Operations, with passive telemetry."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import queue
import threading
import time
import traceback

from .identity import identity
from .model import content_identity


def plain(value):
    if is_dataclass(value):
        return plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def session_view(session):
    if session is None:
        return None
    return {"state": session.state.value, "transport": session.payload_transport,
            "profile_token": session.g2_profile_token,
            "progress_bytes": session.payload_progress_bytes,
            "wire_bytes": session.payload_wire_size,
            "transfer_started_at": session.transfer_started_at,
            "payload_sent_at": session.payload_sent_at, "error": session.error}


def main(connection, root: str, expected_fingerprint: str, peer: str, channel: dict | None = None) -> None:
    # Set this BEFORE importing configuration or any production object. Each
    # spawned process gets its own module constants and native ARDOP singleton.
    os.environ["GUARDIAN_STATE_DIR"] = root
    from guardian.message import Folder, Status
    from guardian.message.mail import MailMessage, Attachment
    from guardian.routing import Route, RouteTable
    from guardian.qt.runtime import ShellRuntime
    from guardian.modem.audio import resolve_device, audio_device_host_api
    import sounddevice as sd

    pending = queue.SimpleQueue()
    commands = queue.SimpleQueue()
    closing = threading.Event()
    last_heartbeat = [time.monotonic()]
    operations = None
    runtime = None
    workers = None
    active_id = None
    live_message = None
    started = None
    completions = {}
    shutdown_lock = threading.Lock()
    shutdown_done = False
    shutdown_errors = []
    previous_channel = None

    def emit(kind, **data):
        pending.put({"kind": kind, "monotonic": time.monotonic(), **plain(data)})

    def shutdown_station():
        nonlocal shutdown_done
        with shutdown_lock:
            if shutdown_done or operations is None:
                return
            shutdown_done = True
            operations._closing.set()
            # Use the same production teardown; explicitly unkey the owned
            # CAT connection before closing it, including after supervisor loss.
            try:
                payload = operations.net.payload
                action = getattr(payload, "shutdown", None)
                if callable(action):
                    action()
                operations.stop_control_channel()
                with operations._radio_lock:
                    if operations.radio.is_open:
                        operations.radio.set_ptt(False)
                        if previous_channel and not operations.radio.no_cat:
                            if previous_channel.frequency_hz:
                                operations.radio.set_frequency(previous_channel.frequency_hz)
                            if previous_channel.mode:
                                operations.radio.set_mode(previous_channel.mode)
            except Exception as exc:
                shutdown_errors.append(str(exc))
            finally:
                runtime.close()

    def read_commands():
        try:
            while not closing.is_set():
                if connection.poll(0.25):
                    command = connection.recv()
                    last_heartbeat[0] = time.monotonic()
                    if command.get("op") == "stop":
                        closing.set()
                    if command.get("op") != "heartbeat":
                        commands.put(command)
        except (EOFError, OSError):
            closing.set()

    def watchdog():
        while not closing.wait(1):
            if time.monotonic() - last_heartbeat[0] > 20:
                emit("error", error="LAB supervisor heartbeat lost; closing production station")
                closing.set()
        # Also close on pipe EOF or explicit stop while the main loop is busy.
        shutdown_station()

    threading.Thread(target=read_commands, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        build = identity()
        if build["fingerprint"] != expected_fingerprint:
            raise RuntimeError("Guardian code/runtime changed before station startup")
        RouteTable([Route(peer, peer)]).save(Path(root) / "routes.json")
        # The normal desktop composition owns initialization, coordination and
        # tick behavior too. A later production integration is inherited here.
        runtime = ShellRuntime()
        operations, config = runtime.operations, runtime.config
        events, snapshots, workers, store = runtime.events, runtime.snapshots, runtime.workers, runtime.mailstore
        devices = {}
        for kind in ("input", "output"):
            selected = getattr(config, f"audio_{kind}")
            index = resolve_device(selected, kind)
            if not isinstance(index, int):
                raise RuntimeError(f"Cannot resolve explicit {kind}: {selected}")
            devices[kind] = {"index": index, "host_api": audio_device_host_api(selected, kind),
                             "device": dict(sd.query_devices(index))}
        emit("prepared", fingerprint=build["fingerprint"], config=asdict(config), devices=devices)
        running = False
        next_snapshot = 0.0
        while not closing.is_set():
            while not commands.empty():
                command = commands.get()
                op = command.get("op")
                if op == "stop":
                    closing.set()
                    break
                if op == "start":
                    if running:
                        raise RuntimeError("Station already started")
                    operations._open_radio()
                    if channel:
                        previous_channel = operations.radio.get_state()
                        if operations.is_no_cat_radio():
                            if config.manual_frequency_hz != int(channel["frequency_hz"]):
                                raise RuntimeError("AIOC/no-CAT radio must already be manually set to the requested frequency")
                        else:
                            operations.radio.set_frequency(int(channel["frequency_hz"]))
                            operations.radio.set_mode(channel["mode"])
                            readback = operations.radio.get_state()
                            if readback.frequency_hz != int(channel["frequency_hz"]) or readback.mode != channel["mode"]:
                                raise RuntimeError("Radio channel readback differs from the requested channel")
                        emit("channel", requested=channel, readback=plain(operations.radio.get_state()),
                             frequency_verified=not operations.is_no_cat_radio())
                    if config.payload_backend == "vara_p2p":
                        # The supervisor has already launched and verified the
                        # exact owned EXE. Production connection cannot fall back.
                        if not operations.connect_vara():
                            raise RuntimeError("VARA connection task was rejected")
                        deadline = time.monotonic() + 15
                        while workers.is_active("vara-control") and time.monotonic() < deadline:
                            workers.drain()
                            time.sleep(0.05)
                        workers.drain()
                        if not operations.vara.connected:
                            raise RuntimeError("Production VARA connection failed")
                    if not operations.start_control_channel():
                        raise RuntimeError("Production control channel did not start")
                    original_event = operations.net.on_event

                    def observe(message, event):
                        nonlocal live_message
                        original_event(message, event)
                        live_message = message
                        if message.direction == "out" and message.state.value in {"delivered", "failed", "cancelled"}:
                            completions.setdefault(message.msg_id, time.monotonic())
                        emit("session", message_id=message.msg_id, state=message.state.value,
                             direction=message.direction, text=event,
                             transport=message.payload_transport,
                             wire_bytes=message.payload_wire_size,
                             progress_bytes=message.payload_progress_bytes,
                             transfer_started_at=message.transfer_started_at,
                             payload_sent_at=message.payload_sent_at)

                    operations.net.on_event = observe
                    running = True
                    emit("ready", config=asdict(config), control_modem=config.active_modem(),
                         profile_token=operations._g2_profile_token() if operations._native_payload_configured() else None)
                elif op == "send":
                    if not running:
                        raise RuntimeError("Station not started")
                    if operations.network_settings_busy():
                        raise RuntimeError("Production station is still busy")
                    data = Path(command["payload_path"]).read_bytes()
                    message = MailMessage(store.next_id(config.callsign), config.callsign, peer,
                        subject=command["subject"], body=command.get("body", ""),
                        attachments=[Attachment(Path(command["payload_path"]).name, data)],
                        created=time.time(), folder=Folder.OUTBOX, status=Status.QUEUED)
                    store.add(message)
                    active_id = message.msg_id
                    expected_content = content_identity(message)
                    original_bytes = message.content_size()
                    bundle_bytes = len(message.to_bundle())
                    started = time.monotonic()
                    emit("submitted", message_id=active_id, content=expected_content,
                         original_bytes=original_bytes, bundle_bytes=bundle_bytes,
                         started=started)
                    if not operations.send_queued(active_id):
                        raise RuntimeError("Production send_queued rejected the message")
                elif op == "calibrate_start":
                    if not running or operations.network_settings_busy():
                        raise RuntimeError("Station is not ready for Auto Tune")
                    if not operations.start_station_calibration(peer, "quick"):
                        raise RuntimeError(f"Auto Tune rejected: {operations.station_lab.error}")
                    emit("calibration_started", station_lab=plain(operations.station_lab))
                elif op == "inspect":
                    message = store.get(int(command["message_id"]))
                    emit("mail", message_id=command["message_id"],
                         content=content_identity(message) if message else None,
                         status=message.status if message else None,
                         folder=message.folder if message else None)
            if running:
                runtime.drain_workers()
                runtime.tick()
            for event in events.drain(1000):
                emit("log", event={**asdict(event), "timestamp": event.timestamp.isoformat()})
            now = time.monotonic()
            if running and now >= next_snapshot:
                next_snapshot = now + 0.25
                runtime.refresh()
                session = operations.net.sessions.get(active_id) if active_id else None
                meta = next((item for item in store.list() if item["msg_id"] == active_id), None) if active_id else None
                emit("snapshot", station=asdict(snapshots.read()),
                     sc_ftn=plain(operations.ofdm_status()), busy=operations.network_settings_busy(),
                     station_lab=plain(operations.station_lab),
                     message_id=active_id, status=meta["status"] if meta else None,
                     elapsed=(completions.get(active_id, now)-started) if started else None,
                     session=session_view(session), live_session=session_view(live_message))
            while not pending.empty():
                connection.send(pending.get())
            closing.wait(0.02)
    except BaseException as exc:
        try:
            connection.send({"kind": "error", "error": str(exc), "traceback": traceback.format_exc()})
        except (OSError, EOFError):
            pass
    finally:
        closing.set()
        if operations is not None:
            shutdown_station()
            # A lost vendor instance must not leave an automatic production
            # restart orphaned. This handle can only belong to this worker.
            vendor = operations._vara_process
            if vendor is not None and vendor.poll() is None:
                vendor.terminate()
        if workers is not None:
            workers.close(wait=False)
        try:
            connection.send({"kind": "stopped", "fingerprint": identity()["fingerprint"],
                             "shutdown_errors": shutdown_errors})
        except (OSError, EOFError):
            pass
        connection.close()
