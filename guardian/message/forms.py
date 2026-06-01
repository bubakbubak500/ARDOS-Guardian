"""Structured message templates (ICS-213, SITREP).

A form is a set of named fields. Filling it produces a clean, fixed-layout
plaintext body plus a subject line, so emergency traffic is consistent and
readable on any receiving station — no special viewer needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FormField:
    key: str
    label: str
    multiline: bool = False


@dataclass
class Form:
    name: str
    fields: list[FormField]
    subject_key: str = ""          # field whose value becomes the subject
    subject_prefix: str = ""       # e.g. "[ICS-213] "

    def render(self, values: dict[str, str]) -> str:
        lines = [f"=== {self.name} ===", ""]
        for f in self.fields:
            val = (values.get(f.key, "") or "").rstrip()
            if f.multiline:
                lines.append(f"{f.label}:")
                lines.extend("  " + ln for ln in (val.splitlines() or [""]))
            else:
                lines.append(f"{f.label}: {val}")
        return "\n".join(lines)

    def subject(self, values: dict[str, str]) -> str:
        base = values.get(self.subject_key, "").strip() if self.subject_key else ""
        return f"{self.subject_prefix}{base}".strip()


FORMS: dict[str, Form] = {
    "ICS-213": Form(
        name="ICS-213 General Message",
        subject_key="subject", subject_prefix="[ICS-213] ",
        fields=[
            FormField("to", "TO (name/position)"),
            FormField("from_", "FROM (name/position)"),
            FormField("subject", "SUBJECT"),
            FormField("datetime", "DATE/TIME"),
            FormField("message", "MESSAGE", multiline=True),
            FormField("approved", "APPROVED BY (name/sig)"),
        ],
    ),
    "SITREP": Form(
        name="Situation Report",
        subject_key="situation", subject_prefix="[SITREP] ",
        fields=[
            FormField("datetime", "DATE/TIME"),
            FormField("location", "LOCATION"),
            FormField("situation", "SITUATION"),
            FormField("casualties", "CASUALTIES"),
            FormField("actions", "ACTIONS TAKEN", multiline=True),
            FormField("needs", "RESOURCES NEEDED", multiline=True),
            FormField("reporter", "REPORTING STATION"),
        ],
    ),
}


def form_names() -> list[str]:
    return ["Plain", *FORMS.keys()]
