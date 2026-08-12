# Guardian G2 — kapacitní modem po verzi 2.3.2

Stav: teoretický a experimentální návrh pro některou z dalších verzí G2.
Tento dokument nic neslibuje jako on-air výsledek. Číselné odhady se musí
potvrdit přehráním stejných nahrávek, simulací a nakonec A/B testem na dvou
rádiích. Navazuje na `next.md`; neopakuje detailní implementační stav AutoTune
a selective-repeat z verze 2.3.2.

## 1. Cíl a ověřený výchozí stav

Cílem není přidat co nejvíce názvů modulací, ale maximalizovat bezchybný
aplikační goodput při zadané skutečné audio/RF šířce a současně zachovat:

- nulové doručení rámce bez platného CRC;
- bezpečný half-duplex provoz a omezenou dobu klíčování;
- robustní řídicí rámce a možnost návratu na pomalejší MCS;
- reprodukovatelné profily, které obě stanice jednoznačně identifikují;
- pravdivé měření wall-clock času, retransmisí, zdvihu, EVM a obsazeného pásma.

Ve stromu vydané verze 2.3.2 jsou implementovány OFDM, SC-HS, SC-FTN a SEFDM.
AFDM v tomto stromu zatím implementováno není; zůstává kandidátem pro samostatný
experiment, zejména pro rychle časově proměnný delay-Doppler kanál. AFDM je
navrženo především pro doubly-dispersive kanály a samo o sobě na statické trase
nevytváří další kapacitu.

Současné SC-FTN má při `tau=0.90` přibližně 2500 datových konstelačních bodů za
sekundu. S 256-QAM a FEC 7/8 je dlouhodobý strop před hlavičkami, tréninkem a CRC:

```text
2500 bodů/s × 8 bitů/bod × 7/8 = 17 500 bit/s
```

Modelovaný aplikační goodput kolem 12,4 kbit/s ukazuje, že máme rezervu jak ve
waveformu/detektoru, tak v kódování a rámcové režii. Výslednou rychlost je nutné
vždy rozkládat takto:

```text
goodput = body/s × bity/bod × FEC rate × rámcová účinnost × (1 - ztráty)
```

Žádná z těchto pák sama neobejde Shannonův limit daný šířkou pásma a skutečným
SNR. V pásmu 2,7 kHz je ideální AWGN kapacita přibližně 18,0 kbit/s při 20 dB,
26,9 kbit/s při 30 dB a 31,4 kbit/s při 35 dB. Praktický modem musí zůstat pod
tímto limitem a navíc platí implementační ztrátu rádia a přijímače.

## 2. Oddělení waveformu, konstelace a kódu

Waveform určuje, kolik spolehlivě rozlišitelných komplexních bodů za sekundu
vytvoříme a jak je rozmístíme v čase a frekvenci. Konstelace určuje počet bitů
vložených do jednoho bodu. FEC část bitů spotřebuje na ochranu.

Přibližný současný počet datových bodů bez preambulí a hlaviček:

| Waveform | Datové body v bloku | Blok | Datové body/s |
|---|---:|---:|---:|
| OFDM BENCH | 44 | 24 ms | 1833 |
| SC-HS | 60 | 26,67 ms | 2250 |
| SC-FTN `tau=0.90` | 60 | 24 ms | **2500** |
| SEFDM | 48 | 24 ms | 2000 |

Počet nosných sám není kapacitní zisk. Multicarrier je výhodný, pokud jeho
jednodušší frekvenční ekvalizace a per-carrier bit-loading překonají cenu CP,
pilotů, vyššího crest factoru a nutného TX back-offu. Na plochém a téměř
statickém FM audio kanálu může být single-carrier výhodnější díky vyššímu
bezpečnému RMS a nižšímu PAPR.

Preferovaný nový základ je proto **blokový SC-FDE/DFT-spread FTN**: po drátě
zůstane signál single-carrierový, ale kanál a ISI se budou efektivně řešit přes
FFT. Nejde o další běžné OFDM. Má zachovat nízký crest factor a současně umožnit
dlouhou MMSE ekvalizaci, noise whitening a iterativní detekci.

## 3. Jemnější modulační a konstelační žebřík

### 3.1 Navržené stupně

Guardian dnes nabízí BPSK, QPSK, 16/64/256-QAM a 16/32-APSK. Pro adaptaci na
rádia, kde 16-QAM funguje stabilně a 64-QAM jen někdy, chybí zejména tří- a
pětibitový mezistupeň. Navržená výzkumná matice je:

| Bitů/bod | Primární kandidát | Alternativa | Úloha |
|---:|---|---|---|
| 1 | BPSK | — | bootstrap, hlavičky, nejhorší linka |
| 2 | QPSK | 4-APSK | robustní data |
| 3 | 8-PSK | geometrická 8bodová | mezikrok QPSK -> 16bodová |
| 4 | 16-QAM | 16-APSK | současný provozní high-rate základ |
| 5 | **32-APSK** | cross/rectangular 32-QAM | hlavní mezikrok 16-QAM -> 64-QAM |
| 6 | 64-QAM | 64-APSK | kvalitní linka |
| 7 | **128-APSK** | cross-128-QAM | mezikrok 64-QAM -> 256-QAM |
| 8 | 256-QAM | 256-APSK | současný laboratorní strop |
| 9 | 512-APSK nebo shaped 512-QAM | cross-512-QAM | mezikrok 256 -> 1024 |
| 10 | 1024-QAM | shaped 1024-QAM | čistá, přesně kalibrovaná linka |

