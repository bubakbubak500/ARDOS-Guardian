"""Guardian app icon, generated with Pillow.

A shield (guardian) carrying broadcast/radio waves and an antenna spark — drawn
programmatically so there's no binary blob to ship, and it scales cleanly to
the 16px tray size and the 256px taskbar size.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ..config import config_dir

# Palette
_BORDER = (21, 67, 96, 255)     # deep navy
_FILL = (36, 113, 163, 255)     # guardian blue
_WAVE = (234, 240, 245, 255)    # near-white
_SPARK = (241, 196, 15, 255)    # amber spark


def _shield_points(s: float, inset: float = 0.0):
    """Return shield polygon points for a canvas of size s, optional inset."""
    pts = [
        (0.16, 0.17), (0.84, 0.17), (0.84, 0.52),
        (0.66, 0.74), (0.50, 0.90), (0.34, 0.74), (0.16, 0.52),
    ]
    cx, cy = 0.5, 0.52
    out = []
    for nx, ny in pts:
        # Pull points slightly toward the centre to make an inset shield.
        nx += (cx - nx) * inset
        ny += (cy - ny) * inset
        out.append((nx * s, ny * s))
    return out


def build_image(size: int = 256) -> Image.Image:
    """Render the Guardian icon at the requested square size."""
    # Render at 4x then downsample for clean anti-aliased edges.
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Shield: navy border, blue fill.
    d.polygon(_shield_points(s), fill=_BORDER)
    d.polygon(_shield_points(s, inset=0.10), fill=_FILL)

    # Broadcast waves: concentric arcs opening upward from a spark.
    cx, cy = 0.5 * s, 0.60 * s
    spark_r = 0.035 * s
    for i, r in enumerate((0.12, 0.19, 0.26)):
        rr = r * s
        bbox = [cx - rr, cy - rr, cx + rr, cy + rr]
        d.arc(bbox, start=210, end=330, fill=_WAVE, width=max(2, int(0.022 * s)))
    # The spark / antenna base.
    d.ellipse([cx - spark_r, cy - spark_r, cx + spark_r, cy + spark_r], fill=_SPARK)

    return img.resize((size, size), Image.LANCZOS)


def ensure_ico(path: Path) -> Path:
    """Write a multi-resolution .ico to `path` (idempotent)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        base = build_image(256)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def get_ico_path() -> Path:
    """Path to the cached .ico, generating it on first use."""
    return ensure_ico(config_dir() / "guardian.ico")


def get_tray_image() -> Image.Image:
    """A PIL image suitable for the system-tray icon."""
    return build_image(64)
