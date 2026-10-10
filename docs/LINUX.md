# Guardian 1.1.24 pro Linux x64

Linuxová edice vychází z Windows 1.1.24. Podporuje SC-FTN a nativní ARDOP
500 Hz. VARA je zašedlá a není dostupná ani jako náhradní přenos.
Windows tag, sestavení a soubory releasu zůstávají původní.

## Spuštění

Balíček je určen pro Linux x86_64 s glibc 2.35 nebo novější (Ubuntu 22.04+,
Linux Mint 21+, Debian 12+). Python, Qt a knihovna ARDOP jsou přibalené.
Rozbalte `Guardian-1.1.24-linux-x64.tar.gz` do zapisovatelné složky a spusťte
soubor `Guardian` uvnitř. Zachovejte celou složku včetně `_internal`.

```sh
tar -xzf Guardian-1.1.24-linux-x64.tar.gz
cd Guardian-1.1.24-linux-x64
./Guardian
```

Na Ubuntu/Debian jsou systémové knihovny pro zvuk a Qt dostupné přes:

```sh
sudo apt install libportaudio2 libpulse0 libegl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0
```

Pro CAT přes Hamlib nainstalujte `libhamlib-utils`; Guardian najde `rigctld`
na PATH. Do CAT portu patří např. `/dev/ttyUSB0` nebo `/dev/ttyACM0`.
Uživatel musí mít přístup k sériovému zařízení (na Ubuntu/Debian skupina
`dialout`; po změně skupiny se znovu přihlaste). PTT a audio RX/TX nastavte
stejně jako na Windows. Ke spojení zvolte na obou stanicích stejný modem
a u SC-FTN stejnou šířku pásma. Windows protistanice může používat 1.1.24.
Stanice pouze s VARA není pro linuxovou edici kompatibilním RF protějškem.

Konfigurace a pošta se ukládají do `~/.guardian`; Qt nastavení do standardní
uživatelské konfigurace prostředí. ARDOS CZ ukládá tajné údaje do desktopového
Secret Service (např. GNOME Keyring). Při jeho nedostupnosti vysvětlí chybu;
neukládá tajné údaje do nešifrovaného souboru. BLE potřebuje BlueZ a desktopový
D-Bus. Automatické získání polohy z Windows a řízení Windows systémové
hlasitosti zde nejsou dostupné; ICOM GPS a hlasitost Guardianu zůstávají.

Linux používá vlastní manifest aktualizací. Stažený archiv projde kontrolou
SHA-256; po ukončení aplikace jej rozbalte do nové složky a spusťte novou verzi.
Uživatelská data zůstávají mimo složku aplikace.

## Sestavení ze zdrojů

Na Linuxu nainstalujte C kompilátor, CMake, Ninja, Git, Python 3.12,
`libpng-dev`, `libjpeg-dev`,
`libportaudio2` a výše uvedené knihovny Qt. V samostatném Python prostředí:

```sh
python -m pip install -e '.[dev]'
bash tools/build_jpegxl_linux.sh
bash build_linux.sh
```

Windows sestavení nadále používá původní `Guardian.spec` a PowerShell skripty.
