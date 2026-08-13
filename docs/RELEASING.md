# Guardian G2 release process

This checklist describes the private G2 `2.x` line. G1 uses its public
`origin/main` repository and must never receive a G2 tag or update manifest.

## Prepare source

1. Work on local branch `g2` and verify it targets remote `g2` branch `main`.
2. Update `guardian/_version.py` using `MAJOR.MINOR.PATCH`.
3. Add `docs/RELEASE_NOTES_MAJOR.MINOR.PATCH.md`.
4. Update `README.md`, `STATUS.md` and, when priorities changed,
   `docs/DEVELOPMENT_BACKLOG.md`.
5. Confirm the G2 URLs, AppId, executable, install directory and application
   data directory remain separate from G1.
6. Run the complete test suite with a workspace-owned `--basetemp`.
7. Commit the reviewed source before producing final binaries.

## Build from the release commit

```powershell
.\build.ps1
.\build_installer.ps1 `
  -Compiler "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" `
  -ReleaseBaseUrl "https://github.com/bubakbubak500/ARDOS-Guardian-G2/releases/latest/download"
```

Verify:

- frozen `Guardian-G2.exe` starts;
- installer signature status is the expected unsigned state;
- clean install, upgrade and uninstall preserve operator data as intended;
- `release/release-manifest.json` names the same version and installer hash;
- `release/SHA256SUMS.txt` matches the installer and ZIP;
- the working tree contains no source changes caused by the build.

## Publish

1. Push local `g2` to `g2/main`.
2. Create annotated tag `vMAJOR.MINOR.PATCH` on the release commit.
3. Push that tag explicitly to remote `g2`.
4. Publish the installer, portable ZIP, manifest and `SHA256SUMS.txt` with the
   matching release notes.
5. Verify the latest-release URL and download hashes from the published assets.

If GitHub Actions is enabled, `.github/workflows/release.yml` repeats the
release-critical tests and build after the tag is pushed. If Actions is
disabled for the private repository, publish the locally verified artifacts
with `gh release create` and record that the build was local.

Never commit a PFX file, private key, token or certificate password. Current
builds are intentionally unsigned and rely on repository provenance plus
published SHA-256 hashes until Authenticode signing is introduced.
