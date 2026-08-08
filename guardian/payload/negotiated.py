"""Route each transfer to the transport that hop actually agreed on.

A station configured for the experimental OFDM modem still has to talk to
stations that are not. The control handshake settles that per hop -- see the
OFDM_PAYLOAD notes in `protocol/frames.py` -- and records the answer on the
message as `payload_transport`. This wrapper is what acts on it.

It exists only for a station that selected an experimental transport. One that
runs VARA never sets the capability bit, so every hop it negotiates comes back
`vara_p2p`, and `Operations` hands the orchestrator the plain VARA backend with
nothing wrapped around it -- the path that has been on air for releases is
untouched.
"""

from __future__ import annotations

from .base import DoneCb, PayloadBackend


class NegotiatedPayload(PayloadBackend):
    """Dispatches `start_send`/`start_receive` on the message's agreed transport."""

    name = "negotiated"

    def __init__(self, default: PayloadBackend,
                 backends: dict[str, PayloadBackend] | None = None,
                 on_log=None) -> None:
        self.default = default
        self.backends = dict(backends or {})
        self.on_log = on_log or (lambda message: None)

    def backend_for(self, msg) -> PayloadBackend:
        """The backend this hop settled on, or the fallback.

        Falling back rather than failing: a peer that turned out not to have the
        experimental transport is a reason to move the message the ordinary way,
        not a reason to lose it. The fallback may itself fail -- a station with no
        VARA installed has nothing to fall back *to* -- and it says so in the log
        rather than pretending the transport matched.
        """
        agreed = getattr(msg, "payload_transport", None) or self.default.name
        chosen = self.backends.get(agreed)
        if chosen is not None:
            return chosen
        if agreed != self.default.name:
            self.on_log(
                f"payload #{getattr(msg, 'msg_id', '?')}: no {agreed} transport "
                f"available, using {self.default.name}"
            )
        return self.default

    def start_send(self, msg, done: DoneCb) -> None:
        self.backend_for(msg).start_send(msg, done)

    def start_receive(self, msg, done: DoneCb) -> None:
        self.backend_for(msg).start_receive(msg, done)

    def cancel(self, msg) -> None:
        self.backend_for(msg).cancel(msg)
