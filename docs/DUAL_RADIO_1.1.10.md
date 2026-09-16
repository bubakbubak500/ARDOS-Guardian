# Guardian 1.1.10: two radios

Development branch: `feature/1.1.10-dual-radio`, based on G1 `v1.1.9`.
Local development only; no tag, push, or merge to main until requested.

Required outcome:
- Optional second radio with the complete radio/audio/modem configuration.
- Independent VARA command/data endpoints and Hamlib connections.
- Live CAT frequency for controllable radios; explicit manual frequency for AIOC/no-CAT.
- Shared mailbox, automatic outgoing radio selection and forwarding across radios.
- Heard stations identify the receiving radio and frequency, including a station heard on both.
- Independent connection/control state, safe settings changes and shutdown.
- Regression and integration tests, local branch commit, Windows installer.

## Nastavení a provoz

1. V Nastavení otevřete **Dvě rádia** a zapněte **Zapnout dvě rádia**.
2. Původní záložky nastavují rádio 1. V části Dvě rádia jsou stejné záložky
   řízení rádia, zvuku a VARA pro rádio 2. Značka, pošta a pravidla sítě jsou společné.
3. Každému rádiu přiřaďte jeho zvukový vstup/výstup a vlastní CAT/PTT port.
   Dvě rádia Hamlib potřebují také dva porty rigctld (výchozí 4532 a 4534).
4. Spusťte dvě instance VARA a v každé nastavte její TCP porty. V Guardianu
   musí odpovídat porty i režim FM/HF. Rádio 2 nabízí výchozí 8400/8401;
   rádio 1 zachovává své nastavení, obvykle 8300/8301. Lze zvolit vlastní
   program VARA FM/HF pro každé rádio. Guardian nastavení samotné VARA nemění.
5. Hamlib a podporované UART rádio poskytují frekvenci přes svůj ovladač.
   Pro AIOC/VOX, Hamlib Dummy nebo rádio bez CAT vyplňte skutečnou frekvenci
   na displeji rádia. Pole pro druhé rádio je také přímo v hlavním okně.
6. V hlavním okně připojte obě rádia, obě VARA a spusťte oba řídicí kanály.
   Připojení a řídicí kanál druhého rádia mají vlastní tlačítka.

Při odeslání stačí zadat cílovou značku. Guardian vybírá rádio podle aktuálně
slyšeného cíle, dosažitelného dalšího uzlu nebo nastavené frekvence trasy.
Poštu přijatou na jednom rádiu může automaticky odeslat druhé rádio.
Každé má vlastní protokolové relace, zvuk, PTT a VARA; dvě různé zprávy mohou
probíhat současně. Stejnou zprávu smí odesílat jen jedno rádio.

Slyšené stanice obsahují sloupec **Rádio**. Stanice slyšená na obou rádiích
má dva záznamy s jejich vlastní frekvencí a časem. Informace z jednoho pásma
se nevydává za přímý příjem na druhém pásmu.

Příchozí rádio, předchozí uzel a zbývající TTL se ukládají do místních dat
pošty. Potvrzení doručení se vrací přes příchozí rádio; při jeho odpojení
čeká ve frontě i přes restart aplikace. Zprávy s vyčerpaným TTL se dále
nepředávají. Odpojená VARA ponechá odchozí zprávu ve frontě.

Změny nastavení čekají na dokončení aktivních relací obou rádií. Změna
hardwarového profilu rádia 2 jeho připojení zastaví; po změně jej znovu
připojte příslušnými tlačítky. Pouhá změna společné identity nebo pravidel
neodpojuje nezměněný hardware. Vypnutí režimu dvou rádií ponechá nastavení
rádia 2 uložené pro příští zapnutí.

## Ověření

- `tests/test_dual_radio.py`: dvě oddělená simulovaná pásma, skutečné poštovní
  balíčky, předávání a potvrzení v obou směrech, automatická volba rádia,
  dvě současné relace, TTL a restart, vyhledání brány přes RREQ/RREP,
  skutečné lokální TCP páry VARA s oddělením dat a PTT, dva samostatné odečty
  frekvence, konfigurace a životní cyklus druhého rádia.
- Qt náhledy českého rozhraní a kontrola malého pracovního prostoru.
- Kompletní regresní sada přes `build.ps1`: **820 testů prošlo**.
  Následná kontrola poslední úpravy zápisu nastavení: **48 testů prošlo**.
- Windows aplikace sestavena přes PyInstaller; zabalené testy
  `--qt-self-test` a `--compression-self-test` skončily `PASS`.
- Instalátor vytvořen přes `build_installer.ps1` (Inno Setup).
- Náhledy a protokoly zabalených testů jsou lokálně v
  `artifacts/dual-radio-1.1.10/`; protokol sestavení je
  `.build-temp/build-1.1.10.log`.

Testy rádia a přenosu jsou simulované; skutečná RF zkouška se dvěma rádii
a dvěma nativními modemy VARA zatím nebyla provedena.

Lokální instalátor: `release/Guardian-1.1.10-setup-win-x64.exe`.
Nevytváří se tag ani veřejný release; zveřejněný manifest zůstává beze změny.
