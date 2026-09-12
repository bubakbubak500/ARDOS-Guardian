"""Shared SC-FTN modem contracts.

The package name is retained for compatibility with the payload backend and
stored profile data.  The production waveform is SC-FTN; framing, FEC,
adaptation, metrics and the ARQ state machine live here because they are shared
by the audio backend and deterministic tests.  Imports of the heavier framing,
link and simulation layers are lazy so importing a configuration or FEC helper
does not initialise a physical-layer implementation.
"""

from __future__ import annotations

from importlib import import_module

from .coding import (
    FEC_SPECS, FecProfile, FecSpec, combine_harq_soft, decode_soft,
    effective_rate, encode_bits, encoded_bits, fec_profile, fec_spec,
    iterative_decode_candidates, puncture, depuncture,
)
from .config import (
    BENCH, DEFAULT_MCS_INDEX, DEFAULT_PROFILE_NAME, HEADER_MCS, MCS_TABLE,
    SC_MCS_TABLE, Mcs, OfdmConfigError, OfdmProfile, PROFILES, best_mcs_for,
    mcs, profile, profile_names, profile_or_default, sc_mcs,
)

_LAZY = {
    "AckBitmap": ("framing", "AckBitmap"),
    "CAPACITY_FRAME_VERSION": ("framing", "CAPACITY_FRAME_VERSION"),
    "DecodedBurst": ("framing", "DecodedBurst"),
    "FRAME_VERSION": ("framing", "FRAME_VERSION"),
    "HEADER_BYTES": ("framing", "HEADER_BYTES"),
    "LEGACY_FRAME_VERSION": ("framing", "LEGACY_FRAME_VERSION"),
    "SUPERFRAME_VERSION": ("framing", "SUPERFRAME_VERSION"),
    "MAX_CAPACITY_SUBBLOCKS": ("framing", "MAX_CAPACITY_SUBBLOCKS"),
    "MAX_SUBBLOCKS": ("framing", "MAX_SUBBLOCKS"),
    "MAX_SUPERFRAME_SUBBLOCKS": ("framing", "MAX_SUPERFRAME_SUBBLOCKS"),
    "OfdmFrameError": ("framing", "OfdmFrameError"),
    "OfdmFrameType": ("framing", "OfdmFrameType"),
    "PhyHeader": ("framing", "PhyHeader"),
    "SubBlock": ("framing", "SubBlock"),
    "build_burst": ("framing", "build_burst"),
    "burst_duration": ("framing", "burst_duration"),
    "decode_burst": ("framing", "decode_burst"),
    "decode_many": ("framing", "decode_many"),
    "probe_burst_info": ("framing", "probe_burst_info"),
    "probe_burst_span": ("framing", "probe_burst_span"),
    "protocol_overhead_bytes": ("framing", "protocol_overhead_bytes"),
    "split_blocks": ("framing", "split_blocks"),
    "max_arq_block_bytes": ("framing", "max_arq_block_bytes"),
    "BurstCodec": ("link", "BurstCodec"),
    "HalfDuplexPipe": ("link", "HalfDuplexPipe"),
    "OfdmBurstCodec": ("link", "OfdmBurstCodec"),
    "OfdmLink": ("link", "OfdmLink"),
    "SimulatedDuplexPipe": ("link", "SimulatedDuplexPipe"),
    "simulated_pair": ("link", "simulated_pair"),
    "AdaptationState": ("metrics", "AdaptationState"),
    "LinkMetrics": ("metrics", "LinkMetrics"),
    "OfdmStatus": ("metrics", "OfdmStatus"),
    "BURST_LADDER": ("adaptation", "BURST_LADDER"),
    "AdaptationConfig": ("adaptation", "AdaptationConfig"),
    "LinkAdaptationController": ("adaptation", "LinkAdaptationController"),
    "TxProfile": ("adaptation", "TxProfile"),
    "analytic": ("phy", "analytic"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(name) from None
    value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = sorted(set(_LAZY) | {
    "FEC_SPECS", "FecProfile", "FecSpec", "combine_harq_soft", "decode_soft",
    "effective_rate", "encode_bits", "encoded_bits", "fec_profile", "fec_spec",
    "iterative_decode_candidates", "puncture", "depuncture", "BENCH",
    "DEFAULT_MCS_INDEX", "DEFAULT_PROFILE_NAME", "HEADER_MCS", "MCS_TABLE",
    "SC_MCS_TABLE", "Mcs", "OfdmConfigError", "OfdmProfile", "PROFILES",
    "best_mcs_for", "mcs", "profile", "profile_names", "profile_or_default",
    "sc_mcs",
})
