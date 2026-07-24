# Release process

Official releases are created by `.github/workflows/release.yml`.

1. Update `guardian/_version.py` using `MAJOR.MINOR.PATCH`.
2. Add `docs/RELEASE_NOTES_MAJOR.MINOR.PATCH.md`.
3. Run tests, build the frozen application, and compile the installer locally.
4. Commit and push the reviewed source to `main`.
5. Create and push the matching `vMAJOR.MINOR.PATCH` tag.
6. GitHub Actions repeats tests and builds, verifies the unsigned status,
   creates checksums and manifest, attests provenance, and publishes the release.

The stable update manifest is:

`https://github.com/bubakbubak500/ARDOS-Guardian/releases/latest/download/release-manifest.json`

Do not replace that URL when signing is added. Never commit a PFX file, private
key, token, or certificate password.
