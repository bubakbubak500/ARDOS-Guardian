"""Spreadsheet-friendly import/export for one shared Guardian topology."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .topology import DIRECTIONS, Link, Topology


COLUMNS = (
    "station_a",
    "station_b",
    "direction",
    "frequency_mhz",
    "mode",
    "working_frequency_mhz",
    "working_mode",
    "cost",
    "enabled",
)


@dataclass
class TopologyImportReport:
    topology: Topology
    problems: list[str]

    @property
    def imported(self) -> int:
        return len(self.topology.links)


def _format_mhz(freq_hz: int) -> str:
    return f"{freq_hz / 1_000_000:.4f}" if freq_hz else ""


def _parse_mhz(value: str) -> int:
    text = value.strip().replace(" ", "").replace(",", ".")
    if not text:
        return 0
    number = float(text)
    return int(round(number if number > 1_000_000 else number * 1_000_000))


def topology_to_csv(topology: Topology) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for link in topology.links:
        writer.writerow(
            [
                link.station_a,
                link.station_b,
                link.direction,
                _format_mhz(link.freq_hz),
                link.mode,
                _format_mhz(link.working_freq_hz),
                link.working_mode,
                f"{link.cost:g}",
                "yes" if link.enabled else "no",
            ]
        )
    return buffer.getvalue()


def write_topology_csv(path: Path | str, topology: Topology) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(topology_to_csv(topology), encoding="utf-8-sig")
    return path


def topology_from_csv(text: str) -> TopologyImportReport:
    text = text.lstrip("\ufeff")
    if not text.strip():
        return TopologyImportReport(Topology(), ["the file is empty"])
    sample = text.splitlines()[0]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    rows = [
        row
        for row in csv.reader(io.StringIO(text), delimiter=delimiter)
        if any(cell.strip() for cell in row)
    ]
    if not rows:
        return TopologyImportReport(Topology(), ["the file has no rows"])
    header = [cell.strip().lower().lstrip("\ufeff") for cell in rows[0]]
    if "station_a" in header:
        index = {name: header.index(name) for name in COLUMNS if name in header}
        body = rows[1:]
        first_number = 2
    else:
        index = {name: position for position, name in enumerate(COLUMNS)}
        body = rows
        first_number = 1

    def cell(row: list[str], name: str) -> str:
        position = index.get(name)
        return row[position].strip() if position is not None and position < len(row) else ""

    topology = Topology()
    problems: list[str] = []
    for number, row in enumerate(body, start=first_number):
        direction = (cell(row, "direction") or "both").lower()
        if direction not in DIRECTIONS:
            problems.append(f"row {number}: unknown direction {direction!r}, using both")
            direction = "both"
        try:
            frequency = _parse_mhz(cell(row, "frequency_mhz"))
        except ValueError:
            problems.append(f"row {number}: unreadable calling frequency, using none")
            frequency = 0
        try:
            working_frequency = _parse_mhz(cell(row, "working_frequency_mhz"))
        except ValueError:
            problems.append(f"row {number}: unreadable working frequency, using none")
            working_frequency = 0
        try:
            cost = float((cell(row, "cost") or "1").replace(",", "."))
            if cost <= 0:
                raise ValueError
        except ValueError:
            problems.append(f"row {number}: cost must be positive, using 1")
            cost = 1.0
        enabled_text = cell(row, "enabled").lower()
        enabled = enabled_text not in ("0", "false", "no", "off", "ne")
        link = Link(
            station_a=cell(row, "station_a"),
            station_b=cell(row, "station_b"),
            direction=direction,
            freq_hz=frequency,
            mode=cell(row, "mode"),
            working_freq_hz=working_frequency,
            working_mode=cell(row, "working_mode"),
            cost=cost,
            enabled=enabled,
        )
        if link.problems():
            problems.append(f"row {number}: {'; '.join(link.problems())}, skipped")
            continue
        topology.add(link)
    return TopologyImportReport(topology, problems)


def read_topology_csv(path: Path | str) -> TopologyImportReport:
    path = Path(path)
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp1250"):
        try:
            return topology_from_csv(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
    return TopologyImportReport(Topology(), [f"{path.name} is not readable text"])
