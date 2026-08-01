# Guardian — analýza a plán modernizace UI, instalace a aktualizací

> Tento dokument je historický implementační plán. Modernizace byla dokončena;
> aktuální otevřené práce jsou vedené v `docs/DEVELOPMENT_BACKLOG.md`.

Datum analýzy: 2026-07-23

## 1. Rozsah a pevné hranice

První etapa modernizuje styl, informační architekturu, instalaci, aktualizace
a technické oddělení UI od běhových služeb.

V této etapě se nemění funkční chování:

- `guardian/payload/vara_p2p.py`
- `guardian/payload/winlink_manual.py`
- řídicí protokol, routing a session state-machine
- modulace AFSK/MFSK a formát zpráv

Změny těchto částí jsou možné až později jako samostatně schválené úkoly a po
charakterizačních testech.

## 2. Výchozí stav Guardianu

### Architektura

Doménová část je už poměrně dobře rozdělena do balíčků `message`, `modem`,
`payload`, `protocol`, `radio`, `routing`, `session` a `vara`.

Slabým místem je prezentační vrstva:

- `guardian/ui/main_window.py` má 2 331 řádků;
- jedna třída současně vytváří widgety, ukládá nastavení, řídí hardware,
  spouští instalaci, obsluhuje mailbox, tickuje síť a formátuje data;
- UI používá vnořené `CTkTabview` — provozní záložky a uvnitř nich dalších
  sedm záložek nastavení;
- neexistuje samostatný design systém ani znovupoužitelné prezentační
  komponenty;
- projekt nemá automatické testy ani release CI;
- dokumentace stále místy popisuje již odstraněnou simulaci a neodpovídá
  aktuálnímu UI;
- verze je pevně zapsaná jako `0.1.0`, chybí jednotný release metadata source.

### Aktuální instalační cesta

- vývojové `setup.ps1` vyžaduje předem nainstalovaný Python;
- PyInstaller umí vytvořit `dist/Guardian/Guardian.exe`, ale není nad ním
  skutečný uživatelský instalátor;
- `Guardian.spec` obsahuje absolutní cesty z jiného počítače;
- chybí odinstalace, upgrade-in-place, Start menu registrace, release
  manifest, kontrolní součty a automatický GitHub release workflow;
- Hamlib lze stáhnout v aplikaci a ověřit SHA-256, ale první spuštění není
  jednotný průvodce všemi potřebnými nástroji;
- VARA FM/HF se systematicky nedetekují a uživateli se nenabízí řízená náprava.

## 3. Srovnání s Modeling Anten

Sousední projekt Antenna Pattern Lab je vhodný jako referenční implementace:

- PySide6 / Qt Widgets a nativní aplikační menu;
- sémantické theme tokeny a samostatný design systém;
- kompaktní technický vzhled bez nadbytečných karet;
- jeden dominantní provozní krok, ostatní akce v menu a dialozích;
- oddělené dialogy pro nastavení, vzhled, externí nástroje, diagnostiku,
  aktualizace a nápovědu;
- PyInstaller one-folder build zabalený Inno Setupem;
- per-user instalace bez administrátorských práv;
- upgrade zachovávající uživatelská data;
- kontrola externích závislostí;
- GitHub Actions release z tagu;
- HTTPS release manifest, SHA-256 kontrola instalátoru a explicitní souhlas
  před jeho spuštěním;
- automatická kontrola aktualizací běží mimo UI thread;
- vizuální validační matice pro světlé/tmavé téma, rozlišení a jazyky.

Guardian nemá kopírovat konkrétní analytické panely Modeling Anten, ale jeho
pravidla hierarchie, menu, tokenů, instalace a release procesu.

## 4. Doporučená cílová technologie

### Doporučení: PySide6 / Qt Widgets

Nový Guardian shell má být postaven v PySide6. Důvody:

- nejbližší shoda s Modeling Anten včetně menu a theme systému;
- kvalitnější nativní formuláře, tabulky, splittery, dialogy, focus a high-DPI;
- signály, workery a model/view usnadní bezpečné oddělení hardware operací od
  UI threadu;
- stabilnější základ pro větší mailbox, seznam relací, log a diagnostiku;
- stejný build/installer/release vzor lze sdílet mezi oběma projekty.

Nevýhodou je větší distribuční balíček. Pro uživatele je ale důležitější
jednokroková instalace a bezchybný běh bez Pythonu než velikost downloadu.

Migrace nesmí být big-bang přepis. Staré UI zůstane dočasně spustitelné, nový
shell se bude doplňovat po obrazovkách nad stejnými doménovými objekty.

## 5. Cílová informační architektura

### Hlavní menu

