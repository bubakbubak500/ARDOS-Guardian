# Guardian 1.1.14

## ARDOP compatibility fix

The bundled ARDOP modem library is now compiled for baseline Windows x64 CPUs. On some older computers, Guardian 1.1.13 could report Windows error `0xc000001d` when loading ARDOP even though the library was present in the installer. The updated library remains included with the Windows installer and portable application.

Existing Guardian settings and operator data are preserved during upgrade.

## Downloads

- **Guardian-1.1.14-setup-win-x64.exe** — Windows x64 installer.
- **Guardian-1.1.14-win-x64.zip** — portable Windows x64 application.
- **SHA256SUMS.txt** — checksums for the installer and portable ZIP.
