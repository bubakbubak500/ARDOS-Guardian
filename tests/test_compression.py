import io
import random
import zipfile

from guardian.message import Attachment, MailMessage


def _mail(payload: bytes, name: str = "report.bin") -> MailMessage:
    return MailMessage(
        msg_id=42,
        source="OK7PS",
        final_dest="OK1AAA",
        subject="Compression test",
        body="SITUATION NORMAL\n" * 1000,
        attachments=[Attachment(name, payload)],
    )


def test_guardian_compression_uses_only_bzip2_and_round_trips(monkeypatch) -> None:
    original = _mail(
        b"station,frequency,snr\nOK7PS,145500000,18.2\n" * 6000,
        "report.csv",
    )
    baseline = original.to_bundle()
    calls = []
    bundle_with = MailMessage._bundle_with

    def record(self, compression, *, compresslevel=None):
        calls.append((compression, compresslevel))
        return bundle_with(self, compression, compresslevel=compresslevel)

    monkeypatch.setattr(MailMessage, "_bundle_with", record)
    encoding = original.to_guardian_bundle(baseline)

    assert calls == [(zipfile.ZIP_BZIP2, 9)]
    assert encoding.method == "bzip2"
    assert len(encoding.data) < len(baseline)
    with zipfile.ZipFile(io.BytesIO(encoding.data)) as archive:
        assert {entry.compress_type for entry in archive.infolist()} == {
            zipfile.ZIP_BZIP2
        }
    assert MailMessage.from_bundle(encoding.data).attachments == original.attachments


def test_guardian_compression_keeps_standard_bundle_when_bzip2_would_grow() -> None:
    payload = random.Random(0x47554152).randbytes(250 * 1024)
    original = _mail(payload, "already-packed.jpg")
    baseline = original.to_bundle()

    encoding = original.to_guardian_bundle(baseline)

    assert encoding.method == "standard"
    assert encoding.data == baseline
    assert encoding.saved_bytes == 0
    assert MailMessage.from_bundle(encoding.data).attachments == original.attachments
