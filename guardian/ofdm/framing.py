"""Shared SC-FTN wire framing, FEC helpers and ARQ data structures.

A burst is two independently coded sections:

    [preamble][training][ header, always bootstrap ][ data, MCS from the header ]

The header is fixed-size, versioned, CRC-protected and always sent at the most
robust MCS, so a receiver can decode it without knowing anything about the
payload in advance -- including which constellation the payload used. That is
what makes adaptive modulation possible later without a negotiation round.

The data section carries its own CRC. A block whose payload CRC fails is
reported as failed and its bytes are discarded; the one thing this layer will
never do is hand a caller bytes it is not sure of.

One burst is deliberately *not* one Guardian attachment. Payloads are cut into
bounded blocks from the start (`profile.block_size`), because a half-hour
transmission that has to restart from the beginning is not a usable link.
"""

from __future__ import annotations

import math
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from ..protocol import crc16
from .coding import (FecProfile, combine_harq_soft, decode_soft, effective_rate, encode_bits,
                     iterative_decode_candidates,
                     encoded_bits, fec_profile, fec_spec)
from .config import HEADER_MCS, OfdmConfigError, OfdmProfile, mcs
from .constellation import bits_per_symbol, demap_llr, evm, map_bits
from .interleaving import deinterleave, interleave
from .metrics import LinkMetrics

#: Payload frame-format version. Independent of the ARDOS control-frame version:
#: this describes the negotiated audio burst, which no legacy station ever hears.
FRAME_VERSION = 2
SUPERFRAME_VERSION = 3
CAPACITY_FRAME_VERSION = 4
LEGACY_FRAME_VERSION = 1

# version, frame_type, msg_id, block_seq, block_count, mcs, payload_len, flags
_HEADER = struct.Struct(">BBIHHBHB")
_CRC = struct.Struct(">H")
#: Header size on the wire, CRC included.
HEADER_BYTES = _HEADER.size + _CRC.size

_SUBBLOCK_MASK = 0x3F
_CAPACITY_SUBBLOCK_MASK = 0x1F
_CAPACITY_FEC_HIGH_FLAG = 0x20
DEFER_ACK_FLAG = 0x40
# The same reserved bit has a control-frame meaning: a POLL carrying it confirms
# that the sender decoded the final ACK bitmap. DATA keeps the original
# DEFER_ACK meaning, so the wire format and older decoders remain compatible.
FINAL_ACK_CONFIRM_FLAG = 0x40
_RETRANSMISSION_FLAG = 0x80
_MANIFEST_ENTRY = struct.Struct(">HH")
_ACK_PREFIX = struct.Struct(">HbB")
_ACK_SPARSE = 0x53
_ACK_SPARSE_COUNT = struct.Struct(">H")
LEGACY_MAX_ARQ_BLOCK_BYTES = 1024
MAX_ARQ_BLOCK_BYTES = 16384
MAX_SUBBLOCKS = 32
MAX_SUPERFRAME_SUBBLOCKS = 63
MAX_CAPACITY_SUBBLOCKS = 31


def max_arq_block_bytes(version: int) -> int:
    """Largest sub-block carried by this frame version.

    Versions 2/3 retain the original 1024-byte wire contract.  Capacity v4
    explicitly opts both peers into larger independently protected sections.
    """
    return (MAX_ARQ_BLOCK_BYTES if int(version) >= CAPACITY_FRAME_VERSION
            else LEGACY_MAX_ARQ_BLOCK_BYTES)


class OfdmFrameError(Exception):
    """A burst that cannot be trusted: bad version, bad CRC, or truncated."""


