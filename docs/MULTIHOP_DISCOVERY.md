# Vícehopové route discovery

Stav k verzi 0.6.58: **implementovány kroky 9 a 10 za dvěma nezávislými
experimentálními přepínači**. Automatické použití nalezených tras a celosíťové
`LINK_ADVERT` jsou ve výchozím stavu vypnuté. Známá síť nadále používá společnou
topologii a z ní odvozené lokální trasy; živé discovery je oddělená,
nepersistovaná a expirovatelná vrstva.

## Implementovaný rozsah 0.6.57

- samostatné rámce `MULTIHOP_RREQ=14` a `MULTIHOP_RREP=15`, takže starý
  jednoskokový `ROUTE_QUERY/ROUTE_OFFER` zůstává kompatibilní;
- expanding ring TTL 2, 4, případně 6 a 8 podle operátorského stropu;
- deduplikace podle původce, ID a cíle, včetně přijetí lepší nebo rozšířené kopie;
- deterministický jitter, pevná životnost breadcrumbů a rozpočet rámců za minutu;
- směrovaný návrat RREP po reverzních breadcrumbech a jeden omezený opakovaný
  RREP místo opakování celého floodu;
- metrika `počet hopů + kumulovaná hrubá penalizace S/N`;
- volatilní dynamické trasy s expirací, stavem selhání a výslovným schválením;
- allowlist/denylist bez tvrzení o kryptografickém ověření identity;
- podzáložka **Síť → Automatická síť** se stavem dotazů, trasami a provozními
  limity;
- realistický RF graf, na kterém každý uzel slyší jen své sousedy.

Monitorovací režim nic nevysílá. Asistovaný režim dovolí dotaz, odpověď a relay
discovery rámců, ale zdroj nezačne přes nalezenou cestu posílat payload, dokud
ji operátor neschválí. Mezilehlé uzly smějí nalezenou cestu použít pro relay jen
tehdy, když mají povolené předávání zpráv i discovery.

## Experimentální rozsah 0.6.58 — kroky 9 a 10

V **Síť → Automatická síť** jsou tři vnořené podzáložky (Vyhledání trasy,
Živá topologie, Nastavení a limity) a dva nezávislé feature flagy, oba výchozí
`false`:

| Automaticky použít trasu | LINK_ADVERT | Výsledek |
|---|---|---|
| vypnuto | vypnuto | stejné bezpečné chování jako 0.6.57 |
| zapnuto | vypnuto | RREQ/RREP trasu lze v Asistovaném režimu použít bez potvrzení |
| vypnuto | zapnuto | živý graf se regeneruje, odvozené trasy čekají na schválení |
| zapnuto | zapnuto | živý graf i čerstvé nalezené trasy mohou směrovat automaticky |

Automatické použití je účinné jen v Asistovaném režimu. Přepnutí do monitoru
okamžitě odebere automatická schválení, ale zachová skutečně ručně schválené
trasy. Selhání next hopu trasu degraduje, odebere její schválení a dovolí nejvýše
jeden nový omezený RREQ pokus; payload se nikdy neposílá floodem ani slepě.

### LINK_ADVERT a živá topologie

`LINK_ADVERT=16` oznamuje jedno čerstvé přímé pozorování:

- `source` je aktuální fyzický vysílač;
- `destination` je vlastník pozorování;
- `next_hop` je soused, kterého vlastník skutečně nedávno slyšel;
- `message_id` identifikuje jednu sadu oznámení;
- spodní půlbyte `flags` nese hrubou penalizaci kvality a horní zůstává nulový;
- TTL je skutečný rozpočet floodu.

Prázdný `next_hop` je pouze jednoskokový advert přítomnosti s TTL 1. Umožní
dvěma dosud tichým stanicím, aby se navzájem zařadily mezi slyšené sousedy;
nikdy se nevkládá do grafu a nepředává se dál. Změna množiny přímých sousedů se
oznámí hned, jinak platí nastavitelný interval nejméně jedna minuta.

