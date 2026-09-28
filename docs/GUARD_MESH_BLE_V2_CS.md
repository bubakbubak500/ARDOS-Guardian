# Guard Mesh ↔ Guardian: BLE API v2

Implementováno v lokálním **Guardianu 1.1.13**. Tento dokument je definice
rozhraní pro aplikaci a firmware Guard Mesh. Neobsahuje předpoklady o názvech
Lua funkcí ve firmwaru: BLE obsluhu a předání dat do jeho UI musí doplnit FW.

## Co je k dispozici

- Průběžně: původní stav Guardianu (TX/RX, inbox, nepřečtené, outbox,
  připojení rádia/CAT, VARA a řídicího kanálu) a nově procenta TX/RX.
- Na vyžádání: úplný stav, živé i uložené kontakty, seznamy zpráv a text zprávy.
- Z Guard Mesh do Guardianu: příjemce, předmět, text a priorita nové zprávy.
  Guardian ji trvale uloží do **K odeslání**, stejně jako při psaní na PC.
  Přílohy, příkazy k připojení rádia, změny nastavení ani vzdálené spouštění
  kódu součástí API nejsou.

Na PC je párování v **Nastavení → Nastavení stanice → Guard Mesh**.
Zavření nastavení ani tlačítko Zrušit neodpojí již navázané BLE. Odpojí je
tlačítko **Odpojit / zrušit** na záložce nebo ukončení aplikace.

## Role, párování a verze

PC je BLE **Central / GATT Client**, Guard Mesh je **Peripheral / GATT Server**.
PC skenuje, zahajuje párování, připojí se a čte Protocol. FW inzeruje UUID služby.
Všechny charakteristiky níže vyžadují šifrované spojení a bond; příkazy přijímá
jen spárovaný PC. Párovací režim zapíná uživatel fyzicky na zařízení.
Windows klient Bleak 3.0.2 používá Just Works; vlastní zadávání PIN nemá.
FW s povinným PIN je třeba nejprve spárovat v nastavení Bluetooth Windows.
Just Works neposkytuje ověření protistrany pomocí PIN.

Protocol vrací přesně 4 bajty:

| Hodnota | Režim |
|---|---|
| `47 4D 01 00` | Původní v1: PC zapisuje pouze Guardian Status |
| `47 4D 02 00` | v2: stav, průběh a obousměrné požadavky |

Guardian 1.1.13 podporuje oba režimy. FW v1 tedy dále funguje beze změny.
Guardian 1.1.12 nerozumí v2; pro v2 aktualizujte i PC. Neznámá verze nebo
neúplná služba se odmítne před odesláním uživatelských dat.

## GATT tabulka

| Prvek | UUID | Vlastnosti | Data |
|---|---|---|---|
| Guardian Display | `f3641400-b000-4042-ba50-05ca45bf8abc` | Primary service + advertising | — |
| Guardian Status | `f3641401-b000-4042-ba50-05ca45bf8abc` | Write with response | PC → FW, 20 bajtů |
| Protocol | `f3641402-b000-4042-ba50-05ca45bf8abc` | Read | FW → PC, 4 bajty |
| Progress | `f3641403-b000-4042-ba50-05ca45bf8abc` | Write with response | PC → FW, 9 bajtů |
| Request | `f3641404-b000-4042-ba50-05ca45bf8abc` | Notify + CCCD | FW → PC, fragmenty JSON |
| Response | `f3641405-b000-4042-ba50-05ca45bf8abc` | Write with response | PC → FW, fragmenty JSON |

Pro v1 stačí první dvě charakteristiky. Pro v2 jsou povinné všechny.
PC po ověření Protocol zapne odběr Request. FW musí čekat na aktivní CCCD
před prvním požadavkem; požadavky jsou notifikace na Request, nikoli zápis z FW
do jiné služby. Odpovědi FW přijímá callbackem Write na Response.
Všechny datové zápisy a notifikace mají nejvýše 20 bajtů, fungují při MTU 23.
Při větším MTU se tento formát nemění. BLE je jediná komunikační cesta.

