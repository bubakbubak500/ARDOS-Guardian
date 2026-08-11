# Guardian G2 — další vývoj po 2.3.1

Stav: živý technický plán, aktualizováno pro implementaci 2.3.2 dne 2026-08-12.
Čísla v tomto dokumentu jsou laboratorní nebo návrhová, dokud je nepotvrdí
stejný test na dvou reálných rádiích.

## Stav implementace 2.3.2

Hotovo: pravdivé TX/RX timing struktury a metriky v UI; obousměrný opt-in
Station Test & AutoTune; bezpečný sweep digitální úrovně i Windows endpointu s
A/B detekcí účinku mixeru, žurnálem a obnovou; JSON/CSV evidence; fast
selective-repeat s vlakem nezávislých microburstů, kumulativním sparse/bitmap ACK
a POLL; více-burstové dekódování OFDM, SC-HS, SC-FTN i SEFDM; samostatný
end-to-end FM model. Výchozí vlak zůstává `1` kvůli kompatibilitě se staršími
protistanicemi.

Naměřeno v deterministickém modelu SC-FTN MCS6/FEC 7/8: 8 KiB při čtyřech
microburstech vzrostlo z 6166 na 7840 bit/s (+27,1 %). U 64 KiB testu omezeného
maximálním souvislým časem klesl počet DATA+ACK PTT cyklů z 8+8 na 4+4 a
goodput vzrostl z 8541 na 9058 bit/s (+6,1 %).

Zůstává pro další verzi: skutečný incremental-redundancy HARQ, adaptivní délka
vlaku podle linky, RF-mask ověření externím SDR/service monitorem a stejný
on-air A/B acceptance test proti VARA FM Narrow na dvou rádiích.

## Cíl

Guardian G2 má na dobrém 12,5kHz FM kanálu překonat skutečný aplikační goodput
VARA FM Narrow, přitom zachovat nulové doručení chybných bajtů, bezpečné PTT a
fungování na horších linkách. Nestačí zvyšovat nominální počet bitů na symbol.
Musíme současně optimalizovat:

1. TX úroveň a využití FM zdvihu;
2. waveform, MCS a FEC pro konkrétní audio cestu;
3. počet PTT obratů, ACK airtime a retransmise;
4. pravdivé měření wall-clock goodputu;
5. adaptaci podle skutečných CRC/PER/SNR/EVM výsledků.

## Co už víme

- Hodnota 2,7 kHz je obsazená **NF/audio šířka**, ne RF odstup kanálů. Guardian
  i VARA FM Narrow ji přenášejí uvnitř stejné třídy FM kanálu.
- Počet nosných sám nezvyšuje kapacitu. VARA FM Narrow má přibližně
  `58 × 42 = 2436` komplexních bodů za sekundu; SC-FTN má po odečtení pilotů
  přibližně 2500 datových bodů za sekundu.
- SC-FTN `tau=0.90` dává proti SC-HS očekávaný zisk přibližně 11,1 %.
- Při stejném 16-QAM/FEC 7/8 prošel současný model s 8 KiB takto:
  SC-HS 6509 bit/s a SC-FTN 7191 bit/s. Publikovaný stupeň VARA FM Narrow
  16-QAM je 5668 bit/s, ale přesnou provozní robustnost nelze odvodit jen z
  názvu konstelace.
- Nejvyšší současný SC-FTN test používá 256-QAM/FEC 7/8 a dosáhl 12428 bit/s
  modelovaného aplikačního goodputu. Publikované maximum VARA FM Narrow je
  12750 bit/s. Rozdíl je dost malý na to, aby jej rozhodla režie a radio path.
- SEFDM `alpha=0.985` je funkční výzkumný waveform, ne kapacitní vítěz. V
  současném 24ms bloku nese 48 datových bodů, zatímco SC-FTN 60. SEFDM navíc
  platí za více pilotů, ICI a vyšší crest factor.
- Format 2 selective-repeat už seskupí více 512B bloků pod jedno PTT, přijímač
  drží všechny CRC-validní bloky a vrací kumulativní bitmapu celé zprávy.

## 1. Nejdřív pravdivé měření času

