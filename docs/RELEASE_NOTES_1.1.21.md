# Guardian 1.1.21 — ARDOS CZ, přímé stanice

První implementace volitelného serverového přenosu podle návrhu ARDOS CZ.

- Nastavení HTTPS serveru a preference přenosu, registrace zařízení pozvánkou,
  Windows Credential Manager a skutečný stav ověřené relace v Síť → ARDOS CZ.
- Asynchronní odesílání online adresátovi a samostatné **Zanechat zprávu**.
- Oddělené serverové stavy; konečné doručení až po trvalém importu adresátem.
- Opakování uploadu a ACK, kontrola konfliktů, deduplikace RF/serverových kopií,
  usmíření neznámého výsledku po výpadku a restartu.
- RF příjem potvrzuje úspěch až po místním importu; při kolizi nebo chybě zápisu
  neodesílá falešné potvrzení.

Režim je výchozí vypnutý. RF bridge cizího původu v tomto milníku není zapnutý.
Server a jeho Docker/Next.js administrace jsou v samostatném privátním repozitáři.
Návod a veřejný kontrakt: [ARDOS CZ](https://github.com/bubakbubak500/ARDOS-Guardian/blob/v1.1.21/docs/ARDOS_CZ_CLIENT_CS.md).

Pro Windows x64 je k dispozici instalátor a přenosný ZIP. Python ani Docker
se na klientské stanici neinstalují. Soukromý pilot vyžaduje přihlášený Tailscale,
pozvánku do testovací sítě, HTTPS adresu serveru a samostatný registrační kód
ARDOS CZ od správce. Každá stanice používá vlastní volací značku.

Přes místní Docker server a Tailscale bylo ověřeno doručení textu a přílohy,
offline depozit, restart serveru, opakované vyzvednutí bez duplikátu, revokace,
návrat běžného odeslání do RF cesty při výpadku API a obnova PostgreSQL zálohy.
Distribuční kontrola ověřuje Qt, kompresi, ARDOP, LAB a knihovny ARDOS CZ v EXE.
Skutečný přenos přes dvě rádia ani automatický RF bridge tímto ověřeny nejsou.
