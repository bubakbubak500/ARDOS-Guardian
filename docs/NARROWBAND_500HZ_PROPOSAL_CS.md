# Návrh odolného přenosu Guardian do 500 Hz

**Aktualizace: lokální implementace 1.1.11 nyní používá knihovnu ARDOP.**
Platný popis funkce, nastavení, testů a známých omezení je v
[ARDOP_1.1.11_CS.md](ARDOP_1.1.11_CS.md). Níže je zachována původní rešerše
a vývoj návrhu; alternativní vlastní modem nebyl realizován.

Datum rešerše: 17. 9. 2026. Navazuje na lokální větev `feature/1.1.10-dual-radio` a dokument `DUAL_RADIO_1.1.10.md`. Jde o návrh, nikoli implementovaný nebo RF ověřený modem.

## Doporučení

Pracovní název **Guardian NB-500**: vlastní binární přenos s adaptivní rychlostí, opravou chyb, selektivním opakováním a samostatným odolným navázáním spojení. Primární předpoklad je KV přes SSB a maximální obsazená RF šířka 500 Hz. Uživatel zatím nepotvrdil volbu SSB versus FM; návrh je podmíněn tímto předpokladem.

**Upřesnění uživatele 17. 9. 2026: nenavrhovat datové režimy pomalejší než ARDOP.** Níže uvedené původní nouzové profily 5–20 bit/s jsou tímto vyřazeny z doporučení. Aktuální rychlostní reference a nové cíle jsou v následující části; původní výpočty pomalých profilů zůstávají pouze jako kontext rešerše.

### Aktualizovaná reference ARDOP 500

Ověřeno ve veřejném zdrojovém kódu `pflarue/ardop` (ardopcf, běžná rodina ARDOP; nesměšovat s ARDOP2 nebo experimentálními OFDM variantami). `GetDataModes(500)` vrací šest stupňů:

| Režim | Šířka Hz | Bd na nosnou | Hrubý tok bit/s | Payload B/rámec | Orientační užitečný tok bit/s |
|---|---:|---:|---:|---:|---:|
| 4FSK.200.50S | 200 | 50 | 100 | 16 | 41 |
| 4PSK.200.100S | 200 | 100 | 200 | 16 | 58 |
| 4PSK.200.100 | 200 | 100 | 200 | 64 | 101 |
| 4PSK.500.100 | 500 | 100 | 400 | 128 | 201 |
| 8PSK.500.100 | 500 | 100 | 600 | 216 | 342 |
| 16QAM.500.100 | 500 | 100 | 800 | 256 | 403 |