Ne všechny řádky musí skončit v uživatelském UI. Laboratoř nejprve porovná
konstelace se stejným počtem bodů při stejném:

- informačním toku a FEC;
- průměrném výkonu;
- špičkovém digitálním rozsahu;
- skutečném FM zdvihu a occupied RF mask;
- vstupním SNR a stejné nahrávce kanálu.

APSK stejného řádu není rychlejší než QAM: například 16-APSK i 16-QAM nesou
čtyři bity na bod. Přínos může být v geometrii kružnic, PAPR a odolnosti vůči
AM/AM nebo AM/PM zkreslení. 32-APSK je ale zároveň skutečný pětibitový mezikrok
mezi 16-QAM a 64-QAM a přes IC-705 může zabránit zbytečnému skoku z 6 přímo na
4 bity/bod.

### 3.2 Geometrické tvarování konstelace

První shaping experiment má být geometrický, protože je nejméně invazivní:

1. AutoTune/Characterizer změří pro každý waveform, šířku a TX drive komplexní
   přenos, EVM, clipping, AM/AM, AM/PM a četnost chyb podle bodu konstelace.
2. Záznamy vytvoří deterministický differentiable nebo tabulkový model
   konkrétní audio/FM cesty.
3. Optimalizátor posune souřadnice bodů a případně poloměry APSK tak, aby
   maximalizoval GMI/achievable information rate při omezení RMS, peak a RF
   masky; nesmí maximalizovat jen minimální eukleidovskou vzdálenost v AWGN.
4. Výsledná konstelace dostane verzované `constellation_id`, normalizaci,
   bit-labeling a golden vectors. Obě stanice používají přesně stejnou tabulku.
5. Kandidát projde A/B replayem proti standardní QAM/APSK na dříve neviděných
   nahrávkách a teprve potom on-air testem.

Je vhodné optimalizovat několik tříd, například IC-705 narrow FM, plochou
lineární audio cestu a širokopásmový datový vstup. Nevytvářet konstelaci pro
každý jednotlivý spoj: přineslo by to negociační chaos a overfitting.

### 3.3 Probabilistic amplitude shaping (PAS)

PAS nemění nutně počet bodů. Mění pravděpodobnost jejich použití: energeticky
výhodnější vnitřní body QAM se vysílají častěji a entropie konstelace se nastaví
plynule mezi celými MCS stupni. Tím lze například z 1024-QAM vytvořit režim s
efektivními 8,4 nebo 9,1 bity na bod místo skoku 8 -> 10.

Navržený řetězec:

```text
payload -> distribution matcher -> shaped amplitudy + znaménkové bity
        -> systematic LDPC -> bit mapper -> SC-FDE-FTN
```

PAS vyžaduje dlouhé bloky, invertibilní distribution matcher, sladění s
systematickým FEC a přesné započítání rate loss. Má proto následovat až po LDPC,
nikoliv být přilepeno k dnešnímu puncturovanému konvolučnímu kódu.

Rozhodovací veličina nebude pouze BER. Použijeme GMI po demapperu, výsledný
goodput, peak/RMS, RF masku a chování při retransmisi. Publikovaný experiment s
probabilisticky tvarovanou 64-QAM ukázal až 15% kapacitní zisk v daném optickém
uspořádání; pro Guardian je to motivace k měření, nikoliv očekávaný výsledek:
<https://arxiv.org/abs/1509.08836>.

### 3.4 Co implementovat jako první

1. 8-PSK a 32-APSK jako praktické mezikroky kolem pozorované hranice IC-705.
2. 128-APSK nebo cross-128-QAM pro sedm bitů/bod.
3. 1024-QAM pouze jako explicitní laboratorní high-SNR režim.
4. Geometricky optimalizované 16/32/64/128bodové kandidáty z Characterizeru.
5. PAS až současně se systematickým LDPC a novým wire/profile ID.

## 4. Šířková rodina pro všechny waveformy

OFDM už má profily od NARROW_1K2 po WIDE_20K a WIDE_40K. SC-HS, SC-FTN a
SEFDM mají dnes pouze přibližně 2,7kHz profil. Další verze má nabídnout stejný
princip profilované šířky všem rodinám; strop této etapy bude 20 kHz.

Navržené uživatelské třídy:

| Třída | Cílově obsazené audio | Typické použití |
|---|---:|---|
| NARROW_1K2 | přibližně 1,2 kHz | omezený filtr, nízké SNR |
| NARROW_2K7 | přibližně 2,7 kHz | současná IC-705/FM reference |
| WIDE_5K | přibližně 5 kHz | širší data/FM cesta |
| WIDE_10K | přibližně 10 kHz | wide FM nebo přímý datový vstup |
| WIDE_20K | nejvýše přibližně 18,8–20 kHz | laboratorní strop při 48 kHz audio |

