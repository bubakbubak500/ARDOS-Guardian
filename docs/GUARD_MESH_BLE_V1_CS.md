# Guardian → LilyGO / Guard Mesh: BLE displej v1

Lokální Guardian 1.1.12, základ GitHub `origin/main` na commitu
`2d3ef8f708807ced7d447f544dd4976e12ecc3f9` (vydání 1.1.11 + oprava CI).

**Zdroj dat je Guardian na PC. LilyGO s Guard Mesh zobrazuje dění v Guardianu.**
Nejde o import zpráv z MeshCore do PC. Wi-Fi se nepoužívá. Implementována je
PC část; tento dokument je smlouva pro následnou úpravu firmwaru. Není to
tvrzení o kompatibilitě se současným Guard Mesh / MeshCore firmwarem.
Lua aplikace ani firmware nebyly v tomto kroku vytvořeny.

## Obsluha v Guardianu

1. Nainstalovat lokální 1.1.12 a otevřít **Provoz → Guard Mesh — BLE…**.
2. Na LilyGO s upraveným firmwarem zapnout **Nastavení → Guardian BLE**.
3. Kliknout **Vyhledat BLE**, vybrat zařízení a **Spárovat a připojit**.
   Párování zahajuje PC, bond uchovává operační systém. Potvrdit případnou
   systémovou výzvu. Obnova již existujícího bondu nemusí zobrazit výzvu.
4. Panel na PC ukazuje stav předaný LilyGO: TX, RX, inbox a nepřečtené.
   Zavření panelu nechá sdílení běžet; **Odpojit / zrušit** nebo ukončení
   Guardianu spojení ukončí. Po výpadku se připojení obnovuje ručně.
5. Odstranění uloženého párování se provádí v nastavení Bluetooth Windows
   a případně v nabídce firmwaru pro smazání bondu.

První verze přenáší **počty** zpráv, nikoli předměty, odesílatele, těla nebo
přílohy. Zobrazení seznamu zpráv a ovládání Guardianu z LilyGO jsou další etapa.

## BLE role a GATT

- **Guardian PC:** BLE Central / GATT Client. Hledá, páruje a zapisuje stav.
- **LilyGO:** BLE Peripheral / GATT Server. Inzeruje službu a přijímá stav.
- PC filtruje podle UUID služby, nikoli podle názvu. Doporučený název zařízení
  je `GuardMesh-XXXX`, kde XXXX umožní uživateli odlišit vlastní zařízení.
- Firmware vyžaduje šifrované spojení a bonding pro obě charakteristiky.
  Párovací režim se zapíná fyzicky na zařízení; mimo něj přijímá jen známý bond.
  Bleak 3.0.2 ve Windows používá `ConfirmOnly` (Just Works); tento první klient
  nemá vlastní zadávání PIN ani numeric comparison. Pro párování přímo z tohoto
  panelu FW poskytne Just Works pouze ve fyzicky zapnutém párovacím režimu.
  Just Works šifruje link, ale neověřuje protistranu pomocí PIN. Pokud FW vyžaduje
  PIN, je nutné nejprve vytvořit bond přes nastavení Bluetooth Windows a pak
  se připojit z Guardianu. Obojí ověřit na konkrétním HW.
- Pouze jeden PC klient současně. GATT callback předá validovaný stav do
  fronty/UI modelu FW; nesmí blokovat mesh rádio ani kreslit přímo v BLE callbacku.

| Prvek | UUID | Vlastnosti | Směr |
|---|---|---|---|
| Služba Guardian Display | `f3641400-b000-4042-ba50-05ca45bf8abc` | Primary service; UUID v advertisingu | — |
| Guardian Status | `f3641401-b000-4042-ba50-05ca45bf8abc` | **Write with response**, encrypted | PC → LilyGO |
| Protocol | `f3641402-b000-4042-ba50-05ca45bf8abc` | **Read**, encrypted | Identifikace rozhraní |

Protocol vrací přesně `47 4D 01 00` (ASCII GM, major 1, minor 0). Guardian
ověří službu, vlastnosti a tyto 4 bajty ještě před prvním zápisem stavu.
Odlišná verze se odmítne. Není potřeba Notify, CCCD, Wi-Fi ani síťový server.

## Stavový paket (20 bajtů, little-endian)

Celý paket se vejde do jednoho ATT Write i při výchozím MTU 23.
Každý paket je úplný snímek; žádné fragmentování ani skládání JSON.

