# Guardian OFDM VHF — testy na pásmu

Čtyři testy, v tomto pořadí. Nic z toho nepotřebuje konzoli, skript ani
PowerShell — všechno je v aplikaci.

*(English version: [OFDM_AIR_TEST.md](OFDM_AIR_TEST.md))*

**Modem už vysílal.** OK7PS a OK2IPW odjeli 2026-08-09 testy 1–4 s dvojicí
IC-705 a to, co z toho vyšlo, změnilo tři čísla v tomto dokumentu a jeden jeho
předpoklad. Výsledky jsou v
[OFDM_AIR_RESULTS_2026-08-09.md](OFDM_AIR_RESULTS_2026-08-09.md); stručně:

- Zvuková cesta propouští asi do **3 kHz** a tam spadne ze skály — 14 dB na
  jedné rozteči subnosných. `BENCH` se do toho vejde, `WIDE_5K` ne a žádná
  úroveň s tím nic neudělá.
- Linka doopravdy dávala **9,7–12,0 dB** po ekvalizaci, zatímco Guardian hlásil
  18–20 dB. Staré číslo neumělo vidět zkreslení. Nové ano a dialogy ukazují obě.
- **MCS3 na téhle cestě fungovat nemůže** a MCS2 leží přesně na svém prahu. To
  je aritmetika, ne závada.

Testy přesto dělejte v pořadí. Pokud selže test 4 a testy 1–3 jste přeskočili,
nebudete vědět, zda je chyba v rádiu, ve zvukové cestě, nebo v softwaru.

---

## Než začnete

- Simplexní kanál VHF, na kterém smíte vysílat a který je volný. Ne převaděč, ne
  volací kanál, ne APRS.
- G2 2.0.4 nainstalovaný na obou stanicích.
- **Před každou relací se ohlaste hlasem.** Jde o experimentální datový signál;
  kdo ho zaslechne, nepozná ho. Řekněte, co děláte.
- Dokud hledáte správné úrovně, vysílejte krátce.

Všechno je na jednom místě:

| | |
|---|---|
| **Nástroje ▸ Test modemu ▸ Soubory** | profily, vysílání testovací dávky, nahrávání a dekódování záznamů |