Každý konkrétní profil musí nést alespoň:

```text
family, profile_version, sample_rate, occupied_low/high,
nyquist_symbol_rate nebo carrier grid, rolloff, tau/alpha,
pilot/training density, equalizer/detector class a povolené MCS
```

Pro single-carrier s RRC přibližně platí:

```text
Nyquist Rs = occupied_bandwidth / (1 + beta)
FTN Rs     = occupied_bandwidth / ((1 + beta) × tau)
```

Při dnešních `beta=0.125`, `tau=0.90` by nominální FTN rychlosti byly:

| Šířka | Přibližná FTN symbolová rychlost |
|---:|---:|
| 1,2 kHz | 1185 Bd |
| 2,7 kHz | 2667 Bd |
| 5 kHz | 4938 Bd |
| 10 kHz | 9877 Bd |
| 20 kHz | 19 753 Bd |

Jde o návrhové hodnoty, ne o automaticky použitelné profily. Nad několika kHz
už nelze vyžadovat celočíselný počet 48kHz vzorků na symbol. Modem musí přejít
na racionální/polyfázový resampler nebo blokovou syntézu. Pro WIDE_20K je 48
kHz na výstupu teoreticky možné, ale filtr má malou rezervu k Nyquistově hraně;
interní oversampling a kvalitní převod na 48 kHz jsou vhodnější.

Šířku nelze jen přepsat jedním násobkem. Pro každou příčku se musí znovu určit:

- dolní a horní audio okraj místo slepého centrování;
- přechodové pásmo filtrů a potlačení aliasů;
- počet pilotů a délka tréninku;
- time spread/CP nebo overlap-save blok;
- tolerovaná CFO a sample-clock chyba;
- TX RMS/peak a skutečný RF zdvih;
- maximální MCS podle measured EVM/PER.

AutoTune nejprve soundingem zjistí, co konkrétní dvojice rádií skutečně propustí.
AUTO pak zvolí nejširší profil, který má rezervu, ne nejširší profil, jenž jednou
prošel. Při stejném celkovém TX výkonu stojí přibližné zdvojnásobení šířky asi
3 dB výkonové hustoty; širší proto nemusí být rychlejší na slabé trase.

Rodiny se mají generovat z parametrických továren, nikoli kopírováním desítek
konstant. Každá kombinace `family × bandwidth` však dostane vlastní wire ID a
golden vectors. Přijímač nesmí odhadovat šířku z náhodného maxima korelace.

## 5. Waveformová větev: SC-FDE/DFT-spread FTN

### 5.1 Proč

Běžný multicarrier nepřidává kapacitu sám o sobě. SC-FDE kombinuje vysílaný
single-carrier průběh s frekvenční ekvalizací srovnatelnou složitostí s OFDM a
bez stejné power-backoff penalizace. Přehledový materiál IEEE 802.16 popisuje
právě lineární nebo DFE frekvenční ekvalizaci single-carrieru s přibližně OFDM
složitostí: <https://www.ieee802.org/16/tg3/contrib/802163c-01_58.pdf>.

Navržený vysílač:

```text
CRC -> LDPC -> interleaver -> QAM/APSK/PAS
    -> volitelné DFT spreading/precoding
    -> spektrální shaping + FTN pulse
    -> prefix/unique word -> real audio
```

Navržený přijímač:

```text
AGC/CFO/SCO/timing -> matched filter -> bloková FFT
 -> odhad kanálu + noise PSD -> MMSE-FDE + whitening
 -> IFFT -> soft ISI cancellation/DFE
 <-> demapper <-> LDPC decoder
```

Blok nemusí používat klasický dlouhý CP. Kandidáty jsou krátký cyclic prefix,
known unique word, overlap-save nebo cyclic suffix podle měření time spreadu.
Výběr musí vycházet z čistého poměru ochranné režie a zbytkového ISI.

### 5.2 Společná optimalizace `beta` a `tau`

Samotné snížení `tau` z 0,90 na 0,80 přidá proti dnešku 12,5 %. Větší skok
vznikne společným vyplněním pásma nižším RRC roll-offem:

| `beta` | `tau` | Symbolová rychlost v 2,7 kHz | Proti dnešku |
|---:|---:|---:|---:|
| .125 | .90 | 2667 Bd | základ |
| .125 | .80 | 3000 Bd | +12,5 % |
| .05 | .85 | 3025 Bd | +13,4 % |
| .05 | .80 | 3214 Bd | **+20,5 %** |
| .02 | .80 | 3309 Bd | **+24,1 %** |

Nižší `beta` znamená delší puls a vyšší citlivost na timing a truncation. Nižší
`tau` znamená silnější záměrné ISI a colored noise po matched filtru. Proto se
tyto profily nesmí aktivovat bez nového detektoru a replay testu.

První experimentální body: `(.125,.90)`, `(.125,.85)`, `(.05,.85)`,
`(.05,.80)`. Bod `(.02,.80)` je stress test, nikoliv očekávaný default.

