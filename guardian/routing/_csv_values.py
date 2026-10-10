"""Frequency cells shared by route and topology CSV import/export."""

from __future__ import annotations


def format_mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000:.4f}" if freq_hz else ""


def parse_mhz(value: str) -> int:
    """Accept decimal MHz (point or comma), a bare Hz figure, or an empty cell."""
    text = value.strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0
    number = float(text)
    return int(round(number if number > 1_000_000 else number * 1_000_000))
