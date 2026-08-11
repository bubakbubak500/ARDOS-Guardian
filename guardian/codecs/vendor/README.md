# Bundled Guardian compression codecs

Pinned upstream codec material is bundled so both Guardian G2 peers use exactly
the same encoder/decoder version. Runtime extraction verifies both the packaged
archive and executable SHA-256 before launch. ZPAQ and LPAQ8 are unmodified
upstream archives; PAQ8PX is repacked with standard Deflate solely so Python's
ZIP reader can extract its published binary and complete source tree.

| Archive | Upstream | License | SHA-256 |
|---|---|---|---|
| `zpaq715.zip` | https://www.mattmahoney.net/dc/zpaq715.zip | Public domain / Unlicense; bundled archive contains source and notices | `e85ec2529eb0ba22ceaeabd461e55357ef099b80f61c14f377b429ea3d49d418` |
| `paq8px187.zip` | https://moisescardona.me/paq8px-builds/ (v187; source fork: https://github.com/moisespr123/paq8px) | GPL-2.0-or-later; Guardian's Deflate repack contains the published x64 Windows binary and complete corresponding source | `514060a7c9bd1bb20a00228dbc03069aae193c477378c1c96a38c9e838abd800` |
| `lpaq8.zip` | https://www.mattmahoney.net/dc/lpaq8.zip | GPL-2.0-or-later; bundled archive contains complete corresponding source | `ea43474526f13338cbb50ce3fbd974a0d088d77a3b73d42010ad11fb89a498b2` |

The accompanying `COPYING-GPL-2.0.txt` is the GPL version 2 license text. PAQ8PX
and LPAQ8 run as separate helper programs; they are not linked into Guardian.

PAQ8PX provenance: the published `paq8px_v187.zip` download has SHA-256
`c174b46c3ca47286ceec39e04588fff9292b9a233e2b265476fab90df243ee38`; its
unmodified Windows x64 executable has SHA-256
`e04a58f19416d2c1b2fdf3e4272de5c6dcd615d1d4f310a7d703e74af125b080`.
