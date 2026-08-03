# Guardian development backlog

_Aktualizováno: 2026-08-03_

Tento dokument je jediný pracovní seznam identifikovaných, ale nedokončených
věcí. Historické plány a release notes popisují stav v okamžiku vydání; pro
volbu další práce je rozhodující tento backlog.

## P0 — vydáno v 0.6.47, čeká na provozní ověření

1. **`RX bad frame: bad magic` — diagnostické zachycení vydáno.** Odmítnutý
   kandidát se nedoručí orchestrátoru a vedle WAV se uloží JSON s modemem, S/N,
   délkou, payloadem a důvodem. **Čeká na další on-air výskyt:** podle capture
   rozlišit zkrácený rámec, kolizi opakování/relay nebo timing hypotézu.
2. **Alert frequency sweep — softwarově zajištěn.** Sweep navštěvuje pouze
   kanály kompatibilní s aktivním AFSK/MFSK modemem a nekoliduje se scannerem.
   **Čeká na provozní ověření:** dvě kopie na HF, 45sekundový home wait a návrat
   frekvence/módu po chybě či přerušení.

## P1 — síťový provoz

1. **Volitelné oddělení calling/working kanálu — vydáno v 0.6.48, čeká na
   provozní ověření.** Výchozí jednokanálové chování zůstává beze změny a nová
   pole UI jsou skrytá do zapnutí v Chování sítě. Dvě CAT stanice před QSY
   ověří shodu pracovního kanálu, přeladí se až po `START_VARA` a před řídicím
   potvrzením se vrátí. Neshoda, No-CAT a starší protistanice nemohou vyvolat
   automatické QSY. **Ověřit na reálném rádiu:** shodu, odmítnutí neshody,
   payload na pracovním kanálu a návrat obou stran po úspěchu i chybě.
2. **Ověřit workflow z v0.6.46 v terénu.** Zaznamenat operátorský test mapových
   vazeb a bezpečného ručního No-CAT QSY na skutečném rádiu; automatické a
   softwarové testy jsou hotové.

## Vydáno v 0.6.53 — určení vlastního lokátoru

Tři cesty nastavení `station_grid` jsou v mapě pohromadě: výběr bodu, ruční
zadání a jednorázová detekce přes Windows Location Service po výslovném
kliknutí a consent dialogu Guardianu. Detekovaný fix se před uložením ukáže
spolu s hlášenou přesností a dočasným bodem v mapě; na disk se uloží pouze
Maidenhead lokátor a přesné souřadnice se zahodí. Rádiové rámce, beacon i
přepínač odesílání polohy zůstaly beze změny. Windows přístup zamítnutý na
release stroji je ověřený. **Provozně ověřeno 2026-08-03:** nainstalovaný
release 0.6.53 získal povolený živý fix a celý tok detekce, náhledu a přijetí
fungoval správně (potvrzeno operátorem). Privacy pravidla a odložený koncept
QR mostu jsou v
[`LOCATOR_DETECTION.md`](LOCATOR_DETECTION.md).

## P2 — rozšíření po rozhodnutí operátora

1. **Import sdílené mesh topologie.** Importovat odkazy místo lokálních tras,
   odvodit směrování z pohledu každé stanice, podporovat ceny/asymetrii a varovat
   před next-hopem, který stanice nikdy neslyšela. Návrh je v `MESH_ROUTING.md`.
2. **Dokončit význam protokolových příznaků šifrování a komprese.** Příznaky
   `ENCRYPTED` a `COMPRESSED` jsou rezervované, ale nejsou aplikované na obsah.
   Nejdřív určit kompatibilitu, správu klíčů a chování vůči starším verzím.
3. **Rozhodnout o VARA `COMPRESSION FILES`.** Změřit přínos pro běžné přílohy a
   ověřit, zda musí být nastavení shodné na obou stranách. Bez měření funkci
   nezapínat.
4. **Rozšířit validaci konfigurace.** First-run readiness a základní UI validace
   jsou hotové; doplnit centrální kontrolu typů, rozsahů a neplatných kombinací
   při načtení nebo importu konfigurace.

## Vydáno v 0.6.54 — rozšíření mapy M1–M3 a M6–M7

