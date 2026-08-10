"""Guardian OFDM VHF -- an experimental native payload modem.

A self-contained OFDM physical layer for moving message payloads over a VHF/UHF
radio through the PC soundcard, with no VARA involved. This package is pure
numpy and stdlib: it imports nothing from Qt, sounddevice, Hamlib or VARA, and it
is tested without any of them. `guardian.payload.ofdm_vhf` is the single place
where this meets a radio.

Layers, bottom up:

    config          waveform profiles and the MCS table -- every number lives here
    constellation   Gray-mapped BPSK/QPSK/16-QAM/64-QAM with soft output
    interleaving    spreads coded bits so a dead carrier costs isolated errors
    phy             bits and bins to real audio and back (Hermitian/DMT)
    sync            finding a burst: detection, timing, frequency offset
    coding          rate-1/2 mother code plus explicit punctured FEC profiles
    framing         robust header, manifest, per-subblock CRC/FEC -- bytes to bursts
    metrics         what the receiver measured, and what adaptation will read
    channel         a deterministic, seeded channel simulator
    adaptation      joint hysteretic FEC and keyed-burst selection
    link            selective-repeat bitmap ARQ over a half-duplex pipe

What is deliberately *not* here yet: automatic MCS selection, per-subcarrier bit
loading, LDPC, and any final RF bandwidth. See docs/ofdm-vhf.md for the forward
design of each, and for why the occupied bandwidth stays a profile parameter
until real radios have been measured.

Note throughout: sample rate is not bandwidth. 48 kHz of soundcard carries a
BENCH burst about 2.4 kHz wide.
"""

from .adaptation import (BURST_LADDER, AdaptationConfig,
                         LinkAdaptationController, TxProfile)
from .coding import (FEC_SPECS, FecProfile, FecSpec, effective_rate,
                     encoded_bits, fec_profile, fec_spec)
from .config import (BENCH, DEFAULT_MCS_INDEX, DEFAULT_PROFILE_NAME, HEADER_MCS,
                     MCS_TABLE, Mcs, OfdmConfigError, OfdmProfile, PROFILES,
                     best_mcs_for, mcs, profile, profile_names,
                     profile_or_default)
from .framing import (AckBitmap, DecodedBurst, FRAME_VERSION, HEADER_BYTES,
                      LEGACY_FRAME_VERSION, OfdmFrameError, OfdmFrameType,
                      PhyHeader, SubBlock, build_burst, burst_duration,
                      decode_burst, protocol_overhead_bytes, split_blocks)
from .link import HalfDuplexPipe, OfdmLink, SimulatedDuplexPipe, simulated_pair
from .metrics import AdaptationState, LinkMetrics, OfdmStatus
from .phy import BurstReceiver, OfdmModulator

__all__ = [
    "AdaptationState",
    "AdaptationConfig",
    "AckBitmap",
    "BENCH",
    "BURST_LADDER",
    "BurstReceiver",
    "DEFAULT_MCS_INDEX",
    "DEFAULT_PROFILE_NAME",
    "DecodedBurst",
    "FEC_SPECS",
    "FRAME_VERSION",
    "FecProfile",
    "FecSpec",
    "HEADER_BYTES",
    "HEADER_MCS",
    "HalfDuplexPipe",
    "LinkMetrics",
    "LinkAdaptationController",
    "LEGACY_FRAME_VERSION",
    "MCS_TABLE",
    "Mcs",
    "OfdmConfigError",
    "OfdmFrameError",
    "OfdmFrameType",
    "OfdmLink",
    "OfdmModulator",
    "OfdmProfile",
    "OfdmStatus",
    "PROFILES",
    "PhyHeader",
    "SimulatedDuplexPipe",
    "SubBlock",
    "TxProfile",
    "best_mcs_for",
    "build_burst",
    "burst_duration",
    "decode_burst",
    "effective_rate",
    "encoded_bits",
    "fec_profile",
    "fec_spec",
    "mcs",
    "profile",
    "profile_names",
    "profile_or_default",
    "protocol_overhead_bytes",
    "simulated_pair",
    "split_blocks",
]