## 6. Nová ekvalizační a detekční architektura

Současný SC-HS/FTN přijímač používá trénovaný regularizovaný komplexní lineární
FIR ekvalizér; FTN má 81 taps. SEFDM používá 97tapový časový inverzní filtr,
následně přesnou inverzi neortogonální matice a pilotní korekci. To je dobrý
výchozí bod, ale ne kapacitní přijímač.

### 6.1 Stupeň E0 — pravdivá synchronizace a měření

Než se srovnají ekvalizéry, musí být společný front-end:

- band-selective AGC bez clippingu;
- hrubá a jemná CFO korekce;
- sample-clock offset a průběžný timing recovery;
- odhad komplexní impulsní/frekvenční odezvy;
- odhad barevného noise PSD mimo známé symboly;
- oddělené EVM před a po ekvalizaci, noise enhancement a residual ISI;
- channel condition number, coherence time a coherence bandwidth.

Bez toho může delší filtr pouze maskovat špatný timing nebo zesilovat notch.
Každý algoritmus se musí testovat nad stejným uloženým raw WAV/IQ capture.

### 6.2 Stupeň E1 — regularizovaný MMSE místo slepé inverze

Zero-forcing se snaží úplně invertovat i hluboký notch a tím extrémně zesílí
šum. MMSE používá odhad kanálu a noise PSD a volí kompromis mezi zbytkovým ISI
a noise enhancementem.

Zlepšit současný LMMSE:

- regularizaci odvodit z měřeného noise PSD, ne z jedné konstanty;
- automaticky volit délku filtru podle channel impulse response;
- použít weighted/ridge LS a validaci na oddělené části trainingu;
- dovolit robustní QR/SVD řešení a omezit condition number;
- sledovat pomalou změnu kanálu pilot-directed RLS/Kalman aktualizací;
- při nejistém rozhodnutí zmrazit decision-directed adaptaci.

To je nejbezpečnější první implementační krok a může zlepšit všechny SC profily
bez změny vysílače nebo wire formátu.

### 6.3 Stupeň E2 — SC-FDE s overlap-save

Dlouhou konvoluci přesunout do frekvenční oblasti:

```text
Y[k] = FFT(received block)
Xhat[k] = Wmmse[k] × Y[k]
xhat[n] = IFFT(Xhat[k])
```

Přínosy:

- dlouhá efektivní odezva za `O(N log N)`;
- snadné použití measured noise PSD po frekvenčních binech;
- stejný základ pro 2,7 až 20 kHz;
- channel shortening a notch-aware regularizace;
- možnost per-bin reliability pro soft demapper.

SC-FDE nemá automaticky kapacitní zisk proti dokonale fungujícímu časovému
ekvalizéru. Je to prostředek, který umožní levněji a stabilněji provozovat
agresivnější FTN, širší profily a iterace.

### 6.4 Stupeň E3 — noise whitening a FTN-aware model

FTN matched filter vytváří známé deterministické ISI a korelovaný šum. Přijímač
nesmí předstírat nezávislý AWGN na každém symbolu.

- vytvořit Gramovu matici pulzů pro konkrétní `beta`, `tau` a truncation;
- zahrnout rádiový kanál a matched filter do společné efektivní odezvy;
- whitenovat šum Cholesky/spektrální faktorizací nebo ekvivalentním FDE filtrem;
- předávat demapperu per-symbol/per-bin variance, ne jednu globální hodnotu;
- měřit residual ISI covariance po equalizeru.

Práce Ishihara/Sugiura kombinuje pro FTN soft MMSE frekvenční ekvalizaci,
whitening a iterativní dekódování a ukazuje důležitost barevného šumu v silně
packed režimu: <https://arxiv.org/abs/1604.03698>.

### 6.5 Stupeň E4 — reliability-gated DFE / interference cancellation

Lineární filtr nechává zbytkové ISI nebo zesiluje šum. Decision-feedback
equalizer odečte příspěvky již rozhodnutých symbolů. Pro Guardian nesmí být
naivní hard DFE, protože jedna chyba by se mohla šířit celým blokem.

Navržený postup:

1. MMSE-FDE vytvoří soft symboly a LLR.
2. Pouze symboly s vysokou spolehlivostí se znovu namapují.
3. Jejich rekonstruované ISI se odečte z přijatého bloku.
4. Zbytek se znovu MMSE ekvalizuje a demapuje.
5. Iterace se zastaví při nezlepšení GMI/EVM nebo po pevném limitu.

DFE v SC-FDE běžně překonává lineární řešení při nižší složitosti než MLSE,
ale musí řídit error propagation; příklad reliability/sparsity řízené varianty:
<https://arxiv.org/abs/1103.5542>.

### 6.6 Stupeň E5 — turbo equalizace s LDPC

Největší očekávaný detekční posun:

```text
equalizer/demapper -> extrinsic LLR -> LDPC decoder
        ^                                  |
        +---- soft symbol priors <---------+
```

