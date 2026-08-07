"""Small stylesheet glyphs, drawn with Pillow and cached on disk.

Qt stylesheets can only point at a file, and a styled ``QCheckBox::indicator``
loses the native check mark entirely -- the widget style stops drawing it as
soon as the sheet claims the sub-control. Guardian has to restyle the indicator
(the Windows 11 style paints a near-black box regardless of the dark palette,
which is invisible against the dark surfaces), so it has to supply the mark
itself. Drawing it here keeps the repository free of binary blobs, exactly like
the application icon.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ..config import config_dir

# Drawn at 4x and downsampled, so the diagonal strokes stay clean at 14 px.
_SCALE = 4
_SIZE = 14


def _rgba(colour: str) -> tuple[int, int, int, int]:
    value = colour.lstrip("#")
    if len(value) == 3:
        value = "".join(channel * 2 for channel in value)
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        255,
    )


def build_check(colour: str, size: int = _SIZE) -> Image.Image:
    """Render a check mark of `colour` on a transparent square."""
    side = size * _SCALE
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = _rgba(colour)
    width = max(2, int(side * 0.13))
    draw.line(
        [
            (side * 0.22, side * 0.53),
            (side * 0.42, side * 0.73),
            (side * 0.79, side * 0.28),
        ],
        fill=ink,
        width=width,
        joint="curve",
    )
    # Round the stroke ends; PIL's line joints do not extend past the vertices.
    radius = width / 2
    for x, y in ((0.22, 0.53), (0.79, 0.28)):
        centre = (side * x, side * y)
        draw.ellipse(
            [
                centre[0] - radius,
                centre[1] - radius,
                centre[0] + radius,
                centre[1] + radius,
            ],
            fill=ink,
        )
    return image.resize((size, size), Image.LANCZOS)


def build_dash(colour: str, size: int = _SIZE) -> Image.Image:
    """Render the partially-checked dash of `colour`."""
    side = size * _SCALE
    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    height = max(2, int(side * 0.13))
    top = (side - height) / 2
    draw.rounded_rectangle(
        [side * 0.24, top, side * 0.76, top + height],
        radius=height / 2,
        fill=_rgba(colour),
    )
    return image.resize((size, size), Image.LANCZOS)


def _cached(name: str, colour: str, builder) -> Path:
    """Write `builder(colour)` to the glyph cache once and return its path.

    A glyph that cannot be written is not worth failing a start-up over: Qt
    ignores a `url()` it cannot load, which leaves a checked box filled with
    the accent colour and no tick -- still unambiguous, just plainer.
    """
    directory = config_dir() / "glyphs"
    path = directory / f"{name}-{colour.lstrip('#').lower()}.png"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            builder(colour).save(path, format="PNG")
    except OSError:
        pass
    return path


def check_path(colour: str) -> Path:
    return _cached("check", colour, build_check)


def dash_path(colour: str) -> Path:
    return _cached("dash", colour, build_dash)


def stylesheet_url(path: Path) -> str:
    """A `url(...)` value Qt accepts on Windows paths with spaces."""
    return f'url("{path.as_posix()}")'
