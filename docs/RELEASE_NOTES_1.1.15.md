# Guardian 1.1.15

## Opravy přenosu SC-FTN

- Průběh příjmu vychází ze skutečné velikosti ARQ bloků v přijatém manifestu.
  Příjem tak odpovídá potvrzeným datům na vysílací straně i při 256B blocích.
  Do přijetí manifestu posledního bloku se celková velikost může lišit nejvýše
  o neúplný poslední blok; opakované bloky se do průběhu nezapočítávají znovu.
- Přijímač předává zjištěnou velikost přenosu hlídání časových limitů. Dlouhá
  příloha proto nepoužívá výchozí limit krátké zprávy (780 sekund), který mohl
  přerušit stále postupující příjem. Hlídání nečinnosti a velikostně odvozený
  absolutní limit zůstávají aktivní.
- MCS, FEC a velikost dávky u ukazatele odpovídají poslední skutečné datové
  dávce. Příjem již nepřepisuje profil počátečním místním MCS1; potvrzovací
  rámce jej nemění. Odesílání ukazuje také skutečný profil záchranného opakování.

Navazuje na 1.1.14 včetně kompatibility ARDOP se staršími x64 procesory.
Politika zvyšování a snižování rychlosti ani formát přenosu se nemění.
Nastavení a data stanice zůstávají při aktualizaci zachována.

## Ověření

- Kompletní sada z hlavní složky: **896 passed**.
- Zabalená aplikace: Qt/BLE, komprese a nativní ARDOP roundtrip **PASS**.
- Verze Windows EXE: **1.1.15**; instalátor sestaven lokálně.
- ZIP ověřen kontrolou CRC a shodou EXE/ARDOP se zabalenou aplikací.

Regresní test simuluje příjem 3131 bloků / přibližně 800 kB, po 50 blocích
za 25 sekund. Kontroluje dokončení za původním časovým limitem, procenta,
změny MCS/FEC, duplicity, krátký poslední blok a funkční ochranné limity.
Jde o simulaci průchodu ARQ, backendem a zobrazením; ověření na fyzických
radiostanicích není součástí tohoto testu.

## Soubory

- `Guardian-1.1.15-setup-win-x64.exe` — instalátor Windows x64.
- `Guardian-1.1.15-win-x64.zip` — přenosná aplikace Windows x64.
