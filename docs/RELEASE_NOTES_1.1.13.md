# Guardian 1.1.13

## Guard Mesh over Bluetooth LE

Guardian can now share its PC status with a paired Guard Mesh device over BLE. The PC handles discovery, pairing and the connection; no Wi-Fi is required.

Open **Settings → Station settings → Guard Mesh** to search for a device, pair or disconnect. The connection stays active after closing settings. Connection controls are grouped in this tab, and the help guide explains the shared data.

## BLE API v2

- Live transmit and receive activity, including transfer percentages.
- Inbox, unread and Outbox message counts.
- Radio/CAT, VARA and control-channel connection states, also available on request.
- Browsing of live and saved contacts.
- Paginated message lists and plain-text message reading, without transferring attachments or marking messages as read.
- Text messages composed on the device can be submitted with a recipient, subject, body and priority. Guardian stores them in its normal Outbox and follows its existing delivery rules.
- Persistent request tokens prevent duplicate messages when a submission is retried after a connection loss or PC restart, while the original message remains in the mailbox.

Existing BLE v1 firmware remains supported. The additional features require Guard Mesh firmware implementing API v2. Device firmware and a Lua application are not included in this release.

Firmware integration reference: [Guard Mesh BLE API v2](https://github.com/bubakbubak500/ARDOS-Guardian/blob/v1.1.13/docs/GUARD_MESH_BLE_V2_CS.md) (Czech, with UUIDs, packet layouts, JSON examples and reconnect rules).

## Validation

The local test suite passed **885 tests**. The packaged Windows application passed its Qt/Windows BLE, image-compression and native ARDOP self-tests. BLE v2 requests and responses were tested with a simulated GATT peer; physical v2 integration still requires the matching device firmware.

The release workflow also runs the test suite and packaged application checks before publishing the installer, portable ZIP, update manifest and SHA-256 checksums.

## Downloads

- **Guardian-1.1.13-setup-win-x64.exe** — Windows x64 installer.
- **Guardian-1.1.13-win-x64.zip** — portable Windows x64 application.
- **SHA256SUMS.txt** — checksums for the installer and portable ZIP.

Upgrading preserves the existing Guardian data and settings.