Tvrzení „A slyší B“ samo dokládá jen jeden směr. Guardian vytvoří routovatelnou
vazbu `A ↔ B` teprve tehdy, když zároveň existuje čerstvé nezávislé tvrzení
„B slyší A“. Jednosměrné pozorování je viditelné v tabulce, ale Dijkstra je
nepoužije. Trasa odvozená z potvrzeného živého grafu má zdroj `link-advert` a
její expirace nepřekročí nejstarší důkaz na cestě.

Adverty používají stejný operátorský TTL, deterministický jitter, deduplikaci,
allowlist/denylist a společný limit rámců za minutu jako RREQ/RREP. Multihopové
předávání navíc vyžaduje současně povolené discovery forwarding a message relay.
Starší Guardian typ 16 zahodí a vytvoří bezpečnou mezeru v grafu. Vypnutí
LINK_ADVERT smaže pouze jeho volatilní pozorování a odvozené trasy; sestavovač,
CSV topologie, ruční trasy i RREQ/RREP cache zůstanou nedotčené.

## Proč současný `ROUTE_QUERY` nestačí

Současný dotaz je jednoskokový. Odpoví soused, který už má ruční/naučenou trasu,
slyší cíl přímo nebo je sám cílem. Dotaz se dál nešíří. To je úsporné a bezpečné
na sdíleném kanálu, ale neumí od nuly objevit cestu:

```text
S6 -> N1 -> N2 -> N3 -> S1
```

Pro známou záchrannou nebo klubovou síť je sestavovač topologie lepší řešení:
nepotřebuje zaplavovat kanál a cesta je kontrolovatelná ještě před provozem.
Vícehopové discovery má význam pro dočasné/ad-hoc sítě, jejichž stav předem
neznáme.

## Chování RREQ/RREP

1. Zdroj vyšle `RREQ` s globálně prakticky jedinečným ID zprávy, cílem a TTL.
2. Každý uzel si na omezenou dobu zapamatuje `(message_id, destination)`,
   předchozí hop a nejlepší dosavadní metriku.
3. První přijatou nebo prokazatelně lepší kopii po krátkém deterministickém
   jitteru odvysílá s TTL−1. Ostatní kopie zahodí.
4. V 0.6.57 vytvoří `RREP` pouze skutečný cíl. Odpověď prostředníka z cache je
   záměrně vypnutá, dokud nebude existovat silnější model důvěry a zdraví trasy.
5. `RREP` nejde broadcastem přes celou síť. Vrací se po uložených reverzních
   breadcrumbech až ke zdroji.
6. Každý uzel po cestě si může krátkodobě naučit: „k cíli pokračuj přes hop,
   odkud přišel RREP“.
7. Zdroj po krátkém sběrném okně zvolí nabídku podle metriky a zahájí běžný
   `HAVE_MSG`/VARA přenos. Samotný payload se nikdy neflooduje.

## Co nese protokol

Minimálně:

- příznak, že jde o vícehopový dotaz, aby nová stanice nezaplavila starý
  jednoskokový `ROUTE_QUERY`;
- ID dotazu / zprávy a konečný cíl;
- TTL a počet hopů;
- volitelnou kumulativní metriku (cena linky, nikoliv pouze S/N posledního
  příjmu);
- u odpovědi identitu cíle a metriku celé nabízené cesty.

RREQ používá `source` jako aktuálního vysílače, `destination` jako hledaný cíl a
`next_hop` jako původce dotazu. RREP používá `source` jako aktuálního vysílače,
`destination` jako původce a `next_hop` jako bezprostřední bod reverzní cesty.
`message_id` je ID dotazu. U těchto dvou typů nese horní půlbyte `flags` počet
hopů a dolní půlbyte kumulovanou hrubou penalizaci kvality; běžné message flags
se tím nemění. TTL zůstává skutečným rozpočtem relaye.

Formát a protokolová verze zůstávají 1, ale nové typy 14/15 starší verze nezná,
takže je zahodí a nikdy samy nezačnou floodovat. Smíšená síť proto bezpečně
vytvoří mezeru v cestě místo nekontrolovaného provozu.

## Mantinely proti zahlcení

- výchozí discovery TTL 4, operátorský strop 8;
- jeden relay každého ID, případně jedna prokazatelně lepší kopie;
- cache ID alespoň po celou maximální dobu discovery;
- náhodný/deterministický jitter před relayem a potlačení při zaslechnutí lepší
  kopie;