class OfdmFrameType(IntEnum):
    DATA = 1
    ACK = 2
    NACK = 3
    POLL = 4

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
    #: Reserved version-2 flag bits.
    flags: int = 0
    version: int = FRAME_VERSION
    #: New fields follow every version-1 positional field for source compatibility.
    fec: FecProfile | int = FecProfile.FEC_1_2
    subblock_count: int = 1
    retransmission: bool = False

    def encode(self) -> bytes:
        if self.version not in (
            LEGACY_FRAME_VERSION, FRAME_VERSION, SUPERFRAME_VERSION,
            CAPACITY_FRAME_VERSION,
        ):
            raise ValueError("unsupported payload frame version")
        if self.version == LEGACY_FRAME_VERSION:
            scheme = int(self.mcs)
            wire_flags = int(self.flags)
        else:
            fec = fec_profile(self.fec)
            if int(fec) > 7 and self.version != CAPACITY_FRAME_VERSION:
                raise ValueError("extended FEC requires payload frame version 4")
            maximum = (
                MAX_SUPERFRAME_SUBBLOCKS if self.version == SUPERFRAME_VERSION
                else MAX_CAPACITY_SUBBLOCKS if self.version == CAPACITY_FRAME_VERSION
                else MAX_SUBBLOCKS
            )
            if not 1 <= int(self.subblock_count) <= maximum:
                raise ValueError(f"subblock_count must be in 1..{maximum}")
            scheme = ((int(fec) & 0x07) << 5) | (int(self.mcs) & 0x1F)
            wire_flags = (int(self.flags) & 0x40) | int(self.subblock_count)
            if self.version == CAPACITY_FRAME_VERSION:
                wire_flags |= ((int(fec) >> 3) & 0x01) << 5
            if self.retransmission:
                wire_flags |= _RETRANSMISSION_FLAG
        body = _HEADER.pack(
            int(self.version) & 0xFF,
            int(self.frame_type) & 0xFF,
            int(self.msg_id) & 0xFFFFFFFF,
            int(self.block_seq) & 0xFFFF,
            int(self.block_count) & 0xFFFF,
            scheme & 0xFF,
            int(self.payload_len) & 0xFFFF,
            wire_flags & 0xFF,
        )
        return body + _CRC.pack(crc16(body))

    @classmethod
    def decode(cls, raw: bytes, *, mcs_lookup=None) -> "PhyHeader":
        if len(raw) < HEADER_BYTES:
            raise OfdmFrameError(f"header is {len(raw)} bytes, need {HEADER_BYTES}")
        body = raw[: _HEADER.size]
        given = _CRC.unpack_from(raw, _HEADER.size)[0]
        want = crc16(body)
        if given != want:
            raise OfdmFrameError(f"header CRC {given:#06x}, computed {want:#06x}")
        version, ftype, msg_id, seq, count, scheme, length, flags = _HEADER.unpack(body)
        if version not in (
            LEGACY_FRAME_VERSION, FRAME_VERSION, SUPERFRAME_VERSION,
            CAPACITY_FRAME_VERSION,
        ):
            raise OfdmFrameError(f"unsupported payload frame version {version}")
        try:
            frame_type = OfdmFrameType(ftype)
        except ValueError:
            raise OfdmFrameError(f"unknown payload frame type {ftype}") from None
        # Reject an out-of-table MCS here rather than letting the data section
        # be demapped with a constellation the sender never used. It surfaces as
        # a frame error, not a config error: this is untrusted input off the air,
        # and `decode_burst` has to be able to catch everything a bad burst does.
        if version == LEGACY_FRAME_VERSION:
            index = scheme
            fec = FecProfile.FEC_1_2
            subblock_count = 1
            retransmission = False
            reserved_flags = flags
        else:
            index = scheme & 0x1F
            fec_value = scheme >> 5
            if version == CAPACITY_FRAME_VERSION:
                fec_value |= ((flags & _CAPACITY_FEC_HIGH_FLAG) >> 5) << 3
            try:
                fec = fec_profile(fec_value)
            except ValueError as exc:
                raise OfdmFrameError(str(exc)) from None
            subblock_count = flags & (
                _CAPACITY_SUBBLOCK_MASK
                if version == CAPACITY_FRAME_VERSION else _SUBBLOCK_MASK
            )
            retransmission = bool(flags & _RETRANSMISSION_FLAG)
            reserved_flags = flags & 0x40
            maximum = (
                MAX_SUPERFRAME_SUBBLOCKS if version == SUPERFRAME_VERSION
                else MAX_CAPACITY_SUBBLOCKS if version == CAPACITY_FRAME_VERSION
                else MAX_SUBBLOCKS
            )
            if not 1 <= subblock_count <= maximum:
                raise OfdmFrameError(f"invalid sub-block count {subblock_count}")
        try:
            (mcs if mcs_lookup is None else mcs_lookup)(index)
        except OfdmConfigError as exc:
            raise OfdmFrameError(str(exc)) from None
        return cls(
            frame_type=frame_type,
            msg_id=msg_id,
            block_seq=seq,
            block_count=count,
            mcs=index,
            fec=fec,
            payload_len=length,
            subblock_count=subblock_count,
            retransmission=retransmission,
            flags=reserved_flags,
            version=version,
        )

    def summary(self) -> str:
        parts = [self.frame_type.label, f"id={self.msg_id}"]
        if self.frame_type is OfdmFrameType.DATA:
            parts.append(f"burst {self.block_seq}")
            parts.append(f"{self.subblock_count} block(s)/{self.block_count}")
            parts.append(f"{self.payload_len} B")
            parts.append(f"MCS{self.mcs}")
            parts.append(f"FEC {fec_spec(self.fec).label}")
        else:
            parts.append(f"burst {self.block_seq}")
        return " ".join(parts)

    @property
    def defer_ack(self) -> bool:
        """This DATA burst is followed by another under the same PTT."""
        return (self.frame_type is OfdmFrameType.DATA
                and bool(self.flags & DEFER_ACK_FLAG))


