"""PayloadBackend interface."""

from __future__ import annotations

from typing import Callable

# Completion callback: ok=True if the payload moved successfully.
DoneCb = Callable[[bool], None]


class PayloadBackend:
    name = "base"

    def start_send(self, msg, done: DoneCb) -> None:
        """Initiator side: move msg's payload to msg.next_hop, then call done(ok).

        Note: end-to-end confirmation still arrives as a RECEIVED control frame
        from the peer; done(ok) only reports whether *our* send succeeded.
        """
        raise NotImplementedError

    def start_receive(self, msg, done: DoneCb) -> None:
        """Responder side: receive the payload, then call done(ok).

        done(True) makes the orchestrator emit RECEIVED (and DELIVERED if we are
        the final destination).
        """
        raise NotImplementedError

    def cancel(self, msg) -> None:
        """Abort any in-progress transfer for this message."""
