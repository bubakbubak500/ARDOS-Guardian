# Guardian 1.1.11 – lokální ARDOP 500 Hz

Navazuje na 1.1.10 dual radio. Přidává experimentální přenos ARDOP jako
knihovnu C v procesu Guardianu. Každé rádio používá vlastní kontext DSP,
ARQ, převodu vzorkování, fronty a audio/PTT. Externí ARDOP program ani VARA
nejsou pro tento režim potřeba.

## Nastavení a použití

Na **obou stanicích** v Nastavení → Modem zvolit
**Guardian ARDOP 500 Hz (experimental)**. Nastavit vlastní audio RX/TX a
rádio/PTT pro příslušný kanál; volbu lze provést nezávisle také pro rádio 2.
Pro přenos v úzkém RF pásmu použít SSB/datové SSB, shodné postranní pásmo
a naladění. Úzký zvukový modem sám nezúží FM vysílač.

Hlasitost ARDOP upravuje řídicí i datové rámce. 100 % zde znamená plnou
úroveň knihovny (její modulator používá drive 30), nikoli příkaz rádiu
vysílat na plný výkon. Úroveň nastavit podle zvukové cesty a ALC rádia.
SC-FTN AutoTune se na ARDOP nepoužívá.

Řídicí modem se automaticky změní na ARDOP FEC 4PSK.200.100. Řídicí rámce
Guardianu do 48 bajtů se vejdou do jednoho 64bajtového rámce ARDOP.
Při dohodě profilu se používá token A500. Neshoda nebo chybějící podpora
přenos odmítne; stanice ARDOP nepřejde automaticky na širší VARA.
Původní řídicí AFSK/MFSK s touto úzkopásmovou sítí přímo nekomunikuje.

## Modem a rychlosti

Jádro vychází z pevně určené revize rfb/ardopb; původ a licence jsou v
`native/ardop/UPSTREAM.md` a `native/ardop/vendor/LICENSE`. DSP se nepřepisuje
do Pythonu. Guardian přidává malé ctypes/C rozhraní a propojení se svými
zprávami, zvukem a PTT.

ARQ má maximum 500 Hz a původní adaptivní žebřík:

| Režim | Hrubá bitová rychlost | Uživatelská kapacita rámce |
|---|---:|---:|
| 4FSK.200.50S | 100 bit/s | 16 B |
| 4PSK.200.100S | 200 bit/s | 16 B |
| 4PSK.200.100 | 200 bit/s | 64 B |
| 4PSK.500.100 | 400 bit/s | 128 B |
| 8PSK.500.100 | 600 bit/s | 216 B |
| 16QAM.500.100 | 800 bit/s | 256 B |

Užitečný průtok je nižší kvůli FEC, leaderům, potvrzením, přepínání rádia
a opakování. U krátkých zpráv se výrazně projeví také řídicí handshake.
V této verzi není zaveden vlastní pomalejší režim pod žebříkem ARDOP.

Zvuková karta běží na 48 kHz mono, modem na 12 kHz. Převod používá
antialiasingový FIR filtr v obou směrech. Vysílání dokončí odtok zvuku
a uvolní PTT před předáním TX_DONE do ARQ. Zpracování DSP běží mimo audio
callback. Po dokončení/selhání přenosu se zvuk vrací řídicímu modemu.

GAR1 obálka obsahuje ID zprávy, délku a 128 bitů SHA-256 pro kontrolu
celého obsahu. Nejde o autentizaci ani šifrování. Limit obálky je 16 MiB;
praktické zprávy v tomto pásmu by měly být výrazně menší. C fronta má
16 KiB a plní se průběžně. Přijímač vrací potvrzení obsahu přes ARQ,
poté se spojení uzavře a pokračují běžné RECEIVED/DELIVERED zprávy Guardianu.

## Ověření a hranice

Závěrečná regresní sada: **836 testů, 0 chyb, 0 přeskočených**.
Po poslední úpravě viditelnosti polí v nastavení prošlo dalších 44 cílených
testů UI a ARDOP. Hotové Windows EXE prošlo ARDOP, Qt a kompresním self-testem.

- Samostatně ověřena modulace a demodulace všech šesti režimů včetně 16QAM
  přes skutečnou C knihovnu a převod 48/12 kHz.
- ARQ přenos binárních dat, opakování po ztraceném spojovacím rámci,
  kontrola obsahu, obrat směru pro potvrzení a ukončení spojení.
- Příjem řídicího rámce s posunem ve vstupním bufferu a přidaným šumem.
- Shoda/neshoda profilu a nezávislá volba modemu obou rádií.
- V čisté lokální ARQ simulaci adaptace dosáhla 8PSK; její vypočtená kvalita
  nevyvolala automatický přechod na 16QAM. Samostatný 16QAM přenos prošel.
  Prahy adaptace nebyly uměle sníženy. Toto chování patří do následného RF
  porovnání s referenčním ARDOP TNC.
- Linuxová knihovna se křížově přeložila pro x86_64-linux-gnu. Běh na Linuxu
  a jeho audio/CAT cesty zatím nejsou ověřeny.
- Test na skutečných rádiích, fading/multipath, úzkopásmový rušič a vzájemná
  kompatibilita s nezávislým ARDOP TNC zatím neproběhly. Nejde o naměřený
  příslib určitého SNR ani průtoku na HF.

## Sestavení

`python tools/build_ardop.py --cc "zig cc"` sestaví Windows DLL; lze použít
C11 GCC/Clang. Lokálně byl použit ověřený Zig 0.14.1. Na Linuxu použít
`python tools/build_ardop.py --cc cc`. `build.ps1` staví nativní modem před
testy a PyInstallerem; současně spuštěný proces používající DLL je nutné
před jejím přepsáním ukončit.

Hotový program umí bez rádia ověřit přibalenou knihovnu:
`Guardian.exe --ardop-self-test --ardop-self-test-report <soubor>`.
Obdobně zůstávají dostupné `--qt-self-test` a `--compression-self-test`.

Lokální instalátor: `release/Guardian-1.1.11-setup-win-x64.exe`.
Verze je určena k lokálnímu ověření; nebyl proveden push, tag ani publikace.
