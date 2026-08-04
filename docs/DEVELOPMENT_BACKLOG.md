# Guardian development backlog

_Aktualizováno: 2026-08-04_

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
3. **Ověřit 0.6.58 discovery na rádiu.** Začít v režimu Pouze sledovat, potom
   na řetězci alespoň tří RF segmentů zkusit asistovaný RREQ/RREP, ruční
   schválení, předání payloadu a návrat `DELIVERED`. Následně odděleně zapnout
   automatické použití a LINK_ADVERT, ověřit bootstrap tiché sítě, obousměrné
   potvrzení, expiraci a zotavení po výpadku. Změřit skutečný airtime a případně
   upravit TTL, interval, jitter a rozpočet před produkčním zapnutím.

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

1. **Dokončit význam protokolových příznaků šifrování a komprese.** Příznaky
   `ENCRYPTED` a `COMPRESSED` jsou rezervované, ale nejsou aplikované na obsah.
   Nejdřív určit kompatibilitu, správu klíčů a chování vůči starším verzím.
2. **Rozhodnout o VARA `COMPRESSION FILES`.** Změřit přínos pro běžné přílohy a
   ověřit, zda musí být nastavení shodné na obou stranách. Bez měření funkci
   nezapínat.
3. **Rozšířit validaci konfigurace.** First-run readiness a základní UI validace
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

## Vydáno v 0.6.55 — sestavovač sítě a spolehlivý Transit

- **Jedna topologie pro celou síť:** nový průvodce na místě stránky scanneru
  importuje sdílené CSV nebo sestaví linky ručně. Linka nese směr, cenu,
  dostupnost, volací a volitelný pracovní kanál.
- **Lokální derivace:** Dijkstra s deterministickým pořadím ceny/hopů/cesty
  odvodí pro každou volací značku jiný next hop. Alternativní první hop se
  stane zálohou; ruční trasy se při přepočtu nepřepisují.
- **Pravdivý stav:** převzetí dalším relayem je `Forwarded`, nikoli
  `Delivered`. Koncové potvrzení se vrací směrovaně po reverzních hopech a může
  později stav povýšit na `Delivered` bez změny wire formátu.
- **Transit retry:** vypočtený next hop se uloží, po změně tras znovu odvodí,
  selhání zprávu ponechá v Transit a automatický retry má pětiminutovou ochranu
  proti opakovanému klíčování. Přenášený bundle nyní skutečně nese všechny
  průchozí hopy.
- **`ANY` fallback:** dokumentovaná záloha nově po selhání preferred hopu
  opravdu spustí jednoskokový `ROUTE_QUERY`.
- **Vícehopové discovery pouze návrh:** RREQ/RREP, deduplikace, airtime limity,
  metrika, důvěra a smíšené verze jsou rozpracované v
  `MULTIHOP_DISCOVERY.md`; rádiový protokol 0.6.55 se nemění.

## Vydáno v 0.6.57 — sledované a asistované vícehopové discovery

- Nové typy `MULTIHOP_RREQ/RREP` používají expanding-ring TTL, deduplikaci,
  jitter, reverse breadcrumbs, omezený opakovaný RREP a vysílací rozpočet.
- Volatilní dynamické trasy expirují, mají metriku hopů/kvality a jsou oddělené
  od ručních i topologických řádků. Zdroj je musí před payloadem schválit.
- Nová podzáložka **Automatická síť** obsahuje režimy Vypnuto / Pouze sledovat /
  Asistovaný, stav dotazů, tabulku tras, trust seznamy a ruční akce.
- RF graf testuje přesnou síť S6–N1–N2–N3–S1, větve, smyčku, ztráty, duplikáty,
  souběh a mezeru tvořenou starší verzí.

## Vydáno v 0.6.58 — experimentální kroky 9 a 10

- **Krok 9 — automatické použití discovery trasy:** samostatný výchozí vypnutý
  přepínač dovolí v Asistovaném režimu použít čerstvou RREQ/RREP trasu bez
  potvrzení. V monitoru se automatická schválení odeberou, ruční zůstanou a
  ruční/topologický override se nikdy nepřepíše.
- **Krok 10 — `LINK_ADVERT`:** nový typ 16 vyměňuje přímá pozorování s TTL,
  deduplikací, jitterem a společným airtime rozpočtem. Jednoskoková přítomnost
  probudí tichou síť; routovatelná je až oboustranně potvrzená vazba. Celý graf i
  odvozené trasy jsou volatilní, expirují a zůstávají oddělené od sestavovače.
- **UI:** Automatická síť má podzáložky Vyhledání trasy, Živá topologie a
  Nastavení a limity, nezávislé experimentální přepínače, interval, stav
  pozorování a ruční akce.
- **Ověření:** RF testy pokrývají samodetekci tiché sítě, S6–N1–N2–N3–S1,
  jednostranné vazby, smyčky, konečný flood, expiraci, přepínání feature flagů
  a automatický end-to-end `DELIVERED`.

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
- Scannerový backend zůstává kvůli kompatibilitě, ale jeho Network UI v 0.6.55
  nahradil **sestavovač sítě**. Odvozené linky/kanály jsou zdrojem pro routing a
  případné pozdější automatické plánování poslechu.

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