Současný simulátor účtuje délku waveformu a abstraktní `ptt_turnaround`, ale
reálná cesta obsahuje také nastavený TX lead, TX tail, audio guard, RX hangover,
čas dekódování a skutečný čas přechodu rádia TX/RX. Nastavené hodnoty se při
vysílání používají, ale modelovaný goodput je zatím všechny neúčtuje stejným
způsobem.

### Požadovaná změna

- `HalfDuplexPipe.send()` má vracet strukturovaný `TxTiming`:
  `lead`, `waveform`, `guard`, `tail`, `keyed_total` a wall-clock čas.
- RX má reportovat `trigger_wait`, `capture`, `hangover`, `decode` a čas do
  připravenosti k odpovědi.
- `OfdmLink` nesmí dopočítávat realitu pouze z `len(waveform)`. Pro reálný pipe
  použije naměřená čísla; simulátor vrátí deterministický ekvivalent.
- UI oddělí:
  - PHY payload rate;
  - protocol payload rate;
  - channel goodput;
  - wall-clock application goodput;
  - keyed duty cycle.
- Každý uložený benchmark ponese PTT backend, lead/tail/guard/hangover, rádio,
  audio zařízení, frekvenci, waveform a verzi Guardianu.

To umožní férové srovnání s VARA při stejném přibližně 300ms AOIC/PTT nastavení
na stejných rádiích.

## 2. Fast selective-repeat: vlak burstů a jeden ACK

### Současný stav

Pro SC-FTN/256-QAM/FEC 7/8 a 8KiB payload dává současný model:

| Data na jeden ACK | DATA + ACK vysílání | Goodput |
|---:|---:|---:|
| 512 B | 16 + 16 | 3177 bit/s |
| 2048 B | 4 + 4 | 7872 bit/s |
| 4096 B | 2 + 2 | 10365 bit/s |
| 8192 B | 1 + 1 | 12428 bit/s |

Agregace tedy už přinesla téměř čtyřnásobek proti stop-and-wait. Další krok
není jeden obří křehký rámec, ale několik samostatně dekódovatelných
microburstů pod jedním PTT a jedna kumulativní odpověď.

```text
PTT ON
  microburst 0: globální bloky 0..15, MORE=1
  microburst 1: globální bloky 16..31, MORE=1
  microburst 2: globální bloky 32..47, MORE=1
  microburst 3: globální bloky 48..63, MORE=0
PTT OFF

RX -> COMPLETE
nebo RX -> MISSING {7, 29, 41}
```

Každý microburst si ponechá vlastní preambuli, robustní hlavičku, manifest a
samostatně FEC/CRC chráněné bloky. Ztráta jedné hlavičky proto nezničí celý
vlak. Přijímač odpoví až na `MORE=0`, po limitu velikosti/času nebo na explicitní
`POLL`.

### Změny ve wire formátu a stavu

- Použít rezervovaný bit formátu 2 jako `MORE/DEFER_ACK`, pokud se prokáže
  bezpečná kompatibilita; jinak zavést explicitní payload frame version 3.
- Manifest dál používá globální čísla bloků. ACK zůstane kumulativní přes celou
  zprávu.
- Pro dobrý kanál odpověď kódovat jako `all_before=N` plus řídké seznamy nebo
  rozsahy chyb. Pro husté chyby zůstane bitmapa efektivnější; enkodér zvolí
  kratší variantu.
- Ztracený poslední microburst nesmí způsobit slepé opakování celého vlaku.
  Krátký robustní `POLL(session, train)` vyžádá stav, který už RX drží.
- RX po dokončení dál po omezenou dobu drží stav a opakuje finální ACK při
  duplicitě nebo POLL, stejně jako dnes opravuje ztracený poslední ACK.
- Adaptace volí nejen FEC a burst bytes, ale i `ack_interval_bytes` a
  `max_train_seconds`.

### Implementační body

- TX: sestavit několik dnešních waveformů, vložit krátkou mezeru a předat jejich
  konkatenaci `RadioAudioPipe.send()` jednou.
- RX: přidat `decode_many()`, který v jednom audio okně najde všechny platné
  preambule a vrátí více `DecodedBurst` objektů.
- `RadioAudioPipe.longest_burst_samples()` rozšířit o explicitní limit vlaku;
  nealokovat neomezený capture buffer.
- Simulátor musí umět poškodit celý microburst, jeho hlavičku nebo jednotlivý
  subblok a přitom ponechat ostatní části vlaku dekódovatelné.
