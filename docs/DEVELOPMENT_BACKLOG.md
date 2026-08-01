# Guardian development backlog

_Aktualizováno: 2026-08-01_

Tento dokument je jediný pracovní seznam identifikovaných, ale nedokončených
věcí. Historické plány a release notes popisují stav v okamžiku vydání; pro
volbu další práce je rozhodující tento backlog.

## P0 — vydáno v 0.6.47, čeká na provozní ověření

1. **Vícekanálový scanner — software vydán.** Operations/runtime integrace,
   produkční UI, snapshoty, workerové CAT ladění, activity/S-meter hold,
   FM/HF kompatibilita a blokace proti relaci/payloadu/alertu jsou otestované.
   **Čeká na reálné rádio:** tune/mode, dwell, hold a návrat na domácí kanál.
2. **`RX bad frame: bad magic` — diagnostické zachycení vydáno.** Odmítnutý
   kandidát se nedoručí orchestrátoru a vedle WAV se uloží JSON s modemem, S/N,
   délkou, payloadem a důvodem. **Čeká na další on-air výskyt:** podle capture
   rozlišit zkrácený rámec, kolizi opakování/relay nebo timing hypotézu.
3. **Alert frequency sweep — softwarově zajištěn.** Sweep navštěvuje pouze
   kanály kompatibilní s aktivním AFSK/MFSK modemem a nekoliduje se scannerem.
   **Čeká na provozní ověření:** dvě kopie na HF, 45sekundový home wait a návrat
   frekvence/módu po chybě či přerušení.

## P1 — síťový provoz

1. **Ověřit multi-hop relay na reálném tříuzlovém spoji.** Řetězec A→B→C,
   TTL a loop avoidance jsou otestované deterministicky, ale chybí zaznamenaný
   tříuzlový on-air test.
2. **Oddělit calling a working frequency.** Současný auto-QSY předpokládá, že
   control burst a pracovní spoj sdílejí aktuální/domovský kanál protistanice.
   Navrhnout a implementovat samostatný volací a pracovní kanál.
3. **Použít kvalitu signálu pro řazení relay kandidátů.** Evidence a zobrazení
   S/N i kanálu fungují a jsou provozně ověřené. Otevřená je pouze routingová
   politika: zapojit S/N do hodnocení kandidátů místo samotné čerstvosti záznamu.
4. **Ověřit workflow z v0.6.46 v terénu.** Zaznamenat operátorský test mapových
   vazeb a bezpečného ručního No-CAT QSY na skutečném rádiu; automatické a
   softwarové testy jsou hotové.

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

## P3 — release hardening a sledovaná rizika

1. **Authenticode podpis instalátoru.** Vývojové releasy jsou záměrně nepodepsané
   a chráněné SHA-256 + build provenance. Podpis doplnit před širší distribucí.
2. **Dokončit UI performance/DPI matici.** Dohledat zdroj jednorázového přibližně
   448ms UI stall a uzavřít kontrolu Light/Dark/System na podporovaných
   rozlišeních a 100/125/150/200 % scalingu.
3. **Vyhodnotit 12bitový station-hash prefix.** Kolize je vzácná a identita je
   fakticky `(source, msg_id)`; změnu řešit až při důkazu problému nebo při nové
   verzi wire protokolu.

## Potvrzeně dokončeno 2026-08-01

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