Dekodér po první iteraci zná pravděpodobnosti bitů. Z nich se vytvoří měkké
odhady symbolů a variance, přijímač odečte očekávané ISI a znovu ekvalizuje.
Typicky 2–5 iterací; hlavička a řídicí rámce zůstanou na jednoduché robustní
cestě. Přehled turbo equalizace uvádí velké BER zlepšení iterací equalizeru a
dekodéru a také lineární aproximace s nižší složitostí než trellisový přijímač:
<https://www2.ensc.sfu.ca/people/faculty/cavers/ENSC805/readings/50comm05-tuchler.pdf>.

Turbo equalizace vyžaduje:

- systematický soft-output LDPC;
- správné extrinsic, nikoliv opakovaně započítané posterior LLR;
- kalibrované LLR podle residual covariance;
- early stop podle parity checks a CRC;
- pevný CPU/latency limit a fallback na E2/E3.

### 6.7 Stupeň E6 — reduced-state BCJR/MLSE

Plné MLSE má počet stavů přibližně rostoucí jako `M^L`, takže je pro 256-QAM a
dlouhé FTN ISI nepoužitelné. Má smysl jen jako:

- referenční oracle pro krátké bloky a BPSK/QPSK;
- reduced-state sequence estimation po channel shortening;
- M-BCJR/list/sphere detektor pro omezený počet nejsilnějších ISI sousedů;
- benchmark, zda jednodušší turbo-MMSE zanechává významnou mezeru.

Pro vysoká QAM existují také polynomialní aproximace sekvenční detekce, například
ADMM FTN detector: <https://arxiv.org/abs/2107.00805>. Nejprve jej použít offline
na replay datech; nezařazovat do realtime cesty bez jasného gain/CPU výsledku.

### 6.8 Stupeň E7 — nelineární kompenzace FM/audio cesty

Lineární equalizer neopraví clipping, limiter, kompresi nebo AM/PM s pamětí.
Characterizer proto musí rozhodnout, zda po E3–E5 zůstává chyba korelovaná s
amplitudou a předchozími vzorky.

Kandidáti v pořadí složitosti:

1. statická komplexní AM/AM + AM/PM LUT a její bezpečná inverze;
2. transmitter digital predistortion omezené peak a RF maskou;
3. memory polynomial s malým řádem a krátkou pamětí;
4. diagonální/sparse Volterra equalizer;
5. malý model-based neuronový residual equalizer až jako poslední experiment.

Nelineární filtr musí být trénován na jiné nahrávce, než na které se reportuje
výsledek. Zakázat extrapolaci mimo změřený TX drive. Pokud začne zvyšovat peak,
occupied bandwidth nebo počet CRC chyb, profil jej nesmí aktivovat.

Volterrovy modely se používají pro kanály aproximovatelné konečnou nelineární
pamětí, ale mají rychle rostoucí počet koeficientů; proto zde pouze sparse nízký
řád: <https://doi.org/10.1109/78.815489>.

### 6.9 OFDM, SEFDM a případné AFDM

- OFDM: přejít ze scalar/ZF equalizace na per-carrier MMSE s interpolovaným
  channel/noise odhadem; potom přidat per-carrier bit/power loading.
- SEFDM: společně řešit kanál a ICI. Sekvenční `inverse matrix` a následná
  scalar korekce nestačí pro agresivní `alpha`; použít regularizovaný joint
  detector, soft interference cancellation a případně turbo iteraci.
- AFDM: hodnotit podle PER při Doppleru/time variation, nikoliv jako automaticky
  rychlejší waveform. Implementovat jen jako nový samostatný modul a profil.

## 7. Moderní FEC a incremental redundancy

Současný puncturovaný K=7 konvoluční kód nabízí 1/2, 2/3, 3/4, 5/6 a 7/8.
Pro přiblížení ke kapacitě navrhnout QC-LDPC mother code s profily například:

```text
1/2, 2/3, 3/4, 4/5, 5/6, 8/9, 9/10, případně jemné mezistupně
```

Požadavky:

- bloky odpovídající microburstům, například 2–16 KiB, plus kratší fallback;
- soft LLR a layered/min-sum decoder s korekcí;
- early parity stop a konečné payload CRC;
- rate matching bez změny identity mother-code bitů;
- redundancy version pro IR-HARQ;
- soft buffer omezený počtem relací, bajty a časem;
- kombinace LLR z opakování i nové parity;
- neměnit robustní BPSK control path, dokud nový bootstrap není zvlášť ověřen.

Skutečná výhra LDPC není jen vyšší code rate. Umožní agresivnější FTN a vyšší
konstelaci při stejném cílovém PER a poskytne soft informace turbo equalizeru.

## 8. Burst train druhé generace

### 8.1 Co máme

Verze 2.3.2 umí pod jedním PTT zřetězit několik **samostatně synchronizovaných**
microburstů. Každý má vlastní preambuli, robustní hlavičku, manifest, FEC a CRC.
Jedna kumulativní ACK/MISSING odpověď pokryje celý vlak. To je robustní a
kompatibilní, ale opakuje acquisition a manifestovou režii.

### 8.2 Superburst/train v2

Pro čistou linku navrhnout nový explicitně verzovaný superframe:

