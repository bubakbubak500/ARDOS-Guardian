Guardian - optional manually installed map / volitelna rucne nahrana mapa
===========================================================================

Copy the complete "tiles" directory into this folder while Guardian is closed.
Guardian expects this exact XYZ layout:

    maps\tiles\<zoom>\<x>\<y>.png

Example:

    maps\tiles\10\553\346.png

Then open Station map, keep Map background enabled, select
"Use manually installed map", and explicitly confirm the choice.

Without both a valid manually copied tile tree and that confirmation, Guardian
continues to use its normal ČÚZK map exactly as before. Guardian never downloads
or modifies the manually installed tiles. The operator is responsible for the
origin and licence of supplied map data.

-------------------------------------------------------------------------------

Pri vypnutem Guardianu zkopirujte do teto slozky celou slozku "tiles".
Guardian ocekava presne tuto XYZ strukturu:

    maps\tiles\<zoom>\<x>\<y>.png

Priklad:

    maps\tiles\10\553\346.png

Potom otevrete Mapu stanic, ponechte zapnuty Mapovy podklad, zvolte
"Pouzit rucne nahranou mapu" a volbu vyslovne potvrdte.

Bez platne rucne zkopirovane sady dlazdic a bez tohoto potvrzeni Guardian dale
pouziva beznou mapu CUZK presne jako dosud. Guardian rucne nahrane dlazdice
nikdy nestahuje ani neupravuje. Za puvod a licenci mapovych dat odpovida uzivatel.
