"""Small runtime translation catalog shared by the UI and operational events."""

from __future__ import annotations

from enum import StrEnum


class Language(StrEnum):
    ENGLISH = "en"
    CZECH = "cs"


_language = Language.ENGLISH


def set_language(value: Language | str) -> Language:
    global _language
    try:
        _language = Language(value)
    except ValueError:
        _language = Language.ENGLISH
    return _language


def language() -> Language:
    return _language


def tr(key: str, **values: object) -> str:
    pair = TRANSLATIONS.get(key)
    template = pair[1] if pair and _language == Language.CZECH else (
        pair[0] if pair else key
    )
    return template.format(**values)


def dual(english: str, czech: str, **values: object) -> str:
    template = czech if _language == Language.CZECH else english
    return template.format(**values)


TRANSLATIONS: dict[str, tuple[str, str]] = {
    # Common
    "common.close": ("Close", "Zavřít"),
    "common.cancel": ("Cancel", "Zrušit"),
    "common.save": ("Save", "Uložit"),
    "common.apply": ("Apply", "Použít"),
    "common.refresh": ("Refresh", "Obnovit"),
    "common.delete": ("Delete", "Odstranit"),
    "common.ready": ("Ready", "Připraveno"),
    "common.missing": ("Missing", "Chybí"),
    "common.available": ("Available", "Dostupné"),
    "common.configured": ("Configured", "Nastaveno"),
    "common.not_configured": ("Not configured", "Nenastaveno"),
    "common.yes": ("Yes", "Ano"),
    "common.no": ("No", "Ne"),
    # Main menu and shell
    "menu.file": ("&File", "&Soubor"),
    "menu.exit": ("Exit", "Ukončit"),
    "menu.view": ("&View", "&Zobrazení"),
    "menu.home": ("Home", "Domů"),
    "menu.mail": ("Mail", "Pošta"),
    "menu.network": ("Network", "Síť"),
    "menu.log": ("Log", "Provozní log"),
    "menu.tools": ("&Tools", "&Provoz"),
    "menu.radio_toggle": (
        "Connect / disconnect radio",
        "Připojit / odpojit rádio",
    ),
    "menu.vara_toggle": (
        "Connect / disconnect VARA",
        "Připojit / odpojit VARA",
    ),
    "menu.control_toggle": (
        "Start / stop control channel",
        "Spustit / zastavit řídicí kanál",
    ),
    "menu.readiness": ("Station readiness", "Připravenost stanice"),
    "menu.diagnostics": ("Diagnostics", "Diagnostika"),
    "menu.updates": ("Check for updates", "Zkontrolovat aktualizace"),
    "menu.settings": ("&Settings", "&Nastavení"),
    "menu.station_settings": ("Station settings", "Nastavení stanice"),
    "menu.theme": ("Theme", "Motiv"),
    "theme.system": ("Follow system", "Podle systému"),
    "theme.light": ("Light", "Světlý"),
    "theme.dark": ("Dark", "Tmavý"),
    "menu.help": ("&Help", "&Nápověda"),
    "menu.user_guide": ("Guardian help", "Nápověda Guardianu"),
    "menu.about": ("About Guardian", "O aplikaci Guardian"),
    "shell.ready": (
        "Guardian operational workspace ready",
        "Provozní plocha Guardianu je připravena",
    ),
    "shell.station_context": ("STATION CONTEXT", "KONTEXT STANICE"),
    "shell.operation": ("OPERATION", "PROVOZ"),
    "shell.station_idle": ("Station idle", "Stanice je neaktivní"),
    "shell.operation_detail": (
        "Connect hardware explicitly, then start the audio control channel "
        "when the station is ready to exchange ARDOS frames.",
        "Nejprve výslovně připojte hardware. Až bude stanice připravena k "
        "výměně rámců ARDOS, spusťte zvukový řídicí kanál.",
    ),
    "shell.connect_radio": ("Connect radio", "Připojit rádio"),
    "shell.disconnect_radio": ("Disconnect radio", "Odpojit rádio"),
    "shell.connect_vara": ("Connect VARA", "Připojit VARA"),
    "shell.disconnect_vara": ("Disconnect VARA", "Odpojit VARA"),
    "shell.start_control": ("Start control", "Spustit řízení"),
    "shell.stop_control": ("Stop control", "Zastavit řízení"),
    "metric.inbox": ("Inbox", "Doručené"),
    "metric.unread": ("Unread", "Nepřečtené"),
    "metric.outbox": ("Outbox", "K odeslání"),
    "metric.transit": ("Transit", "Předávané"),
    "metric.sessions": ("Sessions", "Relace"),
    "metric.heard": ("Heard", "Slyšené"),
    "readiness.title": ("Station readiness", "Připravenost stanice"),
    "readiness.short": (
        "A concise view of the components required for normal operation.",
        "Stručný přehled součástí potřebných pro běžný provoz.",
    ),
    "readiness.component": ("Component", "Součást"),
    "readiness.state": ("State", "Stav"),
    "readiness.detail": ("Detail", "Podrobnosti"),
    "readiness.hint": (
        "Use Tools > Station readiness to locate or install missing components.",
        "Chybějící součásti vyhledejte nebo nainstalujte přes Provoz > "
        "Připravenost stanice.",
    ),
    "activity.title": ("Activity", "Aktivita"),
    "activity.events": ("{count} events", "{count} událostí"),
    "activity.accessible": ("Guardian activity log", "Provozní log Guardianu"),
    "status.radio_on": ("Radio: connected", "Rádio: připojeno"),
    "status.radio_off": ("Radio: off", "Rádio: vypnuto"),
    "status.vara_on": ("VARA: connected", "VARA: připojena"),
    "status.vara_off": ("VARA: off", "VARA: vypnuta"),
    "status.control_on": ("Control: active", "Řízení: aktivní"),
    "status.control_off": ("Control: off", "Řízení: vypnuto"),
    "status.hamlib_ready": ("Hamlib: ready", "Hamlib: připraven"),
    "status.hamlib_missing": ("Hamlib: missing", "Hamlib: chybí"),
    "status.control_active": (
        "Control channel active",
        "Řídicí kanál je aktivní",
    ),
    "status.hardware_connected": ("Hardware connected", "Hardware je připojen"),
    "workspace.status": ("{name} workspace", "Pracovní plocha: {name}"),
    "context.radio_modem": (
        "Radio: {radio}  ·  Control modem: {modem}",
        "Rádio: {radio}  ·  Řídicí modem: {modem}",
    ),
    "context.unread": (
        "Unread messages: {count}",
        "Nepřečtené zprávy: {count}",
    ),
    "context.outbox": (
        "Waiting to send: {count}",
        "Čeká na odeslání: {count}",
    ),
    "context.transit": (
        "Waiting to relay: {count}",
        "Čeká na předání: {count}",
    ),
    "context.sessions": (
        "Active transfers: {count}",
        "Aktivní přenosy: {count}",
    ),
    "context.vara_connecting": (
        "VARA is establishing a link",
        "VARA navazuje spojení",
    ),
    "context.not_configured": ("not configured", "nenastaveno"),
    "ready.identity": ("Station identity", "Identita stanice"),
    "ready.needs_setup": ("Needs setup", "Vyžaduje nastavení"),
    "ready.no_callsign": ("No callsign configured", "Není nastavena volací značka"),
    "ready.radio": ("Radio control", "Řízení rádia"),
    "ready.hamlib_guidance": (
        "Open Station readiness for guided setup",
        "Otevřete Připravenost stanice pro průvodce nastavením",
    ),
    "ready.endpoint": ("Endpoint set", "Koncový bod nastaven"),
    "ready.payload": ("Payload workflow", "Přenos zprávy"),
    "ready.payload_detail": (
        "Uses the shared ARDOS session and payload controller",
        "Používá společný řadič relací a přenosu ARDOS",
    ),
    # Mail
    "mail.title": ("Mail", "Pošta"),
    "mail.compose": ("Compose", "Nová zpráva"),
    "mail.refresh": ("Refresh", "Obnovit"),
    "mail.inbox": ("Inbox", "Doručené"),
    "mail.outbox": ("Outbox", "K odeslání"),
    "mail.sent": ("Sent", "Odeslané"),
    "mail.transit": ("Transit", "Předávané"),
    "mail.drafts": ("Drafts", "Koncepty"),
    "mail.new_count": (", {count} new", ", {count} nových"),
    "mail.peer": ("From / To", "Od / Komu"),
    "mail.subject": ("Subject", "Předmět"),
    "mail.status": ("Status", "Stav"),
    "mail.attachments": ("Attachments", "Přílohy"),
    "mail.size": ("Size", "Velikost"),
    "mail.select": ("Select a message", "Vyberte zprávu"),
    "mail.reply": ("Reply", "Odpovědět"),
    "mail.send_queued": ("Send queued message", "Odeslat zprávu z fronty"),
    "mail.delete": ("Delete", "Odstranit"),
    "mail.delete_confirm": (
        "Delete message #{id} from this station?",
        "Odstranit zprávu #{id} z této stanice?",
    ),
    "mail.send_requires_control": (
        "The message remains queued. Start the live control channel before sending.",
        "Zpráva zůstává ve frontě. Před odesláním spusťte živý řídicí kanál.",
    ),
    "mail.no_subject": ("(no subject)", "(bez předmětu)"),
    "mail.none": ("none", "žádné"),
    "mail.not_found": ("(message not found)", "(zpráva nebyla nalezena)"),
    "status.draft": ("Draft", "Koncept"),
    "status.queued": ("Queued", "Ve frontě"),
    "status.sending": ("Sending", "Odesílá se"),
    "status.delivered": ("Delivered", "Doručeno"),
    "status.received": ("Received", "Přijato"),
    "status.waiting": ("Waiting for pickup", "Čeká na předání"),
    "status.forwarded": ("Forwarded", "Předáno"),
    "status.failed": ("Failed", "Selhalo"),
    "mail.reader": (
        "From: {source}\nTo: {dest}\nSubject: {subject}\nStatus: {status}\n"
        "Route: {route}\nAttachments:\n{attachments}\n{line}\n{body}",
        "Od: {source}\nKomu: {dest}\nPředmět: {subject}\nStav: {status}\n"
        "Trasa: {route}\nPřílohy:\n{attachments}\n{line}\n{body}",
    ),
    "compose.title": ("Compose message", "Napsat zprávu"),
    "compose.to": ("To", "Komu"),
    "compose.template": ("Message template", "Šablona zprávy"),
    "compose.template_plain": ("Plain message", "Běžná zpráva"),
    "compose.priority": ("Priority", "Priorita"),
    "compose.message": ("Message", "Zpráva"),
    "priority.routine": ("Routine", "Běžná"),
    "priority.priority": ("Priority", "Prioritní"),
    "priority.urgent": ("Urgent", "Naléhavá"),
    "priority.emergency": ("Emergency", "Nouzová"),
    "compose.attach": ("Attach files…", "Připojit soubory…"),
    "compose.no_attachments": ("No attachments", "Bez příloh"),
    "compose.queue": ("Queue in Outbox", "Zařadit k odeslání"),
    "compose.destination_required": (
        "Enter a destination.",
        "Zadejte cílovou stanici.",
    ),
    "compose.attach_error": ("Attachment", "Příloha"),
    "compose.large_rf": (" · large for RF", " · velké pro rádiový přenos"),
    "compose.attachment_summary": (
        "{count} file(s), {size} bytes{warning}",
        "{count} souborů, {size} bajtů{warning}",
    ),
    "compose.reply_quote": (
        "\n\n--- {source} wrote ---\n{quoted}",
        "\n\n--- {source} napsal(a) ---\n{quoted}",
    ),
    "compose.structured_hint": (
        "Standardized fields are serialized as readable plain text so every "
        "receiving station can open the message.",
        "Normované položky se odešlou jako čitelný prostý text, takže zprávu "
        "otevře každá přijímající stanice.",
    ),
    "event.mail_queued": (
        "Message #{id} queued for {destination}.",
        "Zpráva #{id} byla zařazena k odeslání pro {destination}.",
    ),
    "event.mail_deleted": (
        "Message #{id} deleted.",
        "Zpráva #{id} byla odstraněna.",
    ),
    # Network and log
    "network.title": ("Network", "Síť"),
    "network.routes": ("Routes", "Trasy"),
    "network.heard": ("Heard stations", "Slyšené stanice"),
    "network.destination": ("Destination", "Cíl"),
    "network.preferred": ("Preferred hop", "Upřednostněný mezilehlý bod"),
    "network.backup": ("Backup", "Záložní bod"),
    "network.frequency": ("Frequency", "Frekvence"),
    "network.mode_vara_fm": ("VARA FM (FM)", "VARA FM (FM)"),
    "network.mode_vara_hf": ("VARA HF (USB)", "VARA HF (USB)"),
    "network.mode": ("Mode", "Režim"),
    "network.add": ("Add or replace route", "Přidat nebo nahradit trasu"),
    "network.remove": ("Remove selected", "Odstranit vybranou"),
    "network.heard_hint": (
        "Stations appear here only after a real control frame is received.",
        "Stanice se zde objeví až po přijetí skutečného řídicího rámce.",
    ),
    "network.callsign": ("Callsign", "Volací značka"),
    "network.age": ("Age", "Stáří"),
    "network.frames": ("Frames", "Rámce"),
    "network.snr": ("Last SNR", "Poslední SNR"),
    "network.last_frame": ("Last frame", "Poslední rámec"),
    "network.route_required": (
        "Enter a destination. The preferred hop may stay empty for a direct route.",
        "Zadejte cíl. Pro přímou trasu může upřednostněný mezilehlý bod zůstat prázdný.",
    ),
    "log.title": ("Log", "Provozní log"),
    "log.all": ("All", "Vše"),
    "log.info": ("Info", "Informace"),
    "log.warning": ("Warning", "Varování"),
    "log.error": ("Error", "Chyby"),
    "log.filter": ("Filter events", "Filtrovat události"),
    "log.copy": ("Copy visible", "Kopírovat zobrazené"),
    # Settings
    "settings.title": ("Station settings", "Nastavení stanice"),
    "settings.intro": (
        "Settings are grouped by operator task. Changes are validated before "
        "they are written to the station profile.",
        "Nastavení jsou seskupena podle činností operátora. Před zápisem do "
        "profilu stanice se změny ověří.",
    ),
    "settings.station": ("Station", "Stanice"),
    "settings.radio": ("Radio control", "Řízení rádia"),
    "settings.vara": ("VARA & payload", "VARA a přenos"),
    "settings.network": ("Network behavior", "Chování sítě"),
    "settings.appearance": ("Appearance", "Vzhled"),
    "settings.language": ("Language", "Jazyk"),
    "language.english": ("English", "Angličtina"),
    "language.czech": ("Czech", "Čeština"),
    # Dialogs/help
    "help.title": ("Guardian help", "Nápověda Guardianu"),
    "help.search": ("Filter topics", "Filtrovat témata"),
    "about.title": ("About Guardian", "O aplikaci Guardian"),
    "about.body": (
        "<b>{app} {version}</b><br>ARDOS control and routing layer.<br><br>"
        "The interface follows the shared Modeling Anten design language.",
        "<b>{app} {version}</b><br>Řídicí a směrovací vrstva ARDOS.<br><br>"
        "Rozhraní používá společný vizuální jazyk Modeling Anten.",
    ),
}
