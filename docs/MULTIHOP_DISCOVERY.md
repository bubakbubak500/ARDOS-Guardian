# Teoretický návrh vícehopového route discovery

Stav k verzi 0.6.55: **návrh, nikoliv implementace**. Guardian 0.6.55 používá
pro známou síť společnou topologii a z ní odvozené lokální trasy. Rádiové rámce
zůstávají beze změny.

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

## Navrhované chování RREQ/RREP

1. Zdroj vyšle `RREQ` s globálně prakticky jedinečným ID zprávy, cílem a TTL.
2. Každý uzel si na omezenou dobu zapamatuje `(message_id, destination)`,
   předchozí hop a nejlepší dosavadní metriku.
3. První přijatou nebo prokazatelně lepší kopii po krátkém deterministickém
   jitteru odvysílá s TTL−1. Ostatní kopie zahodí.
4. Cíl nebo uzel s věrohodnou trasou vytvoří `RREP`.
5. `RREP` nejde broadcastem přes celou síť. Vrací se po uložených reverzních
   breadcrumbech až ke zdroji.
6. Každý uzel po cestě si může krátkodobě naučit: „k cíli pokračuj přes hop,
   odkud přišel RREP“.
7. Zdroj po krátkém sběrném okně zvolí nabídku podle metriky a zahájí běžný
   `HAVE_MSG`/VARA přenos. Samotný payload se nikdy neflooduje.

## Co musí nést protokol

Minimálně:

- příznak, že jde o vícehopový dotaz, aby nová stanice nezaplavila starý
  jednoskokový `ROUTE_QUERY`;
- ID dotazu / zprávy a konečný cíl;
- TTL a počet hopů;
- volitelnou kumulativní metriku (cena linky, nikoliv pouze S/N posledního
  příjmu);
- u odpovědi identitu cíle a metriku celé nabízené cesty.

Současný rámec už má `message_id`, `destination`, `next_hop` a TTL. Základní
prototyp by tedy mohl použít nové rezervované flagové znaménko a lokální tabulku
reverzních breadcrumbů bez zvětšení rámce. Pro kvalitní metriku a jasnou
interoperabilitu je ale čistší nová verze protokolu nebo nové typy rámců. Starší
stanice musí neznámý režim ignorovat, nikdy jej začít samy relayovat.

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
4. použitelná trasa ze společné topologie;
5. nedávno úspěšná naučená cesta;
6. vícehopový RREQ/RREP výsledek;
7. jednoskokový fallback nebo řízené selhání.

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
teoreticky nabídnout atraktivní falešnou trasu. Před zapnutím automatického
vícehopového discovery je proto potřeba alespoň:

- allowlist/denylist relay stanic;
- možnost zakázat dynamickou nabídku pro vybrané skupiny/cíle;
- zřetelný log původu a metriky vybrané nabídky;
- operátorský režim „jen zobrazit, nepoužít“;
- pozdější návrh autentizace nezávislý na rezervovaném příznaku `ENCRYPTED`.

## Doporučené ověření před implementací

1. Simulace RF grafu, kde endpoint slyší jen své sousedy, nikoliv dnešní plně
   propojený loopback bus.
2. Řetězec S6–N1–N2–N3–S1, rozvětvení přes S2/S3/S4 a jedna úmyslná smyčka.
3. Ztracený RREQ, ztracený RREP, duplicitní rámce a současné dotazy dvou zdrojů.
4. Smíšená síť nové/staré verze.
5. Měření skutečného airtime na AFSK i MFSK před stanovením TTL a jitteru.
6. Teprve potom on-air test s nejdříve vypnutým automatickým použitím nabídky.

## Rozhodnutí pro současnou verzi

0.6.55 RREQ/RREP neimplementuje. Známá síť se importuje jako linková topologie,
každá stanice si z ní odvodí vlastní route tabulku a stávající jednoskokové
`ROUTE_QUERY/ROUTE_OFFER` zůstává kompatibilním lokálním fallbackem.