- Výchozí AUTO může začínat současným jedním 2–8KiB burstem. Po čistých
  výsledcích zvětšuje ACK interval; první partial/NACK jej okamžitě zkrátí.

### Další úspora: incremental-redundancy HARQ

Současné FEC je puncturované z rate-1/2 mother code. U chybějícího bloku lze
později poslat především paritní bity vynechané při 7/8 a na RX kombinovat LLR,
místo opakování celého bloku silnějším kódem. Vyžaduje to:

- stabilní interleaver a identitu coding bits;
- uložení soft LLR pro neúspěšné bloky;
- RV (`redundancy version`) ve frame headeru;
- limit paměti a stáří soft bufferu;
- fallback na dnešní úplnou retransmisi.

## 3. Guardian Pair AutoTune

### Proč

Vyšší digitální úroveň zvukovky zvětší FM zdvih a může zlepšovat vzdálené SNR,
dokud nezačne limiter, overdeviation, clipping nebo audio filtr deformovat
konstelaci. Jedna pevná hodnota není vhodná pro všechny waveformy: SC-FTN má
výrazně nižší crest factor než OFDM/SEFDM a může bezpečně používat jinou RMS
úroveň.

AutoTune proto nesmí maximalizovat pouze přijatou amplitudu. Hledá nejnižší TX
drive, který je v bezpečné oblasti blízko nejlepšího SNR/EVM a má požadovanou
CRC/PER rezervu.

### Dva režimy

#### Quick Tune

Určený před běžným provozem; cíl 30–90 sekund na jeden směr.

1. Zavolat konkrétní protistanici na control channelu.
2. Protistanice lokálně přijme požadavek nebo musí být na allowlistu pro
   kalibraci. Výchozí stav je zakázáno; vzdálený uživatel nikdy nesmí bez
   souhlasu opakovaně klíčovat cizí rádio.
3. Obě strany dohodnou pracovní kanál, session nonce, maximální délku, duty
   cycle, waveform a bezpečný rozsah drive.
4. Odeslat robustní referenční burst při konzervativní úrovni.
5. Projít hrubý sweep TX úrovně v dB, vždy od nízké hodnoty nahoru. Každý bod
   nejméně 2–3 opakování.
6. RX vrátí jeden robustní report za skupinu burstů, ne ACK po každém měření.
7. Jakmile clipping roste nebo EVM/SNR dva kroky po sobě klesá, sweep zastavit.
8. Jemně proměřit nejlepší bod a sousedy například po 1 dB.
9. Vybrat nejnižší úroveň do 0,5 dB od nejlepšího bezpečného výsledku, ne
   absolutně nejhlasitější bod.
10. Obrátit role a kalibrovat druhý směr nezávisle.
11. Ukázat návrh operátorovi; uložit a aplikovat až po potvrzení.

#### Full Radio Characterizer

Delší průvodce pro kompletní bench konkrétní dvojice rádia a zvukového zařízení.
Nemá slepě testovat kartézský součin všech voleb. Adaptivně vyřazuje neúspěšné
větve:

1. **Audio path sounding:** multitone/chirp nebo robustní OFDM training změří
   zisk, fázi, group delay, noise a propady přes NF pásmo.
2. **Passband ladder:** 1,2 / 2,4–2,7 / 5 / 10 kHz podle dostupných profilů.
   Po jasném stopbandu se širší profily už nevysílají.
3. **Drive tune:** zvlášť pro každý waveform s materially odlišným crest
   factorem; případně pro skupinu podobných MCS.
4. **Modulation ladder:** BPSK, QPSK, 16/64/256-QAM a 16/32-APSK; po opakovaném
   selhání se vyšší řády přeskočí.
5. **FEC frontier:** proměřit jen FEC kolem nejrychlejšího spolehlivého bodu.
6. **ARQ/window sweep:** 8, 32 a 64 KiB nebo limit podle času zaklíčování.
7. **PTT timing:** samostatně najít nejkratší bezpečný lead/tail/guard. Změny
   jsou návrh pro operátora, ne automatické odstranění bezpečnostní rezervy.
8. **Reverse direction:** TX a RX cesty jsou asymetrické, výsledky se nesmějí
   sdílet bez měření.
9. **Confirmation:** několik náhodných 8/64KiB payloadů na zvoleném bodu,
   nulové wrong-byte delivery a stabilní PER.

