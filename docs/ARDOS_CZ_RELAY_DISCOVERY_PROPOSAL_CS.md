# Návrh oznámení ARDOS CZ brány po RF

Stav: návrh k diskusi, **neimplementováno**. Nezavádí nové rámce, změny
LINK_ADVERT ani automatické serverové trasy. Samotné předání již přijaté RF
zprávy autorizovaným relayem serveru je samostatná implementovaná změna.

## Doporučení

Relay má oznamovat krátkou schopnost „jsem dostupná brána do ARDOS CZ“,
nikoli seznam všech online serverových stanic. To umožní A bez internetu
vybrat R jako kandidáta pro cílovou značku B, kterou po RF přímo neslyší.
Oznámení ještě neslibuje, že B existuje, je online nebo zprávu převezme.

Nezapisovat virtuální sousedství R–B do LINK_ADVERT: smíchalo by skutečnou
RF topologii s dočasnou přítomností v jiné síti. Počet rámců by navíc rostl
s počtem připojených uživatelů a rozesílal jejich seznam všem posluchačům.

## Malý capability rámec

Navrhovaná pole: verze, značka relaye, stabilní identifikátor serverové sítě,
příznak RF→server, pořadové číslo a krátká doba platnosti. Identifikátor nesmí
obsahovat přihlašovací údaje, privátní HTTPS adresu ani seznam stanic.
Konkrétní kódování a typ rámce je potřeba přidělit při budoucí změně protokolu.

Oznamovat jen při zapnutém automatickém relayi, povoleném serverovém přenosu,
platné autentizaci, oprávnění `bridge` a čerstvém serverovém heartbeatu.
Vyslat při změně stavu a poté nejvýše zhruba jednou za 60 sekund s jitterem;
příjemci údaj zahodí do 90 sekund bez obnovy. Jde o návrhové hodnoty k měření
obsazení kanálu, nikoli stávající protokolové konstanty. Při odpojení poslat
best-effort odvolání; bezpečné zapomenutí musí fungovat i bez něj.

Výchozí rozsah je jeden RF hop. Případné šíření dál vyžaduje oddělený TTL,
deduplikaci a připočítávání RF hopů; žádné nekonečné opakování cizích oznámení.
Zachovat zpětnou kompatibilitu: starší klient neznámý typ bezpečně ignoruje.

## Zjištění konkrétního cíle na vyžádání

Před odesláním velkého bundlu může A zaslat R omezený dotaz na B. R ověří
jediný cíl přes serverové availability API a vrátí krátkodobou nabídku cesty:
online / nedostupný / stav neznámý. Odpověď svázat s dotazem, cílem a ID brány.
Veřejná RF odpověď nemusí rozlišovat neexistující značku od offline zařízení.
Nezavádět serverový endpoint pro kompletní adresář.

Keš úspěšného výsledku nejvýše 15–30 sekund; negativního krátce. Sloučit
souběžné dotazy na stejný cíl, omezit dotazy podle původce i celkově a odpovědět
nejvýše jednou na požadavek. U více bran použít jitter a vybrat jednu čerstvou
nabídku podle počtu RF hopů a kvality linky. Ostatním neposílat kopie bundlu.

Při převzetí zprávy musí R serverovou dostupnost a oprávnění ověřit znovu:
mezi nabídkou a uploadem mohl B zmizet nebo být odvolán grant. Offline depozit
nesmí vzniknout automaticky jen proto, že R ohlásil bránu. Stávající omezení
TTL a smyček, nejisté převzetí a RF záloha musí zůstat platné.

## Důvěra a potvrzení

RF oznámení samo není autentizovaný důkaz připojení k serveru. Lze je podvrhnout,
proto je jen vodítkem pro směrování, nikdy oprávněním nebo důkazem doručení.
Server dál ověřuje zařízení R a jeho `bridge`; značka A z RF zůstává neověřená.
Konečný `DELIVERED` smí vzniknout až po trvalém importu a ACK u B a vrací se
po uložené zpáteční RF cestě. Samotná nabídka, přenos A→R ani `accepted`
ze serveru se nesmějí zobrazovat jako doručení B.

## Budoucí ověření před implementací

- A bez účtu, R s bridge, B online: jediný RF první úsek, potom server.
- B se odpojí po nabídce: nová kontrola a řízená RF záloha, žádné falešné ACK.
- Dva relaye a staré či podvržené oznámení: jedna vybraná cesta bez smyček.
- Restart, odvolání grantu, výpadek internetu: nabídka rychle zanikne.
- Starý klient a zatížený kanál: kompatibilita, měření režie a rate limitů.

Pro současný pilot nastavit známou trasu A→R ručně. Implementace tohoto návrhu
má smysl jako další samostatný krok po ověření skutečné RF relay linky.