## Guardian Status — zachovaný v1 paket

Little-endian, `struct.Struct("<2sBBIIII")`, přesně 20 bajtů:

| Offset | Bajtů | Pole |
|---|---:|---|
| 0 | 2 | `47 4D` = ASCII GM |
| 2 | 1 | Verze tohoto paketu: **1**, i v režimu API v2 |
| 3 | 1 | Flags |
| 4 | 4 | Inbox celkem, uint32 |
| 8 | 4 | Nepřečtené v inboxu, uint32 |
| 12 | 4 | Outbox celkem, uint32 (včetně čekajících/neúspěšných) |
| 16 | 4 | Sequence, uint32 |

Flags: bit 0 (`0x01`) aktuální Guardian = vždy 1; bit 1 (`0x02`) odesílání;
bit 2 (`0x04`) příjem; bit 3 (`0x08`) rádio/CAT připojeno; bit 4 (`0x10`)
příkazový port VARA připojen; bit 5 (`0x20`) řídicí kanál aktivní.
Bity 6–7 jsou 0. TX/RX jsou fáze přenosu zprávy, nikoli PTT majáků.
Stavy spojení jsou OR obou rádií. Neaktivní VARA není chyba při ARDOP/SC-FTN.

Příklad: TX+RX, inbox 5, nepřečtené 2, outbox 1, sequence 7:

```text
47 4D 01 07 05 00 00 00 02 00 00 00 01 00 00 00 07 00 00 00
```

## Progress — procenta

Little-endian, `struct.Struct("<2sBBBI")`, přesně 9 bajtů:

| Offset | Bajtů | Pole |
|---|---:|---|
| 0 | 2 | `47 50` = ASCII GP |
| 2 | 1 | Verze: 2 |
| 3 | 1 | TX procent: 0–100 nebo **255 = neznámé/neaktivní** |
| 4 | 1 | RX procent: 0–100 nebo **255 = neznámé/neaktivní** |
| 5 | 4 | Sequence shodná s příslušným Guardian Status |

Příklad: TX 72 %, RX neznámé, sequence 7:

```text
47 50 02 48 FF 07 00 00 00
```

FW spojí stav a procenta podle sequence. Při novějším Status nesmí zobrazovat
procenta starší zprávy; do odpovídajícího Progress použije `—`.
255 nikdy nekreslit jako 0 nebo 100 %. U neaktivního směru kreslit „Neaktivní“.

VARA a SC-FTN používají stejné výpočty jako ukazatel přenosu na PC, včetně
zaokrouhlení na celé procento. VARA TX vychází z velikosti předané modemu minus
BUFFER; neznámá délka příjmu je `null`/255. ARDOP používá průběh payload bajtů.
100 % není potvrzení doručení koncovému příjemci. Při více současných přenosech
stejným směrem obsahuje krátký paket první aktivní přenos v pořadí rádií;
`status.get` vrací i jednotlivé položky `transfers`.

## Rytmus a dostupnost

PC obnovuje snímek po 500 ms, změnu posílá průběžně, beze změn nejpozději po
5 s. V režimu v2 vždy pošle Status a odpovídající Progress se stejnou sequence.
Sequence začíná na 0 pro každé spojení a přetéká modulo 2^32.

FW po **15 s bez validního Status** zobrazí „Guardian nedostupný“ a odstraní
aktuální TX/RX, procenta a počty. Totéž ihned při BLE disconnect. Samotný bond
nebo BLE link není důkaz živé aplikace. Zprávy/odpovědi na jiných charakteristikách
nesmějí omlazovat čas stavového paketu. PC při 15 s starém vlastním snímku
přestane sdílet a odpojí se. Běžné zápisy mají timeout 10 s.

## Request/Response: fragmentace UTF-8 JSON

