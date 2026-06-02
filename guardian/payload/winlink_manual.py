"""Winlink manual hand-off backend.

Guardian does the wake + route negotiation; the *payload* is moved by the
operator's own Winlink session (Winlink Express or Pat). This backend is a thin
adapter to a UI prompt: it asks the operator to perform the transfer and calls
back when they confirm. No automation of Winlink itself — which is exactly why
it's safe and works with the closed Winlink Express.
"""

from __future__ import annotations

from typing import Callable

from .base import DoneCb, PayloadBackend

# prompt(role, msg, done): role is "send" or "receive". The UI shows a dialog
# and calls done(ok) when the operator confirms / cancels.
PromptFn = Callable[[str, object, DoneCb], None]


class WinlinkManualBackend(PayloadBackend):
    name = "winlink_manual"

    def __init__(self, prompt: PromptFn | None = None, on_log=None,
                 on_acquire=None, on_release=None):
        self.prompt = prompt
        self.on_log = on_log or (lambda m: None)
        # Soundcard handoff: free Guardian's control channel so the operator's
        # own Winlink session can own the (single) codec, reclaim it after.
        self.on_acquire = on_acquire
        self.on_release = on_release

    def _wrap(self, done: DoneCb) -> DoneCb:
        """Reclaim the control channel once the operator confirms/cancels."""
        def wrapped(ok: bool) -> None:
            if self.on_release:
                try:
                    self.on_release()
                except Exception:
                    pass
            done(ok)
        return wrapped

    def start_send(self, msg, done: DoneCb) -> None:
        self.on_log(
            f"Winlink hand-off: send message #{msg.msg_id} to {msg.next_hop} "
            f"(final {msg.final_dest}) via your Winlink session."
        )
        if self.on_acquire:
            try:
                self.on_acquire()
            except Exception:
                pass
        if self.prompt:
            self.prompt("send", msg, self._wrap(done))
        else:
            self._wrap(done)(True)  # no UI wired (headless/test)

    def start_receive(self, msg, done: DoneCb) -> None:
        self.on_log(
            f"Winlink hand-off: expect message #{msg.msg_id} from {msg.source} "
            f"via Winlink. Confirm when received."
        )
        if self.on_acquire:
            try:
                self.on_acquire()
            except Exception:
                pass
        if self.prompt:
            self.prompt("receive", msg, self._wrap(done))
        else:
            self._wrap(done)(True)
