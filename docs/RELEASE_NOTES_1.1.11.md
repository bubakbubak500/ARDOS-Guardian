# Guardian 1.1.11

## Dvě rádia a předávání pošty

- Volitelné druhé rádio má vlastní připojení, zvuk, modem, PTT a frekvenci. Obě rádia sdílejí jednu poštovní schránku a mohou současně obsluhovat různé zprávy.
- Guardian volí odchozí rádio podle slyšené stanice, dalšího uzlu a frekvence trasy. Zprávu přijatou na jednom rádiu může předat přes druhé; potvrzení se vrací příchozím kanálem.
- Slyšené stanice ukazují rádio a frekvenci. Nastavení druhého rádia zůstává uložené i po vypnutí režimu dvou rádií.

## Guardian ARDOP 500 Hz

- Přibyl experimentální ARDOP modem jako knihovna přímo v aplikaci. Nepotřebuje samostatný ARDOP program ani VARA. Každé rádio může mít vlastní modem a vlastní ARDOP kontext.
- Pro ARDOP je nutné nastavit stejný režim na obou stanicích. Úzké RF pásmo vyžaduje SSB/datové SSB a odpovídající nastavení rádia; samotný zvukový modem nezúží FM vysílač.
- Přenos používá ARQ do 500 Hz, kontrolu úplného obsahu a odmítá nekompatibilní profil místo automatického přechodu na širší modem.

Nastavení a technické podrobnosti: [dvě rádia](DUAL_RADIO_1.1.10.md) a [ARDOP 500 Hz](ARDOP_1.1.11_CS.md).

## Ověření a omezení

Závěrečná kontrola čisté pracovní kopie: 825 automatických testů prošlo, 11 volitelných porovnání s odděleným G2 referenčním stromem bylo přeskočeno. Lokální Windows aplikace prošla ARDOP, Qt a kompresním self-testem; instalátor 1.1.11 byl vytvořen lokálně. GitHub release sestavení spouští testy a znovu vytváří instalátor i přenosný ZIP.

Zkouška na skutečných rádiích, s úniky signálu a s nezávislým ARDOP TNC zatím neproběhla. ARDOP v této verzi zůstává experimentální; výsledky simulací nejsou příslibem RF průtoku.
