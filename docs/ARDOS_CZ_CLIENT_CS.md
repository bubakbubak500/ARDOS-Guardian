# ARDOS CZ v Guardianu

Volitelný internetový transport pro přímé PC stanice a autorizované RF relaye.
Přímé stanice jsou součástí 1.1.21; níže popsaný RF bridge vyžaduje
klienta 1.1.22 a aktualizovaný server. Instalátor 1.1.21 bridge neobsahuje.
Výchozí stav je vypnuto.
Klient je součástí tohoto veřejného repozitáře. Implementace serveru,
administrace a provozní tajemství patří do samostatného privátního repozitáře.

## Připojení

1. V Nastavení → Síť zapnout ARDOS CZ a zadat HTTPS adresu testovacího serveru.
2. V Síť → ARDOS CZ registrovat zařízení jednorázovou pozvánkou správce.
   Nové pozvánky platí 24 hodin; platnost staničního grantu je samostatná.
3. Guardian ukládá privátní Ed25519 klíč do Windows Credential Manageru.
   Konfigurace, diagnostika a přenosný profil tento klíč neobsahují. Každý
   profil a server mají vlastní identitu zařízení. Pozvánka pro nové zařízení
   může nahradit odvolané zařízení; původní klíč zůstává do úspěšné registrace.
4. Online znamená ověřenou relaci, platný grant a čerstvý potvrzený heartbeat.
   Tlačítko Odpojit pozastaví relaci do Připojit nebo dalšího spuštění aplikace.

Server nepřiděluje oprávnění pouhým zadáním značky. Správce nejprve založí značku,
práva a pozvánku. Stanice vyžaduje `connect`, pro odesílání `deposit`, pro příjem
`receive`. Soukromý test používá HTTPS přes Tailscale Serve; klient nevypíná
ověřování certifikátů a odmítá přesměrování na jiný endpoint. Veřejné kořenové
certifikáty dodává balíček `certifi`, aby zastaralý Windows řetězec nezablokoval
platný certifikát. Kontrola důvěryhodnosti i jména serveru zůstává povinná.

## Odesílání a stavy

Přednostně server, jinak RF zkusí online adresáta. U vlastní odchozí zprávy má
ručně nastavená RF trasa přednost. U relaye má v tomto režimu přednost server
i před známou RF trasou. Preference RF dovoluje server jen přes výslovný depozit.
Serverový worker nepoužívá zvukové/PTT vlákno ani hlavní Qt vlákno pro síť.

**Zanechat zprávu** je samostatné tlačítko pro vlastní zprávu v Outboxu.
Potvrzení vysvětlí uložení do vyzvednutí/expirace. Běžné Odeslat nevytváří
depozit pro offline cíl. Stav **Server převzal; čeká na vyzvednutí** ponechá
místní kopii v Outboxu. **Adresát převzal** a přesun do Sent nastanou až po
ACK trvalého importu příjemcem. Expirace vyžaduje nové rozhodnutí operátora.

Neznámý výsledek uploadu se zapíše do místních metadat a ověřuje stejným
idempotentním klíčem i po restartu. Do 90 sekund se RF pokus potlačí. Potom
může pokračovat RF; příjemce deduplikuje obě cesty. Serverové převzetí již
potvrzené serverem zůstává jeho odpovědností do doručení/expirace. Samotné
vypnutí ARDOS CZ takovou zprávu automaticky neposílá znovu po RF.

Příjem ukládá bundle a index s flush/fsync před ACK. Výpadek zápisu nevede
k serverovému ACK. Stejná kopie po RF/serveru nevytvoří druhý záznam. Kolize
starého 32bitového `msg_id` s jiným obsahem se nepřepisuje: server drží zprávu,
klient ukáže chybu místního importu a vyžaduje zásah operátora. Index zatím
nemigruje na více identit pod jedním číslem. Také RF příjem potvrzuje až po
úspěšném místním importu.

## RF relay → ARDOS CZ

Odesílatel A nemusí mít přístup na server. Zprávu předá po RF relayi R;
R s povoleným automatickým relayem a preferencí serveru ověří adresáta B
asynchronně. Při online B předá obsah serveru a další RF přenos obsahu potlačí.
Původní RF spojení A → R i potvrzení R → A zůstávají nezbytná.

R potřebuje práva `connect` a `bridge` na grantu **i zařízení**, B potřebuje
`connect` a `receive`. Přidání `bridge` ke grantu nemění rozsah již registrovaného
zařízení: správce vytvoří novou pozvánku a relay se znovu zaregistruje.
Nedostupný/neoprávněný server nebo offline cíl uvolní zprávu pro RF zálohu;
neznámý výsledek uploadu se nejprve ověřuje, nejvýše 90 sekund bez rozhodnutí.

Převzetí serverem není konečné doručení. Teprve ACK trvalého importu u B
vyvolá `DELIVERED` po původním RF kanálu směrem k A. Zpáteční hop, zbývající
TTL, serverové převzetí i čekající potvrzení jsou trvale uložené pro restart.
Server potvrzuje identitu relaye, nikoli neověřenou volací značku původce z RF;
u příjmu se zobrazuje relay a neověřený původ.

Odesílatel musí již znát trasu k relayi. Automatické oznámení serverové brány
zatím není implementováno; viz [návrh discovery](ARDOS_CZ_RELAY_DISCOVERY_PROPOSAL_CS.md).

## Veřejný kontrakt v1

- Registrace: `POST /v1/enroll`; přihlášení: challenge + Ed25519 důkaz přes
  `POST /v1/challenge`, `POST /v1/session`. Relace trvá 15 minut.
- `GET /v1/self`, `PUT /v1/presence` (60sekundová lease),
  `POST /v1/delivery/availability` (jeden cíl).
- `PUT /v1/messages`: JSON s `protocol=1`, `ingress=direct|rf_bridge`, `mode=online|leave`,
  `key`, `size`, `bundle_hash` a base64 `bundle`. Původ a cíl se validují i
  uvnitř archivu. U `direct` je původ ověřen proti přihlášenému zařízení;
  `rf_bridge` vyžaduje `bridge` a vrací příjemci `origin_verified=false` a značku relaye.
  Availability přijímá stejný `ingress`; relay zjišťuje stav vlastním handoff klíčem.
- `GET /v1/handoffs/{key}` a `GET /v1/messages/{uuid}` pro stav;
  `GET /v1/mailbox` nabízí nejvýše jeden bundle s opakovatelnou lease;
  `POST /v1/messages/{uuid}/ack` nese `content_hash` po trvalém importu.
- `DELETE /v1/session` odhlašuje zařízení a ruší jeho přítomnost.

Upload používá původ v těle požadavku, aby značky obsahující `/` nezávisely
na dekódování URL reverzní proxy. Standardní ZIP v1, nejvýše 1 MiB komprimovaně,
4 MiB rozbaleně a 64 příloh; žádné spouštění příloh ani extrakce na disk serveru.
SHA-256 obsahu nezahrnuje transportní hop historii ani ZIP časová razítka.
Jiný obsah stejného původu a ID je konflikt. Bridge bez oprávnění je odmítnut.

První verze počítá s přístupem serveru k obsahu; bundle nemá E2E šifrování.
Testovací retence je 7 dní a limit 100 čekajících zpráv na odesílatele/adresáta.
Ostré nasazení a fyzický RF pilot vyžadují samostatné ověření.