Parametry a payload: [FrameInfo v ARDOPC.c](https://github.com/pflarue/ardop/blob/master/src/common/ARDOPC.c). Poslední sloupec je převod komentované tabulky 310/436/756/1509/2566/3024 B/min u [DataModes500 v ARQ.c](https://github.com/pflarue/ardop/blob/master/src/common/ARQ.c), násobeno 8/60 a zaokrouhleno. Je to orientační tabulka uložená v kódu, nikoli nově provedené měření aktuální verze; skutečný tok závisí na časování, zaplnění rámců a retransmisích. Není vhodná jako přesná garantovaná výkonnost.

S znamená krátký datový rámec. Nejnižší tři stupně v 500Hz relaci používají 200 Hz. Implementace také zná 4FSK.500.100 a krátkou variantu, ale ty nejsou v tomto automatickém šestistupňovém seznamu; jsou použity například v seznamech širších relací.

**Nové cíle:** robustní stupeň orientačně 40–60 bit/s, další kolem 100, 200 a 350–450 bit/s čistého toku; 500–700 bit/s ponechat jako výzkumný cíl pro dobrý kanál. Rozhodující podmínka je alespoň srovnatelný užitečný tok proti změřenému ARDOP 500 při stejném kanálu, výkonu, zprávě a úspěšnosti doručení. Číslo 40 bit/s není tvrdá dolní mez okamžité rychlosti: retransmise mohou zpomalit libovolný modem. Nebudeme však přidávat záměrně pomalejší datový profil. Odolnost se má zlepšovat synchronizací, kódováním, kombinováním příjmů a nižší režií. Codec2 DATAC3 samotný nepokrývá nejrychlejší stupeň této reference, proto nemůže být bez dalšího jediným datovým profilem.

**Aktuální směr podle uživatele: držet se rychlostních stupňů a adaptivního přístupu ARDOP 500.** Codec2 je srovnávací kandidát pro případné zlepšení odolnosti, nikoli automaticky preferované řešení. Změna modulace nebo kódování by znamenala vlastní protokol, nikoli automatickou kompatibilitu s ARDOPem.

### Co znamená porovnání s Codec2

Porovnáváme datové režimy knihovny, nikoli hlasový kodek. DATAC3 má podle zveřejněných velikostí a časů asi 316 užitečných bit/s (126×8/3,19), DATAC4 asi 84 (54×8/5,17), ještě bez režie celé potvrzované relace. Nominální čísla v dokumentaci jsou 321 a 87. Je nutné přičíst kompletní burst, ACK, přepínání rádia a opakování. Čísla ARDOPu z tabulky výše pocházejí z jiného výpočtu a nesmějí být vydávána za přímé měření proti Codec2. Zdroj: [Codec2 raw data API a HF režimy](https://github.com/drowe67/codec2/blob/main/README_data.md).

Hotový DATAC3 nemůže na bezchybném kanálu zajistit 400 bit/s čistého přenosu. Potenciální přínos Codec2 je nižší počet chyb ve zhoršeném kanálu: může vyhrát v čase doručení i s nižší nominální rychlostí. Pro tvrzení o konkrétní výhodě v dB nebo procentech zatím nemáme společný benchmark. ARDOP poskytuje kompletní ARQ, zatímco raw-frame API Codec2 nechává obsluhu ztrát a sestavení zprávy nadřazené aplikaci. Pro experiment musí obě řešení přenášet totožnou zprávu za totožných podmínek a započíst veškeré časy i neúspěšné relace.

## Zjištěný stav Guardianu

- `guardian/waveforms/config.py`: SC-FTN profily začínají na 1K2, profil 500 Hz neexistuje. Parametry pulzů a rozestup symbolů jsou oddělené, takže výzkumný SC profil s tau=1 lze konstrukčně odvodit.
- `guardian/ofdm/phy.py` a `config.py`: historický název adresáře neznamená přítomnost funkčního OFDM modemu. Současná produkční fyzická vrstva je SC-FTN.
- `guardian/modem/mfsk.py`: úvodní komentář uvádí přibližně 500 Hz, ale skutečný výchozí rozestup je 125 Hz a 16 tónů leží mezi 400 a 2275 Hz. Ani samotný rozestup krajních tónů není úplná obsazená šířka. `guardian/modem/__init__.py` používá výchozí konstruktor. Celá cesta discovery/handshake/ACK se tedy musí řešit také.
- `guardian/ofdm/link.py`: existuje selective-repeat ARQ s bitmapovými ACK a samostatně chráněnými bloky. Pevné předpoklady o velikostech a časování se musí pro úzký režim přezkoumat.
- `guardian/ofdm/coding.py`: již existuje měkké dekódování a kombinování opakování. U LDPC se parita kombinuje jen při shodném grafu a sazbě; přechod mezi sazbami není automaticky plnohodnotné incremental-redundancy HARQ. Sazba LDPC 1/3 zde není implementována.
- `guardian/payload/negotiated.py`, `base.py`, `guardian/multi_radio.py`: lze navázat novým přenosovým backendem a vyjednáváním po jednotlivých rádiových úsecích. Je nutné ověřit rozšíření konfigurace obou rádií, nikoli předpokládat automatickou podporu.

## Existující možnosti

| Varianta | Doložený údaj | Použití v návrhu |
|---|---|---|
| ARDOP | 200/500Hz režimy, adaptivní modulace, ARQ | Praktická reference hotového přenosu |
| Codec2 DATAC3 | 500 Hz, 126 užitečných B na rámec 3,19 s | Referenční datový modem |
| Codec2 DATAC4 | 250 Hz, 54 užitečných B na rámec 5,17 s | Pomalejší otevřená alternativa |
| Olivia 16/500, 8/500 | Asi 1,95 a 2,92 znaku/s | Reference odolného textového provozu |
| MT63-500 | Asi 5 znaků/s, časové prokládání | Reference proti výpadkům a rušení části spektra |
| JS8 | Normal 50 Hz, perioda 15 s; Slow 25 Hz, 30 s | Inspirace pro nejpomalejší krátké zprávy |

Zdroje: [specifikace ARDOP, historická revize 0.3.1](https://winlink.org/sites/default/files/downloads/_ardop_specification.pdf), [aktuální zdrojové definice ARDOP](https://github.com/pflarue/ardop/blob/master/src/common/ARDOPC.c), [Codec2 data](https://github.com/drowe67/codec2/blob/main/README_data.md), [Olivia](https://www.w1hkj.org/FldigiHelp/olivia_page.html), [MT63](https://www.w1hkj.org/FldigiHelp/mt63_page.html), [JS8 dokumentace vývojové větve](https://js8call.com/JS8Call-improved/d6/d14/md_docs_2user__guide_2JS8Call__User__Guide.html).

Znaky za sekundu nejsou binární propustnost souborového přenosu. Časy Codec2 nezahrnují celou relaci s PTT, potvrzováním a opakováním. Kompatibilita fyzické vrstvy také neznamená kompatibilitu síťového protokolu. Použití Codec2 jako knihovny nevyžaduje přebírat celou aplikaci FreeDATA. Před začleněním cizí implementace ověřit licence konkrétních převzatých částí a možnosti jejího zabalení pro Windows.

## Vlastní fyzická vrstva: kandidáti

### SC-PSK

Výchozí pokus: 300 symbolů/s, RRC alpha=0,35, tau=1, střed audia například 1500 Hz. Ideální šířka pulzů je 300 × 1,35 = 405 Hz; skutečné konečné filtry, náběhy a vysílač se změří. Zbývá rezerva do 500 Hz. BPSK, QPSK a později 16-QAM; piloty, sledování frekvence a časování, adaptivní ekvalizér, LDPC.

Před piloty a rámcovou režií vychází BPSK s kódem 1/3 na 100 bit/s, QPSK s 1/2 na 300 bit/s a 16-QAM s 3/4 na 900 bit/s. To jsou výpočty geometrie, nikoli dosažený uživatelský tok. Nejprve tau=1: FTN přidává úmyslnou mezisymbolovou interferenci a složitost příjmu. Tau<1 má smysl teprve po porovnání při stejné chybovosti, výkonu a šířce.

### MFSK

Pokusy s 8 nebo 16 tóny, měkkými pravděpodobnostmi symbolů, prokládáním a vhodným LDPC. Například 16-FSK při 25 Bd a rozestupu 25 Hz má rozteč krajních tónů 375 Hz, hrubý tok 100 bit/s a při sazbě 1/3 asi 33 bit/s před režií. Spektrální masku musí zajistit tvarování přechodů a ověřené filtry; počet tónů krát rozestup není důkaz splnění 500 Hz.

Výhoda je téměř konstantní obálka a menší potřeba sledovat fázi. Úzká rozteč tónů zvyšuje nároky na frekvenční synchronizaci a odolnost proti Dopplerovu rozšíření. Rušení jednoho tónu se musí projevit jako nízká důvěra, nikoli jako silný platný symbol. MFSK nemusí porazit OFDM nebo koherentní PSK na každém kanálu.

### OFDM

Začít existujícími Codec2 režimy a jejich přijímačem. Vlastní profil má smysl až po ověření této reference. Přínosem je práce s vícecestným šířením a samostatným odhadem kvality jednotlivých nosných. Nevýhodou jsou výkonové špičky a ztráty při příliš velkém Dopplerově rozšíření vzhledem k rozteči nosných. Snížení důvěry rušených nosných musí respektovat i synchronizační a pilotní symboly.

## Cílové rychlosti a chování

Následující rozsahy jsou rozpočtové cíle čisté rychlosti po ustálení relace, ne naměřené prahy citlivosti. Nesmí se spojovat s konkrétním SNR bez simulace a RF zkoušek.

| Profil | Cílový uživatelský tok | Úloha |
|---|---:|---|
| Nouzový | 5–20 bit/s | Velmi krátká zpráva, identita, stav |
| Odolný | 30–80 bit/s | Slabý nebo rušený kanál |
| Běžný | 100–250 bit/s | Krátká pošta a strukturované zprávy |
| Rychlý | 350–700 bit/s | Dobrý stabilní kanál, pozdější optimalizace |

Limit 500 Hz dovoluje použít i užší nouzový profil. Delší symboly pomáhají jen dokud je kanál dostatečně stabilní. Extrémní Doppler vyžaduje jiné nastavení než téměř neměnný slabý signál.

Příklady pro 1 KiB při skutečně dosaženém čistém toku: 20 bit/s = 6 min 50 s; 60 = 2 min 17 s; 150 = 55 s; 250 = 33 s; 500 = 16 s. K tomu počáteční navázání, metadata a případné prostoje nezahrnuté v měřeném toku. 1 KiB zde znamená skutečný obsah přenášený po případné kompresi, nikoli počet znaků editoru.

## Co udělá přenos odolným

1. Stejně odolný discovery a ACK jako nejpomalejší data. Samostatný kompaktní 200–250Hz bootstrap je kandidát; nesmí záviset na dnešním širokém MFSK. Požadavek na úzký režim se musí vztahovat na všechny vysílané rámce.
2. Rámce s 16–32 B pro nouzový profil, 64–128 B pro běžný; přesný rozsah zvolit měřením. Dlouhé FEC bloky zlepšují kódování, ale prodlužují čekání. Nesnižovat slepě všechny rámce na minimum.
3. Selektivní opakování, bitmapové ACK, uchování hotových fragmentů a obnovení po výpadku. Jedinečná relace, pořadí fragmentů, CRC a kontrolní hash celé zprávy; příjem fragmentu není doručení celé zprávy.
4. Měkké kombinování opakování pouze pro jednoznačně totožný kódový blok. Nejprve stejné kódové slovo, potom případně navržený společný mateřský kód pro dodatečnou paritu. Obecné nové LDPC jiné sazby tuto vlastnost nemá.
5. Časové/frekvenční prokládání, odhad rušení, omezení vlivu impulsů a průběžné frekvenční sledování. Ochránit také preambuli a hlavičku: po jejich ztrátě nepomůže dobré kódování dat.
6. Adaptace podle skutečně potvrzených bajtů za celý čas relace, chybovosti a zpoždění, s hysterezí. Neřídit se samotným SNR.
7. Kompaktní binární metadata, komprese jen při skutečné úspoře a omezení zbytečných beaconů. Současný poštovní obal proměřit na krátkých zprávách; pár set bajtů režie je v nouzovém profilu zásadní.

## Férové hodnocení citlivosti a průraznosti

V bílém šumu platí SNR v 500 Hz = SNR v 2500 Hz + 6,99 dB. Guardianův simulátor používá in-band SNR, tedy nelze jeho čísla přímo srovnat s jinou referenční šířkou. Codec2 příklady používají i SNR3k; proti 2500 Hz je rozdíl 0,79 dB. U cizích tabulek je nutné vždy ověřit definici i kanál a cílovou chybovost.

Pro ideální 500Hz AWGN kanál a SNR vztažené k 2500 Hz dává vlastní výpočet C=500 log2(1+5×10^(SNR/10)) horní mez přibližně 35 bit/s při -20 dB, 106 při -15 dB, 292 při -10 dB, 684 při -5 dB a 1292 při 0 dB. Reálný modem s konečnými rámci a režií bude níže. Výpočet není predikce kanálu s rušením nebo úniky.

Šířka 500 Hz zmenší zachycený šum a zlepší možnost vyhnout se cizím signálům, ale sama neprokazuje lepší Eb/N0 ani vyšší dosah. „Průraznost“ hodnotit zvlášť pro slabý signál, úzkopásmové QRM, impulsy a vícecestné šíření. Žádný kandidát nezaručuje příjem přes libovolně silné rušení nebo zahlcený analogový vstup.

U FM ručky 500 Hz audia neznamená 500 Hz RF. Rozhoduje FM zdvih, nejvyšší modulační frekvence, IF filtr a chování demodulátoru. Úzké filtrování audia může pomoci za demodulátorem, ale neodstraní RF práh a capture efekt. Pro FM je nutný samostatný experiment; zdejší KV odhady na něj nepřenášet.

## Experiment před integrací

- Shodné zprávy 32 B, 256 B, 1 KiB a 10 KiB; zveřejnit definici užitečných bajtů a kompletní čas od výzvy do potvrzení.
- AWGN, potom časově proměnné vícecestné HF kanály, například 0,5/2/5 ms rozptylu zpoždění a 0,1/1/3 Hz Dopplerova rozšíření. Současný statický simulátor ech nestačí jako Wattersonův model.
- Frekvenční chyba 0/±10/±25/±50 Hz, drift, chyba zvukového taktu, náhodný začátek rámce, výpadek preambule.
- CW/FSK interferer s různým poměrem rušení/signál a polohou, rušení 50/100/200 Hz části pásma, impulsy a výpadky 50 ms až 2 s. Samostatně testovat falešné synchronizace bez požadovaného signálu.
- Srovnání při stejném průměrném RF výkonu i při stejném povoleném PEP. Zahrnout potřebné snížení buzení, ALC, zkreslení a spektrální rozrůstání. Samotná normalizace audia nestačí.
- Měřit spektrum celého burstu včetně preambule, ACK, náběhů a skutečného vysílače. Pro srovnání s ARDOP uvádět šířku na -26 dB a vedle ní 99% obsazenou šířku; kritéria nezaměňovat.
- Výsledky: frame error rate, pravděpodobnost úspěšného navázání, medián a 95. percentil času doručení, timeouty, užitečné bit/s, zatížení CPU. Vykázat i neúspěšné relace a intervaly nejistoty. Na bod kanálu ideálně alespoň 1000 rámců, u nízké chybovosti více.
- Nakonec dva skutečné KV transceivery, záznamy audia, kontrolované útlumy a provozní trasy. Dual radio ověřit jako dvě nezávislé relace s předáváním pošty; není to samo o sobě koherentní diverzitní příjem.

První výstup další etapy má být reprodukovatelný srovnávací report ARDOP / Codec2 / vlastní kandidáti. Teprve podle něj zafixovat NB-500 profily, protokolové identifikátory a integraci do obou rádií Guardianu.
