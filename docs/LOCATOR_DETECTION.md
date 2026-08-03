# Guardian 0.6.53 — snadné určení vlastního lokátoru

_Implementováno 2026-08-03. Release souhrn je v
[`RELEASE_NOTES_0.6.53.md`](RELEASE_NOTES_0.6.53.md)._

## Cíl

Guardian má operátorovi usnadnit nastavení vlastního Maidenhead/QTH lokátoru
třemi rovnocennými cestami:

1. jednorázově zjistit polohu z lokalizační služby tohoto PC,
2. vybrat bod v mapě,
3. zadat známý lokátor ručně.

Výsledkem všech tří cest zůstává současné pole `station_grid`. Rádiový rámec,
beacon, routing ani interoperabilita se nemění. Guardian nebude do konfigurace
ukládat přesné zeměpisné souřadnice; detekované souřadnice pouze v paměti
převede na lokátor a po potvrzení zahodí.

## Uživatelské rozhraní

V okně **Mapa stanic** bude blok **Moje poloha** se třemi zřetelnými akcemi:

- **Zjistit z tohoto PC** — nový jednorázový pokus přes Windows Location
  Service po samostatném potvrzení přímo v Guardianu. Akce se nikdy nespouští
  při startu ani na pozadí.
- **Vybrat v mapě** — dnešní křížový kurzor a jedno kliknutí, pouze přejmenovaný
  a seskupený s ostatními možnostmi.
- **Zadat lokátor ručně** — dnešní validované pole pro 2 až 10 znaků.

Po úspěšné detekci se hodnota neuloží okamžitě. Guardian ukáže potvrzovací
náhled, například:

```text
Nalezený lokátor: JN89HE12AB
Odhadovaná přesnost polohy: ±42 m
Zdroj: Wi-Fi / satelit / síť / neurčeno

[Použít lokátor]  [Ukázat v mapě]  [Zrušit]
```

**Ukázat v mapě** vystředí mapu a označí dočasný náhled, ale ještě nezmění
uložený lokátor. **Použít lokátor** teprve zavolá stejnou aplikační cestu jako
dnešní ruční zadání a výběr v mapě.

Pokud je hlášená přesnost horší než přibližně 1 km, dialog výsledek nezakáže,
ale jasně ho označí jako orientační a doporučí výběr v mapě nebo ruční zadání.
Deset znaků v takovém případě vyjadřuje střed systémového odhadu, nikoli
záruku, že se stanice v daném malém čtverci skutečně nachází.

Přepínač **Posílat v majáku** zůstane oddělený a beze změny. Zjištění nebo
uložení lokátoru samo o sobě nezapne majáky ani nic neodvysílá.

## Zdroj polohy a souhlas

Implementace je pro Windows a používá nativní
`Windows.Devices.Geolocation.Geolocator`:

- po kliknutí Guardian nejdřív zobrazí vlastní explicitní consent dialog, který
  vysvětlí jednorázové použití, místní převod a nulové ukládání souřadnic;
- po potvrzení následuje jediný `GetGeopositionAsync`, nikoli průběžné sledování;
- požadavek má konečný timeout a UI během čekání zůstane responsivní;
- Guardian nebude sám číst BSSID okolních sítí ani volat externí geolokační API;
- Windows může podle dostupnosti použít GNSS, Wi-Fi, mobilní síť, IP nebo
  systémem nastavenou výchozí polohu;
- zamítnutí, vypnuté služby, chybějící data a timeout vedou zpět k mapě a
  ručnímu zadání, nikdy k vymyšlenému lokátoru;
- při zamítnutém globálním oprávnění desktopových aplikací UI nabídne otevření
  systémové stránky
  **Soukromí a zabezpečení → Poloha**.

Klasický nebalený PySide/Win32 Guardian záměrně nepoužije
`Geolocator.RequestAccessAsync()`. Tento systémový prompt vyžaduje UWP/WinUI
`CoreWindow` a v obyčejném desktopovém okně končí `E_HANDLE`; Microsoft obecně
uvádí omezení metod typu `Request*` v klasických desktopových aplikacích.
Souhlas proto sbírá Guardian a Windows nadále vynucuje globální nastavení
přístupu desktopových aplikací k poloze.

Internet není funkční podmínka Guardianu. Detekce využije to, co právě dokáže
poskytnout Windows; všechny dosavadní offline cesty zůstanou plně použitelné.

## Technické rozdělení

Navržená hranice zabrání tomu, aby se Windows API rozlezlo do mapy a testů:

- malý model výsledku `LocationFix` (`latitude`, `longitude`, `accuracy_m`,
  `source`, `timestamp`), který žije jen po dobu náhledu;
- rozhraní jednorázového poskytovatele polohy nezávislé na Qt;
- Windows adaptér s líným importem PyWinRT; na nepodporované platformě nebo při
  chybějící komponentě vrátí stav „nedostupné“;