```text
PTT ON
  master acquisition + sounding/training
  robustní SUPER header: session, train, profil, MCS/FEC, délka, plán
  codeword 0 + CRC
  mini-sync/pilot anchor + codeword 1 + CRC
  mini-sync/pilot anchor + codeword 2 + CRC
  periodický robustní reacquisition anchor
  ...
  end marker / POLL request
PTT OFF

RX -> COMPLETE
nebo RX -> ACK prefix + sparse MISSING + požadované redundancy versions
```

Master preambule a úplný channel training se zaplatí jednou. Jednotlivé LDPC
codewordy zůstanou samostatně ověřitelné a adresovatelné. Krátký mini-sync
udržuje timing/CFO/channel tracking; periodická plná kotva dovolí přijímači
znovu nastoupit doprostřed vlaku. Nelze odstranit všechny kotvy, protože chyba
první preambule by jinak zničila celý dlouhý přenos.

### 8.3 Adaptivní délka a režie

AUTO řídí nejméně:

- `codeword_bytes`;
- `codewords_per_train`;
- `full_anchor_interval`;
- `pilot_density` a `training_refresh_interval`;
- `max_train_seconds` a duty-cycle limit;
- MCS, FEC rate a případně PAS entropy;
- `ack_interval_bytes`;
- IR-HARQ redundancy version.

Na čistém stabilním kanálu prodlužuje train a zřeďuje plné kotvy. Při první
ztrátě, růstu EVM, timing driftu nebo změně channel response train zkrátí a
obnoví training. Adaptace musí používat hysteresi a nesmí měnit několik pák
současně bez možnosti určit příčinu zhoršení.

### 8.4 Kompaktní metadata

- Super header nese společný waveform/profile/MCS/FEC a rozsah globálních
  sequence numbers.
- Codeword miniheader nese jen delta sequence, délku, flags a RV; chránit jej
  krátkým robustním kódem a CRC.
- Délky lze odvodit z pevné codeword velikosti; poslední blok nese skutečnou
  délku.
- Pro smíšené MCS ve vlaku použít malou tabulku plánů v super headeru, ne plnou
  hlavičku před každým blokem.
- ACK na dobrém kanálu je `all_before=N` plus řídké výjimky. Při hustých chybách
  automaticky použije bitmapu.

### 8.5 IR-HARQ

Chybějící codeword se nejprve neopakuje celý. RX uchová soft LLR a vyžádá RV1,
RV2… s dosud neposlanou paritou. Teprve po limitu kombinací nebo vypršení soft
bufferu následuje plná retransmise robustnějším profilem.

### 8.6 Bezpečnost a fallback

- `max_train_seconds` je tvrdý limit nezávislý na payloadu.
- RX timeout zahrnuje délku vlaku, dekódování a PTT obrat.
- Ztracený end marker vyvolá robustní POLL, ne opakování celého vlaku.
- Stav dokončeného trainu se krátce drží pro opakování finálního ACK.
- Starší stanice superburst v2 neobdrží; po capability negotiation zůstane
  dnešní independent-microburst train.
- Control frames zůstávají krátké a robustní.

## 9. AutoTune, sounding a adaptace jako jeden systém

Characterizer musí pro každou kombinaci `radio × path × bandwidth × waveform`
uložit:

- komplexní frequency response a impulse response;
- noise PSD a in-band SNR;
- group delay, CFO drift a sample-clock ppm;
- safe TX drive, RMS, peak, clipping a odhad zdvihu;
- pre/post equalizer EVM, residual ISI a noise enhancement;
- PER/CRC podle MCS, FEC, délky codewordu a trainu;
- GMI/achievable rate z kalibrovaných LLR;
- measured occupied audio a externě změřenou RF masku, pokud je dostupná;
- CPU čas a latency detektoru.

Výběr nemá maximalizovat SNR nebo nominální bit rate. Kandidátní utility:

```text
expected_goodput = payload_bits × P(success)
                 / (TX + turnaround + ACK + expected_repair time)
```

Omezující podmínky: CRC integrity, RF maska, peak/clip, duty cycle, maximální
latence a minimální rezerva proti naměřenému MCS cliffu.

Adaptace má hierarchii:

1. udržet synchronizaci a bezpečný TX drive;
2. zvolit nejširší profil s rezervou;
3. zvolit waveform/detector podle channel shape a time variation;
4. zvolit konstelační entropy/order;
5. zvolit FEC/RV;
6. až potom prodlužovat train a ředit ACK/kotvy.

## 10. Experimentální metodika

### 10.1 Replay-first

Každý nový přijímač musí umět dekódovat neměnný katalog raw captures. Jeden
capture se přehraje všem kandidátům, aby nový algoritmus neměl výhodnější náhodu.
Dataset zahrne:

- čistý kabel/AOIC při různých úrovních;
- dva IC-705 přes dummy load/attenuator a on-air;
- 1,2/2,7/5/10/20kHz cesty na vhodných rádiích;
- clipping, overdeviation, limiter a různé Windows endpoint levels;
- notch, group delay, echo/multipath, CFO a sample-clock offset;
- statický i mobilní/time-varying kanál;
- alespoň dva různé SNR body kolem každého MCS cliffu.