Každý BLE fragment má 4bajtovou hlavičku a 1–16 bajtů dat:

| Offset | Bajtů | Pole |
|---|---:|---|
| 0 | 1 | Flags: bit 0 START, bit 1 END, ostatní 0 |
| 1 | 1 | transfer_id: 0–255 |
| 2 | 2 | index fragmentu uint16 little-endian, od 0 |
| 4 | 1–16 | UTF-8 JSON data |

- START má index 0; další fragmenty stejného přenosu index +1. Jednofragmentová
  zpráva má flags 3. UTF-8 znak může být rozdělen mezi fragmenty: nejprve spojit
  **bajty**, až po END dekódovat UTF-8 a JSON objekt.
- Maximálně **32768 bajtů JSON** na jeden požadavek/odpověď. Žádný nulový
  terminátor, délkový prefix ani newline navíc. JSON řetězce používají běžné
  escapování JSON; čísla musejí být konečná.
- FW serializuje celé požadavky: fragmenty různých transfer_id se nesmějí
  prokládat. Nový START před dokončením předchozího požadavku je chyba.
- Nejvýše **4 nezodpovězené požadavky**. ID přenosu je do odpovědi rezervované.
  PC vrací stejné transfer_id; znovu použít až po přijetí koncového fragmentu
  odpovědi. Doporučený první klient posílá vždy jen jeden požadavek současně.
- PC serializuje celé odpovědi na Response; mezi jejich fragmenty může
  zapisovat Status/Progress na jejich vlastních charakteristikách.
- Sestavení požadavku nejvýše 30 s; celý požadavek včetně odpovědi nejvýše
  60 s. Porušení rámcování, pořadí, velikosti nebo limitu rozpracovaných
  požadavků ukončí spojení. FW resetuje assembler a rozpracované požadavky
  při každém disconnect. Po opětovném spojení lze požadavek zopakovat.
- Chyby **obsahu** platného JSON objektu vrací běžnou chybovou odpověď.
- Každý JSON požadavek obsahuje `id` (1–64 znaků `[A-Za-z0-9._-]`) a `op`.
  `id` se zkopíruje do odpovědi. JSON `id` je oddělené od číselného transfer_id.

Referenční jednofragmentový objekt `{}` s transfer_id 4:
`03 04 00 00 7B 7D` (platný transport; API následně vrátí chybějící `id`).
Kodér a dekodér jsou v `guardian/guard_mesh_rpc.py`, vektor ověřují testy.

Úspěch:

```json
{"id":"q1","ok":true,"result":{}}
```

Chyba:

```json
{"id":"q1","ok":false,"error":{"code":"not_found","message":"Message not found"}}
```

Stabilní kódy: `invalid_request`, `unknown_operation`, `not_found`,
`list_changed`, `message_changed`, `station_not_configured`, `token_conflict`,
`storage_error`. `message` je diagnostický text; FW lokalizuje podle `code`.
U neplatného `id` může být v odpovědi `id: null`.

## 1. status.get

```json
{"id":"s1","op":"status.get"}
```

Příklad `result`:

```json
{
  "tx":true,"rx":false,"inbox":5,"unread":2,"outbox":1,
  "radio_connected":true,"vara_connected":true,"control_active":true,
  "tx_percent":72,"rx_percent":null,
  "transfers":[{"radio":1,"direction":"send","percent":72}]
}
```

`direction`: `send` nebo `receive`, `radio`: 1 nebo 2, `percent`: 0–100 nebo
`null`. Neaktivní přenosy v `transfers` nejsou. Tímto příkazem lze kdykoli
vyžádat stav rádia, VARA a řídicího kanálu; nezapíná ani nevypíná je.

## 2. contacts.list

```json
{"id":"c1","op":"contacts.list","source":"all","offset":0,"limit":10}
```

