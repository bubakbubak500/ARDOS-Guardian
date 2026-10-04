# Guardian LAB: měření nad aktuální produkční aplikací

LAB je součástí Guardianu 1. Spouští dvě samostatné instance produkčního
`ShellRuntime` a jeho `Operations` ze stejného EXE nebo stejného zdrojového stromu. Každá používá
vlastní proces, konfiguraci, schránku, zvukové cesty a ovladač rádia. Neobsahuje
vlastní modem, řídicí rámce, tabulky MCS, FEC, časování ani algoritmus adaptace.
Starý LAB G2 se nepoužívá a není závislostí.

## Co přesně se měří

Plán vytvoří běžnou zprávu v produkční schránce a zavolá
`Operations.send_queued()`. Následuje skutečná příprava balíčku, případná
komprese, produkční řídicí kanál, vyjednání modemu, přenos a závěrečné
potvrzení. LAB nepřenáší data mezi stanicemi pomocí svého API. API zadává
práci a čte stav; zpráva cestuje přes zvolené zvukové a RF cesty.

PASS vyžaduje současně:

- Odesílatel má trvalý stav `DELIVERED`.
- Příjemce má odpovídající zprávu a shodnou značku zdroje, adresáta, předmět,
  text, prioritu, názvy a SHA-256 všech příloh.
- Produkčně vyjednaný datový transport odpovídá požadovanému modemu.
- Oba procesy potvrdily shodný otisk jádra a runtime před měřením i při ukončení.
- U VARA patří oba TCP porty konkrétnímu procesu z ověřené kopie EXE.

`application_bps` je velikost původního obsahu krát osm dělená časem od
odeslání zprávy po zpracování konečného potvrzení v produkčním kódu. Zahrnuje
přípravu balíčku, řízení relace, DATA i potvrzení. Čas se bere z monotónních
hodin; dokončení není odvozené od obnovování webového UI.

`payload_phase_bps` používá skutečný počet bajtů přenosového balíčku a produkční
začátek/konec datové fáze. Zahrnuje práci datového backendu a jeho režii;
nejde o čistou RF rychlost. Když příslušné časové údaje backend neposkytne,
zůstane hodnota prázdná. Selhání a timeouty nemají úspěšnou datovou rychlost.
Průběžný stav PTT v UI je vzorkovaný, nikoli přesný integrál doby vysílání.

Výchozí příloha má deterministický binární obsah se stejným seedem pro všechny
modemy, směry a opakování. Jde o běžnou přílohu, proto používá i normální
produkční ZIP obálku. Lze zadat vlastní `payload_path`; porovnání obsahu je
nadále přesné. Volba ztrátové optimalizace vlastního obrazového souboru tedy
nemůže získat PASS za bajtově shodný přenos.

## Spuštění a uživatelské rozhraní

V Guardianu: **Nástroje → Produkční LAB (dvě stanice)**. Otevře se místní
webové UI. Je možné spustit také `Guardian.exe --lab serve`. UI a API sdílejí
jediný řadič kampaně; uživatel vidí i běhy zadané automatizací.

1. Importujte dva běžné profily `config.json` z Guardianu, případně vyplňte
   pole jednotlivých stanic. Podporován je Icom/Icom, AIOC/AIOC i Icom/AIOC.
   AIOC je rozhraní zvuku/PTT; ovládání rádia se volí nezávisle.
2. Stanice potřebují odlišné volací značky, COM porty a zvukové cesty.
   Pro Hamlib použijte dva volné místní porty rigctld. Běžný Guardian a jiné
   aplikace musí před během uvolnit tato rádia a zvukové cesty.
3. Vyberte série SC-FTN, VARA FM, VARA HF nebo produkční ARDOP 500 Hz.
   Pásmo SC se vybírá z aktuálního registru profilů. VARA přebírá svůj režim
   z produkční konfigurace a vlastního INI; její šířka se SC volbou nemění.
4. Zvolte velikost přílohy, směry, počet opakování, limit a pauzu mezi přenosy.
   `sequence` zachovává stanice během jedné série; `cold` vytváří nové před
   každým přenosem. Každý směr má vlastní produkční historii adaptace.
5. Zkontrolujte plán a spusťte skutečný přenos. Kontrola plánu sama nevysílá.
   Tlačítko Zastavit zruší kampaň a zavře její vlastní procesy.

Úplný profil JSON zpřístupňuje všechny současné volby Guardianu včetně
kalibrace, komprese a PTT. Parametry automatického SC se řeší v produkčním
backendu; LAB jim nevytváří vlastní náhrady. Výchozí konfigurace bez fyzických
zařízení záměrně není spustitelná.

Volitelné pole série `channel` má například hodnotu
`{"frequency_hz": 144525000, "mode": "FM"}`. CAT rádio se nastaví produkčním
ovladačem a výsledek se přečte zpět; při ukončení se obnoví původní kanál.
U AIOC bez CAT musí odpovídat ručně nastavený `manual_frequency_hz`.
Záznam takovou frekvenci označuje jako neověřenou rádiem.

## Dvě licence VARA a přesná revize

Každá stanice vybere vlastní adresář VARA obsahující INI s příslušnou licencí
a správnými zvukovými zařízeními. EXE musí být bajtově shodné s programem,
který pro daný režim vybírá běžný Guardian z importovaného profilu. Také obě
strany musí používat stejný otisk VARA EXE.

LAB vytvoří soukromou pracovní kopii každého adresáře a ponechá v ní vlastní
licenci. V kopii změní pouze TCP porty, vypne KISS a kontrolu aktualizací.
Zvuk, úroveň a modemový režim přebírá z původního profilu. Při Guardian PTT
vyžaduje profil VARA s PTT Via VOX (`Via=3`), aby VARA neotevírala CAT/COM.
Jména zvukových zařízení VARA musí přesně odpovídat zařízením vyřešeným
produkčním Guardianem. Nejednoznačné nastavení se odmítne.

