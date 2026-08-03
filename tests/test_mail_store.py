import io
import zipfile
from pathlib import Path

from guardian.message import Attachment, Folder, MailMessage, MessageStore, Status


def _mail(msg_id: int = 42) -> MailMessage:
    return MailMessage(
        msg_id=msg_id,
        source="OK7PS",
        final_dest="OK1AAA",
        subject="Status",
        body="All systems operational.",
        attachments=[Attachment("report.txt", b"payload")],
        created=1234.5,
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
        next_hop="OK1BBB",
    )


def test_mail_bundle_round_trip_preserves_transferred_content() -> None:
    original = _mail()

    restored = MailMessage.from_bundle(original.to_bundle())

    assert restored.msg_id == original.msg_id
    assert restored.source == original.source
    assert restored.final_dest == original.final_dest
    assert restored.subject == original.subject
    assert restored.body == original.body
    assert restored.attachments == original.attachments


def test_a_failed_message_is_not_counted_as_waiting_to_send(tmp_path: Path) -> None:
    # A failed send parks the message in the outbox for a retry. Counting it
    # as pending left the station context reading "waiting to send: 1"
    # indefinitely with nothing actually in flight.
    store = MessageStore(tmp_path / "mail")
    for index, status in enumerate((Status.QUEUED, Status.FAILED)):
        store.add(
            MailMessage(
                msg_id=index + 1,
                source="OK7PS",
                final_dest="OK2IPW",
                body="x",
                folder=Folder.OUTBOX,
                status=status,
            )
        )

    assert store.counts()[Folder.OUTBOX] == 2
    assert store.awaiting_send() == 1
    assert store.failed() == 1

    store.set_status(2, status=Status.QUEUED)
    assert store.awaiting_send() == 2
    assert store.failed() == 0


def test_hostile_attachment_names_survive_the_bundle_without_escaping_it() -> None:
    # An attachment name is peer input. Used raw it went straight into the zip
    # path, where it escaped the archive for anything calling extractall() and
    # failed to round-trip -- the attachment was silently lost.
    mail = MailMessage(
        msg_id=7,
        source="OK2IPW",
        final_dest="OK7PS",
        attachments=[
            Attachment(r"..\..\Windows\System32\evil.txt", b"nope"),
            Attachment("../../../etc/passwd", b"also nope"),
            Attachment("photo.jpg", b"jpeg"),
            Attachment("", b"unnamed"),
        ],
    )

    restored = MailMessage.from_bundle(mail.to_bundle())

    assert len(restored.attachments) == 4
    names = [a.name for a in restored.attachments]
    assert names == ["evil.txt", "passwd", "photo.jpg", "attachment"]
    assert not any("/" in name or "\\" in name for name in names)
    assert {a.name: a.data for a in restored.attachments}["photo.jpg"] == b"jpeg"

    with zipfile.ZipFile(io.BytesIO(mail.to_bundle())) as archive:
        for entry in archive.namelist():
            assert ".." not in entry
            assert not entry.startswith("/")


def test_attachments_that_sanitise_alike_stay_distinguishable() -> None:
    mail = MailMessage(
        msg_id=8,
        source="OK2IPW",
        final_dest="OK7PS",
        attachments=[
            Attachment("a/report.pdf", b"first"),
            Attachment("b/report.pdf", b"second"),
        ],
    )

    restored = MailMessage.from_bundle(mail.to_bundle())

    assert [a.name for a in restored.attachments] == ["report.pdf", "report-2.pdf"]
    assert [a.data for a in restored.attachments] == [b"first", b"second"]


def test_store_persists_index_bundle_and_status(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    mail = _mail()
    store.add(mail)

    reloaded = MessageStore(tmp_path)
    restored = reloaded.get(mail.msg_id)

    assert reloaded.counts()[Folder.OUTBOX] == 1
    assert restored is not None
    assert restored.folder == Folder.OUTBOX
    assert restored.status == Status.QUEUED
    assert restored.next_hop == "OK1BBB"

    reloaded.set_status(mail.msg_id, folder=Folder.SENT, status=Status.DELIVERED)
    assert reloaded.counts()[Folder.OUTBOX] == 0
    assert reloaded.counts()[Folder.SENT] == 1


def test_incoming_mail_is_unread_and_delete_removes_bundle(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    incoming = _mail(77)
    incoming.final_dest = "OK7PS"

    stored = store.store_incoming(incoming.to_bundle(), "OK7PS", via="OK1BBB")

    assert stored.folder == Folder.INBOX
    assert store.unread(Folder.INBOX) == 1
    store.mark_read(stored.msg_id)
    assert store.unread(Folder.INBOX) == 0

    bundle_path = tmp_path / f"{stored.msg_id}.bundle"
    assert bundle_path.exists()
    store.delete(stored.msg_id)
    assert not bundle_path.exists()
    assert store.get(stored.msg_id) is None


def test_message_ids_are_stable_prefix_and_increasing_counter(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)

    first = store.next_id("OK7PS")
    second = store.next_id("OK7PS")

    assert first >> 20 == second >> 20
    assert (second & 0xFFFFF) == (first & 0xFFFFF) + 1


def test_clear_removes_every_message_but_keeps_the_id_counter(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    store.add(_mail(store.next_id("OK7PS")))
    store.add(_mail(store.next_id("OK7PS")))
    before = store.next_id("OK7PS")

    assert store.clear() == 2

    assert store.list() == []
    assert list(tmp_path.glob("*.bundle")) == []
    # Ids already reached other stations' session tables; a wiped mailbox is
    # no reason to mint ids the net has seen from us before.
    after = MessageStore(tmp_path).next_id("OK7PS")
    assert (after & 0xFFFFF) == (before & 0xFFFFF) + 1