| Offset | Délka | Pole | Význam |
|---|---:|---|---|
| 0 | 2 | magic | ASCII `GM` (`47 4D`) |
| 2 | 1 | version | `01` |
| 3 | 1 | flags | viz níže |
| 4 | 4 | inbox | Celkový počet zpráv v inboxu Guardianu |
| 8 | 4 | unread | Počet nepřečtených zpráv v inboxu Guardianu |
| 12 | 4 | outbox | Počet zpráv v outboxu, včetně čekajících a neúspěšných |
| 16 | 4 | sequence | Pořadí zápisu od 0, modulo 2^32; při novém spojení od 0 |

Flags:

- bit 0 (`01`): Guardian běží a má aktuální místní snímek; vždy 1.
- bit 1 (`02`): Guardian zahajuje/vede odesílání zprávy (STARTING_VARA/TRANSFERRING).
- bit 2 (`04`): Guardian přijímá nebo čeká na payload v aktivní příjmové relaci (RECEIVING).
- bit 3 (`08`): nejméně jedno rádio hlásí připojení CAT.
- bit 4 (`10`): nejméně jeden příkazový port VARA je připojen.
- bit 5 (`20`): nejméně jeden řídicí kanál Guardianu je aktivní.
- bity 6–7: rezervované, musejí být 0.

TX/RX jsou fáze zprávy pro VARA, SC-FTN i ARDOP, nikoli okamžitá PTT nebo
jednotlivé RF rámce. Při dvou rádiích mohou být TX i RX současně 1. Stav CAT,
VARA a řídicího kanálu je OR obou rádií; společný inbox se počítá pouze jednou.
VARA=0 neznamená chybu při provozu interního ARDOP/SC-FTN modemu.

## Aktualizace a výpadky

- PC snímá stav aplikace po 500 ms. Změny předává nejvýše dvakrát za sekundu,
  beze změn posílá heartbeat každých 5 s. Kratší přechodné fáze se mohou vynechat.
- Všechny zápisy mají ATT odpověď (`response=True`), timeout zápisu 10 s.
  Úspěšná odpověď potvrzuje přijetí GATT serverem, nikoli vykreslení na displeji.
- Pokud se zdrojový snímek PC neobnoví 15 s (např. zamrzlé UI), PC přestane
  publikovat a odpojí BLE; nesmí donekonečna vysílat starý stav jako aktuální.
- **FW po 15 s bez validního paketu zobrazí „Guardian neaktuální / nedostupný“.**
  TX/RX a počty nahradí `—`. Totéž ihned při BLE disconnect. Nula by falešně
  znamenala prázdný inbox. Samotný BLE link ještě neznamená živý Guardian.
- FW resetuje čas posledního stavu a sekvenci při každém novém spojení.
  Zpožděné nebo chybné pakety nesmějí obnovit indikátor aktuálnosti.
- FW odmítne nesprávnou délku, magic, verzi, rezervované bity nebo bit 0=0
  pomocí ATT chyby a nezmění dosavadní stav. Nový validní snímek obnoví displej.

## Referenční vektor

Guardian současně TX a RX, inbox 5, nepřečtené 2, outbox 1, sekvence 7:

```text
47 4D 01 07 05 00 00 00 02 00 00 00 01 00 00 00 07 00 00 00
```

Python definice je `struct.Struct("<2sBBIIII")` v `guardian/guard_mesh.py`.
Firmware má načítat uint32 little-endian po bajtech (ne nevyrovnaným castem
ukazatele). Tento vektor je ověřen testem `test_guard_mesh.py`.

## Požadovaná obrazovka Guard Mesh

Položka **Nastavení → Guardian BLE**: zapnutí, párovací režim, stav PC spojení,
případně zapomenutí bondu. Samostatná obrazovka **Guardian**:

```text
GUARDIAN             PC připojeno
TX: Odesílání        RX: Neaktivní
Inbox: 5            Nepřečtené: 2
Outbox: 1           Stav před 1 s
```

Pro Lua variantu musí FW nejprve zpřístupnit přijatý snímek, BLE connected a
čas posledního validního paketu jako lokální API. Název tohoto Lua API zde
nezavádíme naslepo; žádné existující API Guard Mesh nebylo v tomto kroku ověřeno.
BLE parsování a kontrola zastarání patří do FW; Lua může následně kreslit UI.

## Ověření při integraci FW

Otestovat první párování, druhé připojení se stejným bondem, odmítnutí párování,
vypnutý Bluetooth, odchod z dosahu, restart PC/LilyGO, chybějící/nekompatibilní
službu, výše uvedený vektor a 15sekundové zastarání. Na skutečném Guardianu
ověřit TX, RX, souběh dvou rádií, přijetí nové zprávy a označení jako přečtené.
PC testy používají simulovaný GATT server; fyzické BLE/FW spojení zatím není ověřené.

Implementace PC používá [Bleak client API](https://bleak.readthedocs.io/en/stable/api/client.html)
a [scanner API](https://bleak.readthedocs.io/en/stable/api/scanner.html).