Train, FEC a waveform se testují odděleně i end-to-end. Report vždy uvádí
confidence interval, ne výsledek jednoho seed/capture.

### 10.2 Metriky

- raw coded PHY rate a information PHY rate;
- protocol payload rate během klíčování;
- wall-clock application goodput;
- BLER/PER, počet repair bitů a retransmisí;
- pre/post EVM, GMI, LLR calibration a residual SNR;
- PAPR/crest factor, RMS, peak, clip count a RF occupied bandwidth;
- acquisition/reacquisition failure rate;
- CPU %, nejhorší decode latency a paměť soft bufferu.

Nový režim vyhraje jen tehdy, když zvýší očekávaný goodput při stejných
integritních a RF podmínkách. Vyšší nominální MCS, který zvyšuje retry, nevyhrál.

### 10.3 Porovnávací matice

Minimálně:

```text
waveform: OFDM, SC-HS, SC-FTN, SEFDM, SC-FDE-FTN, případně AFDM
bandwidth: 1.2, 2.7, 5, 10, 20 kHz
modulation: QPSK, 8-PSK, 16-QAM/APSK, 32-APSK,
            64-QAM/APSK, 128bodová, 256-QAM/APSK, 1024-QAM
shaping: uniform, geometric, PAS
FEC: dnešní kód, LDPC rates, IR-HARQ RV
equalizer: current, MMSE, SC-FDE+whitening, DFE/SIC, turbo
train: independent v1, superburst v2
```

Úplný kartézský součin je příliš velký. Použít postupné vyřazování: AWGN,
syntetický realistic FM model, replay, kabelová dvojice, on-air. Kandidát, který
nevykazuje informační zisk už v jednodušší fázi, nepokračuje.

## 11. Doporučené pořadí realizace

### Etapa A — měřitelný přijímač bez změny wire formátu

1. Uložit raw capture, channel/noise odhad, pre/post EVM, GMI a condition number.
2. Implementovat noise-aware MMSE a automatickou délku/regularizaci.
3. Přidat replay A/B harness a bounded realtime benchmark.
4. Přidat 8-PSK, 32-APSK a 128bodový laboratorní mezistupeň.

### Etapa B — parametrické šířky

1. Zavést společnou bandwidth profile factory pro SC-HS/FTN/SEFDM.
2. Implementovat polyfázový resampling/blokovou syntézu.
3. Přidat 1,2/5/10/20kHz testovací i Station profily.
4. Propojit s Characterizerem a capability negotiation.

### Etapa C — nový SC-FDE-FTN modul

1. SC-FDE s MMSE a overlap-save/unique word.
2. FTN Gram/noise whitening.
3. Reliability-gated soft interference cancellation.
4. Sweep `beta × tau × MCS` nad replay datasetem.

### Etapa D — LDPC, turbo a train v2

1. Verzionovaný QC-LDPC mother code a soft decoder.
2. IR-HARQ/soft combining.
3. Turbo equalizer s 2–5 bounded iteracemi.
4. Superburst v2 se sdíleným trainingem a periodickou reacquisition kotvou.

### Etapa E — shaping a nejvyšší režimy

1. Geometricky optimalizované QAM/APSK podle Characterizeru.
2. 1024-QAM a 512bodový mezistupeň.
3. PAS + systematické LDPC + rate adaptation.
4. Nelineární LUT/memory-polynomial kompenzace pouze tam, kde replay prokáže
   skutečný nelineární residual.

## 12. Očekávané páky a hranice očekávání

Orientační samostatné stropy na velmi čisté 2,7kHz lince:

| Změna | Nominální potenciál proti dnešnímu odpovídajícímu bodu |
|---|---:|
| `tau .90 -> .80` | +12,5 % body/s |
| společné `beta .125 -> .05`, `tau .90 -> .80` | přibližně +20,5 % body/s |
| 256 -> 1024-QAM | +25 % bitů/bod |
| 256 -> 512bodový mezistupeň | +12,5 % bitů/bod |
| geometrické/PAS shaping | očekávat jednotky %, výjimečně nižší desítky % |
| lepší LDPC/turbo detektor | zisk hlavně v potřebném SNR/PER, ne pevné procento |
| 2,7 -> 5/10/20 kHz | téměř úměrný šířce jen při dostatku celkového SNR |
| superburst v2 | největší u krátkých codewordů a drahého acquisition/PTT |

Přínosy nelze prostě sečíst a všechny vyžadují vyšší kvalitu kanálu. Kombinace
SC-FDE-FTN, shaped/high-order konstelace, LDPC/turbo equalizace a superburstu má
na čisté trase realistickou šanci vytvořit desítky procent až přibližně 1,5×
skok proti dnešnímu 2,7kHz high-rate režimu. Přesný výsledek určí skutečné SNR,
EVM, RF maska a radio path; 2× v nezměněném 2,7kHz kanálu nelze předpokládat.

Největší fyzický skok poskytne rádio, které skutečně propustí 5–20 kHz. Tam se
šířka násobí s lepší spektrální účinností. AUTO však musí vybírat nejširší
profil s rezervou, protože při pevném výkonu klesá výkonová hustota s šířkou.