```text
Soubor
  Nová zpráva
  Import / export (až bude definováno)
  Ukončit

Provoz
  Připojit / odpojit rádio
  Spustit / zastavit řídicí kanál
  Připojit / odpojit VARA (jen VARA P2P)
  Skenování kanálů

Zobrazení
  Domů
  Pošta
  Síť
  Provozní log
  Obnovit výchozí rozložení

Nastavení
  Stanice a identita
  Rádio a PTT
  VARA a zvuk
  Síť, routing a mesh
  Vzhled a jazyk
  Externí nástroje
  Aktualizace

Nápověda
  První spuštění / kontrola systému
  Diagnostika
  Uživatelská příručka
  O aplikaci
```

### Hlavní pracovní plocha

#### Domů

- kompaktní provozní kontext: callsign, režim, rádio, pásmo/frekvence;
- jediná dominantní akce podle stavu;
- stavový pás Radio / Control channel / VARA / PTT;
- stručná připravenost systému a jedna konkrétní náprava problému;
- mailbox counters a poslední důležitá událost;
- úroveň RX a šum pouze když je řídicí kanál aktivní.

Setup checklist se nezobrazuje trvale po dokončení prvního spuštění. Vrátí se
přes menu jako průvodce nebo při chybějící povinné závislosti.

#### Pošta

- levý seznam složek;
- seznam zpráv;
- detail vybrané zprávy;
- jedna jasná akce „Nová zpráva“;
- odpověď, přílohy, odstranění a předání jsou kontextové akce;
- žádné demo tlačítko v produkčním režimu.

#### Síť

- aktivní a poslední relace se stavem a časem;
- dosažitelné stanice a vybraná trasa;
- provozní monitor je sbalitelný detail, nikoli hlavní obsah;
- ruční „control burst“ editor sem nepatří.

#### Log

- filtrovatelný provozní log;
- úrovně Info / Warning / Error / Debug;
- kopírování a export diagnostického balíčku;
- technické detaily mohou být skryté v běžném režimu.

### Nastavení

Nastavení budou dialog s levou navigací, ne vnořená hlavní záložka.

Logické celky:

1. Stanice — callsign, operátor, výchozí TTL.
2. Rádio — model, backend, COM, CAT, rigctld, PTT.
3. VARA a zvuk — FM/HF, instance/cesty, TCP porty, audio vstup/výstup.
4. Síť — payload režim, control modem, auto-route, auto-relay, beacon.
5. Routing a kanály — trasy, frekvence, režimy, channel plan a auto-QSY.
6. Vzhled — téma, jazyk, hustota rozložení.
7. Externí nástroje — Hamlib, VARA FM, VARA HF a stav jejich instalace.
8. Pokročilé — pouze skutečně podporované bezpečné volby.

Formuláře budou mít validaci po poli, jednotky, rozsahy, popis chyby a tlačítka
Použít / Zrušit. Uložení nesmí při jedné chybě tiše použít nulu.

## 6. Co odstranit nebo přesunout

### Odstranit z produkčního UI

- `Bench test (bypass control net)`;
- `Force SEND over VARA`;
- `Force RECEIVE (LISTEN)`;
- `Simulate receive (demo)`;
- `Compose control burst`;
- `Build burst`;
- `Build + decode (self-test)`;
- testovací Hamlib Dummy z běžného výběru rádia.

Funkce potřebné pro automatické testy mohou zůstat v kódu bez viditelného UI,
nebo se přesunout do samostatného vývojového nástroje, který nebude součástí
produkčního menu.

### Přesunout do Diagnostiky

- Test PTT (s výrazným bezpečnostním potvrzením a automatickým timeoutem);
- seznam USB/COM zařízení a odkazy na ovladače;
- stav a test TCP portů rigctld/VARA;
- surový control-channel monitor;
- cesty k datům, logům a externím programům;
- export diagnostického balíčku.

## 7. Pomalé UI — pracovní diagnóza

Bez měření nelze označit jednu definitivní příčinu, ale kód ukazuje několik
konkrétních rizik:

1. `GuardianApp` obsluhuje příliš mnoho odpovědností v jednom event loopu.
2. `_net_loop()` běží každých 250 ms a vedle state-machine spouští scanner,
   beacon, auto-delivery a periodické překreslení relací a stanic.
3. `_poll()` každé dvě sekundy konfiguruje mnoho widgetů i při nezměněném
   stavu.
4. některé radio operace (`rigctld.ensure`, `radio.open`, PTT, QSY a scanner
   tuning) mohou běžet přímo na UI threadu;
5. některá volání `log()` vznikají z background threadů, přestože Tk widgety
   nejsou thread-safe;
6. vyhledávání Hamlib rádia ničí a znovu vytváří až 300 tlačítek při každém
   stisku klávesy;
