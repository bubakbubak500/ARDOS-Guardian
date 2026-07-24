"""Structured interoperable message templates.

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
    label_cs: str = ""
    multiline: bool = False

    def display_label(self, language: str = "en") -> str:
        return self.label_cs if language == "cs" and self.label_cs else self.label


@dataclass
class Form:
    code: str
    name: str
    name_cs: str
    description: str
    description_cs: str
    fields: list[FormField]
    subject_key: str = ""          # field whose value becomes the subject
    subject_prefix: str = ""       # e.g. "[ICS-213] "

    def display_name(self, language: str = "en") -> str:
        name = self.name_cs if language == "cs" else self.name
        return f"{self.code} — {name}"

    def display_description(self, language: str = "en") -> str:
        return self.description_cs if language == "cs" else self.description

    def render(self, values: dict[str, str]) -> str:
        lines = [f"=== {self.code} — {self.name} ===", ""]
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
        code="ICS-213",
        name="ICS-213 General Message",
        name_cs="Obecná zpráva",
        description=(
            "FEMA/NIMS General Message. Use for formal incident messages and "
            "retain approval/reply information when applicable."
        ),
        description_cs=(
            "Obecná zpráva FEMA/NIMS. Použijte pro formální zprávy při zásahu "
            "a podle potřeby zachovejte údaje o schválení a odpovědi."
        ),
        subject_key="subject", subject_prefix="[ICS-213] ",
        fields=[
            FormField("incident", "INCIDENT NAME (optional)", "NÁZEV UDÁLOSTI (volitelné)"),
            FormField("to", "TO (name and position)", "KOMU (jméno a funkce)"),
            FormField("from_", "FROM (name and position)", "OD (jméno a funkce)"),
            FormField("subject", "SUBJECT", "PŘEDMĚT"),
            FormField("date", "DATE", "DATUM"),
            FormField("time", "TIME (local, 24-hour)", "ČAS (místní, 24hodinový)"),
            FormField("message", "MESSAGE", "ZPRÁVA", multiline=True),
            FormField(
                "approved",
                "APPROVED BY (name / position)",
                "SCHVÁLIL(A) (jméno / funkce)",
            ),
            FormField("reply", "REPLY", "ODPOVĚĎ", multiline=True),
            FormField(
                "replied_by",
                "REPLIED BY (name / position / date-time)",
                "ODPOVĚDĚL(A) (jméno / funkce / datum a čas)",
            ),
        ],
    ),
    "ICS-214": Form(
        code="ICS-214",
        name="Activity Log",
        name_cs="Záznam činnosti",
        description=(
            "FEMA/NIMS Activity Log. Record notable actions and communications "
            "in chronological order for the operational period."
        ),
        description_cs=(
            "Záznam činnosti FEMA/NIMS. Zapisujte významné činnosti a komunikaci "
            "chronologicky v rámci operačního období."
        ),
        subject_key="incident",
        subject_prefix="[ICS-214] ",
        fields=[
            FormField("incident", "INCIDENT NAME", "NÁZEV UDÁLOSTI"),
            FormField(
                "period_from",
                "OPERATIONAL PERIOD FROM (date/time)",
                "OPERAČNÍ OBDOBÍ OD (datum/čas)",
            ),
            FormField(
                "period_to",
                "OPERATIONAL PERIOD TO (date/time)",
                "OPERAČNÍ OBDOBÍ DO (datum/čas)",
            ),
            FormField("unit", "UNIT / RESOURCE DESIGNATOR", "JEDNOTKA / PROSTŘEDEK"),
            FormField("position", "ICS POSITION", "FUNKCE ICS"),
            FormField("agency", "HOME AGENCY / UNIT", "DOMOVSKÁ ORGANIZACE / JEDNOTKA"),
            FormField(
                "resources",
                "RESOURCES ASSIGNED",
                "PŘIDĚLENÉ PROSTŘEDKY",
                multiline=True,
            ),
            FormField(
                "activities",
                "ACTIVITY LOG (date/time — notable activity)",
                "ZÁZNAM ČINNOSTI (datum/čas — významná činnost)",
                multiline=True,
            ),
            FormField(
                "prepared",
                "PREPARED BY (name / position / date-time)",
                "ZPRACOVAL(A) (jméno / funkce / datum a čas)",
            ),
        ],
    ),
    "IARU": Form(
        code="IARU",
        name="International Emergency Message",
        name_cs="Mezinárodní tísňová zpráva",
        description=(
            "Radiogram layout for international amateur-radio emergency "
            "traffic. Keep the text concise and preserve the preamble."
        ),
        description_cs=(
            "Radiogram pro mezinárodní radioamatérský nouzový provoz. Text "
            "udržujte stručný a zachovejte záhlaví zprávy."
        ),
        subject_key="address",
        subject_prefix="[IARU] ",
        fields=[
            FormField("number", "MESSAGE NUMBER", "ČÍSLO ZPRÁVY"),
            FormField("precedence", "PRECEDENCE", "NALÉHAVOST"),
            FormField("origin", "STATION OF ORIGIN", "VYSÍLAJÍCÍ STANICE"),
            FormField("check", "CHECK (word count)", "KONTROLA (počet slov)"),
            FormField("place", "PLACE OF ORIGIN", "MÍSTO PŮVODU"),
            FormField("date", "FILING DATE", "DATUM PODÁNÍ"),
            FormField("time", "FILING TIME (UTC)", "ČAS PODÁNÍ (UTC)"),
            FormField("address", "ADDRESS / ADDRESSEE", "ADRESA / ADRESÁT"),
            FormField("text", "MESSAGE TEXT", "TEXT ZPRÁVY", multiline=True),
            FormField("signature", "SIGNATURE", "PODPIS"),
        ],
    ),
    "SITREP": Form(
        code="SITREP",
        name="Situation Report",
        name_cs="Situační hlášení",
        description=(
            "Concise operational situation report for local procedures. "
            "This is a Guardian template, not a numbered FEMA ICS form."
        ),
        description_cs=(
            "Stručné operační situační hlášení pro místní postupy. Jde o "
            "šablonu Guardianu, nikoli číslovaný formulář FEMA ICS."
        ),
        subject_key="situation", subject_prefix="[SITREP] ",
        fields=[
            FormField("datetime", "DATE/TIME", "DATUM/ČAS"),
            FormField("location", "LOCATION", "MÍSTO"),
            FormField("situation", "SITUATION", "SITUACE"),
            FormField("casualties", "CASUALTIES", "POSTIŽENÉ OSOBY"),
            FormField("actions", "ACTIONS TAKEN", "PROVEDENÁ OPATŘENÍ", multiline=True),
            FormField("needs", "RESOURCES NEEDED", "POTŘEBNÉ PROSTŘEDKY", multiline=True),
            FormField("reporter", "REPORTING STATION", "HLÁSÍCÍ STANICE"),
        ],
    ),
}


def form_names() -> list[str]:
    return ["Plain", *FORMS.keys()]
