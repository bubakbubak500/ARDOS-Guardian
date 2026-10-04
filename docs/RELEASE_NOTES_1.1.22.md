# Guardian 1.1.22 — RF relay přes ARDOS CZ

Relay nyní umí předat přijatou RF zprávu online adresátovi přes ARDOS CZ.
Původní odesílatel nemusí mít serverový účet ani internetové připojení.

- V režimu **Přednostně server, jinak RF** má u přeposílané zprávy přednost
  server i před známou RF trasou. Po dobu ověřování/převzetí se další RF přenos
  obsahu potlačí. Offline cíl nebo odmítnutí uvolní RF zálohu; neznámý výsledek
  uploadu se nejprve ověřuje, nejvýše 90 sekund bez rozhodnutí.
- Konečné **DELIVERED** se vrací původní RF cestou až po trvalém importu a ACK
  adresáta. Serverové převzetí samo neznamená doručení. Návrat funguje i přes
  druhé rádio a čekající potvrzení přežije restart.
- Pozdní serverové potvrzení zastaví RF zálohu včetně zprávy, jejíž komprese
  teprve dobíhá. Přechodné zamčení indexu ve Windows má omezené opakování;
  trvalá chyba zápisu nevede k falešnému potvrzení.
- Příjemce vidí autentizovaný relay a informaci, že původní značka z RF není
  serverem ověřena. Serverová oprávnění relaye a adresáta se kontrolují odděleně.
- Aktualizovaný soukromý server vydává nové jednorázové pozvánky na **24 hodin**.
  Platnost dříve vydaných kódů a staničních grantů se nemění.

## Nastavení relaye

Aktualizujte relay na 1.1.22, zapněte automatický relay a preferenci serveru.
Správce musí přidělit `connect` a `bridge` a vytvořit **novou pozvánku** pro
registraci zařízení; změna grantu sama práva existujícího zařízení nerozšíří.
Adresát potřebuje `connect`, `receive` a být online na stejném serveru.
Pro vlastní odesílání relaye navíc přidělte `deposit`.

Odesílatel musí znát RF trasu k relayi. Automatické oznámení serverové brány je
zatím pouze [návrh](https://github.com/bubakbubak500/ARDOS-Guardian/blob/v1.1.22/docs/ARDOS_CZ_RELAY_DISCOVERY_PROPOSAL_CS.md).
Podrobnosti: [návod ARDOS CZ](https://github.com/bubakbubak500/ARDOS-Guardian/blob/v1.1.22/docs/ARDOS_CZ_CLIENT_CS.md).

Windows x64: instalátor a přenosný ZIP. Klient nepotřebuje Python ani Docker;
pro soukromý pilot potřebuje Tailscale a přístup do správného tailnetu.
Server a administrace zůstávají v samostatném privátním repozitáři.

Ověřeno 116 cílenými klientskými testy, 19 serverovými testy na PostgreSQL
a provozním testem přes skutečné HTTPS/Tailscale. RF úsek v těchto testech byl
simulovaný; fyzický radiový pilot tím není nahrazen. Vydávací workflow navíc
spouští celou testovací sadu a kontroluje sestavené EXE.