### Co musí měřicí burst/report obsahovat

- session ID a monotónní sequence;
- waveform/profile, MCS, FEC a skutečný digitální TX scale/RMS/peak;
- známý PRBS payload odvozený ze session nonce, aby RX mohl určit referenční
  EVM i u payloadu, který neprošel CRC;
- detect/header/manifest/payload CRC výsledek;
- RX peak, RMS, crest factor, clipped sample count a flat-top indikaci;
- training SNR a reference EVM/SNR;
- sync confidence, CFO, sample-clock ppm a phase slope;
- per-carrier gain/noise nebo kompaktní min/median/max/spread;
- délku burstu, PTT timing a teplotu/duty cycle, pokud je dostupná;
- důvod zastavení nebo odmítnutí bodu.

### Skórování

Tvrdě vyřadit bod, pokud:

- vzniklo clipping/flat-top zkreslení nad toleranci;
- robustní header opakovaně neprošel;
- payload dodal chybná data (to je vždy chyba implementace, ne slabý bod);
- překročil se bezpečný drive, duty cycle nebo čas relace.

Z ostatních bodů počítat především očekávaný goodput:

`payload bits / (data airtime + control airtime + PTT + expected retries)`

SNR/EVM slouží jako rezerva a diagnostika. Samotné maximum SNR není optimální
cíl, protože AGC může amplitudu skrýt a vysoký drive může zlepšit SNR za cenu
horší konstelace nebo širší RF emise. Doporučený bod má splnit minimální PER a
SNR/EVM margin a být nejnižší úrovní blízko maxima goodputu.

### Jak řídit celý TX gain chain

FM zdvih určuje součin několika stupňů: digitální amplituda waveformu, hlasitost
aplikační WASAPI session, Windows endpoint volume, výstup DAC/interface a gain
datového vstupu rádia. AutoTune proto nemá Windows gain ignorovat; musí však
jednotlivé stupně měnit odděleně, jinak stejný výsledný zdvih odpovídá mnoha
neidentifikovatelným kombinacím.

- Před kalibrací snapshotovat mute, aplikační session volume a endpoint master
  volume. Zaznamenat také host API; WASAPI shared, WASAPI exclusive, WDM-KS a
  ASIO nemusí Windows mixer respektovat stejně.
- Krátký A/B probe ověří, zda změna Windows volume skutečně mění vzdálený RX
  RMS/EVM. Pokud ne, označit danou cestu jako bypassed a nezkoušet ji dál.
- Výchozí podporovaný scénář je dedikovaný radiový endpoint typu
  `USB Audio CODEC TX`. Po jednorázovém souhlasu smí AutoTune aktivně sweepovat
  Windows endpoint/session gain spolu s `tx_scale`; endpoint gain není pouze
  nouzový fallback. Není však správné hard-codeovat 100 % pro každé zařízení:
  účinný rozsah a zvolená reference jsou součást radio profilu.
- Na sdíleném reproduktorovém endpointu je změna globální hlasitosti výchozím
  stavem zakázaná. Preferovat aplikační session volume; pokud ji použitý
  PortAudio backend nevystaví, vyžádat ruční potvrzení změny master volume.
- Každá automatická změna Windows mixeru je explicitně povolená a vratná.
  `CANCEL`, chyba a timeout okamžitě obnoví snapshot; crash journal umožní
  nabídnout obnovu při příštím startu.
- Protože control modem a payload mohou sdílet stejný endpoint, ověřit AFSK/MFSK
  control burst před a po celé relaci. Dosavadní on-air zkušenost s VARA-style
  laděním říká, že řídicím rámcům sweep nevadí, takže kontrola po každém bodu by
  jen přidávala režii. Pokud by se konkrétní zařízení chovalo jinak, control a
  payload waveform dostanou vlastní scale.
- Modem přidá per-session `tx_scale` po vytvoření normalizovaného waveformu,
  před přehráním a před přidáním ticha. Je to rychlý, lokální a per-waveform
  stupeň, nikoli náhrada za příliš stažený Windows nebo radio gain.
- Scale se omezí podle naměřeného crest factoru a požadovaného digitálního peak
  headroomu, aby nevznikl clipping už před Windows mixerem.