`source`: `all` (výchozí), `live`, `saved`. Živé kontakty pocházejí z aktuálně
slyšených stanic a neexpirovaných objevených tras obou rádií. Uložené kontakty
jsou cíle ručně uložených tras v Guardianu. Stejná značka je uvedena jednou,
se dvěma příznaky `live`/`saved`. Seznam je řazen podle volací značky.

Příklad `result`:

```json
{
  "items":[{"callsign":"OK1ABC","live":true,"saved":true,
    "next_hop":"OK2XYZ","live_next_hop":"OK1ABC","approved":true,
    "grid":"JN99CS","frequency_hz":145500000}],
  "total":1,"revision":"0df46824dbf57a50","next_offset":null
}
```

`next_hop` je uložená předvolba; `live_next_hop` aktuální živá trasa. U přímého
kontaktu odpovídá značce stanice. Nepřímá neodsouhlasená trasa má
`approved:false`; kontakt neznamená záruku okamžitého doručení. Nepřítomné údaje
(např. grid u nepřímé trasy) mohou v objektu chybět, frekvence může být `null`.

## 3. messages.list

```json
{"id":"m1","op":"messages.list","folder":"inbox","offset":0,"limit":10}
```

`folder`: `inbox` (výchozí), `outbox`, `sent`, `draft`, `transit`.
Nejnovější zprávy první, při shodném času podle ID sestupně.

```json
{
  "items":[{"msg_id":123,"source":"OK1ABC","final_dest":"OK7PS",
    "subject":"Zkouška","created":1790540000.0,"status":"received",
    "read":false,"priority":0}],
  "total":1,"revision":"3cce69d4a20e81b8","next_offset":null
}
```

`created` je Unix čas v sekundách. `msg_id` je uint32 (nesmí se ukládat do
podepsaného int32). `subject` je v seznamu zkrácen na 160 Unicode znaků.
Stavy odpovídají Guardianu: `draft`, `queued`, `sending`, `delivered`,
`received`, `waiting`, `forwarded`, `failed`. Metadata neobsahují přílohy.

### Společné stránkování seznamů

`offset` výchozí 0, `limit` výchozí 10 a rozsah 1–20. Odpověď obsahuje `total`,
`next_offset` (null = konec) a neprůhledné `revision`. Další stránku požádat se
stejným `revision` a vráceným `next_offset`. Když se seznam mezitím změní, PC
vrátí `list_changed`; FW zahodí dosavadní stránky a začne od 0 bez revision.
Tím se při nově příchozí zprávě nepomíchají položky dvou různých seznamů.

## 4. message.get

```json
{"id":"t1","op":"message.get","msg_id":123,"offset":0,"limit":512}
```

`offset` a `limit` jsou **Unicode znaky/code points**, nikoli bajty UTF-8.
Limit 1–1024, výchozí 512. FW nemusí sám počítat UTF-8: použije `next_offset`.

```json
{
  "msg_id":123,"source":"OK1ABC","final_dest":"OK7PS","subject":"Zkouška",
  "body":"Ahoj, toto je text zprávy.","offset":0,"total_chars":26,
  "revision":"7ec3080d9ebeea55","next_offset":null
}
```

Text je prostý obsah `body.txt`, žádné HTML ani přílohy. Ani přílohy uložené
u téže zprávy se kvůli prohlížení nerozbalují. Předmět v detailu nejvýše 256
znaků. Čtení nemění příznak přečteno. Na dalších stránkách posílat `revision`;
při úpravě textu/předmětu přijde `message_changed` a prohlížení se obnoví od 0.
Pro BLE prohlížení je limit místního textu 1 MiB v UTF-8; větší text nebo
nedostupné/poškozené úložiště vrátí `storage_error`.

## 5. message.queue — napsaná zpráva z Guard Mesh

```json
{
  "id":"send1","op":"message.queue",
  "token":"7c830595-5a8b-469b-a1b1-ebca00000001",
  "to":"OK1ABC","subject":"Zkouška","body":"Ahoj z Guard Mesh.","priority":0
}
```

