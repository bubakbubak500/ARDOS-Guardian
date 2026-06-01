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

    def __init__(self, prompt: PromptFn | None = None, on_log=None):
        self.prompt = prompt
        self.on_log = on_log or (lambda m: None)

    def start_send(self, msg, done: DoneCb) -> None:
        self.on_log(
            f"Winlink hand-off: send message #{msg.msg_id} to {msg.next_hop} "
            f"(final {msg.final_dest}) via your Winlink session."
        )
        if self.prompt:
            self.prompt("send", msg, done)
        else:
            done(True)  # no UI wired (headless/test): assume operator handled it

    def start_receive(self, msg, done: DoneCb) -> None:
        self.on_log(
            f"Winlink hand-off: expect message #{msg.msg_id} from {msg.source} "
            f"via Winlink. Confirm when received."
        )
        if self.prompt:
            self.prompt("receive", msg, done)
        else:
            done(True)
