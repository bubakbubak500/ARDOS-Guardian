"""Regression anchors for the G1 aggressive message-compression port."""

from __future__ import annotations

import io
import random
import zipfile

import pytest
from PIL import Image

from guardian.message import Attachment, MailMessage
from guardian.message import aggressive


def _mail(payload: bytes = b"", name: str = "report.bin") -> MailMessage:
    return MailMessage(
        msg_id=42,
        source="OK7PS",
        final_dest="OK1AAA",
        subject="Compression test",
        body="SITUATION NORMAL\n" * 1000,
        attachments=[Attachment(name, payload)] if payload else [],
    )


def test_existing_g1_zip_and_bzip2_paths_still_round_trip() -> None:
    original = _mail(b"station,frequency,snr\nOK7PS,145500000,18.2\n" * 5000, "report.csv")
    standard = original.to_bundle()
    guardian = original.to_guardian_bundle(standard)

    assert guardian.method in {"standard", "bzip2"}
    assert MailMessage.from_bundle(standard).attachments == original.attachments
    assert MailMessage.from_bundle(guardian.data).attachments == original.attachments
    with zipfile.ZipFile(io.BytesIO(standard)) as archive:
        assert archive.read("body.txt").startswith(b"SITUATION NORMAL")


def test_aggressive_xz_bundle_round_trips_without_peer_negotiation() -> None:
    original = _mail(b"station report\n" * 50_000, "report.txt")
    baseline = original.to_bundle()

    encoding = original.to_aggressive_bundle(baseline)

    assert encoding.method == "xz-lzma2"
    assert encoding.data.startswith(aggressive.MAGIC)
    assert len(encoding.data) < len(baseline)
    assert any("XZ/LZMA2 preset 7" in detail for detail in encoding.details)
    restored = MailMessage.from_bundle(encoding.data)
    assert (restored.body, restored.attachments) == (original.body, original.attachments)


def test_jpeg_xl_path_requires_bit_identical_reconstruction(monkeypatch) -> None:
    jpeg = b"\xff\xd8" + b"jpeg coefficient payload" * 20_000 + b"\xff\xd9"
    original = _mail(jpeg, "evidence.jpeg")
    seen: list[tuple[bytes, int, str]] = []

    def transcode(payload: bytes, *, timeout: float) -> bytes:
        assert payload == jpeg
        assert timeout > 0
        return b"mock-jxl"

    def reconstruct(payload: bytes, *, expected_size: int, expected_sha256: str) -> bytes:
        seen.append((payload, expected_size, expected_sha256))
        return jpeg

    monkeypatch.setattr(aggressive, "jpeg_to_jxl", transcode)
    monkeypatch.setattr(aggressive, "jxl_to_jpeg", reconstruct)

    encoding = original.to_aggressive_bundle()
    restored = MailMessage.from_bundle(encoding.data)

    assert encoding.method == "xz-lzma2"
    assert restored.attachments == original.attachments
    assert seen and seen[0][0] == b"mock-jxl"
    assert seen[0][1] == len(jpeg)


def test_zopfli_png_preserves_pixels_and_geometry() -> None:
    image = Image.new("RGB", (320, 240))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = ((x // 8) * 7 % 256, (y // 8) * 11 % 256, (x + y) % 256)
    source = io.BytesIO()
    image.save(source, format="PNG", compress_level=1)
    original = source.getvalue()

    optimized = aggressive.optimize_png_zopfli(original)

    assert len(optimized) <= len(original)
    with Image.open(io.BytesIO(original)) as before, Image.open(io.BytesIO(optimized)) as after:
        assert (before.mode, before.size, before.tobytes()) == (
            after.mode,
            after.size,
            after.tobytes(),
        )


def test_aggressive_candidate_growth_falls_back_to_standard(monkeypatch) -> None:
    original = _mail(random.Random(0x4C5A4D41).randbytes(250 * 1024), "packed.bin")
    baseline = original.to_bundle()

    def too_large(_mail):
        return aggressive.AggressiveResult(
            data=baseline + b"x", details=("synthetic growth",)
        )

    monkeypatch.setattr(aggressive, "encode_aggressive_bounded", too_large)
    encoding = original.to_aggressive_bundle(baseline)

    assert encoding.method == "standard"
    assert encoding.data == baseline
    assert encoding.details == ("synthetic growth",)


@pytest.mark.parametrize("failure", [TimeoutError("deadline"), ValueError("limit")])
def test_aggressive_error_and_timeout_fall_back_to_standard(monkeypatch, failure) -> None:
    original = _mail(b"payload" * 1000)
    baseline = original.to_bundle()

    def fail(_mail):
        raise failure

    monkeypatch.setattr(aggressive, "encode_aggressive_bounded", fail)
    encoding = original.to_aggressive_bundle(baseline)

    assert encoding.method == "standard"
    assert encoding.data == baseline
    assert type(failure).__name__ in encoding.details[0]


def test_frozen_encoder_terminates_at_its_deadline(monkeypatch) -> None:
    events: list[object] = []

    class Receiver:
        def poll(self, timeout):
            events.append(("poll", timeout))
            return False

        def close(self):
            events.append("receiver-close")

    class Sender:
        def close(self):
            events.append("sender-close")

    class Process:
        alive = True

        def start(self):
            events.append("start")

        def terminate(self):
            events.append("terminate")
            self.alive = False

        def join(self, timeout):
            events.append(("join", timeout))

        def is_alive(self):
            return self.alive

    class Context:
        def Pipe(self, *, duplex):
            assert duplex is False
            return Receiver(), Sender()

        def Process(self, *, target, args, daemon):
            assert target is aggressive._encode_in_child
            assert daemon is True
            return Process()

    monkeypatch.setattr(aggressive.sys, "frozen", True, raising=False)
    monkeypatch.setattr(aggressive.multiprocessing, "get_context", lambda mode: Context())

    with pytest.raises(TimeoutError, match="50 seconds"):
        aggressive.encode_aggressive_bounded(_mail(b"compress me" * 1000))

    assert "terminate" in events
    assert ("poll", aggressive.ENCODING_TIMEOUT_SECONDS) in events