- `to`: 1–16 ASCII znaků `A-Z a-z 0-9 / -`, značka/cílová skupina.
  Guardian převede na velká písmena. Bez mezer.
- `subject`: výchozí prázdný, nejvýše 256 Unicode znaků, bez NUL.
- `body`: povinný neprázdný text, nejvýše 4096 Unicode znaků, bez NUL.
  Nové řádky a diakritika jsou povoleny. Whitespace-only text se odmítne.
- `priority`: celé číslo 0–3, výchozí 0 (běžná); stejné hodnoty jako Guardian.
- `token`: UUID vytvořené **jednou pro dané stisknutí Odeslat** a uložené na
  zařízení, dokud nepřijde potvrzení. Doporučeno UUID v4.
- Jiná pole jsou odmítnuta, tedy i `attachments`, vlastní `source`, vlastní
  ID zprávy nebo příkazy rádia. Odesílatel je vždy nastavená značka Guardianu.

Odpověď po uložení na disk:

```json
{"id":"send1","ok":true,"result":{"msg_id":123,"accepted":true,"duplicate":false}}
```

`accepted` znamená přijetí do místní fronty. Normální plánovač Guardianu potom
řeší trasu a doručení. Při vypnutém řídicím kanálu se BLE požadavkem sám
nezapne. Další stav zprávy lze sledovat přes `messages.list`. Přijetí do fronty
není totéž jako odeslání či doručení.

### Opakování po výpadku

Při ztracené odpovědi zopakovat tentýž požadavek se **stejným tokenem i obsahem**;
JSON `id` a transfer_id mohou být nové. PC pro stejné spárované zařízení vrátí
původní `msg_id` a `duplicate:true`, nevytvoří druhou zprávu. Token je uložen
spolu se zprávou a přežije restart PC i přesun zprávy do Odeslané.
Stejný token s jiným obsahem vrátí `token_conflict`.

Ochrana platí, dokud je zpráva zachovaná v místní databázi, a je vázaná na
BLE adresu zařízení. Po ručním smazání zprávy nebo změně identity BLE zařízení
se stará operace nesmí automaticky opakovat. Úplně nové odeslání musí mít nové UUID.
Token je místní metadata, nepřenáší se v rádiové zprávě.

## Doporučený postup FW aplikace

1. Po připojení přijímat Status a Progress, zobrazovat aktuálnost a TX/RX.
2. Po aktivaci CCCD lze poslat `status.get` pro úvodní stav.
3. Obrazovka kontakty: `contacts.list`; výběr vyplní `to` nové zprávy.
4. Obrazovka zprávy: `messages.list`; otevření položky volá `message.get`.
5. Editor textu: uložit text + token, poslat `message.queue`; až při `ok:true`
   oznámit „Zařazeno k odeslání“. Po timeoutu nezobrazovat „Neodesláno“ jako
   jistotu — stav je nejistý, zopakovat se stejným tokenem.
6. Při disconnect zahodit neúplné fragmenty a živý stav. Rozpracovaný text
   a nepotvrzený token ponechat. Po opětovném spojení začít nové stránkování.

Firmware validuje délky a verze binárních paketů, meze procent a rezervované
bity. UI aktualizuje ze svého modelu/fronty, ne blokujícím kreslením v BLE callbacku.
Lua aplikace může používat týž model předaný firmwarem; pro wire protokol není
nutná Lua na PC ani změna rádia.

## Ověření integrace

PC testy ověřují v1/v2, UTF-8 fragmentaci, pořadí a limity fragmentů, párování,
odpojení, procenta, stránkování, kontakty, text bez příloh, opakování odeslání
i restart úložiště. Na HW ověřit zvlášť CCCD, oba směry přenosu, souběh heartbeatů
s dlouhou odpovědí, výpadek uprostřed textu a po uložení zprávy před potvrzením.