- **M1 — lokátorová mřížka:** uložená volba vypnuto / 4 / 6 znaků, výpočet jen
  pro viditelnou oblast a pevný limit buněk proti zahlcení při oddálení.
- **M2 — kružnice a měření:** geodetické kružnice 50/100/200 km kolem vlastního
  lokátoru a dočasné dvoubodové měření vzdálenosti a počátečního azimutu;
  pravé tlačítko nebo Esc měření smaže.
- **M3 — stavové barvy:** přímý dosah, cesta přes relay, nyní nedosažitelná a
  historická poloha mají pevné barvy a legendu. Výstražný puls a vybraná stanice
  zůstávají nad stavovou barvou.
- **M6 — offline oblast ČÚZK:** volba zoomů pro právě viditelnou oblast, náhled
  počtu/uložených dlaždic a velikosti, limit 750 dlaždic na úlohu a 512 MB na
  cache, omezená souběžnost, zrušení a zachování již dokončených dlaždic.
- **M7 — PNG:** export právě vykresleného plátna se značkami, cestami,
  překryvy, legendou, časem, verzí Guardianu a atribucí zdroje; export nic
  dodatečně nestahuje.

**M5 — stopa mobilní stanice je odložena a nyní se neimplementuje.** Před
pozdějším návratem je nutné rozhodnout limit bodů, maximální stáří, filtr proti
poskakování a zda má být historie pouze v paměti. Terminátor den/noc byl z
plánu odstraněn rozhodnutím operátora.

Zamítnuto (nezapadá do offline filozofie ARDOS): odhad pokrytí z výškového
modelu terénu — vyžaduje stovky MB dat SRTM nebo síť.

## P3 — release hardening a sledovaná rizika

1. **Authenticode podpis instalátoru.** Vývojové releasy jsou záměrně nepodepsané
   a chráněné SHA-256 + build provenance. Podpis doplnit před širší distribucí.
2. **Dokončit UI performance/DPI matici.** Dohledat zdroj jednorázového přibližně
   448ms UI stall a uzavřít kontrolu Light/Dark/System na podporovaných
   rozlišeních a 100/125/150/200 % scalingu.
3. **Vyhodnotit 12bitový station-hash prefix.** Kolize je vzácná a identita je
   fakticky `(source, msg_id)`; změnu řešit až při důkazu problému nebo při nové
   verzi wire protokolu.

## Potvrzeně dokončeno 2026-08-03

- **Vícekanálový scanner byl provozně ověřen na skutečném CAT rádiu:** ladění
  frekvence a módu, dwell/hold i návrat na domácí kanál fungují správně
  (potvrzeno operátorem).
- Současný scanner je tímto uzavřený, ale není dlouhodobým směrem dalšího
  vývoje. Později jej nahradí **sestavovač sítě**; význam, workflow a migrační
  postup doplní operátor v samostatném zadání.

## Potvrzeně dokončeno 2026-08-01

- Multi-hop relay byl provozně ověřen na reálném tříuzlovém spoji A→B→C;
  předání zprávy, snižování TTL a ochrana proti relay smyčce fungují.
- Relay kandidáti z `ROUTE_OFFER` se řadí podle přímého dosahu, přesného S/N
  konkrétní nabídky, čerstvosti a deterministicky podle volací značky; ruční a
  naučené trasy zůstávají nad dynamickým hledáním.
- Evidence a zobrazení odhadovaného S/N a kanálu u slyšených stanic fungují v
  reálném provozu.
- Čistá instalace na Windows bez Pythonu, upgrade-in-place a odinstalace byly
  otestované; uživatelská data zůstávají zachována podle volby operátora.
- Současná diagnostika, omezená živá historie a export diagnostického balíčku
  jsou pro provoz dostatečné. Perzistence session/event historie na disk není
  dalším rozvojovým cílem.
- First-run readiness, testovací sada a CI jsou zavedené.
- MFSK full-window preamble search, AFC a soft-decision Viterbi jsou
  implementované. Další PLL/transition tracking se bude řešit pouze tehdy,
  pokud provoz na fadingovém kanálu prokáže potřebu.
