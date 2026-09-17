# ARDOP library provenance

Vendored from https://github.com/rfb/ardopb, revision
`8e490476913da9de2b60b2a17bc36a8bcee73a79` (2026-08-06).
Downloaded source archive SHA-256:
`11402cf42d7fa13606b28a29105a6557ec472cac62cdbf914438f6b87f1e8750`.

Upstream derives from ARDOP/ardopcf by Rick Muething, John Wiseman and Peter
LaRue. See the accompanying MIT LICENSE. The Reed–Solomon implementation
credits Simon Rockliff's 1991 reference implementation; its attribution is
preserved in core/codec/rs.c. Guardian does not compile the upstream GUI,
network host server, external /lib dependencies or audio backends.

`vendor/core` and `vendor/shell` are unmodified upstream source. Build inputs
are explicitly listed in tools/build_ardop.py. Guardian's wrapper is
guardian_ardop.c; ABI 1 uses opaque, independent contexts and 48 kHz mono PCM.
No callback into Python, process-global DSP state, sockets or device ownership
crosses that boundary. sys.c supplies the optional capture timestamp helper;
capture is not enabled by the wrapper.

Build Windows: `python tools/build_ardop.py --cc "zig cc"` (Zig 0.14.1 used
for the local release). Linux: `python tools/build_ardop.py --cc cc` with a
C11 GCC/Clang compiler and libm/pthreads. Output goes into native/ardop/bin.
Zig can also cross-compile with `--target x86_64-linux-gnu`; cross-compilation
alone does not establish Linux audio/radio compatibility.

ARQ negotiates a maximum 500 Hz, retaining the standard 200/500 Hz mode ladder.
Guardian control uses a 4PSK.200.100 FEC data frame and its existing CRC.
The `A500` Guardian capability token and GAR1 payload envelope are
application-level conventions; an ordinary ARDOP TNC alone is not a Guardian
mailbox peer. RF interoperability and channel performance require radio tests.