@dataclass(frozen=True)
class SubBlock:
    """One independently recoverable application block inside a keyed burst."""

    sequence: int
    payload: bytes


@dataclass(frozen=True)
class AckBitmap:
    """Compact selective-repeat state carried by one robust control section."""

    total_blocks: int
    received: frozenset[int]
    remote_snr_db: float | None = None
    remote_evm_rms: float | None = None

    def encode(self) -> bytes:
        if not 1 <= int(self.total_blocks) <= 0xFFFF:
            raise ValueError("ACK total_blocks must be in 1..65535")
        bitmap = bytearray((self.total_blocks + 7) // 8)
        for sequence in self.received:
            if not 0 <= int(sequence) < self.total_blocks:
                raise ValueError(f"ACK block {sequence} is outside the message")
            bitmap[sequence // 8] |= 1 << (7 - sequence % 8)
        snr = (-128 if self.remote_snr_db is None else
               max(-127, min(127, round(float(self.remote_snr_db) * 2.0))))
        evm = (255 if self.remote_evm_rms is None else
               max(0, min(254, round(float(self.remote_evm_rms) * 200.0))))
        return _ACK_PREFIX.pack(self.total_blocks, snr, evm) + bytes(bitmap)

    def encode_compact(self) -> bytes:
        """Use a sparse missing-list only when it is shorter than the bitmap."""
        dense = self.encode()
        missing = sorted(set(range(self.total_blocks)) - set(self.received))
        prefix = dense[:_ACK_PREFIX.size]
        sparse = (prefix + bytes([_ACK_SPARSE])
                  + _ACK_SPARSE_COUNT.pack(len(missing))
                  + b"".join(struct.pack(">H", value) for value in missing))
        return sparse if len(sparse) < len(dense) else dense

    @classmethod
    def decode(cls, raw: bytes) -> "AckBitmap":
        if len(raw) < _ACK_PREFIX.size:
            raise OfdmFrameError("ACK bitmap is truncated")
        total, snr, evm = _ACK_PREFIX.unpack_from(raw)
        if len(raw) > _ACK_PREFIX.size and raw[_ACK_PREFIX.size] == _ACK_SPARSE:
            pos = _ACK_PREFIX.size + 1
            if len(raw) < pos + _ACK_SPARSE_COUNT.size:
                raise OfdmFrameError("sparse ACK is truncated")
            count = _ACK_SPARSE_COUNT.unpack_from(raw, pos)[0]
            pos += _ACK_SPARSE_COUNT.size
            if total < 1 or len(raw) != pos + count * 2:
                raise OfdmFrameError("sparse ACK has the wrong length")
            missing = {
                struct.unpack_from(">H", raw, pos + index * 2)[0]
                for index in range(count)
            }
            if any(value >= total for value in missing) or len(missing) != count:
                raise OfdmFrameError("sparse ACK contains an invalid block")
            return cls(
                total_blocks=total,
                received=frozenset(set(range(total)) - missing),
                remote_snr_db=None if snr == -128 else snr / 2.0,
                remote_evm_rms=None if evm == 255 else evm / 200.0,
            )
        expected = _ACK_PREFIX.size + (total + 7) // 8
        if total < 1 or len(raw) != expected:
            raise OfdmFrameError(
                f"ACK bitmap is {len(raw)} bytes, expected {expected} for {total} blocks"
            )
        bitmap = raw[_ACK_PREFIX.size:]
        received = frozenset(
            sequence for sequence in range(total)
            if bitmap[sequence // 8] & (1 << (7 - sequence % 8))
        )
        return cls(
            total_blocks=total, received=received,
            remote_snr_db=None if snr == -128 else snr / 2.0,
            remote_evm_rms=None if evm == 255 else evm / 200.0,
        )


# -- section coding --------------------------------------------------------- #

def coded_bits(byte_count: int,
               fec: FecProfile | int = FecProfile.FEC_1_2) -> int:
    """Actual transmitted coded bits, including flush and puncturing."""
    return encoded_bits(byte_count, fec)


def section_symbols(profile: OfdmProfile, byte_count: int, modulation: str,
                    fec: FecProfile | int = FecProfile.FEC_1_2) -> int:
    """SC-FTN data points one coded section occupies."""
    per_symbol = profile.coded_bits_per_symbol(bits_per_symbol(modulation))
    return int(math.ceil(coded_bits(byte_count, fec) / per_symbol))


def header_symbols(profile: OfdmProfile) -> int:
    """SC-FTN data points the header occupies. Fixed for the receiver."""
    return section_symbols(profile, HEADER_BYTES, HEADER_MCS.modulation)


def encode_section(profile: OfdmProfile, data: bytes, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> np.ndarray:
    """Code, interleave and map one section into a (symbols, carriers) grid."""
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    coded = encode_bits(bits, fec)
    per_symbol = profile.coded_bits_per_symbol(bits_per_symbol(modulation))
    symbols = int(math.ceil(len(coded) / per_symbol))
    # Zero-pad to a whole number of symbols. The padding is deterministic and
    # the receiver drops it after deinterleaving, so it costs nothing but the
    # tail of one symbol.
    padded = np.zeros(symbols * per_symbol, dtype=np.int8)
    padded[: len(coded)] = coded
    mapped = map_bits(interleave(padded), modulation)
    return mapped.reshape(symbols, profile.num_data_carriers)


def decode_section(symbols, noise_var, byte_count: int, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> bytes:
    """Demap, deinterleave and decode one section back to `byte_count` bytes."""
    llr = demap_llr(symbols, modulation, noise_var)
    soft = deinterleave(llr)
    bits = decode_soft(soft[: coded_bits(byte_count, fec)], byte_count, fec)
    return np.packbits(bits[: byte_count * 8].astype(np.uint8)).tobytes()


def decode_section_with_soft(symbols, noise_var, byte_count: int, modulation: str,
                             fec: FecProfile | int = FecProfile.FEC_1_2,
                             prior_soft=None) -> tuple[list[bytes], np.ndarray]:
    """Decode a section and expose deinterleaved LLRs for Chase-HARQ."""
    llr = demap_llr(symbols, modulation, noise_var)
    current_soft = deinterleave(llr)[:coded_bits(byte_count, fec)]
    soft = current_soft
    def crc_accepts(bits) -> bool:
        raw = np.packbits(bits[:byte_count * 8].astype(np.uint8)).tobytes()
        return (len(raw) >= _CRC.size
                and raw[-_CRC.size:] == _CRC.pack(crc16(raw[:-_CRC.size])))

    candidate_sets = [iterative_decode_candidates(
        current_soft, byte_count, fec, accept=crc_accepts,
    )]
    fresh_ok = crc_accepts(candidate_sets[0][-1])
    if prior_soft is not None and not fresh_ok:
        prior_fec, prior = prior_soft
        soft = combine_harq_soft(
            current_soft, fec, prior, prior_fec, byte_count
        )
        # A previous CRC failure can contain confident but wrong LLRs (for
        # example after an erased 16-QAM sub-block).  Always try the fresh retry
        # by itself before the HARQ sum so stale evidence cannot poison a clean,
        # stronger FEC 1/2 retransmission.  The combined candidates remain the
        # fallback that recovers genuinely weak repeated observations.
        candidate_sets.append(iterative_decode_candidates(
            soft, byte_count, fec, accept=crc_accepts,
        ))
    candidates = [bits for group in candidate_sets for bits in group]
    raw = [np.packbits(bits[:byte_count * 8].astype(np.uint8)).tobytes()
           for bits in candidates]
    return raw, soft


def reference_evm(profile: OfdmProfile, symbols, data: bytes,
                  modulation: str,
                  fec: FecProfile | int = FecProfile.FEC_1_2) -> float | None:
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
    reference = encode_section(profile, data, modulation, fec).reshape(-1)
    symbols = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    count = min(len(reference), len(symbols))
    if count < 1:
        return None
    power = float(np.mean(np.abs(reference[:count]) ** 2))
    if power <= 0.0:
        return None
    error = symbols[:count] - reference[:count]
    return float(np.sqrt(float(np.mean(np.abs(error) ** 2)) / power))


def reference_gmi(profile: OfdmProfile, symbols, noise_var, data: bytes,
                  modulation: str,
                  fec: FecProfile | int = FecProfile.FEC_1_2) -> float | None:
    """Bit-metric GMI from a CRC-proven transmitted reference and soft LLRs."""
    raw = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    coded = encode_bits(raw, fec)
    per_symbol = profile.coded_bits_per_symbol(bits_per_symbol(modulation))
    padded = np.zeros(int(math.ceil(len(coded) / per_symbol)) * per_symbol,
                      dtype=np.int8)
    padded[:len(coded)] = coded
    reference = interleave(padded)
    llr = demap_llr(symbols, modulation, noise_var)
    count = min(len(reference), len(llr))
    if count < 1:
        return None
    signed = (2.0 * reference[:count] - 1.0) * llr[:count]
    per_bit = 1.0 - float(np.mean(np.logaddexp(0.0, -signed)) / np.log(2.0))
    return max(0.0, min(1.0, per_bit)) * bits_per_symbol(modulation)


def evm_to_snr_db(evm_rms: float | None) -> float | None:
    """Turn an error-vector magnitude into the SNR it implies, in dB."""
    if evm_rms is None or not np.isfinite(evm_rms) or evm_rms <= 0.0:
        return None
    return float(-20.0 * np.log10(evm_rms))


# -- whole bursts ----------------------------------------------------------- #

def _encode_manifest(blocks: list[SubBlock]) -> bytes:
    body = b"".join(
        _MANIFEST_ENTRY.pack(int(block.sequence) & 0xFFFF, len(block.payload))
        for block in blocks
    )
    return body + _CRC.pack(crc16(body))


def _decode_manifest(raw: bytes, count: int, total_blocks: int,
                     max_block_size: int) -> list[tuple[int, int]]:
    body, given = raw[:-_CRC.size], raw[-_CRC.size:]
    if given != _CRC.pack(crc16(body)):
        raise OfdmFrameError("burst manifest CRC failed")
    entries = [_MANIFEST_ENTRY.unpack_from(body, offset)
               for offset in range(0, len(body), _MANIFEST_ENTRY.size)]
    sequences = [sequence for sequence, _ in entries]
    if len(entries) != count or len(set(sequences)) != count:
        raise OfdmFrameError("burst manifest has duplicate or missing entries")
    if any(sequence >= total_blocks for sequence in sequences):
        raise OfdmFrameError("burst manifest block is outside the message")
    if any(length > max_block_size for _, length in entries):
        raise OfdmFrameError("burst manifest block exceeds the ARQ block size")
    return entries


def _normalise_blocks(header: PhyHeader, payload: bytes,
                      blocks: list[SubBlock] | None) -> list[SubBlock]:
    result = ([SubBlock(header.block_seq, bytes(payload))] if blocks is None
              else [SubBlock(int(block.sequence), bytes(block.payload))
                    for block in blocks])
    if not result:
        raise ValueError("a data burst must contain at least one sub-block")
    if len(result) != header.subblock_count:
        raise ValueError("header sub-block count does not match the manifest")
    if sum(len(block.payload) for block in result) != header.payload_len:
        raise ValueError("header payload length does not match the sub-blocks")
    return result
def protocol_overhead_bytes(header: PhyHeader) -> int:
    """Logical framing/CRC bytes, separate from payload and FEC expansion."""
    if header.frame_type is OfdmFrameType.DATA:
        if header.version == LEGACY_FRAME_VERSION:
            return HEADER_BYTES + _CRC.size
        # Header + manifest entries/CRC + one CRC per independently protected
        # subblock. This is logical overhead; coded-bit and symbol padding loss
        # is reported separately by the benchmark from actual generated samples.
        return (HEADER_BYTES + header.subblock_count * _MANIFEST_ENTRY.size
                + _CRC.size + header.subblock_count * _CRC.size)
    return HEADER_BYTES + (header.payload_len + _CRC.size
                           if header.payload_len else 0)


@dataclass
class DecodedBurst:
    """The outcome of trying to decode one burst."""

    header: PhyHeader | None = None
    payload: bytes | None = None
    blocks: dict[int, bytes] = None  # type: ignore[assignment]
    failed_blocks: set[int] = None  # type: ignore[assignment]
    block_order: tuple[int, ...] = ()
    block_lengths: dict[int, int] = None  # type: ignore[assignment]
    metrics: LinkMetrics = None  # type: ignore[assignment]
    sample_start: int | None = None
    sample_end: int | None = None

    def __post_init__(self) -> None:
        if self.metrics is None:
            self.metrics = LinkMetrics()
        if self.blocks is None:
            self.blocks = {}
        if self.failed_blocks is None:
            self.failed_blocks = set()
        if self.block_lengths is None:
            self.block_lengths = {}

    @property
    def ok(self) -> bool:
        return self.metrics.frame_ok

    @property
    def manifest_ok(self) -> bool:
        """Whether a v2 data burst can safely identify its individual blocks."""
        return bool(self.header is not None and self.block_order)


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


# -- SC-FTN codec boundary ---------------------------------------------------

def _sc_codec():
    # Keep the shared wire module importable without activating the PHY.
    from ..waveforms.framing import ExperimentalBurstCodec
    return ExperimentalBurstCodec


def build_burst(profile, header: PhyHeader, payload: bytes = b"", *,
                blocks: list[SubBlock] | None = None) -> np.ndarray:
    """Build a production SC-FTN burst through the shared codec boundary."""
    return _sc_codec().build_burst(profile, header, payload, blocks=blocks)


def burst_samples(profile, header: PhyHeader,
                  block_lengths: list[int] | None = None) -> int:
    return _sc_codec().burst_samples(profile, header, block_lengths)


def burst_duration(profile, header: PhyHeader,
                   block_lengths: list[int] | None = None) -> float:
    return _sc_codec().burst_duration(profile, header, block_lengths)


def decode_burst(profile, samples, *, start_hint: int | None = None,
                 soft_cache=None) -> DecodedBurst:
    return _sc_codec().decode_burst(profile, samples, start_hint=start_hint,
                                     soft_cache=soft_cache)


def probe_burst_info(profile, samples):
    return _sc_codec().probe_burst_info(profile, samples)


def probe_burst_span(profile, samples):
    return _sc_codec().probe_burst_span(profile, samples)


def decode_many(profile, samples, *, limit: int = 32, soft_cache=None):
    return _sc_codec().decode_many(profile, samples, limit=limit,
                                    soft_cache=soft_cache)