7. logovací textbox roste bez limitu;
8. chybí debounce, dirty-state model a cílené aktualizace pouze změněných
   hodnot;
9. neexistují výkonnostní testy ani měření délky UI callbacků.

### Nápravný model

- veškeré síťové, diskové a hardware operace do workerů;
- komunikace s UI pouze přes Qt signály;
- UI dostává immutable snapshot stavu, ne přímé hardware objekty;
- jeden centrální store/controller publikuje jen změny;
- polling nahradit událostmi, kde je to možné;
- zbytek pollingu zpomalit a koaleskovat;
- virtualizované seznamy/model-view namísto stovek widgetů;
- log držet v omezeném bufferu a starší záznamy zapisovat na disk;
- validaci textových polí debounce 150–300 ms, drahé akce až po potvrzení;
- měřit callbacky delší než 16/50/100 ms;
- cílově žádný hardware nebo síťový callback na UI threadu.

## 8. Instalace bez Pythonu

Produkční uživatel nesmí instalovat Python ani spouštět `pip`.

Navržený řetězec:

1. projekt používá jedno verzovací metadata místo v `pyproject.toml`;
2. PyInstaller vytvoří one-folder distribuci s Python interpreterem,
   PySide6 a všemi knihovnami;
3. Inno Setup vytvoří moderní x64 per-user instalátor;
4. výchozí cesta bude `%LOCALAPPDATA%\Programs\Guardian`;
5. instalátor vytvoří Start menu, volitelně plochu a korektní odinstalaci;
6. upgrade-in-place zachová `%APPDATA%\Guardian`;
7. instalátor i aplikace mají stabilní AppUserModelID;
8. aplikace při prvním spuštění otevře průvodce připraveností.

Python se nebude za běhu stahovat. Stahování Pythonu by přidalo další bod
selhání, vyžadovalo síť a vystavilo uživatele volbě verzí a PATH. Interpreter
a Python knihovny jsou součástí podepsatelného a hashovaného artefaktu.

## 9. Hamlib a VARA FM/HF

Průvodce připraveností vytvoří pro každý nástroj stav:

- Nalezeno a kompatibilní;
- Nalezeno, ale neověřeno / nepodporovaná verze;
- Nenalezeno;
- Běží a port odpovídá;
- Port je obsazen jiným procesem;
- Instalace vyžaduje zásah uživatele.

### Hamlib

- hledat v PATH, běžných instalačních cestách a Guardian-managed adresáři;
- ověřit, že existuje `rigctld` a lze získat jeho verzi;
- nabídnout stažení pouze z oficiálního release kanálu;
- kontrolovat HTTPS host, velikost a SHA-256;
- instalaci provést až po explicitním souhlasu;
- po instalaci znovu automaticky provést detekci.

### VARA FM a VARA HF

- detekovat obě varianty samostatně, protože uživatel může mít jen jednu;
- najít executable, uložit cestu a ověřit očekávané command/data porty;
- nezaměňovat „soubor nalezen“ za „VARA je spuštěná a dostupná“;
- nabídnout oficiální download nebo otevření oficiální stránky až po ověření
  licenčních a redistribučních podmínek;
- bez stabilního oficiálního hashe neoznačovat automaticky stažený soubor za
  ověřený;
- nebalit VARA do Guardian instalátoru bez výslovného práva k redistribuci.

## 10. Aktualizace a release kanál

Guardian převezme princip Modeling Anten:

- aplikace po startu na background workeru načte malý HTTPS manifest;
- manifest obsahuje `version`, `installer_url`, `sha256` a `notes_url`;
- kontrola má krátký timeout a nikdy neblokuje start;
- nový instalátor se stáhne atomicky do `.part`, ověří SHA-256 a teprve potom
  nabídne spuštění;
- aplikace sama nic tiše nespouští ani neinstaluje;
- uživatel může kontrolu vyvolat ručně v menu;
- selhání internetu je neblokující stav.

GitHub Actions workflow po tagu `vMAJOR.MINOR.PATCH`:

1. ověří shodu tagu a verze;
2. spustí úplnou testovací sadu;
3. sestaví PyInstaller distribuci;
4. sestaví Inno Setup instalátor;
5. ověří stav podpisu;
6. vytvoří ZIP, manifest a `SHA256SUMS.txt`;
7. publikuje GitHub Release a build provenance.

Do budoucna se zachová stejný název assetů a URL manifestu, aby bylo možné
doplnit Authenticode podpis bez změny update kanálu.

## 11. Etapový plán

### Etapa 0 — baseline a ochrana funkčnosti

- opravit dokumentaci tak, aby popisovala aktuální stav bez simulace;
- zavést `pyproject.toml` a jednotnou verzi;
- přidat testovací infrastrukturu a CI;
- charakterizační testy configu, mailboxu, routing tabulky, session
  state-machine a obou payload backendů;
