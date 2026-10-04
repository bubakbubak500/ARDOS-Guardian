# Guardian 1.1.19 — produkční LAB

Nový LAB spouští dvě izolované stanice ze stejné aktuální aplikace Guardian 1.
Měří běžné odesílání zpráv včetně komprese, řídicích rámců, vyjednání,
datového modemu a konečného potvrzení. Nemá vlastní implementaci protokolu
ani tabulky SC-FTN; starý LAB z G2 není součástí této cesty.

- **Nástroje → Produkční LAB (dvě stanice)** otevře živé místní webové UI.
- Série SC-FTN, VARA FM/HF a interního ARDOP 500 Hz; samostatné směry,
  opakování, nové nebo zachované stanice, běžné profily a vlastní přílohy.
- Icom/Icom, AIOC/AIOC i kombinovaná sestava; nezávislé zvukové a PTT cesty.
- Dvě soukromé kopie VARA s vlastními licencemi, ověřeným shodným EXE,
  odlišnými TCP porty a kontrolou vlastníka portů. Licenční kódy se neexportují.
- Kontrola shody kódu a runtime, požadovaného versus skutečného modemu,
  stavu DELIVERED a obsahu příloh. Změna revize měření zneplatní.
- Živé stavy a události, rychlost celé zprávy i datové fáze, export JSON/CSV/ZIP
  s konfigurací, identitou sestavení a kontrolními součty.
- Stejné ovládání přes místní autentizované HTTP API a `Guardian.exe --lab`.
- Vydávací kontrola spouští z hotového EXE dva procesy a ověřuje izolaci,
  shodu sestavení i dostupnost všech produkčních backendů a řídicích modemů.

Návod, architektura, API a pravidla údržby:
[Guardian LAB](https://github.com/bubakbubak500/ARDOS-Guardian/blob/v1.1.19/docs/GUARDIAN_LAB_CS.md).

Toto vydání nepřináší změnu řídicích rámců VARA/ARDOP ani algoritmu SC-FTN.
RF laboratoř nebyla při vývoji dostupná: softwarové a distribuční kontroly
nepředstavují kvalifikaci skutečné dvojice rádií ani potvrzení rychlostí.
Aktivované licence a konkrétní zvukové/PTT profily VARA je potřeba připravit
pro první hardwarovou kampaň. Automatický souvislý záznam PCM tato verze nepořizuje.
