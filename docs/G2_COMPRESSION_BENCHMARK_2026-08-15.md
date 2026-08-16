# Guardian G2 compression benchmark — 2026-08-15

## Decision

Guardian uses one compressor: Python's standard-library BZIP2 ZIP method at
level 9. It performs one pass and keeps the ordinary DEFLATE ZIP bundle when the
BZIP2 result is not smaller. The runtime no longer searches candidates or ships
ZPAQ, PAQ8PX or LPAQ8 executables.

Native VARA FILES compression remains a separate operator choice. Settings
allow VARA FILES, Guardian BZIP2, or neither; the two checkboxes are mutually
exclusive so the compression layers cannot be stacked.

## Corpus and method

The deterministic corpus contained four independently transferred Guardian
message attachments near 250 KiB:

| Attachment | Bytes | Content |
|---|---:|---|
| `telemetry.csv` | 256,000 | structured station telemetry rows |
| `field-report.pdf` | 256,329 | valid multi-page PDF with Flate streams |
| `map.png` | 259,403 | generated map-like RGB raster |
| `photo.jpg` | 256,665 | progressive photographic JPEG |

Each candidate encoded the real Guardian ZIP message bundle. Measurements
include bundle construction and a verified `MailMessage.from_bundle` decode.
Built-in methods use median times from three runs; the expensive external
helpers use one run. Times were measured on the development Windows host.

## Results

Encoded bundle bytes are shown in corpus order (CSV, PDF, PNG, JPEG).

| Candidate | Compression time | Encoded bytes | Result |
|---|---:|---|---|
| Stored ZIP | 0.18–0.22 ms | 256,521 / 256,859 / 259,906 / 257,174 | fast but no useful reduction |
| DEFLATE default | 4.3–5.8 ms | 32,624 / 201,261 / 259,954 / 256,635 | standard fallback |
| DEFLATE level 9 | 5.5–12.5 ms | 28,490 / 201,016 / 259,954 / 256,635 | larger on text/PDF than BZIP2 |
| **BZIP2 level 9** | **13.3–18.2 ms** | **15,348 / 184,488 / 261,484 / 258,165** | **selected; fallback prevents image growth** |
| LZMA | 34.8–63.4 ms | 15,282 / 182,111 / 262,854 / 258,103 | only 66 B/2.4 kB smaller on CSV/PDF, 2.5–4.8× slower |
| Adaptive per-entry search | 59.3–148.4 ms | 15,247 / 182,077 / 259,872 / 256,633 | marginally smaller, but tests several codecs every send |
| ZPAQ level 1 | 37.7–61.7 ms | 20,349 / 202,180 / 260,906 / 257,617 | worse size than BZIP2/standard |
| LPAQ8 level 0 | 267.8–366.7 ms | 14,268 / 201,190 / 259,810 / 256,568 | too slow for the small gain |
| PAQ8PX level 1 | 18,930 ms on CSV | 742 on CSV; other three timed out | 18,603 ms decode; unusable latency/memory profile |

BZIP2 lies on the practical speed/size frontier. LZMA saves just 0.04% more on
CSV and 1.3% more on PDF, while taking several times longer. PNG and JPEG are
already packed; the fixed standard-bundle fallback is smaller than either
BZIP2 or LZMA and costs no candidate search.
