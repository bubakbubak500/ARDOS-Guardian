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
Návod a veřejný kontrakt: [ARDOS CZ](ARDOS_CZ_CLIENT_CS.md).

Jde o přípravu testovací verze; vydání instalátoru a skutečné dvoustanicové RF
ověření nejsou nahrazeny softwarovými testy.