- Výsledek se uloží per waveform/radio/audio-output/host-API/band/mode včetně
  endpoint a session volume. Jedna hodnota pro SC-FTN a SEFDM není správná.
- Jestli ani bezpečné maximum digitálního scale a povoleného endpoint gainu
  nedává dostatečný zdvih, Guardian doporučí zvýšit radio/data-input gain. Jestli
  clipuje už minimum, doporučí příslušný analogový nebo RX gain snížit.

### Protokol a bezpečnost

Běžné HAVE_MSG/ACK_HAVE/START/RECEIVED rámce zůstanou beze změny. Kalibrace je
nová, opt-in relace. Vhodný je známý vzor tokenového vyjednání
`WORKING_OFFER/WORKING_ACK`, nikoli přetížení běžného přenosu zprávy.

Navržené control-plane události:

- `CAL_OFFER`: peer, session nonce, plán/hash, kanál, odhad času a duty limit;
- `CAL_ACCEPT` nebo `CAL_BUSY/REFUSE`;
- `CAL_CANCEL`: okamžité bezpečné ukončení a návrat konfigurace;
- `CAL_DONE`: souhrn a návrat na calling channel.

Samotné měřicí waveformy a reporty poběží v payload fázi přes robustní G2 PHY.
Starší Guardian neznámý CAL frame odmítne a nikam se nepřeladí. Požadavky:

- lokální potvrzení nebo explicitní callsign allowlist;
- pevný maximální čas, počet burstů a TX duty cycle;
- žádné automatické QSY mimo oboustranně nakonfigurovaný pracovní kanál;
- PTT vždy v `finally` uvolnit, CANCEL dostupný na obou stanicích;
- watchdog při ztrátě protistrany;
- log všech vysílaných úrovní a výsledků;
- ruční volba, zda výsledek pouze zobrazit, nebo uložit/aplikovat.

### Uložení výsledku

Kalibrační záznam musí být identifikován alespoň:

- radio backend/model a dostupný identifikátor;
- TX a RX audio endpoint;
- FM/HF mód a pásmo/frekvenční rozsah;
- waveform/profile/MCS skupina;
- TX scale, lead, tail, guard a RX hangover;
- protistanice a směr;
- datum, verze Guardianu a verze kalibračního protokolu;
- naměřené SNR/EVM/PER/goodput a doporučená rezerva.

Změna rádia, audio endpointu, sample rate nebo zásadní verze PHY označí výsledek
jako `stale`; nemaže jej, ale nepoužije ho bez potvrzení.

### Co AutoTune neumí garantovat

Protistanice může změřit kvalitu demodulovaného audia, ale bez SDR nebo měřicího
přijímače spolehlivě nezměří adjacent-channel power ani právní RF masku. AutoTune
proto nesmí bez omezení hledat největší možný zdvih. Musí mít konzervativní
horní limit a pro certifikaci RF šířky nabídnout externí SDR/service-monitor
proceduru.

## 4. Waveform roadmap

### SC-FTN

- Hlavní kandidát na rychlost pro současnou 2,7kHz FM audio cestu.
- Priorita: AutoTune, přesné FM/radio měření, burst train, adaptivní MCS a HARQ.
- Teprve po stabilním on-air 256-QAM zkoušet nižší `tau`; `tau=0.80` bude
  pravděpodobně vyžadovat BCJR/MLSE, precoding nebo iterativní detektor.

### SEFDM

- Ponechat jako výzkumný multicarrier modul.
- Současné `alpha=0.985` šetří pásmo, ale bez dalších nosných téměř nezvyšuje
  kapacitu.
- Smysl prokáže pouze tehdy, pokud vyhraje v kanálu s notchem/frekvenčním
  propadem, nebo po přidání nosných a interference cancellation překoná SC-FTN.
- Full Characterizer má dodat per-carrier data potřebná pro adaptive bit-loading
  a vypínání poškozených nosných.

### Time-frequency packed multicarrier

Budoucí samostatný experiment může kombinovat časové FTN a frekvenční SEFDM.
Nejdřív musí existovat offline detector benchmark s omezenou složitostí a
měřením goodput/W CPU. Kandidáti: iterative interference cancellation,
decision-feedback, sphere/list detection nebo turbo equalization. Nesmí se
zařadit do provozního UI jen podle nominální raw rate.

### FM channel model

Současný `realistic` model je lineární audio/SSB-like stresor. Pro poctivý FM
vývoj doplnit samostatný end-to-end model:

