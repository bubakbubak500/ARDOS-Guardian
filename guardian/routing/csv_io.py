"""Read and write the route table as a spreadsheet-friendly CSV.

CSV rather than xlsx on purpose: it opens by double-click in Excel and
LibreOffice Calc, needs no extra dependency in the frozen build, and can still
be read and repaired in Notepad on a field laptop.

Written as UTF-8 **with BOM** and semicolon-separated, which is what a Czech
Excel expects; without the BOM it mangles diacritics, and with a comma it puts
every row in one cell. Reading is deliberately more forgiving: either
separator, BOM or not, any header case, and a decimal comma or point.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .route_table import Route

COLUMNS = (
    "destination",
    "preferred",
    "backup",
    "frequency_mhz",
    "mode",
    "working_frequency_mhz",
    "working_mode",
)

TEMPLATE_ROWS = (
    Route("OK2IPW", "", "", 145_237_500, "FM"),
    Route("OK1AAA", "OK2IPW", "ANY", 145_300_000, "FM"),
    Route("OSTRAVA", "OK1AAA", "", 0, ""),
)


@dataclass
class ImportReport:
    """What an import did, so the operator is never left guessing."""

    routes: list[Route]
    problems: list[str]

    @property
    def imported(self) -> int:
        return len(self.routes)


def _format_mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000:.4f}" if freq_hz else ""


def routes_to_csv(routes) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for route in routes:
        writer.writerow([
            route.destination,
            route.preferred,
            route.backup,
            _format_mhz(route.freq_hz),
            route.mode,
            _format_mhz(route.working_freq_hz),
            route.working_mode,
        ])
    return buffer.getvalue()


def write_csv(path: Path | str, routes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig writes the BOM Excel needs to detect UTF-8.
    path.write_text(routes_to_csv(routes), encoding="utf-8-sig")
    return path


def template_csv() -> str:
    return routes_to_csv(TEMPLATE_ROWS)


def _parse_mhz(value: str) -> int:
    """Accept 145.2375, 145,2375 or a bare Hz figure."""
    text = value.strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0
    number = float(text)
    # Nobody works a station below 1 MHz here, so a large number is already Hz.
    return int(round(number if number > 1_000_000 else number * 1_000_000))


def routes_from_csv(text: str) -> ImportReport:
    text = text.lstrip("﻿")
    if not text.strip():
        return ImportReport([], ["the file is empty"])

    sample = text.splitlines()[0]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return ImportReport([], ["the file has no rows"])

    header = [cell.strip().lower().lstrip("﻿") for cell in rows[0]]
    if "destination" in header:
        index = {name: header.index(name) for name in COLUMNS if name in header}
        body = rows[1:]
    else:
        # No header: assume the documented column order.
        index = {name: position for position, name in enumerate(COLUMNS)}
        body = rows

    def cell(row: list[str], name: str) -> str:
        position = index.get(name)
        if position is None or position >= len(row):
            return ""
        return row[position].strip()

    routes: list[Route] = []
    problems: list[str] = []
    seen: set[str] = set()
    for number, row in enumerate(body, start=2 if body is not rows else 1):
        destination = cell(row, "destination").upper()
        if not destination:
            problems.append(f"row {number}: no destination, skipped")
            continue
        if destination in seen:
            problems.append(f"row {number}: {destination} repeated, last one wins")
        seen.add(destination)
        try:
            freq_hz = _parse_mhz(cell(row, "frequency_mhz"))
        except ValueError:
            problems.append(
                f"row {number}: {destination} has an unreadable frequency "
                f"{cell(row, 'frequency_mhz')!r}, imported without one"
            )
            freq_hz = 0
        try:
            working_freq_hz = _parse_mhz(cell(row, "working_frequency_mhz"))
        except ValueError:
            problems.append(
                f"row {number}: {destination} has an unreadable working frequency "
                f"{cell(row, 'working_frequency_mhz')!r}, imported without one"
            )
            working_freq_hz = 0
        routes.append(
            Route(
                destination=destination,
                preferred=cell(row, "preferred"),
                backup=cell(row, "backup"),
                freq_hz=freq_hz,
                mode=cell(row, "mode"),
                working_freq_hz=working_freq_hz,
                working_mode=cell(row, "working_mode"),
            ).normalised()
        )
    return ImportReport(routes, problems)


def read_csv(path: Path | str) -> ImportReport:
    path = Path(path)
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1250"):
        try:
            return routes_from_csv(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return ImportReport([], [f"{path.name} is not readable text"])