## 13. Acceptance kritéria pro budoucí release

- Všechny nové profily mají wire ID, golden vectors a cross-version odmítnutí.
- Žádný CRC-invalidní payload není doručen aplikaci.
- Nový equalizer překoná nebo vyrovná současný na předem zmrazeném replay setu;
  nesmí pouze vyhrát na vlastním training capture.
- Realtime dekódování nepřekročí RX turnaround budget ani memory limit.
- Šířkový profil je změřen jako skutečně obsazené audio a před provozním
  označením také externí RF maskou.
- MCS/PAS profil má kalibrované LLR a doložený PER cliff s rezervou.
- Superburst přežije ztracený prostřední codeword, end marker i finální ACK bez
  opakování platných dat.
- IR-HARQ kombinuje LLR pouze pro shodnou session/codeword/mother-code identitu.
- AUTO vždy umí ustoupit na dnešní robustní waveform/control path.
- Finální A/B proti VARA FM používá stejný payload, rádia, nastavení PTT,
  přibližný RF zdvih, šířku, časový interval a obousměrný wall-clock goodput.

## 14. Hlavní rozhodnutí k návratu

1. Je hlavní nový PHY `SC-FDE-FTN`, nebo nejprve pouze nový přijímač pro dnešní
   SC-FTN?
2. Které tři široké profily reálné testovací rádio propustí a při jaké sample
   rate/audio interface?
3. Vyhraje na IC-705 při stejném informačním toku 32-APSK nad alternativní
   32-QAM/geometrickou konstelací?
4. Je residual po MMSE/whitening lineární, nebo měřitelně vyžaduje nelineární
   predistortion/equalizer?
5. Kolik turbo iterací přináší skutečný GMI/PER zisk před bodem klesajících
   výnosů?
6. Jak často musí superburst v2 vložit plnou reacquisition kotvu při statické a
   mobilní trase?
7. Je shaping gain větší než jeho distribution-matcher a framing rate loss na
   našich typických délkách zpráv?
8. Který profil maximalizuje očekávaný wall-clock goodput, nikoliv pouze PHY
   bit rate?

## 15. Stav realizace v Guardian G2 2.3.3

Plán byl v 2.3.3 převeden do samostatně volitelných modulů bez změny ověřených
řídicích rámců. Implementováno je:

- společná šířková továrna 1K2/2K7/5K/10K/20K pro SC-HS, SC-FTN,
  SC-FDE-FTN a SEFDM včetně fractional timing;
- jemný MCS žebřík přes 8-PSK, obdélníkové QAM, APSK mezistupně, GQAM a
  1024-QAM, přičemž původní wire ID zůstala beze změny;
- skutečný constant-composition PAS64 jako MCS20 (86 bitů / 16 symbolů), ne
  pouhé přejmenování geometrické konstelace;
- MMSE/IRLS, FTN noise covariance/whitening, condition/noise-gain/residual-ISI
  metriky, overlap-save SC-FDE, reliability-gated korekce a CRC-gated bounded
  LDPC feedback;
- oprava SC edge guardu z 8 na 48 symbolů pro 81tapový ekvalizér;
- systematické sparse-accumulate LDPC 1/2, 3/4 a 9/10, rate-compatible soft
  HARQ kombinace a cache svázaná se session/codeword identitou;
- opt-in superframe v3 až pro 63 samostatně FEC/CRC chráněných bloků pod jednou
  fyzickou synchronizací a jedním ACK, adaptivní délka trainu a adaptivní MCS;
- šířkově vědomý AutoTune protokol v2, raw WAV evidence a deterministický FM
  capacity-report export.

Nelineární predistortion zůstává záměrně vypnutá, dokud uložené radio captures
neprokážou stabilní nelineární residual; slepé zapnutí by mohlo zhoršit RF masku.
Stejně tak se žádný profil neoznačuje za provozně lepší než VARA bez stejného
payloadu, rádia, zdvihu a wall-clock A/B testu. Tato dvě omezení jsou součástí
vědeckého acceptance, nikoli chybějící skrytá volba.

## Reference

- SC-FDE a power-backoff/OFDM srovnání:
  <https://www.ieee802.org/16/tg3/contrib/802163c-01_58.pdf>
- FTN, MMSE-FDE, whitening a iterativní detekce:
  <https://arxiv.org/abs/1604.03698>
- High-order QAM FTN a ADMM sekvenční detekce:
  <https://arxiv.org/abs/2107.00805>
- Reliability/sparsity řízené DFE:
  <https://arxiv.org/abs/1103.5542>
- Turbo equalizace:
  <https://www2.ensc.sfu.ca/people/faculty/cavers/ENSC805/readings/50comm05-tuchler.pdf>
- Probabilistic shaping experiment:
  <https://arxiv.org/abs/1509.08836>
- AFDM pro doubly-dispersive kanály:
  <https://arxiv.org/abs/2104.11331>
- Volterra equalizace nelineárních kanálů:
  <https://doi.org/10.1109/78.815489>
