"""Run Guardian Companion against realistic local-only demo data.

This helper is intended for responsive browser QA and never opens a radio or
touches the user's Guardian mailbox.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from guardian.companion import CompanionController
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.services import (
    EventBus,
    MailboxSnapshot,
    NetworkSnapshot,
    RadioSnapshot,
    SnapshotStore,
)


@dataclass
class _Config:
    callsign: str = "OK7PS"


class _Operations:
    def send_queued(self, _message_id: int) -> bool:
        return True


class _Runtime:
    def __init__(self, root: Path, callsign: str) -> None:
        self.config = _Config(callsign)
        self.events = EventBus()
        self.snapshots = SnapshotStore()
        self.mailstore = MessageStore(root / "mail")
        self.operations = _Operations()

    def refresh(self) -> None:
        counts = self.mailstore.counts()
        self.snapshots.update(
            mailbox=MailboxSnapshot(
                inbox=counts.get(Folder.INBOX, 0),
                unread=self.mailstore.unread(Folder.INBOX),
                outbox=counts.get(Folder.OUTBOX, 0),
                transit=counts.get(Folder.TRANSIT, 0),
            ),
            network=NetworkSnapshot(
                active_sessions=1,
                heard_stations=4,
                control_channel_active=True,
                scanner_active=True,
            ),
            radio=RadioSnapshot(
                connected=True,
                name="IC-705",
                frequency_hz=145_550_000,
                mode="FM",
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--callsign", default="OK7PS")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="guardian-companion-qa-"))
    runtime = _Runtime(root, args.callsign.strip().upper())
    runtime.mailstore.add(
        MailMessage(
            msg_id=24081201,
            source="OK1ABC",
            final_dest=runtime.config.callsign,
            subject="Příjezd zásobovacího vozu",
            body="Vůz dorazí přibližně v 18:40. Potvrď prosím příjem.",
            priority=1,
            created=time.time() - 90,
            hops=["OK1ABC", "OK2RLY"],
            folder=Folder.INBOX,
            status=Status.RECEIVED,
            read=False,
        )
    )
    runtime.events.publish("Message received from OK1ABC.", source="mail")
    runtime.events.publish(
        "Control channel active on 145.5500 MHz.", source="network"
    )
    runtime.refresh()
    controller = CompanionController(runtime, notes_path=root / "notes.json")
    port = controller.start(host="127.0.0.1", port=args.port)
    print(
        f"PAIR_URL=http://127.0.0.1:{port}/#pair={controller.pairing_fragment()}",
        flush=True,
    )

    try:
        while True:
            controller.poll()
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
