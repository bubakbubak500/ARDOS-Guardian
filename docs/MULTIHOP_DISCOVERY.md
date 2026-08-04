# Vícehopové route discovery

Stav k verzi 0.6.57: **implementováno ve sledovacím a asistovaném režimu**.
Automatické použití bez souhlasu operátora a celosíťové `LINK_ADVERT` jsou
záměrně odložené následné kroky. Známá síť nadále používá společnou topologii
a z ní odvozené lokální trasy; živé discovery je oddělená, expirovatelná vrstva.

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
teoreticky nabídnout atraktivní falešnou trasu. Před zapnutím automatického
vícehopového discovery je proto potřeba alespoň:

- allowlist/denylist relay stanic;
- možnost zakázat dynamickou nabídku pro vybrané skupiny/cíle;
- zřetelný log původu a metriky vybrané nabídky;
- operátorský režim „jen zobrazit, nepoužít“;
- pozdější návrh autentizace nezávislý na rezervovaném příznaku `ENCRYPTED`.

## Ověření implementace

Automatické testy používají RF graf S6–N1–N2–N3–S1 s větvemi S2/S3/S4/S5,
úmyslnou smyčku, ztracený první RREQ i RREP, duplikáty, stejné ID od dvou zdrojů,
starší nepodporující uzel, allow/deny pravidla a rozpočet vysílání. Samostatný
end-to-end test po schválení provede existující store-and-forward payload cestu
a vrátí finální `DELIVERED` až k S6.

Časové okno v živé aplikaci se škáluje z airtime aktivního AFSK/MFSK modemu a
počtu hopů. Před případným automatickým režimem stále zbývá skutečný on-air test
nejprve v monitorovacím a potom asistovaném režimu.

## Co zůstává odložené

0.6.57 nemá režim „automaticky použít každou objevenou trasu“. Tato možnost je
bod 9 a vznikne až po provozním ověření. RREQ/RREP také hledá cestu jen ke
známému cíli; samo neobjeví stanici, na kterou se nikdo nezeptá. Bod 10 proto
počítá s omezeným `LINK_ADVERT` nebo obdobnou výměnou potvrzených sousedů pro
skutečnou regeneraci celé topologie. Ani budoucí živá vrstva nesmí bez výslovné
akce přepsat ruční či importovanou topologii.
