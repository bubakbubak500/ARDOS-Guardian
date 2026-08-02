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
    "shell.manual_frequency": (
        "Current radio frequency",
        "Aktuální kmitočet rádia",
    ),
    "shell.manual_frequency_unknown": ("not entered", "nezadaný"),
    "shell.manual_qsy_title": ("Manual radio tuning", "Ruční přeladění rádia"),
    "shell.manual_qsy": (
        "Guardian cannot tune this no-CAT radio. To send to {callsign}, tune "
        "the radio from {current} to {frequency} ({mode}). Press OK only after "
        "the radio is tuned; Cancel leaves the message unsent.",
        "Guardian toto rádio bez CAT nedokáže přeladit. Pro odeslání stanici "
        "{callsign} přelaďte rádio z {current} na {frequency} ({mode}). OK "
        "stiskněte až po přeladění; Zrušit ponechá zprávu neodeslanou.",
    ),
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
    "vara.settings_applied": (
        "VARA settings applied ({bandwidth}).",
        "Nastavení VARA použito ({bandwidth}).",
    ),
    "vara.reconnecting_for_settings": (
        "Reconnecting VARA for the new mode or ports…",
        "Znovu připojuji VARA kvůli novému režimu nebo portům…",
    ),
    "menu.network_import": (
        "Import network from CSV…",
        "Importovat síť z CSV…",
    ),
    "menu.network_export": (
        "Export network to CSV…",
        "Exportovat síť do CSV…",
    ),
    "menu.network_template": (
        "Save network template…",
        "Uložit vzorový soubor sítě…",
    ),
    "network.import_done": (
        "Imported {count} routes.",
        "Importováno {count} tras.",
    ),
    "network.export_done": (
        "Network written to {path}.",
        "Síť zapsána do {path}.",
    ),
    "context.outbox_failed": (
        "Failed, awaiting retry: {count}",
        "Selhalo, čeká na opakování: {count}",
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
    "mail.route": ("Route", "Trasa"),
    "compose.large_attachments_title": (
        "Large attachments",
        "Velké přílohy",
    ),
    "compose.large_attachments": (
        "{size} KB of attachments is roughly {minutes} minutes of airtime on a "
        "VARA FM link, and the channel is occupied the whole time. Queue it "
        "anyway?",
        "{size} kB příloh je na spoji VARA FM zhruba {minutes} minut vysílání a "
        "kanál je po celou dobu obsazený. Přesto zařadit k odeslání?",
    ),
    "mail.attachment_open": ("Open", "Otevřít"),
    "mail.attachment_save": ("Save as…", "Uložit jako…"),
    "mail.attachment_save_all": ("Save all…", "Uložit vše…"),
    "mail.attachment_saved": (
        "Saved {name} to {path}.",
        "Uloženo {name} do {path}.",
    ),
    "mail.attachment_saved_all": (
        "Saved {count} attachments to {path}.",
        "Uloženo {count} příloh do {path}.",
    ),
    "mail.attachment_save_error": (
        "Could not save {name}: {error}",
        "Nepodařilo se uložit {name}: {error}",
    ),
    "mail.attachment_open_error": (
        "Windows could not open {name}. Save it and open it yourself.",
        "Windows nedokázal otevřít {name}. Uložte jej a otevřete ručně.",
    ),
    "mail.attachment_choose_folder": (
        "Choose a folder for the attachments",
        "Vyberte složku pro přílohy",
    ),
    "mail.attachment_risky": (
        "{name} is a program or script. Opening it runs code that arrived over "
        "the radio from {source}. Open it anyway?",
        "{name} je program nebo skript. Otevřením spustíte kód, který přišel "
        "rádiem od {source}. Přesto otevřít?",
    ),
    "mail.attachment_risky_title": (
        "Executable attachment",
        "Spustitelná příloha",
    ),
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
    "map.col_station": ("Station", "Stanice"),
    "map.col_grid": ("Grid", "Lokátor"),
    "map.col_distance": ("km", "km"),
    "map.col_bearing": ("Bearing", "Azimut"),
    "map.col_snr": ("S/N dB", "S/N dB"),
    "map.col_age": ("Heard", "Slyšeno"),
    "map.col_channel": ("MHz", "MHz"),
    "map.col_reaches": ("Reaches", "Dosahuje"),
    "map.alert_age": ("{minutes} min ago", "před {minutes} min"),
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
    "network.scanner": ("Channel scanner", "Scanner kanálů"),
    "network.scanner_hint": (
        "The scanner uses this station's current channel plus compatible route "
        "frequencies. It never mixes FM and HF control modems.",
        "Scanner používá aktuální kanál stanice a kompatibilní frekvence tras. "
        "Nikdy nemíchá řídicí modemy pro FM a HF.",
    ),
    "network.scanner_status": ("Status", "Stav"),
    "network.scanner_stopped": ("Stopped", "Zastaven"),
    "network.scanner_scanning": ("Scanning", "Skenuje"),
    "network.scanner_holding": ("Holding on activity", "Drží kvůli aktivitě"),
    "network.scanner_paused": ("Paused for a session", "Pozastaven kvůli relaci"),
    "network.scanner_current": ("Current scan channel", "Aktuální skenovaný kanál"),
    "network.scanner_channels": ("Compatible channels", "Kompatibilní kanály"),
    "network.scanner_dwell": ("Dwell (seconds)", "Doba poslechu (sekundy)"),
    "network.scanner_use_signal": ("Hold above S-meter value", "Držet nad hodnotou S-metru"),
    "network.scanner_threshold": ("S-meter threshold", "Práh S-metru"),
    "network.scanner_start": ("Start scanner", "Spustit scanner"),
    "network.scanner_stop": ("Stop and return home", "Zastavit a vrátit domů"),
    "network.destination": ("Destination", "Cíl"),
    "network.preferred": ("Preferred hop", "Upřednostněný mezilehlý bod"),
    "network.backup": ("Backup", "Záložní bod"),
    "network.frequency": ("Frequency", "Frekvence"),
    "network.mode_vara_fm": ("VARA FM (FM)", "VARA FM (FM)"),
    "network.mode_vara_hf": ("VARA HF (USB)", "VARA HF (USB)"),
    "network.mode": ("Mode", "Režim"),
    "network.working_frequency": (
        "VARA working frequency",
        "Pracovní frekvence VARA",
    ),
    "network.working_mode": ("VARA working mode", "Pracovní režim VARA"),
    "network.add": ("Add or replace route", "Přidat nebo nahradit trasu"),
    "network.remove": ("Remove selected", "Odstranit vybranou"),
    "network.heard_hint": (
        "Stations appear here only after a real control frame is received.",
        "Stanice se zde objeví až po přijetí skutečného řídicího rámce.",
    ),
    "network.callsign": ("Callsign", "Volací značka"),
    "network.age": ("Age", "Stáří"),
    "network.frames": ("Frames", "Rámce"),
    "network.snr": ("Last S/N (est.)", "Poslední S/N (odhad)"),
    "network.heard_on": ("Heard on", "Slyšeno na"),
    "network.locator": ("Locator", "Lokátor"),
    "network.distance": ("Distance", "Vzdálenost"),
    # Map
    "map.title": ("Station map", "Mapa stanic"),
    "map.menu": ("Station map…", "Mapa stanic…"),
    "map.intro": (
        "Stations appear here once they beacon a position. Drag to pan, wheel "
        "to zoom, or click a station to write it a message. Set your own "
        "position by picking it on the map or by typing the locator.",
        "Stanice se zde objeví, jakmile odvysílají polohu v majáku. Tažením "
        "posunete, kolečkem přiblížíte a klepnutím na stanici jí napíšete "
        "zprávu. Vlastní polohu zadáte klepnutím do mapy nebo napsáním "
        "lokátoru.",
    ),
    "map.pick": ("Pick my position", "Určit mou polohu"),
    "map.locator": ("Locator", "Lokátor"),
    "map.centre": ("Show all", "Zobrazit vše"),
    "map.background": ("Map background", "Mapový podklad"),
    "map.background_hint": (
        "Topographic tiles from ČÚZK, which publishes them free and without "
        "registration. Only what you look at is fetched, and it is kept on "
        "disk so the same ground works later with no network.",
        "Topografické dlaždice z ČÚZK, který je poskytuje zdarma a bez "
        "registrace. Stahuje se jen to, na co se díváte, a ukládá se na disk, "
        "takže stejné území funguje později i bez sítě.",
    ),
    "map.background_off": (
        "Map background off — stations are drawn on the graticule alone.",
        "Mapový podklad vypnut — stanice se kreslí jen do souřadnicové sítě.",
    ),
    "map.attribution": (
        "{source}  ·  {credit}  ·  {tiles} tiles cached ({megabytes} MB)",
        "{source}  ·  {credit}  ·  {tiles} dlaždic v mezipaměti ({megabytes} MB)",
    ),
    "map.you": ("This station", "Tato stanice"),
    "map.transmit": ("Send in beacons", "Posílat v majáku"),
    "map.transmit_hint": (
        "Your locator travels in the presence beacon, so other stations can "
        "place you. Nothing is transmitted while beacons are switched off.",
        "Váš lokátor cestuje v majáku přítomnosti, aby vás ostatní stanice "
        "mohly umístit. Dokud jsou majáky vypnuté, nevysílá se nic.",
    ),
    "map.no_position": (
        "No position set for this station yet.",
        "Poloha této stanice zatím není nastavena.",
    ),
    "map.own_position": (
        "This station: {locator} ({latitude}, {longitude})",
        "Tato stanice: {locator} ({latitude}, {longitude})",
    ),
    "map.bad_locator": (
        "{locator} is not a Maidenhead locator (2 to 10 characters, "
        "e.g. JN89HE12AB).",
        "{locator} není lokátor Maidenhead (2 až 10 znaků, "
        "např. JN89HE12AB).",
    ),
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
    "settings.ptt_test": ("Test PTT", "Test PTT"),
    "settings.ptt_test_hint": (
        "Keys the transmitter for about two seconds, right away, to prove the "
        "interface really switches the radio. A bare carrier on the current "
        "frequency — have an antenna or dummy load connected.",
        "Ihned zaklíčuje vysílač asi na dvě sekundy, aby se ověřilo, že "
        "rozhraní rádio opravdu přepne. Holá nosná na aktuálním kmitočtu — "
        "mějte připojenou anténu nebo umělou zátěž.",
    ),
    "settings.refresh_ports": ("Refresh ports", "Obnovit porty"),
    "settings.ptt_test_running": (
        "Keying the radio…",
        "Klíčuji rádio…",
    ),
    "settings.ptt_test_unsaved": (
        "Save or apply the radio settings first — the test keys the radio "
        "Guardian is actually using, not the values shown here.",
        "Nejprve nastavení rádia uložte nebo použijte — test klíčuje rádio, "
        "které Guardian skutečně používá, ne hodnoty zobrazené zde.",
    ),
    "settings.vara": ("VARA & payload", "VARA a přenos"),
    "settings.network": ("Network behavior", "Chování sítě"),
    "settings.separate_working_channels": (
        "Use separate VARA working channels (advanced, CAT only)",
        "Používat samostatné pracovní kanály VARA (pokročilé, pouze CAT)",
    ),
    "settings.separate_working_channels_hint": (
        "Off by default. When enabled, extra working-channel fields appear in "
        "Network routes. The station that opens the session names the channel "
        "and the other follows it inside the band it already works that peer "
        "on; control stays on the existing calling channel.",
        "Ve výchozím stavu vypnuto. Po zapnutí se v trasách sítě zobrazí "
        "další pole pracovního kanálu. Kanál určuje stanice, která relaci "
        "zahajuje, a druhá se za ní přeladí v rámci pásma, na kterém s ní už "
        "pracuje; řízení zůstává na stávajícím volacím kanálu.",
    ),
    "settings.notify_incoming": (
        "Desktop notifications for incoming mail and alerts",
        "Upozornění na ploše na příchozí zprávy a výstrahy",
    ),
    "settings.notify_incoming_hint": (
        "A tray toast and a soft chime when a message or a routine alert "
        "arrives while Guardian is in the background. URGENT and EMERGENCY "
        "always raise the on-top window.",
        "Bublina u hodin a tichý tón, když zpráva nebo běžná výstraha přijde, "
        "zatímco je Guardian na pozadí. NALÉHAVÉ a NOUZOVÉ vždy vyvolá okno "
        "nad ostatními.",
    ),
    "settings.notify_sound": (
        "Notification sounds",
        "Zvuky upozornění",
    ),
    "settings.notify_sound_hint": (
        "Played on the Windows default output device — never on the device "
        "configured as the radio's audio output.",
        "Přehrává se na výchozím zvukovém zařízení Windows — nikdy na "
        "zařízení nastaveném jako zvukový výstup do rádia.",
    ),
    "notify.mail_title": (
        "Message from {source}",
        "Zpráva od {source}",
    ),
    "notify.urgent_mail_title": (
        "URGENT message from {source}",
        "NALÉHAVÁ zpráva od {source}",
    ),
    "notify.emergency_window": ("Net alert", "Výstraha sítě"),
    "notify.acknowledge": ("Acknowledge", "Potvrdit"),
    "tray.open": ("Open Guardian", "Otevřít Guardian"),
    "settings.appearance": ("Appearance", "Vzhled"),
    "settings.language": ("Language", "Jazyk"),
    "language.english": ("English", "Angličtina"),
    "language.czech": ("Czech", "Čeština"),
    # Net alerts. The sentences are what a one-byte code expands to, so each
    # station reads the alert in its own language -- keep them short and
    # unambiguous, they are read under pressure.
    "alert.mayday": ("MAYDAY — life in danger", "MAYDAY — ohrožení života"),
    "alert.medical": ("Medical emergency", "Zdravotní nouze"),
    "alert.evacuation": ("Evacuation under way", "Probíhá evakuace"),
    "alert.qrt": ("Station going off air (QRT)", "Stanice končí provoz (QRT)"),
    "alert.qsy": ("Changing frequency (QSY)", "Změna kmitočtu (QSY)"),
    "alert.qrv": ("Station ready (QRV)", "Stanice připravena (QRV)"),
    "alert.net_test": ("Net test — exercise only", "Test sítě — pouze cvičení"),
    "alert.power_outage": ("Mains power outage", "Výpadek napájení ze sítě"),
    "alert.battery_only": ("Running on battery", "Provoz na baterii"),
    "alert.hint_detail": ("What happened, where", "Co se stalo a kde"),
    "alert.hint_what_where": ("Injury and location", "Zranění a místo"),
    "alert.hint_area": ("Area concerned", "Kterých míst se týká"),
    "alert.hint_reason": ("Reason, when back", "Důvod, kdy zpět"),
    "alert.hint_frequency": ("New frequency", "Nový kmitočet"),
    "alert.hint_none": ("Optional note", "Volitelná poznámka"),
    "alert.hint_exercise": ("Exercise name", "Název cvičení"),
    "alert.hint_endurance": ("Endurance left", "Zbývající výdrž"),
    "alert.banner_from": ("from {source}", "od {source}"),
    "alert.banner_mine": ("sent by this station", "odesláno touto stanicí"),
    "alert.banner_unknown": (
        "Unknown alert 0x{code:02X}",
        "Neznámá výstraha 0x{code:02X}",
    ),
    "alert.dismiss": ("Dismiss", "Skrýt"),
    "alert.send": ("Alert", "Výstraha"),
    "alert.dialog_title": ("Send net alert", "Odeslat výstrahu do sítě"),
    "alert.dialog_intro": (
        "Broadcast to every station on this frequency. Receiving stations "
        "show it and pass it on.",
        "Vysílá se všem stanicím na tomto kmitočtu. Přijímající stanice ji "
        "zobrazí a předají dál.",
    ),
    "alert.dialog_kind": ("Alert", "Výstraha"),
    "alert.dialog_note": ("Note", "Poznámka"),
    "alert.dialog_room": (
        "{used}/{total} characters",
        "{used}/{total} znaků",
    ),
    "alert.dialog_send": ("Broadcast", "Odvysílat"),
    "alert.dialog_sweep": (
        "Repeat on {count} other known frequencies",
        "Zopakovat na {count} dalších známých kmitočtech",
    ),
    "alert.dialog_sweep_none": (
        "No other frequency is set in the route table",
        "V tabulce tras není nastaven jiný kmitočet",
    ),
    "alert.dialog_sweep_hint": (
        "The radio is tuned to each of them in turn, the alert is repeated "
        "there, and the radio returns to this frequency.",
        "Rádio se postupně přeladí na každý z nich, výstraha se tam zopakuje "
        "a rádio se vrátí na tento kmitočet.",
    ),
    "alert.confirm_title": ("Confirm alert", "Potvrdit výstrahu"),
    "alert.confirm_body": (
        "Broadcast \"{text}\" to the whole net?",
        "Odvysílat „{text}“ celé síti?",
    ),
    "alert.confirm_sweep": (
        "The radio will also be tuned to {count} other known frequencies to "
        "repeat it there.",
        "Rádio se navíc přeladí na {count} dalších známých kmitočtů, aby ji "
        "tam zopakovalo.",
    ),
    "alert.no_control": (
        "Start the control channel before sending an alert.",
        "Před odesláním výstrahy spusťte řídicí kanál.",
    ),
    "alert.stop_scanner": (
        "Stop the channel scanner before sending an alert.",
        "Před odesláním výstrahy zastavte scanner kanálů.",
    ),
    "mail.stop_scanner": (
        "Stop the channel scanner before sending this message.",
        "Před odesláním této zprávy zastavte scanner kanálů.",
    ),
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
