# Guardian 1.1.16

## Opravy ARDOP 500 Hz

- Přepnutí na ARDOP nyní znovu otevře běžící řídicí kanál i při nezměněných
  zvukových zařízeních. Původní AFSK/MFSK tak nezůstane aktivní po změně
  datového režimu. Změna hlasitosti ARDOP se také projeví v řídicích rámcích.
- Řídicí rámce nadále používají úzký ARDOP 4PSK.200.100. Regresní test měří
  jejich spektrum v pásmu 500 Hz a ověřuje příjem přes skutečný zvukový
  buffer řídicího kanálu, včetně delšího ticha před rámcem.
- Volby a tlačítka VARA jsou při ARDOPu zašedlé, samostatně pro každé rádio.
  U SC-FTN zůstává VARA dostupná jako záložní možnost.
- Automatické i ruční majáky jsou po dobu zvoleného ARDOPu pozastavené.
  Uložené povolení a interval se nemění; po přepnutí zpět se plánování obnoví.
- Automatické CAT přeladění použije frekvenci trasy, ale nemění režim rádia,
  ani když síť uvádí FM. Při návratu také neposílá příkaz změny režimu.
  USB/LSB a úzký filtr zůstávají podle nastavení operátora. Oddělený pracovní
  kanál se nabízí se skutečným SSB režimem rádia.
- Příjmové vlákno nyní pořizuje kopii zvukového bufferu pod stejným zámkem
  jako zvukový callback, aby souběžná změna bufferu nezastavila příjem.

Navazuje na 1.1.15. Formát řídicích ani datových rámců se nemění.

## Ověření

- Regresní testy zahrnují přepínání režimu za běhu, ovládání VARA pro obě
  rádia, pozastavení a obnovu majáků a CAT přeladění se zachováním USB/LSB.
- Testy skutečné nativní knihovny ověřují úzké řízení, ARQ přenos,
  opakování ztracených rámců a potvrzení obsahu.
- Release workflow před publikováním provádí úplnou testovací sadu,
  sestavení aplikace a Qt, kompresní a ARDOP self-test zabaleného EXE.

Fyzický přenos mezi radiostanicemi nebyl v rámci této opravy ověřen.

## Soubory

- `Guardian-1.1.16-setup-win-x64.exe` — instalátor Windows x64.
- `Guardian-1.1.16-win-x64.zip` — přenosná aplikace Windows x64.
- `release-manifest.json` — manifest aktualizace.
- `SHA256SUMS.txt` — kontrolní součty instalačních souborů.
