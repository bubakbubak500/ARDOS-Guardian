"""Structured bilingual operator guide."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from ..i18n import dual, tr


@dataclass(frozen=True, slots=True)
class HelpTopic:
    title: str
    html: str
    keywords: str


def _topic(title_en: str, title_cs: str, body_en: str, body_cs: str, keywords: str) -> HelpTopic:
    return HelpTopic(
        dual(title_en, title_cs),
        dual(body_en, body_cs),
        keywords,
    )


def help_topics() -> list[HelpTopic]:
    return [
        _topic(
            "1. First start and safe workflow",
            "1. První spuštění a bezpečný postup",
            """
            <h2>First start and safe workflow</h2>
            <ol>
              <li>Open <b>Settings → Station settings</b> and enter your
              callsign. Choose the radio control method and the payload workflow
              (Guardian VARA P2P or Guardian SC-FTN). VARA is needed for a VARA
              P2P hop or for a hop that falls back because its peer has no SC
              capability.</li>
              <li>Open <b>Tools → Station readiness</b>. Guardian detects
              Hamlib/rigctld, VARA FM and VARA HF without transmitting.</li>
              <li>Connect the radio, the control audio and, when the selected
              hop needs VARA P2P, VARA separately. A green status means the
              local control connection is available; it does not mean that an
              RF link to another station exists.</li>
              <li>Start the live control channel only when the radio, audio
              interface, frequency and licence conditions are ready.</li>
              <li>Compose mail into Outbox. With a live idle control channel,
              eligible queued mail can be picked automatically when its next hop
              is currently heard; the operator can also select it and use Send.
              A queued message by itself does not key a stopped station.</li>
            </ol>
            <p><b>Safety:</b> Guardian never starts the audio transport or keys
            PTT merely because the application was opened. Confirm frequency,
            power, antenna and local regulations before enabling RF.</p>
            """,
            """
            <h2>První spuštění a bezpečný postup</h2>
            <ol>
              <li>Otevřete <b>Nastavení → Nastavení stanice</b> a zadejte
              volací značku. Zvolte způsob řízení rádia a způsob přenosu
              (Guardian VARA P2P nebo Guardian SC-FTN). VARA je třeba pro hop
              VARA P2P nebo pro hop, jehož protistanice SC neumí.</li>
              <li>Otevřete <b>Provoz → Připravenost stanice</b>. Guardian bez
              vysílání zkontroluje Hamlib/rigctld, VARA FM a VARA HF.</li>
              <li>Samostatně připojte rádio, řídicí zvuk a v případě potřeby
              VARA pro hop VARA P2P. Zelený stav znamená dostupné místní řídicí
              spojení; neznamená navázané rádiové spojení s jinou stanicí.</li>
              <li>Živý řídicí kanál spusťte až po kontrole rádia, zvukového
              rozhraní, frekvence a podmínek oprávnění.</li>
              <li>Zprávu nejprve zařaďte do složky K odeslání. Při živém klidovém
              řídicím kanálu může vhodnou zprávu vybrat automatické doručování,
              pokud je další stanice na trase právě slyšet; obsluha může použít i
              Odeslat. Samotná fronta při vypnutém Řízení rádio nezaklíčuje.</li>
            </ol>
            <p><b>Bezpečnost:</b> Samotné spuštění Guardianu nikdy neotevře
            zvukový přenos ani nezaklíčuje PTT. Před povolením RF ověřte
            frekvenci, výkon, anténu a místní předpisy.</p>
            """,
            "first start readiness safety ptt první spuštění bezpečnost",
        ),
        _topic(
            "2. Home workspace and status",
            "2. Domovská plocha a stavové údaje",
            """
            <h2>Home workspace</h2>
            <p>The header identifies the station, active FM/HF mode, payload
            workflow, radio and control modem. The counters summarize Inbox,
            unread mail, Outbox, Transit, active sessions and recently heard
            stations.</p>
            <p><b>Radio</b> reports the CAT/PTT backend. <b>VARA</b> reports
            the command connection to the local modem. <b>Control</b> reports
            the live AFSK/MFSK audio transport. <b>Hamlib</b> reports whether
            rigctld was found. Gray means inactive, amber means attention is
            required, and green means ready/connected; every state is also
            written as text.</p>
            <p>While VARA is moving a payload, a <b>VARA transmission</b> meter
            appears in the middle of the header. Its segments are filled from
            the two numbers VARA itself reports: how many bytes were handed to
            the modem for this envelope, and how many are still queued for RF.
            The difference is what has genuinely left the station, so the meter
            can sit at zero for a few seconds after the hand-off — that is
            VARA acquiring the link, not a stall. On an unregistered FM link
            (566 bps) a short message legitimately takes tens of seconds. The
            meter disappears when the codec returns to the control channel.</p>
            <p>The Activity panel is a bounded operational event history.
            Hardware polling and downloads run outside the UI thread so typing
            and navigation remain responsive.</p>
            """,
            """
            <h2>Domovská plocha</h2>
            <p>Záhlaví určuje stanici, aktivní režim FM/HF, způsob přenosu,
            rádio a řídicí modem. Počítadla shrnují doručené a nepřečtené
            zprávy, frontu k odeslání, předávané zprávy, aktivní relace a
            nedávno slyšené stanice.</p>
            <p><b>Rádio</b> ukazuje stav řízení CAT/PTT. <b>VARA</b> ukazuje
            příkazové spojení s místním modemem. <b>Řízení</b> ukazuje živý
            zvukový přenos AFSK/MFSK. <b>Hamlib</b> oznamuje nalezení rigctld.
            Šedá znamená neaktivní stav, žlutá vyžaduje pozornost a zelená
            připraveno/připojeno; stav je vždy uveden i textem.</p>
            <p>Když VARA přenáší obsah, objeví se uprostřed záhlaví ukazatel
            <b>Přenos VARA</b>. Jeho segmenty se plní podle dvou údajů, které
            hlásí sama VARA: kolik bajtů této obálky převzal modem a kolik jich
            ještě čeká ve vysílací frontě. Rozdíl je to, co stanici skutečně
            opustilo, takže ukazatel může několik sekund po předání stát na nule
            — to VARA navazuje spojení, nikoli zádrhel. Na neregistrovaném FM
            spojení (566 bps) trvá krátká zpráva oprávněně desítky sekund. Po
            vrácení zvukové karty řídicímu kanálu ukazatel zmizí.</p>
            <p>Panel Aktivita obsahuje omezenou historii provozních událostí.
            Dotazy na hardware i stahování běží mimo UI vlákno, takže psaní a
            navigace zůstávají plynulé.</p>
            """,
            "home status counters activity progress transfer domů stav "
            "počítadla aktivita přenos ukazatel",
        ),
        _topic(
            "3. From composing to delivery",
            "3. Cesta zprávy od napsání k doručení",
            """
            <h2>What happens between Send and Delivered</h2>
            <p>Guardian never transmits as a side effect. Every step below is
            either an operator action or a consequence of one, and the whole
            chain is visible in Activity while it runs.</p>
            <ol>
              <li><b>Compose</b> writes a local bundle into Outbox. Nothing is
              on the air yet, and the message survives a restart.</li>
              <li><b>Send</b> resolves the next hop: an explicit or saved manual
              route first, then a destination heard directly, then a usable
              discovered route, then the imported topology and learned paths.
              With no route and nothing heard, the message simply stays queued —
              that is a normal state, not a failure. In the shipped policy,
              route discovery and its automatic-use path are enabled; a route
              query can therefore be followed without a separate approval
              click.</li>
              <li>Guardian offers the message on the control channel with
              <code>HAVE_MSG</code> and waits for <code>ACK_HAVE</code> from
              that hop. This is a short AFSK or MFSK control burst.</li>
              <li>Both stations agree to switch with <code>START_VARA</code>.
              The control channel releases the sound card, and — only if
              separate working channels are enabled — both radios retune.</li>
              <li>Guardian prepares the envelope before the payload phase. The
              selected lossless XZ/image-repack candidate is used only when it
              is smaller; otherwise the ordinary ZIP is sent. VARA P2P opens
              its link and carries the envelope, while SC-FTN is used on a hop
              only after both peers negotiate the same SC profile. If the peer
              does not advertise SC capability, that hop falls back to VARA
              P2P; an incompatible SC profile makes negotiation fail rather
              than silently downgrading.
              The header meter tracks VARA's RF queue draining and Guardian
              disconnects gracefully so VARA flushes what it still holds.</li>
              <li>The sound card returns to the control channel and the
              receiving station confirms. <code>RECEIVED</code> from a relay
              means <b>forwarded</b>; the final station sends a separate
              <code>DELIVERED</code> receipt back along the reverse hops.</li>
            </ol>
            <p>A failed message stays in Outbox and is counted separately from
            what is still queued, so the Outbox figure never hides a problem.
            Transit mail keeps its resolved next hop across a failure or a
            restart and retries no more often than every five minutes.</p>
            <p>The automatic chain is therefore
            <b>heard evidence → route choice → optional QSY → compression and
            payload negotiation → ARQ transfer → receipt → control-channel
            return</b>. The same chain is used when an idle station picks a
            queued message automatically. Because the codec is shared, beacons,
            automatic delivery and the scanner stand down while a payload or
            another exclusive session is active.</p>
            """,
            """
            <h2>Co se děje mezi Odeslat a Doručeno</h2>
            <p>Guardian nikdy nevysílá mimochodem. Každý krok níže je buď akce
            operátora, nebo její důsledek, a celý řetězec je během běhu vidět
            v panelu Aktivita.</p>
            <ol>
              <li><b>Nová zpráva</b> uloží místní balíček do složky K odeslání.
              Zatím není nic ve vzduchu a zpráva přežije restart.</li>
              <li><b>Odeslat</b> určí další bod: nejprve výslovná nebo uložená
              ruční trasa, pak přímo slyšený cíl, použitelná nalezená trasa,
              importovaná topologie a naučené cesty. Bez trasy a bez slyšené
              stanice zpráva prostě zůstane ve frontě — to je normální stav,
              nikoli chyba. V dodávané politice je hledání tras i jejich
              automatické použití zapnuté, takže po dotazu není třeba dalšího
              kliknutí na schválení.</li>
              <li>Guardian nabídne zprávu na řídicím kanálu rámcem
              <code>HAVE_MSG</code> a čeká na <code>ACK_HAVE</code> od
              protistanice. Jde o krátký řídicí rámec AFSK nebo MFSK.</li>
              <li>Obě stanice se rámcem <code>START_VARA</code> dohodnou na
              přepnutí. Řídicí kanál uvolní zvukovou kartu a — jen při zapnutých
              samostatných pracovních kanálech — obě rádia přeladí.</li>
              <li>Guardian obálku připraví ještě před fází payloadu. Bezztrátová
              varianta XZ s repackingem obrázků se použije jen tehdy, když je
              menší; jinak se odešle běžný ZIP. VARA P2P otevře spojení a
              obálku přenese, zatímco SC-FTN se na hopu použije až po shodném
              profilu obou stanic. Když protistanice schopnost SC neohlásí,
              hop přejde na VARA P2P; nekompatibilní SC profil dohodu ukončí,
              místo aby ji tiše snížil. Ukazatel v záhlaví sleduje vyprazdňování
              fronty RF a Guardian
              se odpojí korektně, aby VARA odvysílala i to, co ještě drží.</li>
              <li>Zvuková karta se vrátí řídicímu kanálu a přijímající stanice
              potvrdí. <code>RECEIVED</code> od relaye znamená <b>předáno</b>;
              cílová stanice pošle samostatné potvrzení <code>DELIVERED</code>
              zpět po reverzních hopech.</li>
            </ol>
            <p>Neúspěšná zpráva zůstane ve složce K odeslání a počítá se zvlášť
            od těch, které stále čekají, takže číslo u fronty nikdy neskryje
            problém. Předávaná zpráva si zachová vypočtený next hop i po selhání
            nebo restartu a neopakuje pokus častěji než po pěti minutách.</p>
            <p>Řetězec tedy vypadá takto: <b>slyšený důkaz → volba trasy →
            případné QSY → příprava a dohoda payloadu → ARQ přenos → potvrzení
            → návrat na řídicí kanál</b>. Stejně pracuje i automatické odeslání
            z fronty v klidové stanici. Protože je zvuková karta sdílená, během
            payloadu nebo jiné výhradní relace počkají majáky, automatické
            doručování i scanner.</p>
            """,
            "send flow have_msg ack start_vara delivered received outbox "
            "odeslání průběh doručení fronta",
        ),
        _topic(
            "4. Station settings",
            "4. Nastavení stanice",
            """
            <h2>Station settings</h2>
            <p><b>Station</b> contains the callsign and operator name. The
            callsign is normalized to uppercase and is embedded in ARDOS
            control frames and message metadata.</p>
            <p><b>Radio control</b> selects none, Hamlib/rigctld, or serial
            VOX PTT. For Hamlib select the radio model by name, then set the CAT
            COM port, baud rate, rigctld host/port and executable. For VOX
            choose the COM port and RTS or DTR PTT line.</p>
            <p>For a radio without CAT — a Baofeng-class handheld behind an
            AIOC or similar sound-card cable — pick the <b>Hamlib Dummy</b>
            model, the cable's COM port, and set <b>Hamlib PTT via</b> to RTS
            or DTR. The dummy model never opens the port on its own, so with
            the default CAT command nothing would ever be keyed. Use
            <b>Test PTT</b> to prove the wiring.</p>
            <p><b>Save profile…</b> beside Test PTT stores the radio page under
            a short name, and the picker next to it loads one back. A profile
            carries the radio page only — control method, model, port, baud,
            rigctld host/port/executable, PTT method and line, keying delay —
            so a station that swaps between a CAT radio and a handheld on a
            cable is one pick away from either. Nothing reaches the radio until
            Save or Apply, and no callsign, audio device or VARA port travels
            with a profile. Saving under an existing name replaces it.</p>
            <p><b>VARA &amp; payload</b> stores separate FM and HF command/data
            ports and executable paths, the control-burst modem, and the
            payload workflow. An empty executable field is not a missing
            installation: it means "follow detection", and the greyed text in
            the field is the path Guardian is actually using. Browse to a file
            only to override it — for a second VARA installation or a portable
            copy. <b>Guardian VARA P2P</b> is the interoperable fallback;
            <b>Guardian SC-FTN</b> adds the sound-card modem described below.
            Selecting SC-FTN does not force every hop to use it: the peer must
            advertise a matching profile. A peer without SC capability uses
            VARA P2P; an incompatible SC profile makes negotiation fail rather
            than silently downgrading.</p>
            <p><b>Network behavior</b> contains the bounded TTL, discovery,
            beacon and separate-working-channel options. The production policy
            always keeps host PTT, automatic routing, relay, delivery, QSY,
            discovery forwarding, discovery automatic-use and link adverts
            enabled. Those paths are applied again when configuration is loaded
            or saved, so they are not operator checkboxes. Beacons and separate
            working channels remain explicit opt-ins. The only compression
            switch is lossless Guardian XZ with automatic image repacking; old
            VARA FILES/BZIP2 settings are migrated into it and are not stacked.</p>
            <p>With XZ enabled, eligible JPEG images can be stored losslessly
            as JPEG XL inside the package and restored to their original JPEG
            bytes on receipt. PNG recompression uses Zopfli while preserving
            image pixels. Guardian then evaluates the compressed package; it
            keeps the ordinary ZIP if the candidate is not smaller or preparation
            fails or times out. Preparation runs in the background. This trades
            local processing time for fewer bytes on the radio link; there is
            no separate image-repacking switch to configure.</p>
            <p><b>Appearance</b> changes theme and language immediately after
            Save or Apply. Settings are stored in the user profile, not in the
            installation directory.</p>
            """,
            """
            <h2>Nastavení stanice</h2>
            <p><b>Stanice</b> obsahuje volací značku a jméno operátora. Volací
            značka se převádí na velká písmena a zapisuje do řídicích rámců
            ARDOS i metadat zpráv.</p>
            <p><b>Řízení rádia</b> nabízí žádné řízení, Hamlib/rigctld nebo
            sériové PTT pro VOX. Pro Hamlib vyberte model rádia podle názvu a
            nastavte port COM pro CAT, rychlost, adresu/port rigctld a cestu k
            programu. Pro VOX zvolte port COM a linku PTT RTS nebo DTR.</p>
            <p>Pro rádio bez CAT — ruční stanici typu Baofeng přes kabel AIOC
            apod. — zvolte model <b>Hamlib Dummy</b>, COM port kabelu a
            <b>PTT přes (Hamlib)</b> nastavte na RTS nebo DTR. Model Dummy sám
            port nikdy neotevírá, takže s výchozím povelem CAT by se nikdy nic
            nezaklíčovalo. Zapojení ověřte tlačítkem <b>Test PTT</b>.</p>
            <p><b>Uložit profil…</b> vedle Testu PTT uloží stránku rádia pod
            krátkým názvem a rozbalovací seznam vedle jej zase načte. Profil
            nese jen stránku rádia — způsob řízení, model, port, rychlost,
            adresu/port/program rigctld, způsob a linku PTT a zpoždění
            klíčování — takže stanice, která střídá rádio s CAT a ruční
            stanici na kabelu, je od každého z nich na jedno kliknutí. Do rádia
            se nic nedostane, dokud nedáte Uložit nebo Použít, a s profilem
            nikdy neputuje volací značka, zvukové zařízení ani port VARA.
            Uložení pod existujícím názvem jej nahradí.</p>
            <p><b>VARA a přenos</b> uchovává oddělené příkazové a datové porty
            a cesty k programům pro FM a HF, modem řídicích rámců a způsob
            payloadu. Prázdné pole s programem neznamená chybějící instalaci:
            znamená „řídit se detekcí“ a šedý text v poli je cesta, kterou
            Guardian skutečně používá. Soubor vyberte jen tehdy, chcete-li
            detekci přebít — například u druhé instalace VARA nebo přenosné
            kopie. <b>Guardian VARA P2P</b> je interoperabilní záloha;
            <b>Guardian SC-FTN</b> přidává modem ze zvukové karty popsaný níže.
            Volba SC-FTN nevynutí tento modem na každém hopu: protistanice musí
            ohlásit shodný profil. Když schopnost SC neohlásí, hop použije VARA
            P2P; nekompatibilní SC profil dohodu ukončí, místo aby se tiše
            snížil.</p>
            <p><b>Chování sítě</b> obsahuje omezené volby TTL, hledání tras,
            majáků a samostatných pracovních kanálů. Dodávaná produkční politika
            vždy drží zapnuté hostitelské PTT, automatické routování, relay,
            doručování, QSY, předávání discovery, automatické použití discovery
            a linkové adverty. Při načtení i uložení konfigurace se znovu
            vynutí, takže to nejsou přepínače pro obsluhu. Majáky a samostatné
            pracovní kanály jsou stále výslovné volby. Jediná komprese je
            bezeztrátový Guardian XZ s automatickým přebalením obrázků; staré
            nastavení VARA FILES/BZIP2 se do něj převede a nekombinuje.</p>
            <p>Při zapnutém XZ se vhodné obrázky JPEG mohou uvnitř balíčku
            bezeztrátově uložit jako JPEG XL a u příjemce obnovit do původních
            bajtů JPEG. PNG se znovu komprimuje pomocí Zopfli se zachováním pixelů.
            Guardian porovná výsledný balíček: není-li menší nebo příprava selže
            či překročí časový limit, ponechá běžný ZIP. Příprava běží na pozadí.
            Delší místní zpracování tak může ušetřit přenášené bajty; samostatný
            přepínač přebalování obrázků se nenastavuje.</p>
            <p><b>Vzhled</b> mění motiv a jazyk ihned po Uložit nebo Použít.
            Nastavení se ukládá do profilu uživatele, nikoli do instalačního
            adresáře.</p>
            """,
            "settings callsign hamlib vox vara ttl language nastavení značka jazyk",
        ),
        _topic(
            "5. Hamlib, radio and PTT",
            "5. Hamlib, rádio a PTT",
            """
            <h2>Hamlib, radio and PTT</h2>
            <p>Guardian talks to a Hamlib <code>rigctld</code> TCP service,
            rather than implementing vendor CAT protocols. On Connect radio it
            may start the configured local rigctld and then open the driver.
            Existing responsive rigctld services are reused.</p>
            <p>Select a common radio from the model list, or use Browse all
            supported radios to load the authoritative list from the installed
            Hamlib. The COM port must belong to the radio interface and must not
            be held exclusively by another application. A wrong model, baud
            rate or CI-V address can make rigctld accept TCP while the radio
            does not answer.</p>
            <p>VARA host PTT lets Guardian act on VARA's PTT ON/OFF notices. The
            shipped production policy keeps <code>vara_host_ptt</code> true and
            reapplies it when configuration is loaded, saved or applied, so
            Guardian is the host-side PTT path for VARA. VARA and Guardian must
            still be configured so one physical CAT/PTT line has one owner;
            never test PTT into an unsuitable load or occupied channel.</p>
            """,
            """
            <h2>Hamlib, rádio a PTT</h2>
            <p>Guardian komunikuje se službou Hamlib <code>rigctld</code> přes
            TCP a neimplementuje jednotlivé protokoly CAT výrobců. Při volbě
            Připojit rádio může spustit nastavené místní rigctld a poté otevřít
            ovladač. Již spuštěná a odpovídající služba se znovu použije.</p>
            <p>Běžné rádio vyberte ze seznamu modelů. Volba Všechna podporovaná
            rádia načte úplný a směrodatný seznam z nainstalovaného Hamlibu.
            Port COM musí patřit rozhraní rádia a nesmí jej výhradně držet jiná
            aplikace. Chybný model, rychlost nebo adresa CI-V mohou způsobit,
            že rigctld přijímá TCP, ale rádio neodpovídá.</p>
            <p>Hostitelské PTT VARA dovolí Guardianu reagovat na hlášení PTT
            ON/OFF. Dodávaná produkční politika drží
            <code>vara_host_ptt</code> zapnuté a znovu je vynutí při načtení,
            uložení i použití konfigurace, takže hostitelskou cestu PTT pro VARA
            obsluhuje Guardian. VARA a Guardian přesto nastavte tak, aby jedno
            fyzické vedení CAT/PTT mělo jediného vlastníka; PTT nikdy netestujte
            do nevhodné zátěže ani na obsazeném kanálu.</p>
            """,
            "hamlib rigctld radio cat civ ptt rádio",
        ),
        _topic(
            "6. VARA FM/HF and payload modes",
            "6. VARA FM/HF a způsoby přenosu",
            """
            <h2>VARA and payload modes</h2>
            <p>Guardian connects to VARA's local command and data TCP ports.
            FM and HF settings are remembered separately. Connect VARA verifies
            only the local TCP endpoint; the remote link is created later by
            the ARDOS session.</p>
            <p><b>VARA P2P</b> is self-contained — no Winlink, no internet.
            After HAVE_MSG, ACK_HAVE and START_VARA, Guardian asks VARA to
            connect to the next hop and writes one framed envelope. Guardian
            owns the link for the whole transfer and closes it gracefully, so
            VARA flushes whatever it still holds before the radio goes quiet.</p>
            <p>Guardian deliberately does not toggle LISTEN around a
            connection: VARA's own reference warns that LISTEN ON or OFF
            arriving mid-connection disconnects it. The data socket is
            persistent, so before each transfer Guardian discards anything left
            in it by an earlier session — otherwise a stale envelope would be
            delivered one message behind.</p>
            <p>Timing is deliberately governed by VARA's own link state and
            buffer telemetry. Guardian budgets from the envelope size and the
            reported modem state rather than promising a fixed transfer time;
            a link that is still keying can be working while the byte meter has
            not moved yet. The exact time depends on VARA mode, peer conditions,
            ARQ and the prepared envelope.</p>
            <p>The control modem is independent from the payload modem. With
            automatic control-modem selection, FM uses AFSK 1200 and HF uses
            MFSK-16; an explicit control-modem choice overrides that mapping.
            Selecting SC-FTN changes the payload workflow, not this control
            modem.</p>
            """,
            """
            <h2>VARA a způsoby přenosu</h2>
            <p>Guardian se připojuje k místním příkazovým a datovým TCP portům
            VARA. Nastavení FM a HF se pamatují odděleně. Připojení VARA ověří
            pouze místní TCP bod; vzdálené spojení vznikne až během relace ARDOS.</p>
            <p><b>VARA P2P</b> je samostatný postup — bez Winlinku a bez
            internetu. Po rámcích HAVE_MSG, ACK_HAVE a START_VARA požádá
            Guardian VARA o spojení s dalším bodem a zapíše jednu rámcovanou
            obálku. Spojení drží po celý přenos a ukončí je korektně, aby VARA
            odvysílala i to, co ještě má, než rádio ztichne.</p>
            <p>Guardian záměrně nepřepíná LISTEN kolem spojení: referenční
            popis VARA varuje, že LISTEN ON nebo OFF doručený uprostřed spojení
            způsobí rozpad. Datový socket je trvalý, takže před každým přenosem
            Guardian zahodí, co v něm zbylo po dřívější relaci — jinak by se
            stará obálka doručila o zprávu pozadu.</p>
            <p>Čas se záměrně řídí stavem linky a telemetrií fronty VARA.
            Guardian počítá rozpočet z velikosti obálky a hlášeného stavu
            modemu, nikoli ze slibu pevné doby; linka, která stále klíčuje, může
            pracovat, i když se ukazatel bajtů zatím nepohnul. Skutečný čas
            závisí na režimu VARA, podmínkách protistanice, ARQ a připravené
            obálce.</p>
            <p>Řídicí modem je nezávislý na payloadu. Při automatické volbě se
            pro FM použije AFSK 1200 a pro HF MFSK-16; výslovná volba řídicího
            modemu toto mapování přebije. Volba SC-FTN mění workflow payloadu,
            nikoli tento řídicí modem.</p>
            """,
             "vara fm hf p2p winlink payload afsk mfsk přenos",
         ),
        _topic(
            "7. Automatic network dependencies",
            "7. Automatické závislosti sítě",
            """
            <h2>What runs automatically</h2>
            <p>The useful mental model is a dependency chain, not a collection
            of unrelated switches:</p>
            <p><b>heard evidence → route choice → optional QSY → prepare and
            negotiate payload → ARQ transfer → receipt → return to control</b></p>
            <p>Each arrow has a gate. Opening Guardian only opens the local
            interface. A live control channel needs selected audio input and
            output, the active control modem and a usable radio/PTT path. Only
            after a real control frame is heard can a station be considered a
            current next hop. A route by itself is not proof that the peer is
            listening now.</p>
            <h2>Production policy</h2>
            <p>The shipped configuration enforces host PTT, automatic routing,
            relay, queued delivery, automatic QSY, discovery forwarding,
            discovery automatic-use and live link adverts on load, save and
            apply. These paths therefore do not appear as ordinary operator
            checkboxes. A station may answer and forward permitted discovery
            traffic, use a usable discovered route, offer relay service and try
            eligible queued traffic without first changing hidden policy fields.
            It still never keys merely because the application was opened.</p>
            <p>Two on-air features remain explicit choices. <b>Presence
            beacons</b> are off until enabled and then use their configured
            interval. <b>Separate VARA working channels</b> are also off until
            enabled; they need real CAT on both peers. The calling channel is
            used for negotiation, the initiator proposes its configured working
            channel, and both stations return to the calling channel for the
            control receipt. With the option off, the existing single-channel
            sequence is used.</p>
            <h2>When queued delivery starts</h2>
            <p>Automatic delivery is a quiet-station service. The control channel
            must be live and the network must be idle: no payload, scanner,
            calibration or other exclusive session may be active. A periodic
            sweep checks a currently heard next hop, considers one eligible
            queued message (Outbox before Transit), and starts it only when the
            route still resolves to that heard hop. The check runs at most once
            every ten seconds and an individual message has a five-minute
            retry cooldown. Messages marked <b>FAILED</b> are skipped until the
            operator retries them. Queueing a message therefore does not start
            radio traffic when Control is stopped; with a live idle Control
            channel, the next check can pick it automatically. A message can
            wait normally until its next hop is heard.</p>
            <p>Manual Send uses the same route, QSY, payload and receipt guards.
            The distinction is who starts the attempt: the operator or the
            idle-station sweep. When a payload owns the shared audio device,
            beacons, automatic delivery and the scanner wait until the control
            channel is available again.</p>
            <h2>Discovery, topology and QSY</h2>
            <p>Discovery has two supported modes. <b>Off</b> does not participate
            in multi-hop discovery. <b>On</b> may answer route requests,
            search for a requested route and exchange live link observations.
            The old receive-only Monitor mode is retired; a migrated profile is
            read as On. In the production policy, automatic route use is
            on, so a valid RREQ/RREP result can carry a message without a
            normal approval click. TTL, route lifetime, frame budget and
            allow/deny lists still bound the traffic. Link-advert observations
            are live and expire; they become useful for routing only when the
            required reciprocal evidence exists. Imported Topology is a
            separate configured graph and is not experimental.</p>
            <p>Automatic QSY depends on a frequency in the selected route and a
            radio driver that can retune safely. If a negotiated separate
            working channel is used, that channel is proposed and checked on
            the calling channel before the payload. The transfer returns to
            control before the final receipt. A missing frequency, incompatible
            mode, or unavailable CAT path prevents unsafe automatic retuning;
            inspect Activity and the route row instead of assuming that a route
            implies a frequency.</p>
            <h2>Payload preparation is part of the dependency chain</h2>
            <p>Before announcing a payload, Guardian builds the bundle in a
            worker. The only production compression option is lossless Guardian
            XZ (LZMA2) with automatic lossless image repacking. A candidate is
            kept only if it makes the bundle smaller; otherwise the ordinary
            ZIP is sent. Legacy VARA FILES/BZIP2 settings are migrated into this
            policy and are written false, so compression methods are never
            stacked. XZ changes bundle size and preparation time, not the SC
            waveform, ARQ rules or receipt protocol.</p>
            <p>The selected payload workflow is negotiated per hop. SC-FTN
            needs both peers to advertise the same valid SC profile. A peer that
            does not advertise SC capability uses the VARA P2P fallback; an
            incompatible SC profile is a negotiation failure rather than a
            silent downgrade. Native SC transfer therefore does not require
            VARA on a matching hop, while a capability fallback hop does. The
            active control modem remains independent: automatic
            selection maps FM to AFSK 1200 and HF to MFSK-16, unless an explicit
            control-modem choice overrides it.</p>
            """,
            """
            <h2>Co běží automaticky</h2>
            <p>Síťové funkce spolupracují v tomto pořadí:</p>
            <p><b>důkaz slyšení → volba trasy → případné QSY → příprava a dohoda
            payloadu → ARQ přenos → potvrzení → návrat na řídicí kanál</b></p>
            <p>Každá šipka má podmínku. Spuštění Guardianu otevře jen místní
            rozhraní. Živý řídicí kanál potřebuje zvolený zvukový vstup i
            výstup, aktivní řídicí modem a použitelnou cestu rádia/PTT. Teprve
            po skutečném řídicím rámci lze stanici považovat za právě slyšený
            next hop. Samotná trasa není důkazem, že protistanice nyní poslouchá.</p>
            <h2>Produkční politika</h2>
            <p>Dodávaná konfigurace při načtení, uložení i použití vynutí
            hostitelské PTT, automatické routování, relay, doručování z fronty,
            automatické QSY, předávání discovery, jeho automatické použití a
            živé linkové adverty. Proto se tyto cesty neukazují jako běžné
            přepínače obsluhy. Stanice tedy může odpovídat a předávat povolený
            discovery provoz, použít použitelnou nalezenou trasu, nabídnout
            relay a zkusit vhodnou čekající zprávu bez změny skrytých polí.
            Přesto nikdy nezaklíčuje jen proto, že se otevřela aplikace.</p>
            <p>Dvě funkce vysílání zůstávají výslovnou volbou. <b>Majáky
            přítomnosti</b> jsou do zapnutí vypnuté a potom běží v nastaveném
            intervalu. <b>Samostatné pracovní kanály VARA</b> jsou také do
            zapnutí vypnuté a na obou stranách potřebují skutečné CAT. Dohoda
            proběhne na volacím kanálu, iniciátor navrhne svůj pracovní kanál a
            obě stanice se pro potvrzení vrátí na volací kanál. Bez volby se
            používá dosavadní jednokanálové pořadí.</p>
            <h2>Kdy začne doručování z fronty</h2>
            <p>Automatické doručování slouží klidové stanici. Řídicí kanál musí
            být živý a síť nečinná: nesmí běžet payload, scanner, kalibrace ani
            jiná výhradní relace. Pravidelný průchod ověří právě slyšený next
            hop, vezme jednu vhodnou čekající zprávu (nejprve K odeslání, potom
            Předávané) a spustí ji jen tehdy, když trasa stále ukazuje na tento
            slyšený hop. Kontrola probíhá nejvýše jednou za deset sekund a jedna
            zpráva má pětiminutovou prodlevu mezi pokusy. Zprávy ve stavu
            <b>FAILED</b> se přeskočí, dokud je obsluha ručně nezopakuje.
            Zařazení zprávy tedy při vypnutém Řízení samo nezaklíčuje rádio;
            při živém klidovém Řízení ji může nejbližší kontrola vybrat
            automaticky. Zpráva může normálně čekat, dokud neuslyší další bod.</p>
            <p>Ruční Odeslat používá stejné ochrany trasy, QSY, payloadu a
            potvrzení. Rozdíl je jen v tom, kdo pokus spustí: obsluha, nebo
            průchod klidovou stanicí. Jakmile payload vlastní sdílené zvukové
            zařízení, majáky, automatické doručování i scanner počkají na návrat
            řídicího kanálu.</p>
            <h2>Discovery, topologie a QSY</h2>
            <p>Discovery má dvě podporované polohy. <b>Vypnuto</b> se neúčastní
            vícehopového hledání. <b>Zapnuto</b> smí odpovídat na dotazy,
            hledat vyžádanou trasu a vyměňovat živá pozorování linek. Staré
            pouze sledovací Monitor je zrušené; migrovaný profil se čte jako
            Zapnuto. V produkční politice je automatické použití zapnuté,
            takže platný výsledek RREQ/RREP může nést zprávu bez běžného kliknutí
            na schválení. TTL, životnost, rozpočet rámců i seznamy povolených a
            zakázaných stanic provoz stále omezují. Pozorování linkových advertů
            jsou živá a expirují; pro routování jsou použitelná až po potřebném
            oboustranném potvrzení. Importovaná Topologie je samostatný
            nastavený graf a není experimentální.</p>
            <p>Automatické QSY závisí na frekvenci ve vybrané trase a na ovladači
            rádia, který umí bezpečně přeladit. Při dohodnutém samostatném
            pracovním kanálu se návrh zkontroluje na volacím kanálu před
            payloadem. Přenos se před závěrečným potvrzením vrátí k řízení.
            Chybějící frekvence, nekompatibilní režim nebo nedostupné CAT
            zabrání nebezpečnému automatickému přeladění; sledujte Aktivitu a
            řádek trasy, protože samotná trasa frekvenci nezaručuje.</p>
            <h2>Příprava payloadu je součást řetězce</h2>
            <p>Před nabídkou payloadu Guardian sestaví balíček ve workeru. Jediná
            produkční komprese je bezeztrátový Guardian XZ (LZMA2) s automatickým
            bezeztrátovým přebalením obrázků. Kandidát se použije jen tehdy,
            zmenší-li balíček; jinak odejde běžný ZIP. Staré nastavení VARA
            FILES/BZIP2 se do této politiky převede a uloží jako vypnuté, takže
            se metody nevrství. XZ mění velikost a čas přípravy balíčku, nikoli
            waveform SC, pravidla ARQ ani protokol potvrzení.</p>
            <p>Zvolený workflow payloadu se dohodne na každém hopu. SC-FTN
            potřebuje, aby obě stanice ohlásily stejný platný SC profil. Stanice,
            která schopnost SC vůbec neohlásí, použije zálohu VARA P2P;
            nekompatibilní SC profil znamená chybu dohody, nikoli tiché
            přepnutí. Nativní SC přenos tedy na shodném hopu VARA nepotřebuje,
            ale hop se záložní schopností ano. Aktivní
            řídicí modem zůstává nezávislý: automatická volba mapuje FM na AFSK
            1200 a HF na MFSK-16, pokud ji nepřebije výslovná volba řídicího
            modemu.</p>
            """,
            "automatic delivery dependencies auto_route auto_relay auto_deliver "
            "auto_qsy discovery_forward discovery_auto_use link_advert_enabled "
            "vara_host_ptt heard route qsy compression receipt dependency "
            "automatické doručování závislosti trasa slyšené QSY komprese",
        ),
        _topic(
            "8. Mail and standardized templates",
            "8. Pošta a normované šablony",
            """
            <h2>Mail and standardized templates</h2>
            <p>Compose creates a local message bundle containing UTF-8 text,
            metadata and optional binary attachments. The message is first
            placed in Outbox; composing alone never transmits.</p>
            <p><b>Plain message</b> has a subject and free text. <b>ICS-213</b>
            mirrors the FEMA/NIMS General Message fields. <b>ICS-214</b>
            captures a chronological Activity Log. <b>IARU</b> preserves the
            radiogram preamble and concise emergency text. <b>SITREP</b> is a
            Guardian operational template and is explicitly not a numbered
            FEMA form.</p>
            <p>Structured fields are serialized into readable English-labelled
            plain text for interoperability. The Czech UI translates the input
            labels but does not localize the on-air field names. Add attachments
            cautiously: large files consume significant airtime.</p>
            <p>Inbox holds messages addressed to this station. Transit holds
            bundles for relay. Sent contains confirmed outbound messages.
            Deleting removes the local bundle and cannot be undone.</p>
            """,
            """
            <h2>Pošta a normované šablony</h2>
            <p>Nová zpráva vytvoří místní balíček s textem UTF-8, metadaty a
            volitelnými binárními přílohami. Nejprve se uloží do složky
            K odeslání; samotné psaní nikdy nevysílá.</p>
            <p><b>Běžná zpráva</b> obsahuje předmět a volný text.
            <b>ICS-213</b> odpovídá polím obecné zprávy FEMA/NIMS.
            <b>ICS-214</b> zachycuje chronologický záznam činnosti.
            <b>IARU</b> zachovává záhlaví radiogramu a stručný nouzový text.
            <b>SITREP</b> je operační šablona Guardianu a není číslovaným
            formulářem FEMA.</p>
            <p>Strukturovaná pole se kvůli interoperabilitě zapisují do
            prostého textu s anglickými názvy. České rozhraní překládá popisy
            vstupu, nikoli názvy odesílané vzduchem. Přílohy přidávejte opatrně:
            velké soubory spotřebují mnoho vysílacího času.</p>
            <p>Doručené obsahují zprávy pro tuto stanici. Předávané uchovávají
            balíčky pro relay. Odeslané obsahují potvrzené odchozí zprávy.
            Odstranění smaže místní balíček a nelze je vrátit.</p>
            """,
            "mail compose template ics 213 214 iaru sitrep attachment pošta šablona",
        ),
        _topic(
            "9. Routes, heard stations and sessions",
            "9. Trasy, slyšené stanice a relace",
            """
            <h2>Routes and network state</h2>
            <p>A manual route maps a final destination to a preferred next hop,
            optional backup, control/direct-QSY frequency and mode. Add or replace saves
            the normalized uppercase route. Remove selected deletes only that
            destination entry.</p>
            <p>The Routes table also lists what the station currently observes —
            heard stations, discovered RREQ routes and live topology — each with
            its source and an <b>Expires in</b> value. Those rows are read-only,
            expire on their own and are never written to the route file; a
            planned route hides the duplicate observation for its destination.
            <b>Save as manual route</b> copies the selected live or generated row
            in as a permanent manual route, carrying the frequency a station was
            actually heard on, and is also how you create a manual override for a
            generated Topology row.</p>
            <p>When automatic route discovery is enabled and no manual or
            learned route exists, ARDOS broadcasts ROUTE_QUERY and evaluates
            ROUTE_OFFER responses. A direct destination wins; relay candidates
            with a measurement are ranked by S/N, then freshness and callsign.
            Heard stations appear only after a real control frame; age is
            measured from the latest frame.</p>
            <p>TTL limits relay depth. Auto relay allows this station to hold
            and forward traffic for another destination. Auto QSY uses the
            route frequency before VARA P2P and restores the prior frequency
            afterwards when supported by the radio driver.</p>
            <p><b>Network builder</b> imports one shared link topology or builds
            it in a three-step wizard. Each PC derives its own routes from its
            configured callsign. Links may be directional, disabled or carry a
            positive cost and calling/working channels. Generated routes are
            labelled Topology; saving the same destination manually creates an
            override.</p>
            <p><b>Route discovery</b> is its own page with two positions. Off
            ignores every multi-hop discovery frame. On answers a query
            about this station, may look for a route you ask for, and can
            exchange live link observations. In the shipped policy,
            automatic route use is always enabled, so a usable RREQ/RREP
            result can carry an originating message without a separate approval
            click. Learned routes expire and never overwrite manual or Topology
            rows. The receive-only Monitor position of earlier releases is gone:
            it could neither answer a query nor produce a usable route, so a
            profile holding it is read as On after an upgrade.</p>
            <p>Find route uses expanding TTL rings and displays the query and
            returned path. It is disabled, with the reason on the page, unless
            the control channel is running and the mode is On. A station
            heard directly is listed as a one-hop route so the operator can see
            at a glance what is reachable now. Clearing dynamic routes does not
            touch the route table or builder.</p>
            <p>The operator-facing bounds live in Settings → Network behavior:
            maximum TTL (capped at 8), route lifetime, the frames-per-minute
            airtime budget and the allow/deny lists. Production policy keeps
            forwarding and relay service available together, so a node never
            advertises a payload path it refuses to serve. Automatic delivery,
            automatic QSY and automatic discovery use follow the dependency
            gates described in the automatic-network topic.</p>
            <p>Automatic use lets a fresh RREQ/RREP route carry a message
            without approval; it does nothing while discovery is off. The
            <b>LINK_ADVERT</b> live-topology feature is covered in its own help
            topic: it exchanges direct observations on a schedule and derives
            a volatile graph. It is a supported discovery path, separate from an
            active RREQ/RREP search. Turning discovery off gates both paths;
            turning the optional presence beacon off does not.</p>
            <p>A relay's RECEIVED means <b>Forwarded</b>, not final delivery.
            The final station sends a directed DELIVERED receipt back over the
            reverse hops. Transit mail keeps its resolved next hop across
            failure/restart and retries no more often than every five minutes.</p>
            <p>Separate VARA working channels are an advanced opt-in under
            Network behavior and require real CAT on both peers. Until enabled,
            their route fields are hidden and single-channel operation is
            unchanged. When enabled, the station that opens the session
            proposes its own working channel on the calling channel and the
            receiving station follows it, even when its route table names a
            different one. A proposal is followed only within the band that
            station already works the peer on, only on a mode the local VARA
            can use, and only with automatic QSY and a CAT radio; anything
            else is refused. Both then move for the VARA payload only and
            return before control confirmations resume.</p>
            <p>Last S/N is estimated from the received audio against the idle
            noise floor, not reported by the modem, and stays empty until that
            floor has settled. Heard on is the frequency this radio was tuned
            to when the frame arrived.</p>
            <p>The route frequencies are also the channel list for a net alert:
            when the sweep is confirmed in the alert dialog, the radio visits
            each of them, repeats the same alert, and returns to the frequency
            and mode it started on. Only channels compatible with the active
            FM or HF control modem are visited.</p>
            <p>The previously tested scanner engine remains compatible in the
            backend, but its Network page is now the topology builder. Generated
            local routes provide the channel plan for routing and any future
            automatic listening workflow.</p>
            """,
            """
            <h2>Trasy a stav sítě</h2>
            <p>Ruční trasa přiřazuje konečnému cíli upřednostněný další bod,
            volitelnou zálohu, řídicí frekvenci či frekvenci přímého QSY a režim. Přidání nebo nahrazení
            uloží trasu normalizovanou na velká písmena. Odstranění smaže jen
            záznam vybraného cíle.</p>
            <p>Tabulka Trasy zobrazuje i to, co stanice právě pozoruje — slyšené
            stanice, nalezené trasy RREQ a živou topologii — vždy se zdrojem a
            hodnotou <b>Expiruje za</b>. Tyto řádky jsou jen ke čtení, expirují
            samy a do souboru tras se nikdy nezapisují; plánovaná trasa skryje
            duplicitní pozorování téhož cíle. <b>Uložit jako ruční trasu</b>
            zkopíruje vybraný živý nebo odvozený řádek dovnitř jako trvalou ruční
            trasu i s kmitočtem, na kterém byla stanice skutečně slyšena, a je to
            zároveň způsob, jak vytvořit ruční override odvozeného řádku
            Topologie.</p>
            <p>Je-li zapnuto automatické hledání a neexistuje ruční ani naučená
            trasa, ARDOS vyšle ROUTE_QUERY a vyhodnotí odpovědi ROUTE_OFFER.
            Přímý cíl má přednost; relay kandidáti s měřením se řadí podle S/N,
            potom podle čerstvosti a volací značky. Slyšené stanice se zobrazí
            jen po skutečném řídicím rámci; stáří se počítá od posledního rámce.</p>
            <p>TTL omezuje hloubku předávání. Automatický relay dovolí stanici
            podržet a předat provoz jinému cíli. Auto QSY před VARA P2P použije
            frekvenci z trasy a po skončení podle možností rádia obnoví původní.</p>
            <p><b>Sestavovač sítě</b> importuje jednu sdílenou topologii linek
            nebo ji vytvoří v tříkrokovém průvodci. Každý počítač odvodí vlastní
            trasy podle nastavené značky. Linka může být jednosměrná, zakázaná,
            mít kladnou cenu a volací/pracovní kanál. Odvozené trasy jsou
            označené Topologie; ruční uložení stejného cíle vytvoří override.</p>
            <p><b>Hledání trasy</b> je samostatná stránka se dvěma polohami.
            Vypnuto ignoruje všechny vícehopové discovery rámce. Zapnuto
            odpovídá na dotaz po této stanici, smí hledat trasu, o kterou
            požádáte, a vyměňovat živá pozorování linek. V dodávané politice je
            automatické použití nalezené trasy vždy zapnuté, takže použitelný výsledek
            RREQ/RREP může nést odchozí zprávu bez samostatného kliknutí na
            schválení. Naučené trasy expirují a nepřepisují ruční ani
            topologické řádky. Poloha Pouze sledovat z dřívějších verzí je
            zrušená: neumožňovala odpovědět na dotaz ani získat použitelnou
            trasu, takže se profil, který ji má uložený, po aktualizaci čte jako
            Zapnuto.</p>
            <p>Najít trasu používá rozšiřované kruhy TTL a zobrazuje dotaz i
            vrácenou cestu. Dokud neběží řídicí kanál a není zvolen režim
            Zapnuto, je tlačítko nedostupné a důvod je vypsaný na stránce. Přímo
            slyšená stanice je uvedená jako jednoskoková trasa, takže je hned
            vidět, co je právě dosažitelné. Vymazání dynamických tras nemění
            tabulku tras ani sestavovač.</p>
            <p>Obsluhou nastavitelné limity jsou v Nastavení → Chování sítě:
            maximální TTL (strop 8), životnost trasy, vysílací rozpočet v rámcích
            za minutu a seznamy povolených a zakázaných stanic. Produkční
            politika drží předávání a relay společně dostupné, takže uzel
            nenabízí payloadovou cestu, kterou odmítá obsloužit. Automatické
            doručování, QSY a použití discovery se řídí podmínkami popsanými v
            tématu automatických závislostí.</p>
            <p>Automatické použití dovolí čerstvé trase RREQ/RREP přenést zprávu
            bez schválení; při vypnutém hledání nedělá nic. Funkce živé
            topologie <b>LINK_ADVERT</b> je popsána ve vlastním tématu:
            vyměňuje přímá pozorování v intervalu a odvozuje volatilní graf.
            Jde o podporovanou cestu discovery oddělenou od aktivního hledání
            RREQ/RREP. Vypnutí discovery zablokuje obě cesty; vypnutí
            volitelného majáku přítomnosti linkové adverty nevypíná.</p>
            <p>RECEIVED od relaye znamená <b>Předáno</b>, nikoli koncové
            doručení. Cílová stanice pošle směrované DELIVERED zpět po reverzních
            hopech. Transit zpráva zachová vypočtený next hop i po selhání nebo
            restartu a neopakuje automatický pokus častěji než po pěti minutách.</p>
            <p>Samostatné pracovní kanály VARA jsou pokročilá volitelná funkce
            v Chování sítě a na obou stranách vyžadují skutečné CAT. Dokud ji
            nezapnete, její pole tras jsou skrytá a jednokanálový provoz se
            nemění. Po zapnutí navrhne stanice, která relaci zahajuje, na
            volacím kanálu svůj pracovní kanál a přijímající stanice se za ní
            přeladí, i když má ve své tabulce tras jiný. Návrh přijme jen
            v pásmu, na kterém s protistanicí už pracuje, jen v režimu, který
            místní VARA umí, a jen se zapnutým automatickým QSY a CAT rádiem;
            cokoli jiného odmítne. Přeladí se pouze na payload a před řídicím
            potvrzením se obě vrátí.</p>
            <p>Poslední S/N je odhad z přijatého zvuku proti klidové úrovni
            šumu, nikoli údaj z modemu, a zůstává prázdné, dokud se úroveň
            neustálí. Slyšeno na je kmitočet, na kterém bylo rádio naladěno při
            příjmu rámce.</p>
            <p>Kmitočty z tras slouží také jako seznam kanálů pro výstrahu do
            sítě: pokud přeladění v dialogu výstrahy potvrdíte, rádio postupně
            navštíví každý z nich, zopakuje tam stejnou výstrahu a vrátí se na
            původní kmitočet i režim. Navštíví jen kanály kompatibilní s právě
            aktivním řídicím modemem pro FM nebo HF.</p>
            <p>Dříve ověřený scannerový engine zůstává kompatibilní v backendu,
            ale jeho stránku v Síti nahradil sestavovač topologie. Odvozené
            místní trasy poskytují plán kanálů pro routing a případný budoucí
            automatický poslech.</p>
            """,
            "route heard sessions ttl relay qsy discovery rreq rrep link advert "
            "trasa slyšené relace hledání trasy živá topologie",
        ),
        _topic(
            "10. LINK_ADVERT live topology",
            "10. Živá topologie LINK_ADVERT",
            """
            <h2>LINK_ADVERT and live topology</h2>
            <p><code>LINK_ADVERT</code> is a supported discovery mechanism for
            exchanging observations of direct neighbours. It is separate from
            the optional presence <b>beacon</b>. Production configuration keeps
            link adverts enabled, while the beacon checkbox remains off until
            the operator opts into periodic station-presence frames. Turning
            the beacon off therefore does not turn off LINK_ADVERT. Stopping
            the live control channel stops all on-air control traffic, including
            both kinds of advert.</p>
            <p>In <b>On</b> discovery, Guardian sends adverts on the
            configured live-topology interval when the control channel is free.
            With no known neighbour it can send a one-hop presence advert to
            bootstrap a quiet peer. When neighbours are known, it advertises
            direct observations and may relay them only within the discovery
            TTL, forwarding/relay policy, trust lists and frame-per-minute
            budget. <b>Off</b> discovery gates advert transmission and receipt;
            the beacon setting is not a substitute for this discovery mode.</p>
            <p>The <b>Live topology</b> view keeps owner, neighbour, direction,
            quality, age and expiry evidence. A row can remain visible as a
            unidirectional observation, but the live route builder uses a link
            only after both ends independently confirm the relationship. The
            resulting graph is volatile: entries age out, clearing it removes
            live-derived routes, and nothing is silently written into the
            manual route file. <b>Save as manual route</b> is the explicit way
            to keep a selected destination.</p>
            <p><b>Example:</b> A advertises that it hears B, and B independently
            advertises that it hears A. This confirms A–B. With a similarly
            confirmed B–C link, a live path A → B → C can be derived even when
            A cannot hear C directly. A's one-sided claim alone is insufficient.
            Once the required evidence expires, it can no longer support that
            live route.</p>
            <h2>How LINK_ADVERT differs from route search</h2>
            <p><b>RREQ/RREP discovery</b> is an active query. Guardian expands
            the TTL rings, waits for route offers and ranks usable replies. In
            the production policy, automatic discovery use may immediately
            pass a message over a usable result. <b>LINK_ADVERT</b> is periodic
            neighbour evidence: it builds or refreshes a directed live graph
            before a message needs a route. It does not replace the on-demand
            query, and an advert observation is not a delivery receipt.
            <b>Topology</b> imported or built in the wizard is a separate,
            configured graph; it is not experimental and is not overwritten by
            live observations.</p>
            <p>These mechanisms still share safety limits. TTL, allow/deny
            lists, relay permission, the airtime frame budget and a live idle
            control channel must all permit a frame. A beacon carries optional
            station presence and no acknowledgement; LINK_ADVERT carries link
            observations; RREQ/RREP carries a route search. Their labels in the
            Network workspace make the source of a row visible.</p>
            """,
            """
            <h2>LINK_ADVERT a živá topologie</h2>
            <p><code>LINK_ADVERT</code> je podporovaný mechanismus discovery pro
            výměnu pozorování přímých sousedů. Je oddělený od volitelného
            <b>majáku</b> přítomnosti. Produkční konfigurace linkové adverty
            drží zapnuté, zatímco přepínač majáku zůstává vypnutý, dokud jej
            obsluha výslovně nepovolí. Vypnutí majáku tedy LINK_ADVERT nevypne.
            Zastavení živého řídicího kanálu zastaví veškerý řídicí provoz ve
            vzduchu, oba druhy advertů.</p>
            <p>V režimu discovery <b>Zapnuto</b> Guardian posílá adverty v
            nastaveném intervalu živé topologie, když je řídicí kanál volný. Bez
            známého souseda může poslat jednoskokový advert přítomnosti a tím
            zviditelnit stanici v dosud tiché síti. Se známými sousedy oznamuje přímá pozorování
            a může je předávat jen v mezích TTL discovery, politiky forwarding/
            relay, seznamů důvěry a rozpočtu rámců za minutu. <b>Vypnuto</b>
            blokuje odesílání i příjem advertů; nastavení majáku tento režim
            nenahrazuje.</p>
            <p>Pohled <b>Živá topologie</b> drží vlastníka, souseda, směr,
            kvalitu, stáří a čas do expirace. Řádek může zůstat viditelný jako
            jednosměrné pozorování, ale sestavovač živých tras použije linku až
            poté, co vztah nezávisle potvrdí oba konce. Výsledný graf se uchovává pouze v paměti: záznamy stárnou, vymazání odebere trasy odvozené ze živé
            topologie a nic se tiše nezapíše do souboru ručních tras. Chcete-li
            cíl zachovat, použijte výslovně <b>Uložit jako ruční trasu</b>.</p>
            <p><b>Příklad:</b> A oznámí, že slyší B, a B nezávisle oznámí, že
            slyší A. Tím je potvrzena vazba A–B. Pokud stejným způsobem platí
            B–C, může vzniknout živá cesta A → B → C, i když A přímo neslyší C.
            Pouhé tvrzení A „slyším B“ k potvrzení vazby nestačí. Jakmile potřebné
            pozorování zestárne, přestává být podkladem pro živou trasu.</p>
            <h2>Rozdíl mezi LINK_ADVERT a hledáním trasy</h2>
            <p><b>Discovery RREQ/RREP</b> je aktivní dotaz. Guardian rozšiřuje
            kruhy TTL, čeká na nabídky tras a řadí použitelné odpovědi. V
            produkční politice může automatické použití discovery ihned předat
            zprávu po použitelné cestě. <b>LINK_ADVERT</b> je pravidelný důkaz
            sousedství: staví nebo obnovuje živý směrovaný graf dříve, než je
            trasa potřeba. Nenahrazuje dotaz na vyžádání a pozorování advertu
            není potvrzení doručení. <b>Topologie</b> importovaná nebo sestavená
            v průvodci je jiný, nastavený graf; není experimentální a živá
            pozorování jej nepřepisují.</p>
            <p>Mechanismy sdílejí bezpečnostní limity. TTL, seznamy povolených a
            zakázaných stanic, povolení relaye, rozpočet rámců a živý nečinný
            řídicí kanál musí vysílání dovolit. Maják nese volitelnou přítomnost
            stanice a nemá potvrzení; LINK_ADVERT nese pozorování linek;
            RREQ/RREP nese hledání trasy. V pracovním prostoru Sítě je u řádku
            vidět zdroj záznamu.</p>
            """,
            "LINK_ADVERT LINK ADVERT linkadvert live topology discovery rreq rrep "
            "presence beacon reciprocal neighbours forwarding TTL route advert "
            "živá topologie linkový advert sousedé reciproční discovery maják",
        ),
        _topic(
            "11. Net alerts and notifications",
            "11. Výstrahy do sítě a upozornění",
            """
            <h2>Net alerts</h2>
            <p>An alert is broadcast to everyone on the current frequency and
            flooded hop to hop, so it must fit in a single control burst. What
            travels is a <b>one-byte code</b> plus an optional short note of at
            most 25 characters. The code carries the meaning: each station
            expands it into a full sentence in its own language, so two
            operators who never agreed on wording still understand each other.
            A note that is too long is truncated rather than refused — a
            slightly abbreviated alert still beats one that was never sent.</p>
            <p>The seed codes cover three priorities. <b>Emergency:</b> MAYDAY,
            medical emergency, evacuation. <b>Priority:</b> QSY, power outage,
            running on battery. <b>Routine:</b> QRT, QRV, net test. Codes are
            permanent once used on the air — new ones are added, never
            renumbered — so an older station always reads a known code
            correctly.</p>
            <p>Sending an alert asks for confirmation and states the airtime it
            costs, roughly 1.2 s on AFSK and 1.7 s on MFSK per burst. If the
            dialog's channel sweep is confirmed, the radio then visits every
            frequency in the route table, repeats the same alert there, and
            returns to the frequency and mode it started on. Only channels
            compatible with the active FM or HF control modem are visited.</p>
            <h2>How an incoming alert is announced</h2>
            <p>A received alert raises the banner above the workspace and stays
            there until dismissed; dismissal is tracked by arrival time, so a
            newer alert behind a dismissed one is still shown. On the map the
            origin locator gets a pulsing ring, with the sender and kind named
            in a chip above the canvas.</p>
            <p>Notification has two deliberate levels. Routine mail gets a tray
            toast and a soft chime, skipped entirely when Guardian is already
            the active window. An URGENT or EMERGENCY alert gets a window that
            stays on top and a sound that repeats until someone acknowledges
            it. Alerts older than 15 minutes no longer interrupt anyone; the
            banner and the log keep the history.</p>
            <p>The chime has one hard rule: it must never reach the
            transmitter. Guardian's audio output is wired to the radio, and on
            many stations that same USB codec is also the Windows default
            device. Guardian therefore checks the default output and stays
            silent when it cannot prove the sound stays in the shack. It also
            stays silent while transmitting and while VARA holds the codec —
            the operator is listening to the channel, not to the desktop.</p>
            """,
            """
            <h2>Výstrahy do sítě</h2>
            <p>Výstraha se vysílá všem na aktuálním kmitočtu a šíří se hop po
            hopu, takže se musí vejít do jediného řídicího rámce. Vzduchem
            putuje <b>jednobajtový kód</b> a volitelná krátká poznámka nejvýše
            o 25 znacích. Význam nese kód: každá stanice si jej rozvine do celé
            věty ve svém jazyce, takže si rozumí i dva operátoři, kteří se na
            znění nikdy nedohodli. Příliš dlouhá poznámka se zkrátí, místo aby
            byla odmítnuta — mírně zkrácená výstraha je pořád lepší než
            neodeslaná.</p>
            <p>Výchozí sada kódů pokrývá tři priority. <b>Nouzové:</b> MAYDAY,
            zdravotní příhoda, evakuace. <b>Přednostní:</b> QSY, výpadek
            napájení, provoz z baterie. <b>Běžné:</b> QRT, QRV, test sítě. Kód
            je po prvním použití ve vzduchu trvalý — nové se přidávají, nikdy se
            nepřečíslovávají — takže i starší stanice přečte známý kód
            správně.</p>
            <p>Odeslání výstrahy vyžaduje potvrzení a uvádí, kolik vysílacího
            času stojí: zhruba 1,2 s na AFSK a 1,7 s na MFSK za jeden rámec.
            Potvrdíte-li v dialogu přeladění, rádio pak postupně navštíví každý
            kmitočet z tabulky tras, zopakuje tam stejnou výstrahu a vrátí se na
            původní kmitočet i režim. Navštíví jen kanály kompatibilní s právě
            aktivním řídicím modemem pro FM nebo HF.</p>
            <h2>Jak se ohlásí příchozí výstraha</h2>
            <p>Přijatá výstraha vyvolá pruh nad pracovní plochou a zůstane tam,
            dokud jej nezavřete; zavření se pamatuje podle času příchodu, takže
            novější výstraha za tou zavřenou se zobrazí také. V mapě dostane
            místo původu pulzující kroužek a nad plátnem se objeví štítek se
            značkou odesílatele a druhem výstrahy.</p>
            <p>Upozorňování má záměrně dvě úrovně. Běžná zpráva dostane
            oznámení v oznamovací oblasti a tichý zvuk, který se úplně vynechá,
            pokud je Guardian právě aktivním oknem. Výstraha URGENT nebo
            EMERGENCY otevře okno, které zůstává navrchu, a zvuk se opakuje,
            dokud jej někdo nepotvrdí. Výstrahy starší než 15 minut už nikoho
            nevyrušují; historii drží pruh a provozní log.</p>
            <p>Pro zvuk platí jedno tvrdé pravidlo: nikdy nesmí odejít do
            vysílače. Zvukový výstup Guardianu je zapojený do rádia a na mnoha
            stanicích je tatáž karta USB zároveň výchozím zařízením Windows.
            Guardian proto kontroluje výchozí výstup a mlčí, pokud nemůže
            prokázat, že zvuk zůstane v šacku. Mlčí i během vysílání a po dobu,
            kdy zvukovou kartu drží VARA — operátor v tu chvíli poslouchá kanál,
            ne plochu.</p>
            """,
            "alert mayday evacuation qsy priority notification toast sound "
            "výstraha nouze evakuace upozornění zvuk oznámení",
        ),
        _topic(
            "12. VARA spectrum and waterfall",
            "12. Spektrum a waterfall VARA",
            """
            <h2>Spectrum and waterfall</h2>
            <p>The floating spectrum window (<b>View → VARA spectrum &amp;
            waterfall</b>, Ctrl+Shift+W) shows the receive audio Guardian's own
            input device is hearing. It opens automatically when the station is
            configured for VARA P2P, and it is an independent top-level window
            rather than a child of the shell, so either window can be focused
            or moved to a second screen.</p>
            <p>It is a listening aid, not a decoder. It never keys the radio,
            never opens the transmit path, and does not need VARA to be
            connected — it reads the same audio input the control modem uses.
            Use it to confirm that receive audio actually arrives, that the
            level is neither silent nor clipping, and that a VARA carrier is
            landing where you expect it in the passband.</p>
            <p>The spectrum trace is the current FFT of the input; the
            waterfall below scrolls that trace over time, so a burst that has
            already ended is still visible for a few seconds. That history is
            what makes it useful for a link that transmits in short bursts: you
            can see a peer's reply after the fact.</p>
            <p>The window follows the application theme. Closing it stops the
            analysis; it costs nothing while closed.</p>
            """,
            """
            <h2>Spektrum a waterfall</h2>
            <p>Plovoucí okno spektra (<b>Zobrazení → Spektrum a waterfall
            VARA</b>, Ctrl+Shift+W) ukazuje přijímaný zvuk, který slyší vstupní
            zařízení Guardianu. Otevře se samo, je-li stanice nastavena na VARA
            P2P, a je samostatným oknem, nikoli potomkem hlavního okna, takže
            lze zaostřit kterékoli z nich nebo je přesunout na druhou
            obrazovku.</p>
            <p>Jde o pomůcku pro poslech, nikoli o dekodér. Nikdy nezaklíčuje
            rádio, neotevírá vysílací cestu a nepotřebuje připojenou VARA —
            čte tentýž zvukový vstup jako řídicí modem. Použijte jej k ověření,
            že přijímaný zvuk skutečně přichází, že úroveň není ani nulová, ani
            přebuzená, a že nosná VARA dopadá v propustném pásmu tam, kam
            čekáte.</p>
            <p>Křivka spektra je aktuální FFT vstupu; waterfall pod ní tuto
            křivku posouvá v čase, takže už skončený rámec je ještě několik
            sekund vidět. Právě tato historie dělá okno užitečným pro spojení
            vysílající v krátkých dávkách: odpověď protistanice si prohlédnete
            i zpětně.</p>
            <p>Okno se řídí motivem aplikace. Zavřením se analýza zastaví;
            zavřené nestojí nic.</p>
            """,
            "spectrum waterfall fft audio level receive "
            "spektrum waterfall zvuk úroveň příjem",
        ),
        _topic(
            "13. Station map and own position",
            "13. Mapa stanic a vlastní poloha",
            """
            <h2>Station map and own position</h2>
            <p>The station map remains useful offline: heard locators, relay
            paths, alert origins and the graticule do not depend on raster
            tiles. Click a heard row to centre it or double-click to compose.</p>
            <p><b>My position</b> has manual, map and PC-location methods, plus
            a conditional radio method. <b>Detect from this PC</b> asks for
            explicit consent, requests one Windows fix, and previews its
            Maidenhead locator, reported source and accuracy. Exact coordinates
            are held only for that preview and are never saved. Press <b>Use
            locator</b> to accept or <b>Discard</b> to keep the previous value.
            A result worse than 1 km is marked approximate. Windows location
            permission can be changed through the link shown after a denial.</p>
            <p><b>Pick on map</b> arms a crosshair for exactly one click.
            <b>Locator</b> accepts a known 2, 4, 6, 8 or 10 character Maidenhead
            square. Both methods work without Windows location or internet.</p>
            <p><b>Load GPS</b> is visible only when the selected radio is a
            connected <b>IC-705</b>. It reads one recent valid NMEA fix from the
            radio's explicit <b>USB(B) GPS Out</b> interface. Guardian identifies
            that interface from the device description automatically, never
            opens the configured CAT/PTT port, and does not offer a COM picker.
            If the USB(B) role is missing, ambiguous or shared with CAT/PTT, it
            refuses to guess. Enable GPS Out on the IC-705, let the bounded read
            finish, then accept or discard the locator preview.</p>
            <p><b>Send in beacons</b> is independent. Detecting, picking or
            typing a locator does not enable beacons and does not transmit.
            When both beacons and this switch are enabled, only the accepted
            Maidenhead locator uses the existing presence-beacon field.</p>
            <p><b>Map tools</b> can draw a 4/6-character locator grid and
            geodesic 50/100/200 km rings. <b>Measure</b> uses two clicks for
            distance and initial bearing; Esc or right-click clears it. Marker
            colours and the legend distinguish direct, relay, unavailable and
            historical position evidence.</p>
            <p><b>Save area offline</b> prepares only the visible ČÚZK area at
            selected zoom levels after showing count and size. Downloads are
            bounded and cancellable. <b>Export PNG</b> saves the rendered map,
            overlays, time, version and attribution without fetching anything
            new.</p>
            """,
            """
            <h2>Mapa stanic a vlastní poloha</h2>
            <p>Mapa zůstává užitečná i offline: lokátory slyšených stanic,
            relay trasy, místa výstrah a souřadnicová síť nezávisí na
            rastrových dlaždicích. Klepnutím na řádek stanici vystředíte,
            dvojím klepnutím jí napíšete.</p>
            <p><b>Moje poloha</b> nabízí ruční zadání, výběr v mapě a polohu z
            PC, plus podmíněnou cestu z rádia. <b>Zjistit z tohoto PC</b> vyžádá
            výslovný souhlas, jednorázově požádá Windows a ukáže náhled
            Maidenhead lokátoru, hlášený zdroj a přesnost. Přesné souřadnice
            existují jen po dobu náhledu a nikdy se neukládají. Volbou
            <b>Použít lokátor</b> výsledek přijmete, volbou <b>Zahodit</b>
            zachováte předchozí hodnotu. Výsledek horší než 1 km je označen jako
            orientační. Po zamítnutí lze odkazem otevřít nastavení polohy
            Windows.</p>
            <p><b>Vybrat v mapě</b> zapne křížový kurzor právě pro jedno
            klepnutí. Pole <b>Lokátor</b> přijímá známý Maidenhead čtverec o 2,
            4, 6, 8 nebo 10 znacích. Obě cesty fungují bez polohy Windows i bez
            internetu.</p>
            <p><b>Načíst GPS</b> je vidět jen tehdy, když je vybrané rádio
            připojený <b>IC-705</b>. Načte jeden aktuální platný NMEA fix z jeho
            explicitního rozhraní <b>USB(B) GPS Out</b>. Guardian rozhraní
            automaticky rozpozná podle popisu zařízení, nikdy neotevře nastavený
            port CAT/PTT a nenabízí výběr COM. Chybí-li role USB(B), je-li
            nejednoznačná nebo sdílená s CAT/PTT, odmítne hádat. Na IC-705
            povolte GPS Out, vyčkejte na omezené čtení a náhled lokátoru přijměte
            nebo zahoďte.</p>
            <p><b>Posílat v majáku</b> je nezávislé. Detekce, výběr ani ruční
            zadání nezapnou majáky a nic nevysílají. Teprve při zapnutých
            majácích i tomto přepínači použije přijatý Maidenhead lokátor
            stávající pole majáku přítomnosti.</p>
            <p><b>Nástroje mapy</b> vykreslí 4/6znakovou lokátorovou mřížku a
            geodetické kružnice 50/100/200 km. <b>Změřit</b> použije dvě
            klepnutí pro vzdálenost a počáteční azimut; Esc nebo pravé tlačítko
            měření smaže. Barvy značek a legenda rozlišují přímý dosah, relay,
            nyní nedostupnou a historickou polohu.</p>
            <p><b>Uložit oblast offline</b> připraví pouze viditelnou oblast
            ČÚZK ve zvolených zoomech po zobrazení počtu a velikosti. Stahování
            má limity a lze je zrušit. <b>Exportovat PNG</b> uloží vykreslenou
            mapu, překryvy, čas, verzi a atribuci bez dalšího stahování.</p>
            """,
            "map position locator detect windows accuracy mapa poloha lokátor přesnost",
        ),
        _topic(
            "14. Dependencies and first-run readiness",
            "14. Závislosti a připravenost",
            """
            <h2>Dependencies and readiness</h2>
            <p>Guardian includes Python and Python libraries in the installer.
            Hamlib and VARA are external radio tools. Readiness scans explicit
            paths, PATH and common installation directories without launching
            the tools or transmitting.</p>
            <p>Guardian may download the official portable Hamlib package only
            after consent and validates published integrity data. For separately
            licensed VARA, Guardian can download only the reviewed official
            Winlink-hosted archive whose URL, size and SHA-256 are pinned in this
            release. Download and vendor-installer launch require separate
            confirmations.</p>
            <p>For normal operation set a real callsign, satisfy Hamlib only if
            the Hamlib backend is selected, and install the VARA flavor selected
            for the current workflow. VARA is required for a VARA P2P hop and
            for a per-hop fallback when the peer has no SC capability; a matching
            native SC-FTN hop uses Guardian's audio modem instead.</p>
            <h2>Feature prerequisites</h2>
            <table width="100%">
              <tr><th>Feature</th><th>What must be ready</th></tr>
              <tr><td>Control channel</td><td>Selected RX/TX audio devices,
              the active control modem and a connected radio/PTT path.</td></tr>
              <tr><td>VARA P2P</td><td>The matching VARA FM or HF process and
              its local command/data TCP ports.</td></tr>
              <tr><td>SC-FTN payload</td><td>Guardian audio endpoints and a
              matching SC profile at each SC-capable peer; otherwise VARA P2P
              must be available for the fallback hop.</td></tr>
              <tr><td>Automatic QSY / working channel</td><td>A route frequency
              and real CAT on the stations that must retune.</td></tr>
              <tr><td>IC-705 GPS</td><td>A selected, connected IC-705 with an
              explicitly identified USB(B) GPS Out serial interface.</td></tr>
              <tr><td>Map tiles and updates</td><td>Internet only for online
              downloads; locators, overlays, diagnostics and saved map areas
              remain usable offline.</td></tr>
            </table>
            <p>Use <b>Tools → Diagnostics → Connection diagnostics</b> for
            readiness snapshots, or <b>Tools → Diagnostics → SC-FTN modem</b>
            for the selected SC profile and its resolved geometry. A readiness
            check is observational: it does not start a modem, key PTT or send
            network traffic.</p>
            """,
            """
            <h2>Závislosti a připravenost</h2>
            <p>Instalátor Guardianu obsahuje Python i jeho knihovny. Hamlib a
            VARA jsou externí rádiové nástroje. Průvodce kontroluje zadané cesty,
            PATH a běžné instalační adresáře, aniž programy spouští nebo vysílá.</p>
            <p>Guardian může až po souhlasu stáhnout oficiální přenosný balíček
            Hamlib a ověřuje zveřejněné údaje integrity. Pro samostatně
            licencovanou VARA smí stáhnout jen prověřený oficiální archiv
            hostovaný Winlinkem, jehož URL, velikost a SHA-256 jsou připnuté v
            této verzi. Stažení a spuštění instalátoru dodavatele vyžadují dvě
            samostatná potvrzení.</p>
            <p>Pro běžný provoz nastavte skutečnou volací značku, zajistěte
            Hamlib jen při zvoleném backendu Hamlib a nainstalujte variantu VARA
            použitou v aktuálním postupu. VARA je nutná pro hop VARA P2P a pro
            záložní hop, jehož protistanice nemá schopnost SC; shodný nativní hop
            SC-FTN používá zvukový modem Guardianu.</p>
            <h2>Předpoklady funkcí</h2>
            <table width="100%">
              <tr><th>Funkce</th><th>Co musí být připraveno</th></tr>
              <tr><td>Řídicí kanál</td><td>Zvolený zvukový vstup/výstup,
              aktivní řídicí modem a připojené rádio s cestou PTT.</td></tr>
              <tr><td>VARA P2P</td><td>Odpovídající proces VARA FM nebo HF a
              jeho místní příkazové/datové TCP porty.</td></tr>
              <tr><td>Payload SC-FTN</td><td>Zvukové konce Guardianu a shodný
              SC profil u každého peeru se schopností SC; jinak musí být pro
              záložní hop dostupná VARA P2P.</td></tr>
              <tr><td>Automatické QSY / pracovní kanál</td><td>Frekvence v
              trase a skutečné CAT u stanic, které se mají přeladit.</td></tr>
              <tr><td>GPS IC-705</td><td>Vybrané připojené IC-705 s výslovně
              rozpoznaným sériovým rozhraním USB(B) GPS Out.</td></tr>
              <tr><td>Dlaždice mapy a aktualizace</td><td>Internet jen pro
              online stahování; lokátory, překryvy, diagnostika a uložené oblasti
              mapy fungují offline.</td></tr>
            </table>
            <p>Pro snímek připravenosti použijte <b>Provoz → Diagnostika →
            Diagnostika připojení</b>, pro zvolený SC profil a jeho vypočtenou
            geometrii <b>Provoz → Diagnostika → Modem SC-FTN</b>. Kontrola pouze
            pozoruje stav: nespouští modem, nezaklíčuje PTT ani neposílá síťový
            provoz.</p>
            """,
            "dependencies readiness hamlib vara python závislosti připravenost",
        ),
        _topic(
            "15. SC-FTN modem, MCS, FEC and ARQ",
            "15. Modem SC-FTN, MCS, FEC a ARQ",
            """
            <h2>What Guardian SC-FTN is</h2>
            <p><b>Guardian SC-FTN</b> (single-carrier faster-than-Nyquist) is the
            native single-carrier payload modem. The production waveform family has six selectable audio
            profiles and does not use an FFT/OFDM payload implementation; some
            internal compatibility names still contain “OFDM”. The sound-card
            sample rate is <b>48,000 samples/s</b>. That is an audio timing
            reference, not a 48 kbps payload promise.</p>
            <p>The operator selects the SC-FTN bandwidth label. Guardian then
            resolves the physical profile and automatic link policy. The
            current effective default geometry is:</p>
            <table width="100%">
              <tr><th>Label</th><th>Audio waveform width</th><th>Center</th><th>Symbol rate</th></tr>
              <tr><td>1K2</td><td>1,200 Hz</td><td>1,050 Hz</td><td>1,185.2</td></tr>
              <tr><td>2K7</td><td>≈2,858.8 Hz</td><td>1,779.4 Hz</td><td>2,823.5</td></tr>
              <tr><td>4K5</td><td>4,500 Hz</td><td>2,600 Hz</td><td>4,444.4</td></tr>
              <tr><td>5K</td><td>5,000 Hz</td><td>2,950 Hz</td><td>4,938.3</td></tr>
              <tr><td>10K</td><td>10,000 Hz</td><td>5,450 Hz</td><td>9,876.5</td></tr>
              <tr><td>20K</td><td>18,750 Hz</td><td>9,825 Hz</td><td>18,518.5</td></tr>
            </table>
            <p>The values are audio waveform geometry. They are not a guarantee
            of RF occupied bandwidth: the radio's filter, modulation and FM
            deviation still matter. They also do not select a frequency or
            promise a working-channel width. The 2K7 policy deliberately
            resolves a Nyquist symbol rate of about 2,541.2 and a symbol rate of
            about 2,823.5, so its effective audio width is about 2,858.8 rather
            than the label's nominal 2,700. The SC-FTN modem workspace and
            <b>Tools → Diagnostics → SC-FTN modem</b> show the effective values
            for the selected radio and profile.</p>
            <h2>Waveform and receiver</h2>
            <p>All six profiles use raised-cosine shaping with root-raised-cosine
            (RRC) roll-off
            <code>beta=0.125</code> and genuine faster-than-Nyquist spacing
            <code>tau=0.90</code>. The receiver uses an 81-tap
            minimum-mean-square-error (MMSE) equalizer,
            two equalizer iterations and noise whitening. Only the 2K7 profile
            enables symbol-clock tracking; it is not a generic property of
            every bandwidth.</p>
            <p>A physical burst has a guard, two repeated 64-symbol BPSK
            preamble halves and 96 known training symbols. The payload is sent
            in physical blocks with 60 data symbols and four known pilot symbols.
            The receiver correlates the preamble, estimates carrier-frequency
            offset from the repeated halves, matched-filters the audio, solves
            the MMSE channel and uses pilots for per-block phase correction.
            The transmitter produces a real single-carrier audio waveform centred at the
            profile's resolved audio frequency.</p>
            <h2>Framing, MCS, FEC and ARQ</h2>
            <p>The robust bootstrap header identifies the framing and selected
            profile; the automatic policy uses QPSK for that bootstrap. A data
            frame carries a manifest with subblock sequence and length, encoded
            payload bytes and a CRC for each ARQ block. The receiver takes the
            FEC (forward error correction) profile from the header rather than
            guessing it. Interleaving, soft decoding and an automatic repeat
            request (ARQ) selective-repeat ACK bitmap let the sender
            retransmit missing blocks. When the same FEC family is retried,
            soft information can be combined as HARQ; a receipt is emitted only
            after the framed payload passes its checks.</p>
            <p>SC-FTN has MCS0 through MCS20. MCS (modulation and coding scheme)
            is an explicit modulation profile, not a linear speed scale: entries include BPSK, QPSK,
            QAM, APSK and GQAM families, so a larger index is not by itself a
            promise of proportionally higher delivery. For example, MCS0 is
            BPSK, MCS1 is QPSK and MCS17 is 64-GQAM. <b>FEC is selected
            separately from MCS.</b> LDPC (low-density parity-check) 1/2 carries
            more redundancy than LDPC 4/5; the trade-off is robustness versus
            coded efficiency, while
            modulation and FEC together determine the selected frame format.</p>
            <h3>How LDPC, CRC and ARQ cooperate</h3>
            <p>LDPC adds parity information described by a sparse set of checks.
            The receiver iteratively uses those checks and the confidence of
            received bits to recover damaged data. Rate 1/2 allocates roughly
            half the coded bits to information; rate 4/5 allocates roughly four
            fifths. More redundancy can help a weak link, but leaves less room
            for useful data. FEC does not guarantee recovery: CRC checks each
            recovered block, and ARQ requests blocks that still fail. Compatible
            repeated observations can contribute soft information to decoding
            rather than discarding every unsuccessful attempt.</p>
            <h2>Automatic per-profile policy</h2>
            <table width="100%">
              <tr><th>Path</th><th>Starting policy</th><th>ARQ / burst</th><th>Data-burst train limit</th></tr>
              <tr><td>2K7</td><td>MCS1, LDPC 1/2; adapt up to MCS17
              (64-GQAM)</td><td>256-byte blocks; 512–16,384-byte keyed
              bursts; rescue MCS0</td><td>14.5 s</td></tr>
              <tr><td>Other standard profiles</td><td>MCS17, LDPC 4/5; maximum
              MCS17</td><td>2,048-byte blocks; up to 16,384-byte bursts;
              rescue MCS1</td><td>18 s</td></tr>
              <tr><td>Guardian K5, non-2K7</td><td>MCS1, LDPC 1/2; adapt up to
              MCS3</td><td>256-byte blocks; 512–16,384-byte keyed bursts;
              rescue MCS0</td><td>7.5 s</td></tr>
            </table>
            <p>These are automatic starting points, ceilings, burst granularity
            and maximum duration of a train of data bursts for the current
            implementation, not throughput claims. After each burst, delivery
            feedback records complete versus missing blocks, retransmitted
            bytes, remote SNR (signal-to-noise ratio) and EVM (error-vector
            magnitude). A loss resets the clean streak, lowers
            the MCS and strengthens FEC immediately; when FEC is already at
            rate 1/2, the controller shortens the burst. Three clean bursts can
            promote one dimension at a time, alternating FEC and burst length,
            subject to measured SNR/EVM margins. The learned controller belongs
            to the peer path, so one weak direction does not rewrite every
            station's policy.</p>
            <p>2K7 also has a rapid acquisition path. A short robust transfer
            establishes initial evidence; clean delivery together with suitable
            SNR/EVM can allow larger groups of blocks and faster FEC promotion.
            Losses restore conservative adaptation and a recovery cooldown.
            SNR alone is not proof that a dense modulation will deliver reliably.</p>
            <p>A transient loss gets the configured same-profile retry; the 2K7
            and K5 rescue policy can then use the robust MCS0 path. The resolved
            settings summary, modem workspace and diagnostics show the selected
            geometry and default policy. During an active SC transfer, the
            transfer indicator shows current MCS, FEC, burst, ARQ, retries and
            available SNR/EVM telemetry.</p>
            <h2>Negotiation with VARA</h2>
            <p>SC-FTN is negotiated independently on each hop. Both peers must
            advertise the same valid SC profile token for a native SC payload.
            If the peer does not advertise SC capability, Guardian can use the
            configured VARA P2P fallback for that hop. If both sides advertise
            SC but their profiles are incompatible, negotiation fails rather
            than silently changing the profile. The control modem remains a
            separate AFSK 1200 or MFSK-16 transport; selecting SC-FTN does not
            change the control modem.</p>
            """,
            """
            <h2>Co je Guardian SC-FTN</h2>
            <p><b>Guardian SC-FTN</b> (single-carrier faster-than-Nyquist) je
            nativní jednonosný datový modem.
            Produkční rodina waveformů má šest volitelných zvukových profilů a
            pro payload nepoužívá implementaci FFT/OFDM; některé interní názvy
            kvůli kompatibilitě stále obsahují „OFDM“. Vzorkování zvukové karty
            je <b>48 000 vzorků/s</b>. Jde o časovou referenci zvuku, nikoli o
            příslib payloadu 48 kb/s.</p>
            <p>Obsluha zvolí štítek šířky SC-FTN. Guardian potom vypočítá fyzický
            profil a automatickou politiku linky. Aktuální efektivní výchozí
            geometrie je:</p>
            <table width="100%">
              <tr><th>Štítek</th><th>Šířka zvukového waveformu</th><th>Střed</th><th>Symbolová rychlost</th></tr>
              <tr><td>1K2</td><td>1 200 Hz</td><td>1 050 Hz</td><td>1 185,2</td></tr>
              <tr><td>2K7</td><td>≈2 858,8 Hz</td><td>1 779,4 Hz</td><td>2 823,5</td></tr>
              <tr><td>4K5</td><td>4 500 Hz</td><td>2 600 Hz</td><td>4 444,4</td></tr>
              <tr><td>5K</td><td>5 000 Hz</td><td>2 950 Hz</td><td>4 938,3</td></tr>
              <tr><td>10K</td><td>10 000 Hz</td><td>5 450 Hz</td><td>9 876,5</td></tr>
              <tr><td>20K</td><td>18 750 Hz</td><td>9 825 Hz</td><td>18 518,5</td></tr>
            </table>
            <p>Hodnoty popisují geometrii zvukového waveformu. Nejsou zárukou
            RF obsazené šířky: záleží také na filtru rádia, modulaci a odchylce
            FM. Nevybírají kmitočet ani neslibují šířku pracovního kanálu.
            Politika 2K7 záměrně používá Nyquistovu symbolovou rychlost asi
            2 541,2 a symbolovou rychlost asi 2 823,5, takže efektivní zvuková
            šířka je asi 2 858,8, nikoli jmenovitých 2 700. Efektivní hodnoty
            vybraného rádia a profilu ukazuje pracovní prostor SC-FTN a
            <b>Provoz → Diagnostika → Modem SC-FTN</b>.</p>
            <h2>Waveform a přijímač</h2>
            <p>Všech šest profilů používá tvarování raised-cosine s
            root-raised-cosine (RRC) roll-off
            <code>beta=0.125</code> a skutečným rychlejším než Nyquistovým
            rozestupem <code>tau=0.90</code>. Přijímač používá 81tapový
            minimum-mean-square-error (MMSE) ekvalizér, dvě iterace a whitening
            šumu. Pouze profil 2K7 zapíná
            sledování symbolových hodin; nejde o obecnou vlastnost všech šířek.</p>
            <p>Fyzický burst obsahuje guard, dvě opakované 64symbolové poloviny
            BPSK preambule a 96 známých tréninkových symbolů. Payload je v
            blocích po 60 datových a čtyřech známých pilotních symbolech.
            Přijímač preambuli koreluje, z opakovaných polovin odhadne posun
            nosné, zvuk filtruje, vyřeší kanál MMSE a piloty opraví fázi každého
            bloku. Vysílač vytváří reálný zvukový signál s jedinou nosnou,
            jejíž střední frekvenci určuje vypočtený zvukový profil.</p>
            <h2>Framing, MCS, FEC a ARQ</h2>
            <p>Robustní bootstrap hlavička identifikuje framing a profil; při
            automatické politice používá QPSK. Datový rámec obsahuje manifest s
            pořadím a délkou subbloků, zakódované bajty payloadu a CRC pro každý
            ARQ blok. Přijímač získává FEC (dopřednou korekci chyb) z hlavičky,
            nehádá jej. Prokládání, měkké dekódování a bitmapa selective-repeat
            ACK s automatickým opakováním požadavku (ARQ) dovolí znovu poslat
            chybějící bloky. Při opakování stejné rodiny FEC lze měkkou informaci
            spojit jako HARQ; potvrzení vznikne až po úspěšných kontrolách.</p>
            <p>SC-FTN má MCS0 až MCS20. MCS (modulation and coding scheme) je
            konkrétní modulační profil, ne lineární měřítko rychlosti: tabulka
            obsahuje rodiny BPSK, QPSK, QAM,
            APSK a GQAM, takže vyšší index sám o sobě neslibuje úměrně rychlejší
            doručení. MCS0 je například BPSK, MCS1 QPSK a MCS17 64-GQAM.
            <b>FEC se volí odděleně od MCS.</b> LDPC (low-density parity-check)
            1/2 nese více redundance než LDPC 4/5; jde o kompromis odolnosti a
            kódové účinnosti, zatímco společně modulace a FEC určují formát
            rámce.</p>
            <h3>Jak spolupracují LDPC, CRC a ARQ</h3>
            <p>LDPC přidává kontrolní informace popsané řídkou soustavou vazeb.
            Přijímač z nich a z míry jistoty přijatých bitů postupnými iteracemi
            opravuje poškozená data. Poměr 1/2 vyhrazuje přibližně polovinu
            zakódovaných bitů užitečné informaci, poměr 4/5 přibližně čtyři
            pětiny. Větší redundance může pomoci slabé lince, ale zbývá méně
            prostoru pro vlastní zprávu. FEC nezaručuje opravu každé chyby:
            obnovený blok ověří CRC a neúspěšné bloky si ARQ vyžádá znovu.
            Slučitelná opakovaná pozorování mohou do dekódování přidat měkkou
            informaci, místo aby se každý neúspěšný pokus celý zahodil.</p>
            <h2>Automatická politika podle profilu</h2>
            <table width="100%">
              <tr><th>Cesta</th><th>Výchozí politika</th><th>ARQ / burst</th><th>Limit série datových burstů</th></tr>
              <tr><td>2K7</td><td>MCS1, LDPC 1/2; adaptace až do MCS17
              (64-GQAM)</td><td>bloky 256 B; bursty 512–16 384 B;
              záchrana MCS0</td><td>14,5 s</td></tr>
              <tr><td>Ostatní standardní profily</td><td>MCS17, LDPC 4/5;
              maximum MCS17</td><td>bloky 2 048 B; bursty do 16 384 B;
              záchrana MCS1</td><td>18 s</td></tr>
              <tr><td>Guardian K5 mimo 2K7</td><td>MCS1, LDPC 1/2; adaptace
              až do MCS3</td><td>bloky 256 B; bursty 512–16 384 B;
              záchrana MCS0</td><td>7,5 s</td></tr>
            </table>
            <p>Jde o výchozí body, stropy, granularitu burstů a nejdelší trvání
            série datových burstů v aktuální implementaci, nikoli o tvrzení
            přenosové rychlosti. Po každém burstu se ze zpětné vazby zaznamenají
            úplné a chybějící bloky, opakované bajty, vzdálené SNR (poměr signálu
            k šumu) a EVM (vektorová chyba modulace). Ztráta
            vynuluje čistou sérii, sníží MCS a ihned posílí FEC; když je FEC už
            na poměru 1/2, zkrátí se burst. Tři čisté bursty mohou po jednom
            povýšit FEC nebo délku burstu, střídavě a jen při dostatečné rezervě
            SNR/EVM. Naučený řadič patří ke směru peeru, takže slabá strana
            nepřepíše politiku celé stanice.</p>
            <p>2K7 má také rychlý rozběh. Krátký odolný přenos nejprve ověří
            linku; bezchybné doručení společně s vhodným SNR/EVM může dovolit
            větší seskupení bloků a rychlejší přechod k účinnějšímu FEC. Chyby
            vracejí konzervativní adaptaci a prodlevu před dalším zvyšováním.
            Samotné vysoké SNR není důkaz, že hustší modulace doručí data spolehlivě.</p>
            <p>Po dočasné ztrátě proběhne nastavený pokus se stejným profilem;
            politika 2K7 a K5 pak může přejít na odolnou cestu MCS0. Souhrn
            nastavení, pracovní prostor modemu a diagnostika ukazují vybranou
            geometrii a výchozí politiku. Během aktivního SC přenosu ukazuje
            indikátor aktuální MCS, FEC, burst, ARQ, opakování a dostupnou
            telemetrii SNR/EVM.</p>
            <h2>Dohoda s VARA</h2>
            <p>SC-FTN se vyjednává na každém hopu zvlášť. Pro nativní SC payload
            musí oba peery ohlásit stejný platný token profilu SC. Když peer
            schopnost SC vůbec neohlásí, může Guardian pro tento hop použít
            nastavenou zálohu VARA P2P. Když obě strany SC ohlásí, ale profily
            jsou nekompatibilní, dohoda selže, místo aby se profil tiše změnil.
            Řídicí modem zůstává samostatným přenosem AFSK 1200 nebo MFSK-16;
            volba SC-FTN jej nemění.</p>
            """,
            "SC-FTN sc_ftn MCS MSC FEC LDPC ARQ GQAM QAM APSK BPSK QPSK "
            "waveform modem 48k sample tau beta RRC MMSE equalizer symbol clock "
            "bandwidth symbol rate occupied audio diagnostics burst framing CRC "
            "SC-FTN MCS FEC LDPC ARQ waveform modem vzorkování symbolová rychlost",
        ),
        _topic(
            "16. Auto Tune walkthrough",
            "16. Průvodce Auto Tune",
            """
            <h2>What Auto Tune measures</h2>
            <p><b>Auto Tune</b> measures one Guardian SC-FTN audio path with a
            consenting peer and saves a bounded station-calibration record. It
            calibrates the selected Guardian modem and digital transmit level;
            it does not calibrate VARA, choose a frequency or replace a radio
            alignment procedure. A quick result is saved automatically for the
            selected station/audio/PHY identity.</p>
            <h2>Open and enable it</h2>
            <ol>
              <li>Open <b>View → Auto Tune</b>. The menu item is always
              available, including while VARA is selected, so the page can
              explain why it is unavailable.</li>
              <li>In <b>Settings → Station settings → VARA &amp; payload</b>,
              select <b>Guardian SC-FTN</b> and choose the SC bandwidth. This
              selects the modem to calibrate; coordination still uses the
              separate control channel.</li>
              <li>Connect the radio and verify audio/PTT, then use the main
              <b>Start control</b> button. Make sure the
              peer is ready to receive and agree on the test before starting.</li>
              <li>Enter the peer callsign, then choose <b>Call and start</b>.
              Guardian shows a consent confirmation before it sends the
              request. The remote operator must choose <b>Accept</b> or
              <b>Refuse</b>; refusing or cancelling leaves no sweep running.</li>
            </ol>
            <p>If VARA is selected, the page remains readable but its Auto Tune
            controls are disabled with an inline explanation. Start, Accept,
            Refuse, Cancel and Report are all unavailable in that state. Choose
            SC-FTN first; Auto Tune never changes the payload selection for you.</p>
            <h2>Quick sweep and consent</h2>
            <p>A quick sweep tests 15 configured digital-amplitude levels with
            two repeats in each direction. The values span 0.001 to 1.8 and are
            <b>digital audio amplitudes</b>, not RF power, watts or a VARA volume
            setting. Guardian coordinates the outbound and return direction,
            keeps the run bounded and reports frame, SNR, EVM, peak and sync
            observations. The radio may key during an accepted sweep, so the
            peer's consent and the operator's normal frequency and licence
            checks remain required.</p>
            <p>While calibration is active, payload sessions, scanner activity,
            network-setting changes and another calibration are blocked by the
            operation guard. <b>Cancel</b> stops the active run and preserves
            its cancellation status/report. The peer's offer is also bounded:
            the remote operator must explicitly accept it before that station
            keys for the test.</p>
            <h2>Read the result</h2>
            <p>On successful Quick Tune, Guardian automatically saves the
            selected digital transmit volume for the station/audio/PHY identity.
            The report remains available with the peer, direction, selected
            bandwidth, recommended MCS, transmit scale and reason. Use the
            report view to inspect the per-level table and export JSON or CSV
            when the buttons are enabled. A full measurement asks you to review
            the report before applying a recommendation; Quick Tune's saved
            selected volume is the automatic part.</p>
            <p>Auto Tune does not enable beacons, alter discovery, change routes,
            select a working channel or promise a payload rate. If it cannot
            run, read the page hint and Activity: the usual causes are VARA
            still selected, no live control plane, no peer consent, a busy
            payload/session, or a radio/audio identity that is not ready.</p>
            """,
            """
            <h2>Co Auto Tune měří</h2>
            <p><b>Auto Tune</b> změří jednu zvukovou cestu Guardian SC-FTN se
            souhlasícím peerem a uloží omezený kalibrační záznam stanice. Kalibruje
            vybraný modem Guardianu a digitální vysílací úroveň; nekalibruje VARA,
            nevolí kmitočet ani nenahrazuje seřízení rádia. Výsledek Quick Tune
            se automaticky uloží pro vybranou identitu stanice, zvuku a PHY.</p>
            <h2>Otevření a zapnutí</h2>
            <ol>
              <li>Otevřete <b>Zobrazení → Auto Tune</b>. Položka menu je vždy
              dostupná, i když je zvolena VARA, aby stránka mohla vysvětlit,
              proč je funkce nedostupná.</li>
              <li>V <b>Nastavení → Nastavení stanice → VARA a přenos</b> vyberte
              <b>Guardian SC-FTN</b> a šířku SC. Tím určíte měřený modem;
              koordinace nadále používá samostatný řídicí kanál.</li>
              <li>Připojte rádio, ověřte zvuk a PTT a stiskněte hlavní tlačítko
              <b>Spustit řízení</b>. S protistanicí se předem domluvte, že je
              připravená test přijmout.</li>
              <li>Zadejte volací značku protistanice a zvolte <b>Zavolat a spustit</b>.
              Guardian před odesláním žádosti zobrazí potvrzení souhlasu. Vzdálený
              operátor musí zvolit <b>Přijmout</b> nebo <b>Odmítnout</b>; odmítnutí
              či zrušení sweep nespustí.</li>
            </ol>
            <p>Je-li zvolena VARA, stránka zůstane čitelná, ale ovládací prvky
            Auto Tune jsou s vysvětlením deaktivované. V tomto stavu jsou
            nedostupné Zavolat a spustit, Přijmout test, Odmítnout, Zrušit i
            Zobrazit report.
            Nejprve
            zvolte SC-FTN; Auto Tune za vás workflow payloadu nezmění.</p>
            <h2>Rychlý sweep a souhlas</h2>
            <p>Rychlý sweep vyzkouší 15 digitálních úrovní se dvěma opakováními
            v každém směru. Hodnoty 0,001 až 1,8 jsou <b>digitální amplitudy
            zvuku</b>, nikoli výkon RF, watty ani nastavení hlasitosti VARA.
            Guardian koordinuje směr ven i zpět, běh omezuje a hlásí pozorování
            rámce, SNR, EVM, špičky a synchronizace. Po přijetí může rádio během
            testu zaklíčovat, proto je stále nutný souhlas peeru a obvyklá
            kontrola kmitočtu a oprávnění.</p>
            <p>Během kalibrace ochrana operací zablokuje payload, scanner, změny
            síťového nastavení i druhou kalibraci. <b>Zrušit</b> zastaví aktivní
            běh a zachová stav i zprávu o zrušení. Nabídka peeru je také
            omezená: vzdálený operátor musí před zaklíčováním test výslovně
            přijmout.</p>
            <h2>Čtení výsledku</h2>
            <p>Po úspěšném Quick Tune Guardian automaticky uloží zvolenou
            digitální vysílací hlasitost pro identitu stanice, zvuku a PHY.
            Report zůstane dostupný s peerem, směrem, šířkou, doporučeným MCS,
            vysílacím měřítkem a důvodem. V zobrazení si prohlédněte tabulku
            úrovní a podle dostupnosti exportujte JSON nebo CSV. Úplné měření
            vyžaduje kontrolu reportu před použitím doporučení; automatickou částí
            Quick Tune je uložená zvolená hlasitost.</p>
            <p>Auto Tune nezapíná majáky, nemění discovery, trasy ani pracovní
            kanál a neslibuje rychlost payloadu. Když neběží, přečtěte nápovědu
            stránky a Aktivitu: obvyklou příčinou je zvolená VARA, chybějící živá
            řídicí rovina, chybějící souhlas peeru, obsazený payload/relace nebo
            nepřipravená identita rádia a zvuku.</p>
            """,
            "Auto Tune autotune station calibration SC-FTN MCS FEC LDPC ARQ "
            "digital amplitude TX scale RF power consent peer quick sweep report "
            "JSON CSV SNR EVM sync View Auto Tune Zobrazení kalibrace amplituda souhlas",
        ),
        _topic(
            "17. Updates and diagnostics",
            "17. Aktualizace, diagnostika a soukromí",
            """
            <h2>Updates and diagnostics</h2>
            <p>The update check reads a small HTTPS manifest from the trusted
            GitHub channel on a worker thread. It never installs silently.
            Download and launch require separate confirmations. A downloaded
            installer is renamed from <code>.part</code> only after SHA-256
            matches the manifest.</p>
            <p>Diagnostics shows version, platform, paths, configuration,
            snapshots, dependency states and bounded events. It intentionally
            excludes message bodies and attachments. The report still contains
            callsigns and local paths; review it before sharing. Open it at
            <b>Tools → Diagnostics → Connection diagnostics</b>. The adjacent
            <b>SC-FTN modem</b> workspace shows the selected profile's effective
            geometry and automatic policy.</p>
            """,
            """
            <h2>Aktualizace a diagnostika</h2>
            <p>Kontrola aktualizací načte ve worker vlákně malý HTTPS manifest
            z důvěryhodného kanálu GitHub. Nikdy neinstaluje tiše. Stažení a
            spuštění vyžadují dvě samostatná potvrzení. Stažený instalátor se
            přejmenuje z <code>.part</code> až po shodě SHA-256 s manifestem.</p>
            <p>Diagnostika zobrazuje verzi, platformu, cesty, konfiguraci,
            snapshoty, stav závislostí a omezenou historii událostí. Záměrně
            neobsahuje texty zpráv ani přílohy. Obsahuje však volací značky a
            místní cesty; před sdílením ji zkontrolujte. Otevřete ji v nabídce
            <b>Provoz → Diagnostika → Diagnostika připojení</b>. Sousední pracovní
            prostor <b>Modem SC-FTN</b> ukazuje efektivní geometrii a automatickou
            politiku zvoleného profilu.</p>
            """,
            "update sha diagnostics privacy aktualizace diagnostika soukromí",
        ),
        _topic(
            "18. Troubleshooting",
            "18. Řešení potíží",
            """
            <h2>Troubleshooting</h2>
            <h3>Radio does not connect</h3>
            <p>Check backend, model ID, COM port, baud rate and rigctld path.
            Close other applications holding the COM port. Use Diagnostics to
            inspect the latest error.</p>
            <h3>VARA does not connect</h3>
            <p>Start the matching VARA FM/HF application, confirm command/data
            ports and ensure local firewall rules allow loopback TCP.</p>
            <h3>No station is heard</h3>
            <p>Confirm the live control channel is active, the correct audio
            devices are selected at the Windows level, RX audio reaches the
            modem, both stations use the same control modem/frequency, and PTT
            timing is suitable.</p>
            <h3>Mail stays queued</h3>
            <p>This is expected while Control is off or no route/peer responds.
            Inspect Activity, Network routes and Heard stations. Do not repeatedly
            send on an occupied channel.</p>
            <h3>The transmission meter stops moving</h3>
            <p>Zero for the first few seconds is normal: VARA has the envelope
            but has not reported a drained byte yet. A meter that never moves
            at all usually means VARA is not keying — check that something owns
            PTT for it (either VARA's own COM port or <i>Let Guardian key the
            radio for VARA</i>, never both), and watch the PTT indicator. If
            Activity reports “no BUFFER telemetry”, VARA is transmitting but
            not reporting; Guardian then holds the link until the modem goes
            quiet instead of aborting a transfer that is actually working.</p>
            <h3>Checkboxes or panels look wrong after a theme change</h3>
            <p>Theme and language apply immediately on Save or Apply. If a
            dialog was already open when the theme changed, close and reopen
            it.</p>
            <h3>A route exists but nothing is sent</h3>
            <p>Usable discovered routes are applied automatically. Check that
            discovery is On, the route has not expired, the next hop is heard
            and the live control channel is idle. A one-way LINK_ADVERT row is
            not yet a confirmed live route. Check the selected payload and any
            VARA fallback, frequency and Activity messages. Failed mail needs
            a manual retry; recent automatic attempts also have a cooldown.
            Use <i>Save as manual route</i> to retain a route, but remember that
            saving it does not establish a live connection.</p>
            """,
            """
            <h2>Řešení potíží</h2>
            <h3>Rádio se nepřipojí</h3>
            <p>Zkontrolujte backend, ID modelu, port COM, rychlost a cestu k
            rigctld. Ukončete jiné aplikace, které drží port COM. Poslední chybu
            najdete v Diagnostice.</p>
            <h3>VARA se nepřipojí</h3>
            <p>Spusťte odpovídající VARA FM/HF, ověřte příkazový/datový port a
            povolení lokálního TCP ve firewallu.</p>
            <h3>Není slyšena žádná stanice</h3>
            <p>Ověřte aktivní živý řídicí kanál, správná zvuková zařízení ve
            Windows, přítomnost RX zvuku, shodný modem/frekvenci obou stanic a
            vhodné časování PTT.</p>
            <h3>Zpráva zůstává ve frontě</h3>
            <p>Je to očekávané při vypnutém Řízení nebo bez odpovědi trasy či
            protistanice. Zkontrolujte Aktivitu, trasy a Slyšené stanice.
            Neopakujte vysílání na obsazeném kanálu.</p>
            <h3>Ukazatel přenosu se nehýbe</h3>
            <p>Nula v prvních sekundách je normální: VARA obálku má, ale zatím
            nenahlásila jediný odvysílaný bajt. Ukazatel, který se nepohne
            vůbec, obvykle znamená, že VARA neklíčuje — ověřte, že PTT za ni
            někdo obsluhuje (buď vlastní port COM ve VARA, nebo <i>Klíčovat
            rádio pro VARA prostřednictvím Guardianu</i>, nikdy obojí), a
            sledujte indikaci PTT. Hlásí-li Aktivita „no BUFFER telemetry“,
            VARA vysílá, ale nehlásí to; Guardian pak drží spojení, dokud modem
            neztichne, místo aby přerušil přenos, který ve skutečnosti
            funguje.</p>
            <h3>Po změně motivu vypadá dialog jinak</h3>
            <p>Motiv i jazyk se použijí ihned po Uložit nebo Použít. Pokud byl
            některý dialog v tu chvíli otevřený, zavřete jej a otevřete
            znovu.</p>
            <h3>Trasa existuje, ale nic se neodesílá</h3>
            <p>Použitelné nalezené trasy se používají automaticky. Ověřte režim
            Zapnuto, platnost trasy, slyšitelnost další stanice a nečinný živý
            řídicí kanál. Jednosměrný řádek LINK_ADVERT ještě není potvrzenou
            živou trasou. Zkontrolujte vybraný modem i případnou zálohu VARA,
            frekvenci a zprávy v Aktivitě. Selhanou zprávu je nutné zopakovat
            ručně; mezi automatickými pokusy také platí prodleva. Volba
            <i>Uložit jako ruční trasu</i> cestu uchová, ale samotné uložení
            nenaváže spojení s protistanicí.</p>
            """,
            "troubleshooting error radio vara audio queued progress "
            "potíže chyba zvuk fronta ukazatel",
        ),
        _topic(
            "19. Glossary",
            "19. Slovníček pojmů",
            """
            <h2>Glossary</h2>
            <p><b>ARDOS</b> — Guardian's own control layer: the short frames
            that negotiate a transfer, carry alerts and announce presence. It
            is separate from VARA, which only moves the payload.</p>
            <p><b>Control channel</b> — the live AFSK 1200 or MFSK-16 audio
            transport carrying ARDOS frames. Nothing is heard or transmitted
            while it is stopped.</p>
            <p><b>Payload / envelope</b> — the message bundle VARA carries, in
            one framed block with a CRC. It is padded to a 256-byte floor so
            every transfer is a usable air frame.</p>
            <p><b>Next hop</b> — the station this one actually calls. It equals
            the destination for a direct contact and is an intermediate station
            otherwise.</p>
            <p><b>TTL</b> — how many further hops a frame may take. It bounds
            flooding; the cap is 8.</p>
            <p><b>RREQ / RREP</b> — the route request and route reply of
            multi-hop discovery. A learned route always expires.</p>
            <p><b>LINK_ADVERT</b> — the topology advertisement in which a station
            publishes its direct neighbours, letting a quiet net draw its own
            map.</p>
            <p><b>Topology</b> — an imported or wizard-built graph of station
            links, shared by the whole net; each PC derives its own routes from
            it.</p>
            <p><b>Calling vs working channel</b> — the channel a session is set
            up on, versus the optional separate channel the VARA payload moves
            on. Working channels require real CAT on both peers.</p>
            <p><b>QSY</b> — a change of frequency. Automatic QSY retunes before
            a VARA transfer and restores the previous frequency afterwards.</p>
            <p><b>Maidenhead locator</b> — the grid square identifying a
            position (for example JN99CS). Guardian accepts 2 to 10
            characters.</p>
            <p><b>BUFFER</b> — VARA's report of how many bytes are still queued
            for RF. It is what drives the transmission meter.</p>
            <p><b>rigctld</b> — the Hamlib TCP service Guardian talks to instead
            of implementing vendor CAT protocols itself.</p>
            <p><b>RECEIVED vs DELIVERED</b> — a relay confirms it forwarded the
            message; only the final station confirms delivery.</p>
            """,
            """
            <h2>Slovníček pojmů</h2>
            <p><b>ARDOS</b> — vlastní řídicí vrstva Guardianu: krátké rámce,
            které domlouvají přenos, nesou výstrahy a hlásí přítomnost. Je
            oddělená od VARA, která přenáší jen samotný obsah.</p>
            <p><b>Řídicí kanál</b> — živý zvukový přenos AFSK 1200 nebo MFSK-16
            s rámci ARDOS. Dokud je zastavený, nelze nic slyšet ani vysílat.</p>
            <p><b>Obálka (payload)</b> — balíček zprávy, který nese VARA, v
            jednom rámcovaném bloku s CRC. Doplňuje se na minimum 256 bajtů,
            aby byl každý přenos použitelným rádiovým rámcem.</p>
            <p><b>Další bod (next hop)</b> — stanice, kterou tato skutečně
            volá. Při přímém spojení je totožná s cílem, jinak jde o stanici
            mezilehlou.</p>
            <p><b>TTL</b> — kolik dalších hopů smí rámec ujít. Omezuje šíření;
            strop je 8.</p>
            <p><b>RREQ / RREP</b> — dotaz na trasu a odpověď při vícehopovém
            hledání. Naučená trasa vždy expiruje.</p>
            <p><b>LINK_ADVERT</b> — oznámení topologie, kterým stanice
            zveřejní své přímé sousedy, takže si tichá síť nakreslí vlastní
            mapu.</p>
            <p><b>Topologie</b> — importovaný nebo v průvodci sestavený graf
            linek sdílený celou sítí; každý počítač si z něj odvodí vlastní
            trasy.</p>
            <p><b>Volací a pracovní kanál</b> — kanál, na kterém se relace
            domluví, oproti volitelnému samostatnému kanálu, na kterém se
            přenáší obsah VARA. Pracovní kanály vyžadují skutečné CAT na obou
            stranách.</p>
            <p><b>QSY</b> — změna kmitočtu. Automatické QSY přeladí před
            přenosem VARA a po něm obnoví původní kmitočet.</p>
            <p><b>Maidenhead lokátor</b> — čtverec označující polohu (například
            JN99CS). Guardian přijímá 2 až 10 znaků.</p>
            <p><b>BUFFER</b> — hlášení VARA o tom, kolik bajtů ještě čeká na
            odvysílání. Právě z něj vychází ukazatel přenosu.</p>
            <p><b>rigctld</b> — služba Hamlibu nad TCP, se kterou Guardian
            komunikuje místo toho, aby sám implementoval protokoly CAT
            výrobců.</p>
            <p><b>RECEIVED a DELIVERED</b> — relay potvrzuje, že zprávu předal;
            doručení potvrzuje až koncová stanice.</p>
            """,
            "glossary terms ardos ttl rreq link advert qsy locator buffer "
            "slovníček pojmy zkratky",
        ),
    ]


class HelpDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("help.title"))
        self.setMinimumSize(980, 680)
        outer = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("help.search"))
        self.search.textChanged.connect(self._filter)
        outer.addWidget(self.search)
        body = QHBoxLayout()
        self.topics = QListWidget()
        self.topics.setMinimumWidth(280)
        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        body.addWidget(self.topics)
        body.addWidget(self.viewer, 1)
        outer.addLayout(body, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            tr("common.close")
        )
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self._all_topics = help_topics()
        self.topics.currentItemChanged.connect(self._show_topic)
        self._filter("")

    def _filter(self, query: str) -> None:
        def searchable(value: str) -> str:
            return "".join(
                char for char in unicodedata.normalize("NFKD", value.casefold())
                if not unicodedata.combining(char)
            )

        needles = searchable(query).split()
        self.topics.clear()
        for topic in self._all_topics:
            haystack = searchable(f"{topic.title} {topic.keywords} {topic.html}")
            if not all(needle in haystack for needle in needles):
                continue
            item = QListWidgetItem(topic.title)
            item.setData(Qt.ItemDataRole.UserRole, topic)
            self.topics.addItem(item)
        if self.topics.count():
            self.topics.setCurrentRow(0)
        else:
            self.viewer.clear()

    def _show_topic(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        topic = current.data(Qt.ItemDataRole.UserRole)
        self.viewer.setHtml(topic.html)
