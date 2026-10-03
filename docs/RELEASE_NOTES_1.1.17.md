# Guardian 1.1.17

## Guard Mesh Bluetooth reconnection

- Guardian remembers the last successfully connected Guard Mesh device. If the BLE link drops, it retries in the background after 1, 2, 5, then 10 seconds between further attempts. Settings does not need to stay open.
- The first retry uses the known device. Later attempts scan for the Guard Mesh service and match the saved device address, which also helps after Windows sleep or a Bluetooth adapter reset. Only one BLE operation runs at a time.
- On every new connection, Guardian discovers the service again, enables request notifications, and resumes status and progress updates. Incomplete requests from the previous session are discarded.
- The device choice survives a Guardian restart. **Disconnect / cancel** stops automatic attempts and forgets the choice. Guardian does not remove the Windows Bluetooth bond.
- BLE discovery is limited to the Guard Mesh service. Other Bluetooth devices are not controlled by this feature.

The Guard Mesh firmware and BLE protocol are unchanged. This update follows Guardian 1.1.16.

## Verification

- Automated BLE and API v2 tests cover link loss, retry, rescanning, restoration after restart, manual cancellation, and request handling.
- Physical tests with a T-Deck, moving out of range, and Windows sleep/wake are still needed to confirm adapter behavior.

## Files

- `Guardian-1.1.17-setup-win-x64.exe` — Windows x64 installer.
- `Guardian-1.1.17-win-x64.zip` — portable Windows x64 application.
- `release-manifest.json` — update manifest.
- `SHA256SUMS.txt` — checksums for the release packages.
