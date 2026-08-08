"""Guardian app icon, generated with Pillow.

A shield (guardian) carrying broadcast/radio waves and an antenna spark, with a
large red "2" stamped over the right half to mark the G2 line — drawn
programmatically so there's no binary blob to ship, and it scales cleanly to
the 16px tray size and the 256px taskbar size.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from ..config import config_dir

# Bump whenever build_image() changes what the icon looks like. It is folded
# into the cached filename so an operator who has already launched Guardian
# once gets the new artwork; see get_ico_path().
ICON_REVISION = 2

# Palette
_BORDER = (21, 67, 96, 255)     # deep navy
_FILL = (36, 113, 163, 255)     # guardian blue
_WAVE = (234, 240, 245, 255)    # near-white
_SPARK = (241, 196, 15, 255)    # amber spark
# G2 mark. The red and the guardian blue have almost the same luminance, so red
# alone smears into the shield at 16px; _G2_EDGE is a dark halo drawn under the
# glyph to hold the edge at every size.
_G2 = (236, 58, 47, 255)        # G2 red
_G2_EDGE = (12, 34, 48, 255)    # near-black navy halo


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


# The "2" is drawn with vector primitives rather than a font: get_ico_path()
# runs on end-user machines, where no particular TrueType file is guaranteed to
# exist, and ImageFont.load_default(size=...) needs Pillow >= 10.1 while
# requirements.txt only asks for >= 10.0.
# Fractions of the canvas, except _TWO_ARC (degrees) and _TWO_OVERLAP (strokes).
_TWO_BOX = (0.43, 0.19, 0.85, 0.72)   # left, top, right, bottom: the glyph's extent
_TWO_STROKE = 0.115                   # stroke width
_TWO_BOWL = 0.58                      # bowl height, as a fraction of the glyph box
_TWO_BOWL_INSET = 0.045               # bowl is this much narrower than the bar
_TWO_FOOT = 0.06                      # diagonal lands this far right of the bar's end
# Where the bowl starts and where it hands over to the diagonal. The end angle
# is tuned so the bowl's tangent there runs straight into the diagonal: too
# little and the two meet in a kink, too much and the bowl overshoots it. It
# needs re-tuning if _TWO_BOWL_INSET, _TWO_BOWL or _TWO_FOOT change.
_TWO_ARC = (152.0, 36.0)
_TWO_OVERLAP = 0.30                   # diagonal backs this far into the bowl's end


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = math.hypot(dx, dy) or 1.0
    return dx / length, dy / length


def _two_shapes(s: float, grow: float = 0.0):
    """The "2" for a canvas of size s: a bowl arc, a straight wedge and a bar.

    Filling three overlapping shapes, rather than stroking a path, is what keeps
    the joints clean: the bar's ends stay square, and the wedge is buried in the
    bowl at one end and in the bar at the other, so there is nothing to patch.

    `grow` inflates the glyph by that many pixels on every edge, which is how the
    dark halo is produced; both passes describe the same letter.
    """
    left, top, right, bottom = (n * s for n in _TWO_BOX)
    left, top, right, bottom = left - grow, top - grow, right + grow, bottom + grow
    t = _TWO_STROKE * s + 2 * grow

    # Bowl: an elliptical arc filling the top of the box. ImageDraw.arc puts the
    # stroke *inside* the box it is given and thickens it radially, so the box is
    # the bowl's outer edge and the stroke runs inward from there.
    inset = _TWO_BOWL_INSET * s
    a, b = (right - left) / 2 - inset, _TWO_BOWL * (bottom - top) / 2
    cx, cy = (left + right) / 2, top + b
    bbox = [cx - a, cy - b, cx + a, cy + b]
    end = math.radians(_TWO_ARC[1])
    radial = (math.cos(end), math.sin(end))
    tip = (cx + a * radial[0], cy + b * radial[1])

    # Baseline: a plain rectangle. Square ends, exactly one stroke thick, and no
    # end cap that can bulge wider than the stem.
    bar = [left, bottom - t, right, bottom]

    # Diagonal: one quadrilateral from the bowl's open end down to the baseline.
    # Its top edge is the arc's own radial end cap, backed a little way into the
    # bowl, and its bottom edge lies on the baseline inside the bar — so both
    # ends of it are invisible and neither can read as a bulb.
    over = t * _TWO_OVERLAP
    back = _unit(a * math.sin(end), -b * math.cos(end))    # back along the bowl
    outer = (tip[0] + back[0] * over, tip[1] + back[1] * over)
    inner = (outer[0] - radial[0] * t, outer[1] - radial[1] * t)

    foot = (left + _TWO_FOOT * s, bottom)
    ux, uy = _unit(inner[0] - foot[0], inner[1] - foot[1])
    down = (bottom - outer[1]) / -uy if uy else 0.0        # back to the baseline
    heel = (outer[0] - ux * down, bottom)
    return bbox, bar, [foot, inner, outer, heel], max(1, int(round(t)))


def _draw_two(d: ImageDraw.ImageDraw, s: float, grow: float, color) -> None:
    """Fill the G2 "2" — bowl, wedge and bar — in one colour."""
    bbox, bar, wedge, width = _two_shapes(s, grow)

    d.arc(bbox, start=_TWO_ARC[0], end=_TWO_ARC[1], fill=color, width=width)
    d.polygon(wedge, fill=color)
    d.rectangle(bar, fill=color)


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

    # The G2 mark: dark halo first, then the red glyph on top of it.
    _draw_two(d, s, 0.016 * s, _G2_EDGE)
    _draw_two(d, s, 0.0, _G2)

    return img.resize((size, size), Image.LANCZOS)


def ensure_ico(path: Path, *, overwrite: bool = False) -> Path:
    """Write a multi-resolution .ico to `path` (idempotent unless `overwrite`).

    The build writes to a fixed path that PyInstaller and Inno Setup read, so it
    passes overwrite=True: a checked-out tree can already hold an .ico from an
    older revision of the artwork, and it would otherwise be shipped as-is.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite or not path.exists():
        base = build_image(256)
        base.save(path, format="ICO",
                  sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


def get_ico_path() -> Path:
    """Path to the cached .ico, generating it on first use.

    The revision is part of the filename rather than a marker file: an operator
    who has launched Guardian once already has guardian.ico in their config
    directory, and an existence check would keep serving that stale artwork
    forever. A new name simply misses the cache and regenerates, with no
    read-compare-rewrite step that could half-fail.
    """
    return ensure_ico(config_dir() / f"guardian-g2-r{ICON_REVISION}.ico")


def get_tray_image() -> Image.Image:
    """A PIL image suitable for the system-tray icon.

    Currently unused — the Qt shell reads get_ico_path() — but it renders from
    build_image(), so it cannot drift away from the shipped artwork.
    """
    return build_image(64)
