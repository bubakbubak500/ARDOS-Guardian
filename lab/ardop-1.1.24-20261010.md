# Guardian 1.1.24 — ARDOP a následná kontrola SC-FTN

Datum: 10. 10. 2026. Produkční LAB používá skutečný `ShellRuntime`,
řídicí transport, vyjednání profilu a doručení do schránky. Soukromé konfigurace
a stav jednotlivých stanic jsou uloženy u běhu; uživatelská schránka se nemění.

## Rádiová cesta

| Stanice | Rádio | CAT | Audio |
|---|---|---|---|
| OK7PS | IC-705, 33003269 | COM5 / rigctld 14532 | USB Audio CODEC 3, MME |
| OK2IPW | IC-705, 13006172 | COM7 / rigctld 14533 | USB Audio CODEC 4, MME |

Kmitočet zůstává **144 600 000 Hz**. ARDOP používá USB a filtr 500 Hz;
následný SC-FTN používá FM a filtr 15 000 Hz. LAB kontroluje skutečné CAT
čtení módu i šířky filtru a při ukončení obnovuje původní nastavení.
ARDOP vysílací úroveň je 10 %; SC-FTN používá uloženou směrovou kalibraci
z ověřené verze 1.1.23 (`sc_ftn`, 2K7, TX scale 0.016).

## Nalezené a opravené chyby

1. Krátké ARDOP řídicí přijímače nikdy nedosáhly původní dvacetisekundové
   podmínky pro široké hledání frekvence. Zachycené skutečné řídicí rámce měly
   odchylku přibližně +34 Hz a −35 Hz. Čerstvý dekodér nyní hledá celý povolený
   rozsah ihned. Po opravě dekóduje původní záznamy v obou směrech bez
   dodatečného posouvání frekvence nebo normalizace.
2. Po ztrátě prvního DISC mohl příchozí IDLE vyvolat DataACK a přepsat bajty
   uloženého DISC. Opakování pak obsahovalo nesprávnou hlavičku a příjemce
   neuvolnil modem pro Guardian RECEIVED. Při odpojování nyní stav mění jen
   DISC/END; reset maže i počet pokusů. Nativní test se ztrátou DISC před
   opravou selhal, po opravě prochází, stejně jako test ztraceného END.
   Další živá diagnostika prokázala kolize DISC s opakovaným IDLE: původní
   odesílatel byl po GACK v roli IRS a začal vysílat mimo svůj odpovědní slot.
   Požadavek na odpojení nyní v této roli počká na DATA/IDLE protějšku a
   odpoví DISC. Toto čekání ověřuje nativní regresní test.
3. Časový limit nabídky profilu/pracovního kanálu začínal jejím zařazením do
   fronty. U ARDOP první opakování zasáhlo konec odpovědi. Nyní se čekání na
   odpověď počítá od dokončení TX; čekající TX má samostatný konečný limit.
   Pozastavení audiokanálu zachovává tyto nabídky i jejich callback a kontrolu
   platnosti.
4. Při zvoleném ARDOP jsou pozastaveny majáky a link adverty. Uložená
   preference zůstává zachována pro návrat do jiného režimu.
5. ARQ odpověď nově čeká před zapnutím PTT na dokončení audio guardu a PTT
   tailu protějšku (350 ms). Původních 150 ms po zapnutí PTT nestačilo a rádio
   mohlo ztratit začátek krátkého rámce. Test se skutečným nativním DISC
   neprošel při původním okamžitém klíčování a po opravě jej dekóduje celý.

Živé CAT čtení při TX i RX potvrdilo USB/500 Hz, split byl vypnutý. Pozorované
přepnutí do FM bylo obnovení původního nastavení po ukončení neúspěšné kampaně.

## Ověření kódu

- 159 testů: session, SC-FTN vyjednání, produkční LAB, dvě rádia,
  ruční majáky a Qt shell.
- 25 testů: skutečná nativní ARDOP DLL, ARQ a frekvenční odchylky.
- 8 testů: Hamlib.
- 12 testů: pozastavení/obnovení řídicího audiokanálu včetně nabídky profilu
  a skutečné nativní odpojení při modelované slepé době protějšího rádia.
- Nativní DLL znovu přeložena z opravených zdrojů; `git diff --check` bez chyb.

## Výsledky skutečných přenosů

| Režim | Směr | Příloha | Celkový čas | Výsledek |
|---|---|---:|---:|---|
| ARDOP USB/500 Hz | OK7PS → OK2IPW | 128 B | 105,766 s | PASS |
| ARDOP USB/500 Hz | OK2IPW → OK7PS | 128 B | 106,531 s | PASS |
| ARDOP USB/500 Hz | OK7PS → OK2IPW | 2048 B | 194,719 s | PASS |
| SC-FTN FM/2K7 | OK7PS → OK2IPW | 20 480 B | 38,000 s | PASS |
| SC-FTN FM/2K7 | OK2IPW → OK7PS | 20 480 B | 35,937 s | PASS |

Všechny přenosy mají `byte_exact=true`, `sender_delivered=true`, správně
vyjednaný transport a profil. Čas je celý přenos Guardian zprávy včetně
vyjednání a závěrečného potvrzení. Porovnání obsahu zahrnuje zdroj, cíl,
předmět, tělo, prioritu i název, délku a SHA-256 přílohy.

- ARDOP: `20261010-083924-301e15df`, 3/3 PASS, `completed`, `parity=verified`.
- SC-FTN: `20261010-084707-4ebb450c`, 2/2 PASS, `completed`, `parity=verified`.
- Obě kampaně použily stejné zdroje a nativní DLL verze 1.1.24:
  `2537073a350878d9c99c7ea4999e5170cde661e59259d65b471bae088ad2e335`.
  Identita byla po dokončení znovu porovnána s aktuálním pracovním stromem.
- Nezávislá živá CAT kontrola během ARDOP potvrdila USB/500 Hz na obou rádiích
  jak při zapnutém, tak při vypnutém PTT (`usb-live-readback.json`).
- Závěrečná kontrola (`hardware-after.json`) potvrdila na obou rádiích
  144 600 000 Hz, FM, 15 000 Hz a PTT vypnuté. Vysílací výkon odpovídá
  počátečnímu čtení; vlastní LAB/rigctld procesy byly ukončeny.

Ověření proběhlo v pracovním Python běhu s přeloženou nativní DLL;
nejde o zkoušku zabaleného instalátoru. Režim jedné stanice se dvěma rádii
nebyl touto dvojicí samostatných LAB stanic provozně ověřován.

Podrobné plány, události, identity sestavení a souhrny:
`output/ardop-live-20261010/`.
