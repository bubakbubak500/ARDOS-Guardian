"""Store-and-forward mail: rich messages (text + attachments) + a mailbox.

This is the Winlink-like layer on top of Guardian's control/routing/payload
stack. A MailMessage is serialised to a compressed bundle for transfer over
VARA P2P; the MessageStore persists messages in folders (inbox / outbox / sent
/ transit) so they survive restarts and can wait for a hop to become reachable.
"""

from .mail import (
    Attachment,
    MailMessage,
    Folder,
    Status,
    safe_attachment_name,
)
from .store import MessageStore

__all__ = [
    "Attachment",
    "MailMessage",
    "Folder",
    "Status",
    "MessageStore",
    "safe_attachment_name",
]