- zachytit screenshoty současného Guardian UI v typických stavech;
- přidat měření UI callbacků a reprodukovat zadrhávání při psaní.

Výstup: měřitelný baseline, nikoli změna chování.

### Etapa 1 — distribuční základ

- opravit přenositelný PyInstaller spec;
- přidat ikony a metadata aplikace;
- vytvořit Inno Setup instalátor;
- ověřit čistou instalaci bez Pythonu, upgrade a odinstalaci;
- zavést release workflow, checksums a manifest.

Výstup: jeden instalační soubor pro běžného uživatele.

Stav: dokončeno. Čistá instalace na Windows bez Pythonu, upgrade-in-place a
odinstalace byly provozně ověřené a potvrzené 2026-08-01.

### Etapa 2 — aplikační služby a bezpečné workery

- oddělit `GuardianApp` od doménových objektů pomocí controller/service vrstvy;
- definovat snapshoty Radio, VARA, Mailbox, Network a Dependency;
- všechny blocking operace přesunout mimo UI thread;
- zavést thread-safe log/event bus;
- přidat omezený log buffer a strukturované úrovně.

Výstup: stávající funkce mají čisté rozhraní použitelné starým i novým UI.

Stav: dokončeno jako kompatibilní mezivrstva; podrobnosti a ověření jsou v
`docs/STAGE_2_REPORT.md`.

### Etapa 3 — nový PySide6 shell a design systém

- zavést sémantické tokeny odvozené z Modeling Anten;
- vytvořit nativní menu;
- vytvořit shell, operační header, status strip, dominantní akci a theme
  controller;
- podporovat Light, Dark a System;
- minimum 1180×720, ověřit 1366×768 a 1920×1080;
- ověřit Windows scaling 125 %, 150 % a 200 %.

Výstup: nový Home bez změny rádiové/síťové logiky.

Stav: dokončeno; nový PySide6 Home je výchozí a původní provozní konzole
zůstává dostupná beze změny. Podrobnosti jsou v `docs/STAGE_3_REPORT.md`.

### Etapa 4 — nastavení a první spuštění

- nový Settings dialog podle logických celků;
- validace, jednotky, chyby a Apply/Cancel;
- průvodce připraveností;
- detekce Hamlib, VARA FM a VARA HF;
- řízené stažení nebo otevření oficiálního zdroje;
- samostatná Diagnostika.

Výstup: nový uživatel se dostane od instalace k připravenému programu bez
Pythonu a bez ručního hledání technických cest.

### Etapa 5 — Pošta, Síť a Log

- převést mailbox na splitter/list/detail;
- převést session a heard stations na Qt model/view;
- oddělit routing editor od živého síťového přehledu;
- odstranit produkční demo/bench/self-test ovládání;
- převést log a export diagnostiky.

Výstup: úplná funkční parita nového UI pro podporované běžné scénáře.

### Etapa 6 — aktualizace a release

- update dialog;
- neblokující startovní kontrola;
- ověřené stažení instalátoru;
- release notes;
- end-to-end test release artefaktů na čistém Windows profilu.

Výstup: bezpečný přechod mezi verzemi přes GitHub Releases.

### Etapa 7 — odstranění starého UI

- porovnat funkční paritu podle checklistu;
- provést hardware smoke test bez změny protokolů;
- odebrat CustomTkinter až po schválení nového UI;
- odstranit osiřelé závislosti a vývojové obrazovky;
- aktualizovat uživatelskou příručku.

## 12. Akceptační kritéria celé modernizace

- instalace na čistém Windows profilu bez Pythonu;
- první spuštění vede uživatele ke zprovoznění Hamlib a zvolené VARA varianty;
- žádný network/hardware/disk download neblokuje UI;
- souvislé psaní do polí bez viditelného zasekávání;
- funkční chování VARA P2P a Winlink backendu je proti baseline beze změny;
- normální menu neobsahuje bench, demo ani protocol self-test;
- nastavení jsou rozdělena podle úkolů, ne podle interních modulů;
- automatické aktualizace pouze informují; stažení a spuštění vyžaduje souhlas;
- instalátor a update jsou kontrolovány SHA-256;
- build a release jsou reprodukovatelné v CI;
- Light/Dark/System jsou vizuálně ověřeny na podporovaných rozlišeních a DPI;
- kritický stav není sdělen pouze barvou;
- data a konfigurace přežijí upgrade i odinstalaci, pokud uživatel výslovně
  nepožádá o jejich odstranění.

## 13. Další práce

Etapový plán modernizace je dokončený. Další práci vybírat podle priorit v
`docs/DEVELOPMENT_BACKLOG.md`.
