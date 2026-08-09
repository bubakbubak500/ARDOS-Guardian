"""Bytes to bursts: header, FEC, interleaving, constellation, and back.

A burst is two independently coded sections:

    [preamble][training][ header, always MCS0 ][ data, MCS from the header ]

The header is fixed-size, versioned, CRC-protected and always sent at the most
robust MCS, so a receiver can decode it without knowing anything about the
payload in advance -- including which constellation the payload used. That is
what makes adaptive modulation possible later without a negotiation round.

The data section carries its own CRC. A block whose payload CRC fails is
reported as failed and its bytes are discarded; the one thing this layer will
never do is hand a caller bytes it is not sure of.

One burst is deliberately *not* one Guardian attachment. Payloads are cut into
bounded blocks from the start (`OfdmProfile.block_size`), because a half-hour
transmission that has to restart from the beginning is not a usable link.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from ..modem.fec import K as FEC_K
from ..modem.fec import conv_encode, viterbi_decode_soft
from ..protocol import crc16
from .config import HEADER_MCS, OfdmConfigError, OfdmProfile, mcs
from .constellation import bits_per_symbol, demap_llr, evm, map_bits
from .interleaving import deinterleave, interleave
from .metrics import LinkMetrics
from .phy import BurstReceiver, OfdmModulator
from .sync import candidates

#: OFDM frame-format version. Independent of the ARDOS control-frame version:
#: this one describes the payload waveform, which no legacy station ever hears.
FRAME_VERSION = 1

# version, frame_type, msg_id, block_seq, block_count, mcs, payload_len, flags
_HEADER = struct.Struct(">BBIHHBHB")
_CRC = struct.Struct(">H")
#: Header size on the wire, CRC included.
HEADER_BYTES = _HEADER.size + _CRC.size

#: Bits the convolutional encoder appends to flush its register.
_FLUSH_BITS = FEC_K - 1


class OfdmFrameError(Exception):
    """A burst that cannot be trusted: bad version, bad CRC, or truncated."""


class OfdmFrameType(IntEnum):
    DATA = 1
    ACK = 2
    NACK = 3

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class PhyHeader:
    """The robust bootstrap header at the front of every burst."""

    frame_type: OfdmFrameType
    msg_id: int
    block_seq: int = 0
    block_count: int = 1
    mcs: int = HEADER_MCS.index
    payload_len: int = 0
    #: Reserved. A per-subcarrier bit-loading map would announce itself here.
    flags: int = 0
    version: int = FRAME_VERSION

    def encode(self) -> bytes:
        body = _HEADER.pack(
            int(self.version) & 0xFF,
            int(self.frame_type) & 0xFF,
            int(self.msg_id) & 0xFFFFFFFF,
            int(self.block_seq) & 0xFFFF,
            int(self.block_count) & 0xFFFF,
            int(self.mcs) & 0xFF,
            int(self.payload_len) & 0xFFFF,
            int(self.flags) & 0xFF,
        )
        return body + _CRC.pack(crc16(body))

    @classmethod
    def decode(cls, raw: bytes) -> "PhyHeader":
        if len(raw) < HEADER_BYTES:
            raise OfdmFrameError(f"header is {len(raw)} bytes, need {HEADER_BYTES}")
        body = raw[: _HEADER.size]
        given = _CRC.unpack_from(raw, _HEADER.size)[0]
        want = crc16(body)
        if given != want:
            raise OfdmFrameError(f"header CRC {given:#06x}, computed {want:#06x}")
        version, ftype, msg_id, seq, count, index, length, flags = _HEADER.unpack(body)
        if version != FRAME_VERSION:
            raise OfdmFrameError(f"unsupported OFDM frame version {version}")
        try:
            frame_type = OfdmFrameType(ftype)
        except ValueError:
            raise OfdmFrameError(f"unknown OFDM frame type {ftype}") from None
        # Reject an out-of-table MCS here rather than letting the data section
        # be demapped with a constellation the sender never used. It surfaces as
        # a frame error, not a config error: this is untrusted input off the air,
        # and `decode_burst` has to be able to catch everything a bad burst does.
        try:
            mcs(index)
        except OfdmConfigError as exc:
            raise OfdmFrameError(str(exc)) from None
        return cls(
            frame_type=frame_type,
            msg_id=msg_id,
            block_seq=seq,
            block_count=count,
            mcs=index,
            payload_len=length,
            flags=flags,
            version=version,
        )

    def summary(self) -> str:
        parts = [self.frame_type.label, f"id={self.msg_id}"]
        if self.frame_type is OfdmFrameType.DATA:
            parts.append(f"block {self.block_seq + 1}/{self.block_count}")
            parts.append(f"{self.payload_len} B")
            parts.append(f"MCS{self.mcs}")
        else:
            parts.append(f"block {self.block_seq + 1}")
        return " ".join(parts)


# -- section coding --------------------------------------------------------- #

def coded_bits(byte_count: int) -> int:
    """Coded bits the rate-1/2 encoder produces for `byte_count` bytes."""
    return 2 * (byte_count * 8 + _FLUSH_BITS)


def section_symbols(profile: OfdmProfile, byte_count: int, modulation: str) -> int:
    """OFDM symbols one coded section occupies."""
    per_symbol = profile.coded_bits_per_symbol(bits_per_symbol(modulation))
    return int(math.ceil(coded_bits(byte_count) / per_symbol))


def header_symbols(profile: OfdmProfile) -> int:
    """OFDM symbols the header occupies. Fixed -- the receiver counts on it."""
    return section_symbols(profile, HEADER_BYTES, HEADER_MCS.modulation)


def encode_section(profile: OfdmProfile, data: bytes, modulation: str) -> np.ndarray:
    """Code, interleave and map one section into a (symbols, carriers) grid."""
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    coded = conv_encode(bits)
    per_symbol = profile.coded_bits_per_symbol(bits_per_symbol(modulation))
    symbols = int(math.ceil(len(coded) / per_symbol))
    # Zero-pad to a whole number of symbols. The padding is deterministic and
    # the receiver drops it after deinterleaving, so it costs nothing but the
    # tail of one symbol.
    padded = np.zeros(symbols * per_symbol, dtype=np.int8)
    padded[: len(coded)] = coded
    mapped = map_bits(interleave(padded), modulation)
    return mapped.reshape(symbols, profile.num_data_carriers)


def decode_section(symbols, noise_var, byte_count: int, modulation: str) -> bytes:
    """Demap, deinterleave and decode one section back to `byte_count` bytes."""
    llr = demap_llr(symbols, modulation, noise_var)
    soft = deinterleave(llr)
    bits = viterbi_decode_soft(soft[: coded_bits(byte_count)])
    return np.packbits(bits[: byte_count * 8].astype(np.uint8)).tobytes()


def reference_evm(profile: OfdmProfile, symbols, data: bytes,
                  modulation: str) -> float | None:
    """RMS error against the symbols the sender must have transmitted.

    Once a section has decoded and passed its CRC, the bytes are known, and
    re-encoding them reproduces the exact constellation points that went into
    the transmitter. Comparing those with what came out of the equaliser gives
    the true error vector -- which is a different quantity from the
    decision-directed one, and the only one worth acting on.

    The difference is not a nicety. A decision-directed measurement slices every
    symbol to its nearest point, so as a link degrades the measured error stops
    growing and starts *shrinking*: the symbols are landing near the wrong
    points, and the wrong point is close by. That is why the failing 64-QAM
    bursts in the 2026-08-09 tests reported a better EVM than the QPSK bursts
    that decoded perfectly. Against the reference they report 30%, and the
    ordering comes back the right way round.

    None when there is nothing to compare -- an empty section, or a reference
    that came out shorter than the symbols handed in.
    """
    reference = encode_section(profile, data, modulation).reshape(-1)
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    count = min(len(reference), len(symbols))
    if count < 1:
        return None
    power = float(np.mean(np.abs(reference[:count]) ** 2))
    if power <= 0.0:
        return None
    error = symbols[:count] - reference[:count]
    return float(np.sqrt(float(np.mean(np.abs(error) ** 2)) / power))


def evm_to_snr_db(evm_rms: float | None) -> float | None:
    """Turn an error-vector magnitude into the SNR it implies, in dB."""
    if evm_rms is None or not np.isfinite(evm_rms) or evm_rms <= 0.0:
        return None
    return float(-20.0 * np.log10(evm_rms))


# -- whole bursts ----------------------------------------------------------- #

def build_burst(profile: OfdmProfile, header: PhyHeader,
                payload: bytes = b"") -> np.ndarray:
    """Render one burst as real audio samples.

    An ACK or NACK carries no payload and is therefore header-only: short, and
    at the most robust MCS, because the whole ARQ loop stalls if it is missed.
    """
    if header.frame_type is OfdmFrameType.DATA:
        if len(payload) != header.payload_len:
            raise ValueError(
                f"header says {header.payload_len} payload bytes, given {len(payload)}"
            )
        if len(payload) > profile.block_size:
            raise ValueError(
                f"payload of {len(payload)} B exceeds the profile block size "
                f"{profile.block_size} B"
            )
    modulator = OfdmModulator(profile)
    grids = [encode_section(profile, header.encode(), HEADER_MCS.modulation)]
    if header.frame_type is OfdmFrameType.DATA:
        framed = payload + _CRC.pack(crc16(payload))
        grids.append(encode_section(profile, framed, mcs(header.mcs).modulation))
    return modulator.burst(np.vstack(grids))


def burst_samples(profile: OfdmProfile, header: PhyHeader) -> int:
    """Length in samples of the burst `build_burst` would produce."""
    modulator = OfdmModulator(profile)
    symbols = header_symbols(profile)
    if header.frame_type is OfdmFrameType.DATA:
        symbols += section_symbols(
            profile, header.payload_len + _CRC.size, mcs(header.mcs).modulation
        )
    return modulator.burst_samples(symbols)


def burst_duration(profile: OfdmProfile, header: PhyHeader) -> float:
    """Airtime in seconds of the burst `build_burst` would produce."""
    return burst_samples(profile, header) / profile.sample_rate


@dataclass
class DecodedBurst:
    """The outcome of trying to decode one burst."""

    header: PhyHeader | None = None
    payload: bytes | None = None
    metrics: LinkMetrics = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.metrics is None:
            self.metrics = LinkMetrics()

    @property
    def ok(self) -> bool:
        return self.metrics.frame_ok


def decode_burst(profile: OfdmProfile, samples) -> DecodedBurst:
    """Find, synchronise and decode a burst in a buffer.

    Every plausible burst position is tried in order until one produces a valid
    header, because a detector false alarm sitting earlier in the buffer than the
    real burst would otherwise lose the whole exchange. What comes back is the
    first burst that decoded, or -- if none did -- the most informative failure.

    Never raises for bad input and never returns unverified bytes: a burst that
    fails anywhere comes back with `metrics.frame_ok` False, `metrics.error`
    saying why, and `payload` None.
    """
    modulator = OfdmModulator(profile)
    found = candidates(samples, profile, modulator)
    if not found:
        return DecodedBurst(metrics=LinkMetrics(error="no burst detected"))

    # One candidate at a time, stopping at the first readable header. Decoding
    # them all first and then picking cost a Viterbi pass per surplus candidate
    # -- around 200 ms each on a 512-byte block -- inside the window the far end
    # is holding its transmitter off waiting for an answer.
    fallback: DecodedBurst | None = None
    for sync in found:
        attempt = _decode_at(profile, sync)
        if attempt.header is not None:
            return attempt
        # Nothing had a readable header yet. Keep the candidate that got
        # furthest, which is the one whose error message is worth logging.
        if fallback is None or (attempt.metrics.snr_db is not None
                                and fallback.metrics.snr_db is None):
            fallback = attempt
    return fallback if fallback is not None else DecodedBurst(
        metrics=LinkMetrics(error="no burst detected")
    )


def _decode_at(profile: OfdmProfile, sync) -> DecodedBurst:
    """Try to decode a burst at one already-synchronised position."""
    metrics = LinkMetrics(
        sync_confidence=sync.confidence,
        cfo_hz=sync.cfo_hz,
        audio_rms=sync.audio_rms,
        crest_factor_db=sync.crest_factor_db,
    )
    try:
        receiver = BurstReceiver(profile, sync.samples, sync.burst_start)
    except IndexError:
        metrics.error = "burst truncated before the training block"
        return DecodedBurst(metrics=metrics)

    metrics.snr_db = receiver.snr_db
    metrics.channel_response = receiver.channel

    head_count = header_symbols(profile)
    if receiver.available_data_symbols() < head_count:
        metrics.error = "burst truncated before the header"
        return DecodedBurst(metrics=metrics)

    head_symbols, head_var, _ = receiver.data_symbols(0, head_count)
    head_bytes = decode_section(head_symbols, head_var, HEADER_BYTES,
                                HEADER_MCS.modulation)
    try:
        header = PhyHeader.decode(head_bytes)
    except OfdmFrameError as exc:
        metrics.error = f"header rejected: {exc}"
        return DecodedBurst(metrics=metrics)

    # The header passed its own CRC, so its bytes are known and the burst can be
    # measured honestly from here on -- including when the data section is about
    # to fail, which is precisely when the operator needs the number.
    _set_evm(metrics, reference_evm(profile, head_symbols, head_bytes,
                                    HEADER_MCS.modulation), "reference_header")

    metrics.mcs = header.mcs
    if header.frame_type is not OfdmFrameType.DATA:
        metrics.frame_ok = True
        return DecodedBurst(header=header, payload=b"", metrics=metrics)

    modulation = mcs(header.mcs).modulation
    framed_bytes = header.payload_len + _CRC.size
    data_count = section_symbols(profile, framed_bytes, modulation)
    if receiver.available_data_symbols() < head_count + data_count:
        metrics.error = (
            f"burst holds {receiver.available_data_symbols() - head_count} of the "
            f"{data_count} data symbols the header announced"
        )
        return DecodedBurst(header=header, metrics=metrics)

    data_symbols, data_var, slope = receiver.data_symbols(head_count, data_count)
    metrics.phase_slope = slope
    framed = decode_section(data_symbols, data_var, framed_bytes, modulation)
    payload, given = framed[: header.payload_len], framed[header.payload_len:]
    want = _CRC.pack(crc16(payload))
    if given != want:
        # The data bytes are not trustworthy, so they cannot serve as a
        # reference. The header measurement already on `metrics` was taken at
        # MCS0 over the same channel moments earlier and is the better estimate;
        # keep it, and fall back to decision-directed only if it is missing.
        if metrics.evm_rms is None:
            _set_evm(metrics, evm(data_symbols, modulation), "decision_directed")
        metrics.error = f"payload CRC failed on block {header.block_seq}"
        return DecodedBurst(header=header, metrics=metrics)

    # The whole data section is known now: far more symbols than the header, and
    # at the constellation actually in use, so it supersedes the header figure.
    _set_evm(metrics, reference_evm(profile, data_symbols, framed, modulation),
             "reference")
    metrics.frame_ok = True
    return DecodedBurst(header=header, payload=payload, metrics=metrics)


def _set_evm(metrics: LinkMetrics, value: float | None, source: str) -> None:
    """Record an error-vector measurement and the SNR it implies."""
    if value is None:
        return
    metrics.evm_rms = value
    metrics.evm_source = source
    metrics.residual_snr_db = evm_to_snr_db(value)


def split_blocks(payload: bytes, block_size: int) -> list[bytes]:
    """Cut a message into bounded blocks. An empty message is one empty block."""
    if block_size < 1:
        raise ValueError("block_size must be >= 1")
    if not payload:
        return [b""]
    return [payload[i: i + block_size] for i in range(0, len(payload), block_size)]