1. TX audio filter/pre-emphasis nebo data path;
2. nastavitelný peak deviation a limiter;
3. komplexní RF FM modulaci;
4. RF AWGN a multipath;
5. discriminator/de-emphasis/RX audio filter;
6. AGC, clipping, sample clock a soundcard cestu.

Výsledky lineárního a FM modelu se musí reportovat odděleně.

## 5. Férový acceptance test proti VARA FM

VARA i Guardian se měří na stejné dvojici rádií, frekvenci, výkonu, anténách,
audio zařízeních a PTT/AOIC nastavení. VARA compression vypnout a použít
pseudonáhodný nekomprimovatelný payload.

Pro každý modem:

- 8, 64 a 256 KiB;
- nejméně 5 opakování v každém směru;
- čas od prvního požadavku na PTT po finální potvrzení posledního bajtu;
- medián a nejhorší/p95 wall-clock goodput;
- first-pass block rate, retransmitted bytes, DATA/ACK airtime a počet PTT;
- nulové wrong-byte delivery;
- stejné časové okno nebo střídání pořadí, aby se nezaměnila změna kanálu za
  rozdíl modemu.

Úspěch není jeden rekordní burst. Provozní režim má proti stejné VARA FM Narrow
relaci přinést opakovatelný medián alespoň o 10 % vyšší, bez horší dokončovací
úspěšnosti a bez porušení RF/duty omezení. Menší rozdíl se označí jako parita.

## 6. Doporučené pořadí implementace

1. **Timing truth:** skutečné lead/guard/tail/hangover/wall-clock metriky.
2. **Lokální drive sweep:** ručně spuštěné bursty s explicitním `tx_scale`,
   současný záznam/decode na druhé stanici, export CSV/JSON.
3. **Pair Quick Tune:** CAL offer/accept/report, bezpečnost a obousměrné uložení.
4. **Full Radio Characterizer:** sounding, bandwidth a adaptivní MCS/FEC matrix.
5. **Burst train:** `decode_many`, deferred cumulative ACK, POLL a adaptivní
   interval.
6. **Incremental-redundancy HARQ.**
7. **FM channel model a korelace simulátoru s uloženými on-air captures.**
8. **A/B acceptance proti VARA FM Narrow.**
9. **Teprve podle měření:** silnější SEFDM nebo time-frequency packed modul.

## 7. Minimální testovací brány

- AutoTune nikdy neaplikuje hodnotu bez souhlasu a po CANCEL obnoví stav.
- Ztráta každého jednotlivého CAL control/report rámce skončí timeoutem, ne
  nekonečným PTT.
- Clipping nebo překročení duty limitu sweep okamžitě zastaví.
- `decode_many` rozliší sousední microbursty, ztracenou hlavičku a falešnou
  preambuli bez doručení chybných dat.
- Burst train přežije ztracený prostřední microburst, poslední microburst,
  kumulativní ACK i POLL.
- Sparse ACK a bitmap ACK reprezentují totožný stav.
- HARQ kombinace nesmí přijmout blok bez finálního CRC.
- Simulátor je deterministický podle seedu; on-air report obsahuje surová data,
  aby se výběr doporučeného bodu dal zpětně přepočítat.
- Každé tvrzení o rychlosti uvádí, zda jde o PHY, channel nebo wall-clock
  application goodput.

## 8. Otevřené experimentální otázky

- Jaký minimální TX peak headroom a clipping limit koreluje s IC-705 FM
  overdeviation?
- Má nejlepší drive zůstat per waveform, nebo se významně liší i podle MCS?
- Kolik opakování na sweep bod stačí, aby AGC a krátkodobý fading nezvolily
  falešné maximum?
- Je pro dobrý kanál lepší dlouhý train bez parity, nebo kratší train s několika
  proactive parity bloky?
- Jaký maximální čas souvislého PTT je provozně a tepelně vhodný?
- Vyhraje SEFDM v reálném notch/multipath testu, nebo má zůstat pouze laboratoř?
- Kolik z rozdílu proti VARA tvoří waveform a kolik timing/adaptace jejich
  uzavřeného protokolu?

Odpovědi se mají ukládat jako reprodukovatelné captures a benchmark reports,
ne pouze jako ručně opsaná maxima z UI.
