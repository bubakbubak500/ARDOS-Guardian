"""Small end-to-end check used against the frozen release executable."""

from __future__ import annotations

import io

from PIL import Image

from .aggressive import MAGIC, encode_aggressive_bounded
from .mail import Attachment, MailMessage


def run() -> str:
    image = Image.new("RGB", (640, 480))
    pixels = image.load()
    for y in range(image.height):
        for x in range(image.width):
            pixels[x, y] = (x * 255 // image.width, y * 255 // image.height, 96)

    jpeg_stream = io.BytesIO()
    image.save(jpeg_stream, format="JPEG", quality=92, progressive=True)
    png_stream = io.BytesIO()
    image.resize((320, 240)).save(png_stream, format="PNG", compress_level=1)
    original_jpeg = jpeg_stream.getvalue()
    original_png = png_stream.getvalue()
    mail = MailMessage(
        msg_id=239,
        source="SELFTEST",
        final_dest="SELFTEST",
        body="Guardian compression self-test\n" * 1000,
        attachments=[
            Attachment("evidence.jpg", original_jpeg),
            Attachment("map.png", original_png),
        ],
    )

    encoded = encode_aggressive_bounded(mail)
    if not encoded.data.startswith(MAGIC):
        raise RuntimeError("aggressive self-test did not produce a G2XZ1 envelope")
    if not any(detail.startswith("JPEG XL evidence.jpg:") for detail in encoded.details):
        raise RuntimeError(
            "frozen self-test did not use bundled JPEG XL tools: "
            + "; ".join(encoded.details)
        )
    if not any(detail.startswith("ZopfliPNG map.png:") for detail in encoded.details):
        raise RuntimeError(
            "frozen self-test did not use bundled ZopfliPNG: "
            + "; ".join(encoded.details)
        )
    restored = MailMessage.from_bundle(encoded.data)
    if restored.attachments[0].data != original_jpeg:
        raise RuntimeError("JPEG XL self-test did not restore exact JPEG bytes")
    with (
        Image.open(io.BytesIO(original_png)) as before,
        Image.open(io.BytesIO(restored.attachments[1].data)) as after,
    ):
        if before.size != after.size or before.mode != after.mode:
            raise RuntimeError("ZopfliPNG self-test changed image geometry")
        if before.tobytes() != after.tobytes():
            raise RuntimeError("ZopfliPNG self-test changed image pixels")
    return "PASS\n" + "\n".join(encoded.details) + "\n"