- nejvýše jeden aktivní dotaz na cíl od jedné stanice;
- globální časový a vysílací limit za minutu;
- žádné automatické opakování po vyčerpání rozpočtu bez nové zprávy nebo zásahu
  operátora;
- RREP pouze po reverzní cestě;
- přijatá nabídka nikdy nepřepíše ručně zamknutou trasu.

## Metrika a precedence

Navržené pořadí rozhodování:

1. explicitní next hop zvolený operátorem;
2. ruční zamknutý override;
3. čerstvý přímo slyšený cíl;
4. operátorem schválená a dosud živá RREQ/RREP trasa;
5. použitelná trasa ze společné topologie;
6. nedávno úspěšná naučená cesta;
7. jednoskokový fallback nebo řízené selhání.

Schválená živá trasa topologii nepřepisuje; pouze ji po dobu své expirace
dočasně předchází. Bez schválení zůstává společná topologie cold-start plánem.

Metrika celé cesty nemá být prostým S/N posledního hopu. Praktický základ je
`součet ceny linek + penalizace za hop + penalizace za stáří/selhání`. Nouzové
priority nesmějí automaticky snížit bezpečnostní limity airtime.

## Smyčky, restarty a potvrzení

- TTL a deduplikace ukončí broadcastovou smyčku.
- Naučená cesta musí mít expiraci a po restartu se nesmí bez ověření považovat
  za živou.
- Reverzní breadcrumb pro RREP musí mít pevnou životnost a vazbu na cíl.
- End-to-end `DELIVERED` může využít stejný princip směrovaného návratu, který
  Guardian 0.6.55 používá pro skutečné potvrzení doručení. Discovery cache a
  delivery receipt cache ale musí zůstat oddělené.

## Bezpečnost a důvěra

Současný protokol neověřuje kryptograficky identitu uzlu. Cizí stanice může
teoreticky nabídnout atraktivní falešnou trasu nebo sousedské tvrzení. Proto je
automatika označena jako experimentální, výchozí stav je vypnutý a před on-air
zapnutím je potřeba využít alespoň:

- allowlist/denylist relay stanic;
- možnost zakázat dynamickou nabídku pro vybrané skupiny/cíle;
- zřetelný log původu a metriky vybrané nabídky;
- operátorský režim „jen zobrazit, nepoužít“;
- pozdější návrh autentizace nezávislý na rezervovaném příznaku `ENCRYPTED`.

## Ověření implementace

Automatické testy používají RF graf S6–N1–N2–N3–S1 s větvemi S2/S3/S4/S5,
úmyslnou smyčku, ztracený první RREQ i RREP, duplikáty, stejné ID od dvou zdrojů,
starší nepodporující uzel, allow/deny pravidla a rozpočet vysílání. Samostatné
end-to-end testy provedou existující store-and-forward payload cestu jak po
ručním schválení, tak se zapnutým automatickým použitím a vrátí finální
`DELIVERED` až k S6.

LINK_ADVERT testy pokrývají vypnutý feature flag, jednosměrné pozorování,
bootstrap úplně tiché sítě, automatickou regeneraci vícehopové cesty, větve a
smyčky, ztracený první advert, mezeru tvořenou starší verzí, konečný počet
rámců, nezávislé kombinace obou přepínačů, expiraci i odstranění pouze živé
vrstvy po vypnutí.

Časové okno v živé aplikaci se škáluje z airtime aktivního AFSK/MFSK modemu a
počtu hopů. Před běžným používáním automatických přepínačů stále zbývá skutečný
on-air test nejprve v monitorovacím, potom asistovaném a nakonec experimentálním
režimu.

## Co zůstává odložené

- kryptografické ověření identity a sousedských tvrzení;
- per-cíl trust pravidla nad rámec společného allowlistu/denylistu;
- trvalé ukládání živého grafu (záměrně se po restartu znovu ověřuje);
- sloučení živého grafu do sestavovače nebo importované topologie;
- produkční zapnutí obou experimentů ve výchozím stavu před on-air měřením.
