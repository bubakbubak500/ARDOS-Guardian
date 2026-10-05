# Guardian 1.1.23 — adaptivní SC-FTN a kratší předávání DATA/ACK

Tato verze navazuje na 1.1.22. SC-FTN nyní rychleji vybírá modulaci, kódování
a velikost datového okna podle potvrzených bloků a změřené kvality příjmu.
Při chybách zmenší okno nebo ustoupí na robustnější profil; úspěšný krátký
profil může na delší zprávě ověřit rychlejší variantu. Rozhodování používá
zpětnou vazbu linky, nikoli pevné nastavení pro IC-705.

- Po naučení směrové linky nezačíná každý další přenos nejmenším oknem.
  Zkoušky vyšší kapacity se omezují podle zbývajících dat a skutečné ceny
  DATA/ACK cyklu.
- SC-FTN může potvrdit shodný profil už v původní výměně HAVE/ACK.
  Starší protistrana dál používá samostatnou výměnu profilu.
- Časování zahájení přenosu čeká na skutečné odvysílání řídicího rámce.
  Zvukový výstup lze mezi datovými okny ponechat otevřený, takže odpadá
  opakovaná inicializace zařízení. Ochranné časy pro klíčování zůstávají.
- LAB opravuje ukončení procesů stanic a do izolované kopie VARA FM přenáší
  i datové soubory `.dat`.

## Ověření na dvojici IC-705

Na 144,6 MHz FM byly odeslány skutečné zprávy s 20 KiB nekomprimovanou
přílohou mezi OK7PS a OK2IPW. Quick Auto Tune obou směrových cest vybral
digitální úroveň 0,016; všechny čtyři přenosy SC-FTN byly bajtově přesné.

Quick Auto Tune je kalibrace konkrétní zvukové a rádiové cesty. Po změně
konfigurace, portu nebo zvukového zařízení ji spusťte znovu v běžné instalaci;
naměřená laboratorní úroveň se automaticky nepřenáší na jinou cestu.
