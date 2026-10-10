# Guardian 1.1.24 — dvě rádia a oprava ARDOP

Pracovní verze navazuje na 1.1.23. ARDOP a následná kontrola SC-FTN byly
10. 10. 2026 ověřeny přes dvě skutečná rádia IC-705 v produkčním LABu.

- Ruční maják se zařadí na oba zapnuté rádiové kanály, pokud jsou jednotlivě
  připravené k vysílání. Automatický maják zůstává plánovaný pro každý kanál.
- Ruční i pravidelné oznámení sousedů používá společný seznam stanic slyšených
  přes obě rádia a vysílá jej každým dostupným směrem.
- Nastavení, registrace a připojení ARDOS CZ jsou spolu v samostatné kartě
  Nastavení. Stav serveru je poslední indikátor spodní lišty; zelená znamená
  ověřené online spojení.
- Zvolený SC-FTN se ve spodní liště zobrazuje jako připravený i ve chvíli,
  kdy právě neprobíhá přenos.
- ARDOP nyní hledá frekvenční odchylku při prvním přijatém řídicím rámci.
  Opraveno odpojení ARQ, zachování rámce DISC při opakování a vysílací slot
  odpovědi. Před zapnutím PTT se čeká na uvolnění protějšího rádia.
- Opakování nabídky profilu a pracovního kanálu čeká na dokončení vysílání
  předchozí nabídky. Pozastavení audiokanálu zachovává čekající nabídky.
- Při zvoleném ARDOP jsou majáky a link adverty pozastavené; uložené nastavení
  se zachovává pro ostatní režimy. Trasy z advertů zůstávají platné do své
  běžné expirace, i při restartu audiokanálu; nová oznámení mají navazující ID.
- Při omezeném rozpočtu advertů se střídá začátek seznamu sousedů, aby se
  postupně oznámili všichni sousedé slyšení přes obě rádia.
- ARDOP má vlastní stav připraveno/aktivní, zobrazení skutečného CAT módu
  a průběh přenosu. Připravenost ani počty nepřebírá z VARA.
- Nastavení ARDOP nevyžaduje nepoužívaný host, porty a program VARA.
  Kontroly VARA zůstávají pro SC-FTN, který jej může použít jako náhradní cestu.
- Odesílatel ARDOP uvádí velikost obálky, takže LAB správně počítá přenesené
  bajty a rychlost datové fáze.
- LAB umí nastavit a přečíst šířku CAT filtru a po ukončení obnoví původní mód
  i šířku filtru.

## Ověření na rádiích

Na stejných 144,600 MHz prošly tři ARDOP zprávy v USB/500 Hz (128 B v obou
směrech a 2 KiB příloha) a dvě SC-FTN zprávy ve FM (20 KiB v obou směrech).
LAB ověřil obsah po bajtech, potvrzení doručení a shodnou identitu kódu/DLL
1.1.24 pro obě kampaně. Obě rádia zůstala ve FM se stejným kmitočtem a PTT
vypnutým.

Podrobnosti: `lab/ardop-1.1.24-20261010.md`. Změny majáků a link advertů v
režimu jedné stanice se dvěma rádii jsou ověřené cílenými testy; samostatná
provozní zkouška tohoto režimu zůstává na další ladění.

Po následných opravách, před níže uvedeným refaktoringem, prošly další dvě
ARDOP zprávy s přílohou 512 B v USB/500 Hz a dvě SC-FTN zprávy s přílohou
20 KiB ve FM,
vždy v obou směrech. Ověřené jsou také nenulové počty a rychlost datové fáze
ARDOP. Prošlo 278 cílených testů. Záznam:
`lab/1.1.24-review-fixes-20261010.md`.

## Kontrola a zjednodušení aplikace

Čtyři samostatné průchody pokryly opětovné použití kódu, jeho kvalitu,
efektivitu a srozumitelnost v aktuálních zdrojích aplikace. Změny zachovávají
veřejná rozhraní, formáty dat, pořadí callbacků, zámky a časování přenosů.

- Společný seznam sousedů pro adverty vynechává rádia se zastaveným řídicím
  kanálem. Pozorování pro zobrazení slyšených stanic zůstávají zachována.
- Ruční oznámení v Síti je dostupné, pokud je může vyslat alespoň jedno rádio;
  ARDOP na primárním rádiu už neblokuje dostupný druhý kanál.
- Sdílené jsou převody frekvencí CSV, atomický zápis poštovního bundle,
  aktualizace lokálních metadat s rollbackem a stavy internetové úschovy.
- Sjednocena je prodleva při odklíčování PTT, dokončení předání VARA,
  parsování starých AFSK/MFSK rámců a společné CRC/HARQ dekódování. Specifické
  demapování jednotlivých modemů zůstává v původních funkcích.
- Graf topologie se sestaví jednou pro výpočet tras. AFSK opakovaně nevytváří
  stejnou akviziční sekvenci. Mapový FIFO používá deque, cache prokládání má
  omezenou velikost a čtení ARDOP kopíruje jen skutečně přijaté bajty.
- Odstraněny jsou nepoužívané privátní funkce, stavové cache a importy.
  Odmítnutí neplatných JSON čísel v Guard Mesh má přímo pojmenovanou funkci.
- Audiokanál a spektrum sdílejí ukončení streamu: i po chybě startu nebo
  zastavení se zavře zvukové zařízení a zůstane zachována původní chyba.

Porovnání s uchovanými implementacemi před refaktoringem prošlo v 266 případech
topologie, AFSK, starých rámců a CRC/HARQ, včetně přesné shody bajtů a soft dat.
Prošlo také 311 cílených regresních testů. Nové testy pokrývají oba nálezy
dvou rádií, chyby ukončování audio streamů a vyprázdnění omezené cache.

Kompletní regresní sada nad výsledným kódem: **1110 testů prošlo**, za
800,03 s. Běh používal izolované APPDATA/TEMP, Qt offscreen a jeden výpočetní
thread BLAS; nastavení aplikace se tím nemění. Záznam:
`output/test-simplify-final.log`. Porovnání původních a nových implementací:
`output/simplify-equivalence-1.1.24.json`.

Diagnostický výpis po 180 s zachytil dlouhé přepočítání Qt stylů v testu
opakovaného ukládání nastavení. Test nakonec prošel i v kompletní sadě;
samostatně v novém procesu prošel za 12,99 s. Test sdílí QApplication,
nahrazuje dialogovou smyčku synchronním callbackem a staré widgety čekají na
odložené odstranění. Dotčená logika tématu a obnovy okna se při refaktoringu
neměnila. Záznam samostatné kontroly: `output/test-simplify-qt-isolated.log`.

## Závěrečný RF test po refaktoringu

Na připojených IC-705 prošly všechny čtyři celé produkční přenosy: dvě ARDOP
zprávy s přílohou 512 B v USB/500 Hz a dvě SC-FTN zprávy s přílohou 20 KiB
ve FM, vždy v obou směrech. Obsah odpovídal po bajtech, odesílatelé dostali
potvrzení doručení a obě kampaně ověřily shodnou identitu kódu a DLL.

Živé CAT čtení během ARDOP potvrdilo USB/500 Hz při TX i RX. Po ukončení
zůstala obě rádia ve FM/15 kHz na 144,600 MHz, s původním výkonem a vypnutým
PTT. Podrobnosti: `lab/1.1.24-final-rf-20261010.md`.
