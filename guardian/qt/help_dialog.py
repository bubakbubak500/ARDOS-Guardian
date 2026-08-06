"""Structured bilingual operator guide."""

from __future__ import annotations

from dataclasses import dataclass

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
              callsign. Choose the radio control method and the active VARA
              flavor.</li>
              <li>Open <b>Operation → Station readiness</b>. Guardian detects
              Hamlib/rigctld, VARA FM and VARA HF without transmitting.</li>
              <li>Connect the radio and VARA separately. A green status means
              the local control connection is available; it does not mean that
              an RF link to another station exists.</li>
              <li>Start the live control channel only when the radio, audio
              interface, frequency and licence conditions are ready.</li>
              <li>Compose mail into Outbox. Transmission occurs only after an
              operator selects the queued message and sends it while the live
              control channel is active.</li>
            </ol>
            <p><b>Safety:</b> Guardian never starts the audio transport or keys
            PTT merely because the application was opened. Confirm frequency,
            power, antenna and local regulations before enabling RF.</p>
            """,
            """
            <h2>První spuštění a bezpečný postup</h2>
            <ol>
              <li>Otevřete <b>Nastavení → Nastavení stanice</b> a zadejte
              volací značku. Zvolte způsob řízení rádia a používanou variantu
              VARA.</li>
              <li>Otevřete <b>Provoz → Připravenost stanice</b>. Guardian bez
              vysílání zkontroluje Hamlib/rigctld, VARA FM a VARA HF.</li>
              <li>Samostatně připojte rádio a VARA. Zelený stav znamená, že je
              dostupné místní řídicí spojení; neznamená navázané rádiové
              spojení s jinou stanicí.</li>
              <li>Živý řídicí kanál spusťte až po kontrole rádia, zvukového
              rozhraní, frekvence a podmínek oprávnění.</li>
              <li>Zprávu nejprve zařaďte do složky K odeslání. Přenos začne až
              po výběru zprávy a příkazu k odeslání při aktivním živém kanálu.</li>
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
            <p>Panel Aktivita obsahuje omezenou historii provozních událostí.
            Dotazy na hardware i stahování běží mimo UI vlákno, takže psaní a
            navigace zůstávají plynulé.</p>
            """,
            "home status counters activity domů stav počítadla aktivita",
        ),
        _topic(
            "3. Station settings",
            "3. Nastavení stanice",
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
            <p><b>VARA & payload</b> stores separate FM and HF command/data
            ports and executable paths. VARA P2P lets Guardian transfer the
            bundle. Manual Winlink hand-off pauses at an operator confirmation.</p>
            <p><b>Network behavior</b> controls TTL, route discovery, relay,
            queued delivery, automatic QSY and beacons. Enable relay or beacons
            only when you understand their on-air effect.</p>
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
            <p><b>VARA a přenos</b> uchovává oddělené příkazové/datové porty a
            cesty k programům pro FM a HF. VARA P2P přenáší balíček přímo.
            Ruční Winlink se zastaví na potvrzení operátora.</p>
            <p><b>Chování sítě</b> nastavuje TTL, hledání tras, předávání,
            doručování z fronty, automatické QSY a majáky. Relay či majáky
            zapněte jen tehdy, když rozumíte jejich dopadu do vysílání.</p>
            <p><b>Vzhled</b> mění motiv a jazyk ihned po Uložit nebo Použít.
            Nastavení se ukládá do profilu uživatele, nikoli do instalačního
            adresáře.</p>
            """,
            "settings callsign hamlib vox vara ttl language nastavení značka jazyk",
        ),
        _topic(
            "4. Hamlib, radio and PTT",
            "4. Hamlib, rádio a PTT",
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
            <p>VARA host PTT lets Guardian act on VARA's PTT ON/OFF notices.
            Use it only when VARA itself is configured not to own the same COM
            port. Never test PTT into an unsuitable load or occupied channel.</p>
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
            ON/OFF. Použijte jej jen tehdy, když VARA sama neovládá stejný port
            COM. PTT nikdy netestujte do nevhodné zátěže ani na obsazeném kanálu.</p>
            """,
            "hamlib rigctld radio cat civ ptt rádio",
        ),
        _topic(
            "5. VARA FM/HF and payload modes",
            "5. VARA FM/HF a způsoby přenosu",
            """
            <h2>VARA and payload modes</h2>
            <p>Guardian connects to VARA's local command and data TCP ports.
            FM and HF settings are remembered separately. Connect VARA verifies
            only the local TCP endpoint; the remote link is created later by
            the ARDOS session.</p>
            <p><b>VARA P2P</b> is self-contained: after HAVE_MSG, ACK_HAVE and
            START_VARA, Guardian asks VARA to connect to the next hop and sends
            the compressed message bundle. <b>Manual Winlink</b> releases the
            shared resources and asks the operator to complete and confirm the
            Winlink transfer. Do not confirm before the external transfer has
            actually finished.</p>
            <p>The control modem is independent from VARA: AFSK 1200 is used
            for FM and MFSK-16 for HF when automatic selection is enabled.</p>
            """,
            """
            <h2>VARA a způsoby přenosu</h2>
            <p>Guardian se připojuje k místním příkazovým a datovým TCP portům
            VARA. Nastavení FM a HF se pamatují odděleně. Připojení VARA ověří
            pouze místní TCP bod; vzdálené spojení vznikne až během relace ARDOS.</p>
            <p><b>VARA P2P</b> je samostatný postup: po HAVE_MSG, ACK_HAVE a
            START_VARA Guardian požádá VARA o spojení s dalším bodem a odešle
            komprimovaný balíček zprávy. <b>Ruční Winlink</b> uvolní sdílené
            prostředky a vyzve operátora k dokončení a potvrzení přenosu ve
            Winlinku. Nepotvrzujte jej před skutečným dokončením externího přenosu.</p>
            <p>Řídicí modem je nezávislý na VARA: při automatické volbě se pro
            FM používá AFSK 1200 a pro HF MFSK-16.</p>
            """,
            "vara fm hf p2p winlink payload afsk mfsk přenos",
        ),
        _topic(
            "6. Mail and standardized templates",
            "6. Pošta a normované šablony",
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
            "7. Routes, heard stations and sessions",
            "7. Trasy, slyšené stanice a relace",
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
            ignores every multi-hop frame. Assisted answers a query about this
            station, may look for a route you ask for, and normally pauses an
            originating message until you approve the route it found. Learned
            routes expire and never overwrite manual or Topology rows. The
            receive-only Monitor position of earlier releases is gone: it could
            neither answer a query nor produce a usable route, so a profile
            holding it is read as Assisted after an upgrade.</p>
            <p>Find route uses expanding TTL rings and displays the query and
            returned path. It is disabled, with the reason on the page, unless
            the control channel is running and the mode is Assisted. A station
            heard directly is listed as a one-hop route so the operator can see
            at a glance what is reachable now. Clearing dynamic routes does not
            touch the route table or builder.</p>
            <p>The bounds live in Settings → Network behavior: forwarding for
            other stations, maximum TTL (capped at 8), route lifetime, the
            frames-per-minute airtime budget and the allow/deny lists.
            Forwarding works only together with Relay messages for other
            stations, so a node never advertises a payload path it refuses to
            serve.</p>
            <p>Automatic use lets a fresh RREQ/RREP route carry a message
            without approval; it does nothing while discovery is off. The last
            page holds the one remaining experiment: LINK_ADVERT periodically
            exchanges direct observations and regenerates a volatile graph. An
            empty one-hop presence advert bootstraps quiet neighbours; only
            observations confirmed independently from both sides become
            routable. Advert flooding uses the same TTL, relay permission, trust
            lists and frame budget. Turning it off removes its effect
            immediately.</p>
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
            Vypnuto ignoruje všechny vícehopové rámce. Asistovaný odpovídá na
            dotaz po této stanici, smí hledat trasu, o kterou požádáte, a
            odchozí zprávu běžně pozdrží, dokud nalezenou trasu neschválíte.
            Naučené trasy expirují a nepřepisují ruční ani topologické řádky.
            Poloha Pouze sledovat z dřívějších verzí je zrušená: neumožňovala
            odpovědět na dotaz ani získat použitelnou trasu, takže se profil,
            který ji má uložený, po aktualizaci čte jako Asistovaný.</p>
            <p>Najít trasu používá rozšiřované kruhy TTL a zobrazuje dotaz i
            vrácenou cestu. Dokud neběží řídicí kanál a není zvolen Asistovaný
            režim, je tlačítko nedostupné a důvod je vypsaný na stránce. Přímo
            slyšená stanice je uvedená jako jednoskoková trasa, takže je hned
            vidět, co je právě dosažitelné. Vymazání dynamických tras nemění
            tabulku tras ani sestavovač.</p>
            <p>Limity jsou v Nastavení → Chování sítě: předávání za jiné
            stanice, maximální TTL (strop 8), životnost trasy, vysílací rozpočet
            v rámcích za minutu a seznamy povolených a zakázaných stanic.
            Předávání funguje jen současně s povoleným předáváním zpráv, takže
            uzel nenabízí payloadovou cestu, kterou odmítá obsloužit.</p>
            <p>Automatické použití dovolí čerstvé trase RREQ/RREP přenést zprávu
            bez schválení; při vypnutém hledání nedělá nic. Poslední stránka
            drží jediný zbývající experiment: LINK_ADVERT pravidelně vyměňuje
            přímá pozorování a regeneruje volatilní graf. Prázdný jednoskokový
            advert přítomnosti probudí i dosud tiché sousedy; routovatelná jsou
            pouze pozorování nezávisle potvrzená z obou stran. Flood advertů
            používá stejné TTL, povolení relaye, trust seznamy a rozpočet rámců.
            Vypnutí okamžitě odstraní jeho účinek.</p>
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
            "8. Station map and own position",
            "8. Mapa stanic a vlastní poloha",
            """
            <h2>Station map and own position</h2>
            <p>The station map remains useful offline: heard locators, relay
            paths, alert origins and the graticule do not depend on raster
            tiles. Click a heard row to centre it or double-click to compose.</p>
            <p><b>My position</b> offers three equal methods. <b>Detect from
            this PC</b> asks for explicit consent, requests one Windows fix,
            and previews its Maidenhead locator, reported source and accuracy.
            Exact coordinates are held only for that preview and are never
            saved. Press <b>Use locator</b> to accept or <b>Discard</b> to keep
            the previous value. A result worse than 1 km is marked approximate.
            Windows location permission can be changed through the link shown
            after a denial.</p>
            <p><b>Pick on map</b> arms a crosshair for exactly one click.
            <b>Locator</b> accepts a known 2, 4, 6, 8 or 10 character Maidenhead
            square. Both methods work without Windows location or internet.</p>
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
            <p><b>Moje poloha</b> nabízí tři rovnocenné možnosti. <b>Zjistit z
            tohoto PC</b> vyžádá výslovný souhlas, jednorázově požádá Windows a
            ukáže náhled Maidenhead lokátoru, hlášený zdroj a přesnost. Přesné
            souřadnice existují jen po dobu náhledu a nikdy se neukládají.
            Volbou <b>Použít lokátor</b> výsledek přijmete, volbou
            <b>Zahodit</b> zachováte předchozí hodnotu. Výsledek horší než 1 km
            je označen jako orientační. Po zamítnutí lze odkazem otevřít
            nastavení polohy Windows.</p>
            <p><b>Vybrat v mapě</b> zapne křížový kurzor právě pro jedno
            klepnutí. Pole <b>Lokátor</b> přijímá známý Maidenhead čtverec o 2,
            4, 6, 8 nebo 10 znacích. Obě cesty fungují bez polohy Windows i bez
            internetu.</p>
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
            "9. Dependencies and first-run readiness",
            "9. Závislosti a připravenost",
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
            for the current workflow.</p>
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
            použitou v aktuálním postupu.</p>
            """,
            "dependencies readiness hamlib vara python závislosti připravenost",
        ),
        _topic(
            "10. Updates, diagnostics and privacy",
            "10. Aktualizace, diagnostika a soukromí",
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
            callsigns and local paths; review it before sharing.</p>
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
            místní cesty; před sdílením ji zkontrolujte.</p>
            """,
            "update sha diagnostics privacy aktualizace diagnostika soukromí",
        ),
        _topic(
            "11. Troubleshooting",
            "11. Řešení potíží",
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
            """,
            "troubleshooting error radio vara audio queued potíže chyba zvuk",
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
        needle = query.strip().lower()
        self.topics.clear()
        for topic in self._all_topics:
            haystack = f"{topic.title} {topic.keywords} {topic.html}".lower()
            if needle and needle not in haystack:
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