- řadič v Qt, který zobrazí souhlas v UI vlákně, dokončení asynchronní operace
  předá zpět přes signal a nedovolí souběžné požadavky;
- jediným perzistentním zápisem zůstane `station_grid` přes existující `_apply`;
- `to_locator(..., MAX_LOCATOR_CHARS)` zůstane jediným převodem souřadnic.

PyWinRT balíčky musí být podmíněné `sys_platform == 'win32'` a explicitně
zahrnuté a ověřené v PyInstaller sestavení. Dokončení WinRT operace přichází z
cizího vlákna; soukromý Qt signal je proto musí vrátit do UI vlákna, aniž by se
GUI během hledání polohy zablokovalo.

## Stavy a texty, které musí UI rozlišit

- čekání na potvrzení consent dialogu Guardianu,
- hledání polohy a možnost pokus zrušit,
- přístup povolen,
- přístup zamítnut,
- lokalizační služby Windows vypnuté,
- služba nebo hardware nejsou dostupné,
- timeout / žádná data,
- výsledek je orientační kvůli velké nejistotě,
- komponenta pro detekci není v této instalaci dostupná.

Diagnostický log smí uvést stav, zdroj a zaokrouhlenou přesnost, ale nesmí do
diagnostického balíčku zapsat přesné dočasné souřadnice. Uložený `station_grid`
zůstává součástí konfigurace a tedy i dnešního diagnostického exportu.

## Testy a ověření

Automatické testy pokrývají:

1. převod známého `LocationFix` na desetiznakový lokátor;
2. potvrzení, zrušení a náhled bez předčasné změny konfigurace;
3. zamítnutí, vypnutou službu, timeout, chybějící provider a nepřesný fix;
4. zachování ručního zadání a výběru v mapě;
5. že detekce nemění `beacon_position`, `beacon_enabled` ani obsah rámce;
6. že se přesné souřadnice neobjeví v konfiguraci ani diagnostice;
7. import a běh PyInstaller artefaktu bez vývojového Pythonu.

Manuální matice před vydáním: notebook s Wi-Fi, PC pouze na Ethernetu,
zamítnuté oprávnění, vypnutá poloha Windows, počítač bez použitelného fixu a
upgrade existující instalace. U každého pokusu zaznamenat hlášenou přesnost a
porovnat lokátor s ručně ověřeným místem, nikoli ukládat souřadnice uživatele.

## Kritéria přijetí pro 0.6.53

- Operátor vždy rozumí, zda polohu teprve prohlíží, nebo ji už uložil.
- Bez vědomého kliknutí se poloha nevyžaduje.
- Po zamítnutí nebo chybě zůstávají ruční zadání a mapa okamžitě dostupné.
- Na disku zůstane pouze Maidenhead lokátor; přesný fix se po dialogu zahodí.
- Žádná nová data se neposílají přes internet přímo Guardianem.
- On-air beacon je bitově stejný jako v 0.6.52 pro stejnou konfiguraci.
- Funkce je ověřena v instalátoru, nejen při spuštění ze zdrojového stromu.

## Odložená záložka — QR most k telefonu

QR kód není součástí 0.6.53. Zůstává jako obecný koncept pro budoucí bezpečný
most mezi Guardianem a telefonem, až pro něj vznikne širší použití než jediné
zjištění polohy. Možné budoucí scénáře jsou spárování companion aplikace,
předání připravené zprávy nebo profilu do terénního zařízení a teprve případně
jednorázové předání přesnějšího telefonního fixu.

Případná realizace musí nejdřív vyřešit, zda běží čistě v místní síti, nebo přes
službu, HTTPS důvěru, jednorázový náhodný token s krátkou platností, potvrzení na
obou zařízeních, ochranu proti opakování a nulovou závislost základního Guardianu
na cloudu. Do té doby nevytvářet server, účet, tokenový protokol ani QR
závislost.

## Zdroje pro implementaci

- Microsoft Learn: `Windows.Devices.Geolocation` a očekávané zdroje/přesnosti
  — <https://learn.microsoft.com/en-us/uwp/api/windows.devices.geolocation>
- Microsoft Learn: jednorázové získání polohy v desktopové aplikaci
  — <https://learn.microsoft.com/en-us/windows/apps/develop/maps-and-location/get-location>
- Microsoft Learn: pravidla `Geolocator.RequestAccessAsync`
  — <https://learn.microsoft.com/en-us/uwp/api/windows.devices.geolocation.geolocator.requestaccessasync>
- Microsoft Learn: omezení WinRT `Request*` metod v klasických desktopových
  aplikacích
  — <https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/winrt-api-desktop-app-support>
- PyWinRT namespace package `winrt-Windows.Devices.Geolocation`
  — <https://pypi.org/project/winrt-Windows.Devices.Geolocation/>
