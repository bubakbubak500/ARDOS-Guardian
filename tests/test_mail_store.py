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