Licenční kód se nepřenáší do UI, protokolu ani exportu. LAB ověří přítomnost
licence pro značku, nikoli její platnost u výrobce: rozhraní VARA ji
spolehlivě nedokládá. Aktivované licence proto musí být připravené ve zdrojových
profilech. Nastavení a získané rychlosti jsou součástí důkazů.

Před startem nesmí být vybrané porty obsazené. Po spuštění se pomocí Windows
TCP tabulky ověří, že command/data port vlastní právě spuštěný proces.
Kontrola se opakuje před a po každém přenosu. LAB neukončuje cizí instance
VARA. Selhání, změna binárky nebo ztráta vlastnictví portů ukončí měření.

## Automatizace

Zdrojová varianta: `python -m guardian.lab ...`; distribuce:
`Guardian.exe --lab ...`. Příkaz `serve --no-browser` spustí službu bez otevření UI.
Soubor `%APPDATA%\Guardian\lab\connection.json` obsahuje URL a místní bearer
token. Alternativní adresář lze zvolit přes `--root`.

```powershell
Guardian.exe --lab template --output plan.json
Guardian.exe --lab validate --plan plan.json --output checked.json
Guardian.exe --lab run --plan plan.json --arm --output started.json
Guardian.exe --lab status --output status.json
Guardian.exe --lab stop
Guardian.exe --lab shutdown
Guardian.exe --lab export --run-id 20261004-120000-1234abcd --output evidence.zip
Guardian.exe --lab self-test --output lab-parity.json
```

| API | Význam |
| --- | --- |
| GET `/api/template` | Výchozí plán z aktuálního StationConfig a registru SC |
| GET `/api/devices` | Produkční seznam zvukových zařízení |
| POST `/api/validate` | Kontrola plánu, bez rádia |
| POST `/api/run` | `{"plan": ..., "arm": true}`; vrátí ID běhu |
| GET `/api/status` | Stanice, průběh, výsledky a stav shody |
| GET `/api/events?after=N` | Číslované události, kurzor a příznak zkrácené historie |
| POST `/api/stop` | Zrušení aktivního běhu |
| GET `/api/runs` | Dokončené i neúspěšné uložené kampaně |
| GET `/api/export/ID` | ZIP dokončeného běhu |
| POST `/api/shutdown` | Zastavení kampaně i služby |

Všechny API požadavky vyžadují `Authorization: Bearer TOKEN`. POST používá JSON.
Služba poslouchá jen na `127.0.0.1`; nepovoluje cizí webové origins ani CORS.
Zavření záložky prohlížeče běžící kampaň nezastaví; použijte **Zastavit** nebo
**Ukončit LAB**. Po změně zdrojových souborů je nutný restart služby, aby
nemohla kombinovat starý načtený řadič s novým jádrem.
Nejde o vzdálené RF API vystavené do sítě. MCP klient může používat stejné
API, vlastní MCP server není pro automatizaci nutný.

## Důkazy, reprodukovatelnost a údržba

Adresář kampaně obsahuje plán, sestavení, skutečné profily po produkčním
načtení, identitu zařízení, události, výsledky JSON/CSV a závěrečný stav.
Export má kontrolní součty a záměrně zahrnuje pouze tyto důkazy. Soukromé
VARA INI, EXE, schránky a obsah příloh zůstávají v podadresáři `private`.
Plány i výsledky mohou obsahovat značky, názvy zařízení a místní cesty.

Identita zdrojového běhu zahrnuje obsah všech Python/HTML souborů Guardianu,
nativní ARDOP, Python a verze závislostí. Zmrazené vydání zahrnuje EXE,
DLL/PYD/PYZ knihovny, UI a manifest zdrojové revize vložený při sestavení.
Při změně souborů během běhu se výsledek neoznačí jako platné měření.
Kryptografický otisk dokládá shodu softwaru; nezaručuje stejnou RF cestu,
nastavení rádia mimo CAT ani zatížení hostitele. Tyto podmínky patří do poznámek
a hardwarové kvalifikace.

Při běžné úpravě DSP, protokolu nebo adaptace není co kopírovat do LABu:
nové vydání jej automaticky spustí nad novým jádrem. Pokud se změní rozhraní
`Operations`, aktualizuje se adaptér `guardian.lab.station` a jeho testy.
Schéma plánu je verzované; neznámé schéma nebo odstraněné konfigurační pole
se odmítne, dokud není přidána výslovná migrace. Nepodporované volby se
nesmějí tiše zahodit. Staré výsledky zůstávají přiřazené původní revizi.

Vydávací workflow provádí softwarové testy a také z vytvořeného EXE spouští
dvě izolované stanice, porovná jejich identitu a načte produkční backendy
a řídicí modemy. Tento self-test nikdy neotevírá audio ani rádio a neměří
propustnost. Skutečná kvalifikace dvou rádií, obou licencí VARA a všech tří
modemů vyžaduje dostupnou RF sestavu. Vzorkované živé statistiky a protokol
nejsou náhradou souvislého záznamu IQ/PCM; ten tato verze automaticky nepořizuje.

Moduly: `model` (plán), `identity` (shoda), `station` (produkční adaptér),
`vara` (vlastněné externí procesy), `runner` (série a důkazy), `server`/`cli`
(automatizace) a `dashboard.html` (UI). Pro novou měřenou vlastnost se nejprve
vytvoří produkční diagnostické rozhraní a LAB jej pouze čte. Nový modem se
připojuje přes běžný produkční výběr backendu a příslušné integrační testy.