Záznamy i vygenerované soubory se ukládají do
`%APPDATA%\Guardian-G2\captures\`.

### Žebřík profilů

Šest stupňů, každý přibližně dvojnásobek předchozího. Všechny stupně mají
**stejnou rozteč subnosných i stejný ochranný interval**, takže posun po žebříku
mění **pouze** šířku pásma — odolnost proti kmitočtové odchylce a proti odrazům
zůstává stejná.

| Profil | Obsazeno | Základní pásmo | Vzorkování | Rychlost QPSK |
|---|---|---|---|---|
| `NARROW_1K2` | 1,2 kHz | 539–1758 Hz | 48 kHz | 917 b/s |
| `BENCH` | 2,4 kHz | 539–2977 Hz | 48 kHz | 1 833 b/s |
| `WIDE_5K` | 4,9 kHz | 539–5414 Hz | 48 kHz | 3 708 b/s |
| `WIDE_10K` | 9,8 kHz | 539–10289 Hz | 48 kHz | 7 417 b/s |
| `WIDE_20K` | 18,8 kHz | 539–19289 Hz | 48 kHz | 14 250 b/s |
| `WIDE_40K` | 40,0 kHz | 1102–41133 Hz | **96 kHz** | 30 500 b/s |

Žádný z nich není ověřený profil pro provoz na pásmu. Existují proto, aby se
vyzkoušely.

`WIDE_40K` vyžaduje zvukovou kartu, která se otevře na 96 kHz — pokud vaše
nedokáže, žebřík končí u `WIDE_20K`.

**Rozšíření pásma není zdarma.** Stejná vysílací úroveň rozprostřená na dvojnásobek
subnosných znamená o 3 dB méně na každou z nich. Naměřeno na tomto žebříku: přechod
z `BENCH` na `WIDE_20K` stojí asi 9 dB a přinese asi osminásobnou propustnost.
Nejlepší volbou tedy není nutně nejširší profil, který se ještě dekóduje, ale
nejširší, který se dekóduje **s rezervou**.

---

# Test 1 — Ověřte software bez rádia (5 minut)

Zajistí, že cokoli uvidíte později, bude vlastnost rádia, a ne instalace.

**Nástroje ▸ Test modemu.** Profil `BENCH`, MCS1.

**Uložte testovací soubor k vysílání** a hned ho **dekódujte zpět**. Tím na
jednom počítači ověříte celou cestu od vygenerování k dekódování, ještě než do ní
vstoupí rádio.

Pokud cokoli z toho neprojde, problém není ve vašem rádiu.

---

# Test 2 — Jakou šířku pásma vaše rádio skutečně propustí?

**Tohle je ten důležitý test.** Jedno rádio vysílá, druhá stanice nahrává. Přijímač
Guardianu do toho vůbec nevstupuje, takže výsledek nemůže zkreslit softwarová
chyba na druhé straně.

O odpovědi nerozhoduje kanálový rastr rádia, ale **šířka pásma, kterou propustí
jeho přijímací zvuková cesta** — a ta se u téhož rádia liší podle toho, odkud zvuk
odebíráte.

### Postup

**Stanice A:**
1. Nalaďte rádio na testovací kanál se svým běžným datovým zdvihem. Zapište si
   nastavení úrovně. Ohlaste se hlasem.
2. Nástroje ▸ Test modemu → profil `BENCH`, MCS1 → **Vysílat do rádia**,
   3 opakování. Guardian sám naklíčuje rádio a přehraje dávku do nastaveného
   výstupu TX; předem vám řekne, jak dlouho bude vysílat, a vyžádá si potvrzení.
   Tři dávky z jednoho vysílání znamenají tři nezávislá měření.

   (*Uložit soubor k vysílání* tam zůstává, pokud ten WAV potřebujete k něčemu
   jinému, ale pro tento test ho nepotřebujete.)

**Stanice B:**
3. V **Nástroje ▸ Test modemu ▸ Soubory** zvolte stejný profil a spusťte
   nahrávání **dřív**, než stanice A začne vysílat. Po vysílání nahrávání
   ukončete. Guardian záznam automaticky dekóduje zvoleným profilem.
4. Přečtěte si verdikt. **Pokud hlásí ticho nebo přebuzení, nejdřív to opravte a
   test zopakujte** — jinak je vše ostatní bezcenné.

### Potom stoupejte po žebříku

Zopakujte s `WIDE_5K`, `WIDE_10K`, `WIDE_20K` (a `WIDE_40K`, pokud vaše karta umí
96 kHz). Poznamenejte si, kde se přestane dekódovat.

U dvojice IC-705 to skončilo hned: `WIDE_5K` ukázal 42–53 dB rozptylu kanálu,
protože všechno, co dá nad 3,2 kHz, leží 20–45 dB dole. Pokud vaše rádio dělá
totéž, žebřík končí a vaším profilem je `BENCH` — zabírá 539–2977 Hz, což je
skoro přesně to, co tahle cesta propustí.

**Potom rozmítání zdvihu, což je ta část, která se vynechala a která je
nejdůležitější.** Vraťte se na `BENCH` MCS1 a zopakujte ho při třech úrovních
buzení — běžné, výrazně nižší, výrazně vyšší — a pokaždé si zapište **rozdíl
mezi oběma SNR**. Ten rozdíl je zkreslení, je to těch 8 dB, které stojí mezi
touhle linkou a MCS2, a úroveň buzení je nejpravděpodobnější věc, která ho řídí.
Nic jiného v tomhle dokumentu nepřinese tolik.

### Dvě SNR a proč dialog ukazuje obě

Tohle je to nejdůležitější, co první testy na pásmu naučily.

**Odstup linky (co modem dostal)** se měří tak, že se z toho, co burst
dekódoval, zase spočítá vysílaný signál a porovná se s tím, co vyšlo z
ekvalizéru. Počítá se do něj všechno: šum, zkreslení, chyba odhadu kanálu,
ztráty samotného ekvalizéru. **Tohle je číslo, které předpovídá, jestli režim
bude fungovat.**

**Odstup jen vůči šumu** vychází ze dvou trénovacích symbolů na začátku burstu.
Jsou identické, takže jejich rozdíl nechá jen náhodný šum — a vyruší, přesně,
každé zhoršení, které je v obou stejné. Každé deterministické zkreslení, které
rádio přidá, je v obou stejné. 2026-08-09 hlásil 18–20 dB na lince, která
doopravdy dávala 9,7–12,0 dB.

Ani jedno není špatně. Měří různé věci a **rozdíl mezi nimi je zkreslení ve vaší
cestě**. Rozdíl kolem 3 dB je vlastní ztráta přijímače a lepší to nebude. Rozdíl
8 dB, jaký ukázala tahle rádia, znamená, že většina toho, co lince škodí, není
šum — a přidat výkon s tím nehne.

### Ke každému záznamu si zapište

Profil · zdvih · spolehlivost synchronizace · **odstup linky** · odstup jen vůči
šumu · EVM · kmitočtovou odchylku (CFO) · rozptyl kmitočtové charakteristiky ·
PASS/FAIL.

| Hodnota | V pořádku | Když není |
|---|---|---|
| no burst detected | — | Burst v souboru není, je příliš slabý, nebo nesouhlasí vzorkovací kmitočet. Nejdřív zkontrolujte úroveň. |
| spolehlivost synchronizace | > 0,7 | Burst je blízko šumu. Přidejte úroveň, nebo zvolte užší profil. |
| **odstup linky** | viz tabulka režimů níže | Podle tohohle se rozhoduje. |
| rozdíl proti odstupu vůči šumu | ~3 dB | 8 dB je zkreslení, ne šum. Měňte buzení, ne výkon. |
| EVM | < 25 % | Nad tím jsou bezpečné jen MCS0/MCS1. |
| CFO | u FM blízko 0 | Velká odchylka u FM je nečekaná, napište mi. U SSB jde o rozdíl naladění a je normální. |
| rozptyl charakteristiky | < 10 dB | **Tohle je to zjištění.** Velký rozptyl je zvukový filtr rádia — hranice toho, co propustí. `WIDE_5K` ukázal na IC-705 42–53 dB, což je ten filtr, ne linka. |

### Co který režim potřebuje

Naměřeno rozmítáním simulátoru kanálu po 1 dB, osm bloků po 512 B na krok, se
čtením téhož **odstupu linky**, jaký přijímač hlásí na pásmu.

| Režim | | Potřebný odstup linky | Na cestě IC-705 z 2026-08-09 (9,7–12,0 dB) |
|---|---|---|---|
| MCS0 | BPSK r=1/2 | ≤ 3 dB | pohodlně |
| MCS1 | QPSK r=1/2 | ≤ 3 dB | pohodlně — **tenhle používejte** |
| MCS2 | 16-QAM r=1/2 | 10,5 dB | na prahu; dekóduje se, spolehlivé nebude |
| MCS3 | 64-QAM r=1/2 | 15,5 dB | chybí 5 dB — nikdy se nedekódoval a ani nemohl |

Guardian to teď řekne za vás: analyzujte libovolný záznam a řádek **Co tento
odstup unese** pojmenuje nejrychlejší režim, který linka udrží, a — když burst
selhal — o kolik chybělo použitému režimu.

Z rozptylu kmitočtové charakteristiky se odvozuje skutečný profil pro pásmo.
Pošlete mi záznamy a odvodíme ho společně; sada subnosných má vzejít z měření, ne
z odhadu.

---

# Test 3 — Živý poloduplexní přenos

Teprve teď zapněte celou cestu na obou stanicích, s profilem, který se v testu 2
ukázal jako pohodlný.

Obě stanice: Nastavení ▸ Přenos a datový modem → *Guardian OFDM VHF
(Experimentální)*, stejný profil, stejné MCS. Odešlete krátkou zprávu — několik set
bajtů, ne přílohu.

Tím se prověří zvuková cesta v reálu. Počítejte s tím, že budete ladit tohle,
všechno na téže stránce nastavení:

| Příznak | Změňte | Kam |
|---|---|---|
| Nic se nedekóduje, protistanice nikdy neodpoví | Předstih klíčování | Nahoru — vysílač nestoupne dřív, než začne preambule |
| Bursty se dekódují, ale poslední blok zprávy selže | Doběh klíčování | Nahoru — konec burstu se odřezává |
| Bloky selhávají trvale i při dobrém odstupu linky | Modulace OFDM (MCS) | Dolů — nejdřív se podívejte do tabulky režimů výše |
| Odpovědi přicházejí, ale pozdě, a roste počet opakování | — | Napište mi; je potřeba rozšířit časový limit, není to otázka nastavení |

**Čtěte pozorně řádky „no answer" — teď říkají, o kterou závadu jde.** Před
2.0.5 vysílající stanice, která nic neslyšela, jen zapsala `no answer to
block 0`, což zakrývalo dva úplně jiné problémy. Teď napíše jedno z:

- `nothing heard (squelch floor -52 dBFS, opens at -42 dBFS)` — nic se nikdy
  nedostalo nad šumovou bránu. Závada v úrovni příjmu, klíčování nebo kabeláži,
  nebo protistanice vůbec neodpověděla. Které to je, poznáte z těch dvou
  úrovní: práh hodně nad skutečným šumem znamená, že problém je brána, ne rádio.
- `2 burst(s) heard, none usable: header rejected: ...` — odpověď dorazila a
  modem ji nepřečetl. Problém kvality signálu.

Jedna věc, která vypadala jako selhání a nebyla: pokud protistanice ukazuje
zprávu přijatou a doručenou, zatímco vy ukazujete selhání, ztratilo se poslední
potvrzení. Přijímač teď po dokončení zprávy podrží kanál po dobu jednoho okna
pro odpověď a na opakované vysílání odpoví znovu, takže ztracené poslední
potvrzení stojí jeden burst navíc místo celého přenosu.

Potom odešlete zprávu s malou přílohou, aby se prověřilo dělení na bloky a jejich
skládání na více než dvou blocích.

**Uložte si protokol (log) z celé relace** — řádky ze zdrojů `payload` a `control`
nesou SNR a EVM každého bloku, počty opakování a důvod každého selhání.

---

# Test 4 — Celá cesta a záložní režim

Dvě části, obě krátké, obě dokazují něco konkrétního.

**4a — skutečná zpráva od začátku do konce.** Napište v Poště zprávu s přílohou a
odešlete ji druhé stanici přes OFDM. Sledujte, jak dojde, jak je potvrzena a jak se
zobrazí jako doručená. Tohle je celý zásobník: řídicí navázání ARDOS, dohoda o
způsobu přenosu, vlastní přenos OFDM a doručenka. Poznamenejte si naměřenou
propustnost a srovnejte ji s hodnotou PHY daného profilu — rozdíl tvoří preambule,
hlavička, potvrzení a překlápění PTT, a je skutečný.

**4b — bezpečnostní vlastnost.** Přepněte **jednu** stanici zpět na *Guardian VARA
P2P*, druhou nechte na OFDM a odešlete zprávu. Přenos musí projít **přes VARA** a
protokol to má uvést. Tím je dokázáno, že se stanici nikdy nepřehraje signál, na
který neposlouchá: obě strany musí OFDM potvrdit nezávisle, jinak se pár vrátí k
VARA ještě před tím, než se cokoli vyšle.

Pokud se v testu 4b k VARA **nevrátí**, přestaňte — to je chyba v protokolu a
potřebuji o ní vědět okamžitě.

---

# Co mi poslat

## 1. Záznamy — mají větší cenu než všechno ostatní dohromady

Vše z `%APPDATA%\Guardian-G2\captures\` a k tomu poznámku, co který časový údaj
znamená (profil, zdvih, který test).

Proč mají větší cenu než jakýkoli protokol: se záznamem mohu spustit **celý
přijímač** přesně nad tím, co vaše rádio vyprodukovalo, kolikrát chci, a se
změnami. Protokol mi řekne, jak to skončilo; záznam mi umožní to spravit. Squelch,
časování burstu, profil pro pásmo, ekvalizér — všechno se dá vyvíjet nad záznamem,
aniž byste znovu museli na pásmo.

**Nezkracujte je, nenormalizujte, nepotlačujte šum a nepřevádějte na MP3.** Ticho
před burstem a za ním je také informace — je to šumové pozadí, proti kterému se
squelch měří — a ztrátová komprese zničí fázové vztahy, na kterých modem stojí.
Guardian nic z toho záměrně nedělá, takže soubor, který sám zapsal, je už v
podobě, kterou potřebuji.

## 2. Protokol Guardianu

Z pracovní plochy Protokol nebo z `%APPDATA%\Guardian-G2\`. Celou relaci, včetně
selhání — zejména selhání.

## 3. Údaje o stanicích

Model rádia na každé straně · zvukový interfejs (AIOC, digirig, zvuková karta,
kabel do mikrofonního vstupu) · čím se klíčuje (CAT/Hamlib, VOX, sériová linka) ·
kmitočet a druh provozu · nastavení šířky kanálu, pokud ho rádio má · zdvih /
zesílení mikrofonu / úroveň příjmu a na co jste je měnili · přibližná vzdálenost a
zda byla přímá viditelnost.

## 4. Vaši tabulku z testu 2

Hodnoty ke každému záznamu. Pokud máte jen čísla a ne WAV soubory, pošlete čísla —
ale jednat mi umožní právě ty WAV soubory.

## 5. Co vás překvapilo

Cokoli, co se chovalo jinak, než popisuje tento dokument, trvalo delší dobu, než
mělo, nebo vás nechalo na pochybách, co software právě dělá. Poslední kategorie je
nejužitečnější a hlásí se nejméně často.

---

## Jak to poslat

Záznamy zabalte do ZIPu. Pokud je archiv příliš velký, pošlete nejdřív záznamy
z testu 2 — jeden čistý záznam se známou úrovní má větší cenu než deset
nepopsaných.
