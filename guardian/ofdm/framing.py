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

from ..protocol import crc16
from .coding import (FecProfile, combine_harq_soft, decode_soft, effective_rate, encode_bits,
                     iterative_decode_candidates,
                     encoded_bits, fec_profile, fec_spec)
from .config import HEADER_MCS, OfdmConfigError, OfdmProfile, mcs
from .constellation import bits_per_symbol, demap_llr, evm, map_bits
from .interleaving import deinterleave, interleave
from .metrics import LinkMetrics
from .phy import BurstReceiver, OfdmModulator
from .sync import candidates

#: OFDM frame-format version. Independent of the ARDOS control-frame version:
#: this one describes the payload waveform, which no legacy station ever hears.
FRAME_VERSION = 2
SUPERFRAME_VERSION = 3
LEGACY_FRAME_VERSION = 1

# version, frame_type, msg_id, block_seq, block_count, mcs, payload_len, flags
_HEADER = struct.Struct(">BBIHHBHB")
_CRC = struct.Struct(">H")
#: Header size on the wire, CRC included.
HEADER_BYTES = _HEADER.size + _CRC.size

_SUBBLOCK_MASK = 0x3F
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
MAX_ARQ_BLOCK_BYTES = 1024
MAX_SUBBLOCKS = 32
MAX_SUPERFRAME_SUBBLOCKS = 63


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
        ):
            raise ValueError("unsupported OFDM frame version")
        if self.version == LEGACY_FRAME_VERSION:
            scheme = int(self.mcs)
            wire_flags = int(self.flags)
        else:
            fec = fec_profile(self.fec)
            maximum = (MAX_SUPERFRAME_SUBBLOCKS
                       if self.version == SUPERFRAME_VERSION else MAX_SUBBLOCKS)
            if not 1 <= int(self.subblock_count) <= maximum:
                raise ValueError(f"subblock_count must be in 1..{maximum}")
            scheme = (int(fec) << 5) | (int(self.mcs) & 0x1F)
            wire_flags = (int(self.flags) & 0x40) | int(self.subblock_count)
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
        ):
            raise OfdmFrameError(f"unsupported OFDM frame version {version}")
        try:
            frame_type = OfdmFrameType(ftype)
        except ValueError:
            raise OfdmFrameError(f"unknown OFDM frame type {ftype}") from None
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
            try:
                fec = fec_profile(scheme >> 5)
            except ValueError as exc:
                raise OfdmFrameError(str(exc)) from None
            subblock_count = flags & _SUBBLOCK_MASK
            retransmission = bool(flags & _RETRANSMISSION_FLAG)
            reserved_flags = flags & 0x40
            maximum = (MAX_SUPERFRAME_SUBBLOCKS
                       if version == SUPERFRAME_VERSION else MAX_SUBBLOCKS)
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
        return bool(self.flags & DEFER_ACK_FLAG)


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
    """OFDM symbols one coded section occupies."""
    per_symbol = profile.coded_bits_per_symbol(bits_per_symbol(modulation))
    return int(math.ceil(coded_bits(byte_count, fec) / per_symbol))


def header_symbols(profile: OfdmProfile) -> int:
    """OFDM symbols the header occupies. Fixed -- the receiver counts on it."""
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
    soft = deinterleave(llr)[:coded_bits(byte_count, fec)]
    if prior_soft is not None:
        prior_fec, prior = prior_soft
        soft = combine_harq_soft(
            soft, fec, prior, prior_fec, byte_count
        )
    candidates = iterative_decode_candidates(soft, byte_count, fec)
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


def build_burst(profile: OfdmProfile, header: PhyHeader,
                payload: bytes = b"", *,
                blocks: list[SubBlock] | None = None) -> np.ndarray:
    """Render one burst as real audio samples.

    An ACK or NACK carries no payload and is therefore header-only: short, and
    at the most robust MCS, because the whole ARQ loop stalls if it is missed.
    """
    modulator = OfdmModulator(profile)
    grids = [encode_section(profile, header.encode(), HEADER_MCS.modulation)]
    if header.frame_type is OfdmFrameType.DATA:
        if header.version == LEGACY_FRAME_VERSION:
            if blocks is not None or len(payload) != header.payload_len:
                raise ValueError("legacy DATA header does not match its payload")
            framed = payload + _CRC.pack(crc16(payload))
            grids.append(encode_section(profile, framed, mcs(header.mcs).modulation))
        else:
            members = _normalise_blocks(header, payload, blocks)
            if any(len(block.payload) > MAX_ARQ_BLOCK_BYTES for block in members):
                raise ValueError("sub-block exceeds the protocol ARQ block size")
            if len({block.sequence for block in members}) != len(members):
                raise ValueError("burst contains duplicate sub-block numbers")
            manifest = _encode_manifest(members)
            grids.append(encode_section(profile, manifest, HEADER_MCS.modulation))
            for block in members:
                framed = block.payload + _CRC.pack(crc16(block.payload))
                grids.append(encode_section(
                    profile, framed, mcs(header.mcs).modulation, header.fec
                ))
    elif payload:
        if len(payload) != header.payload_len:
            raise ValueError("control header does not match its payload")
        framed = payload + _CRC.pack(crc16(payload))
        grids.append(encode_section(profile, framed, HEADER_MCS.modulation))
    return modulator.burst(np.vstack(grids))


def burst_samples(profile: OfdmProfile, header: PhyHeader,
                  block_lengths: list[int] | None = None) -> int:
    """Length in samples of the burst `build_burst` would produce."""
    modulator = OfdmModulator(profile)
    symbols = header_symbols(profile)
    if header.frame_type is OfdmFrameType.DATA:
        if header.version == LEGACY_FRAME_VERSION:
            lengths = [header.payload_len]
        elif block_lengths is None:
            remaining = header.payload_len
            lengths = []
            for position in range(header.subblock_count):
                slots = header.subblock_count - position
                length = min(
                    MAX_ARQ_BLOCK_BYTES,
                    int(math.ceil(remaining / max(1, slots))),
                )
                lengths.append(length)
                remaining -= length
        else:
            lengths = [int(length) for length in block_lengths]
        if header.version != LEGACY_FRAME_VERSION:
            manifest_bytes = header.subblock_count * _MANIFEST_ENTRY.size + _CRC.size
            symbols += section_symbols(profile, manifest_bytes, HEADER_MCS.modulation)
        symbols += sum(
            section_symbols(profile, length + _CRC.size,
                            mcs(header.mcs).modulation, header.fec)
            for length in lengths
        )
    elif header.payload_len:
        symbols += section_symbols(
            profile, header.payload_len + _CRC.size, HEADER_MCS.modulation
        )
    return modulator.burst_samples(symbols)


def burst_duration(profile: OfdmProfile, header: PhyHeader,
                   block_lengths: list[int] | None = None) -> float:
    """Airtime in seconds of the burst `build_burst` would produce."""
    return burst_samples(profile, header, block_lengths) / profile.sample_rate


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


def decode_burst(profile: OfdmProfile, samples, *, soft_cache=None) -> DecodedBurst:
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
        attempt = _decode_at(profile, sync, soft_cache=soft_cache)
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


def decode_many(profile: OfdmProfile, samples, *, limit: int = 32,
                soft_cache=None) -> list[DecodedBurst]:
    """Decode every independently synchronised burst in one capture window."""
    modulator = OfdmModulator(profile)
    found = candidates(samples, profile, modulator, limit=max(1, int(limit)))
    decoded: list[DecodedBurst] = []
    consumed_until = -1
    for sync in found:
        if sync.burst_start < consumed_until:
            continue
        attempt = _decode_at(profile, sync, soft_cache=soft_cache)
        if attempt.header is None:
            continue
        lengths = ([attempt.block_lengths[sequence]
                    for sequence in attempt.block_order]
                   if attempt.block_lengths else None)
        span = burst_samples(profile, attempt.header, lengths)
        attempt.sample_start = sync.burst_start
        attempt.sample_end = sync.burst_start + span
        decoded.append(attempt)
        consumed_until = attempt.sample_end
    return decoded


def _decode_at(profile: OfdmProfile, sync, *, soft_cache=None) -> DecodedBurst:
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
        return _decode_control(profile, receiver, header, head_count, metrics)
    if header.version == LEGACY_FRAME_VERSION:
        return _decode_legacy_data(profile, receiver, header, head_count, metrics)
    return _decode_v2_data(
        profile, receiver, header, head_count, metrics, soft_cache=soft_cache
    )


def _decode_control(profile: OfdmProfile, receiver: BurstReceiver,
                    header: PhyHeader, cursor: int,
                    metrics: LinkMetrics) -> DecodedBurst:
    """Decode the optional robust ACK/NACK payload and its CRC."""
    if header.payload_len == 0:
        metrics.frame_ok = True
        return DecodedBurst(header=header, payload=b"", metrics=metrics)
    framed_bytes = header.payload_len + _CRC.size
    count = section_symbols(profile, framed_bytes, HEADER_MCS.modulation)
    if receiver.available_data_symbols() < cursor + count:
        metrics.error = "control payload is truncated"
        return DecodedBurst(header=header, metrics=metrics)
    symbols, variance, slope = receiver.data_symbols(cursor, count)
    metrics.phase_slope = slope
    framed = decode_section(symbols, variance, framed_bytes, HEADER_MCS.modulation)
    payload, given = framed[:header.payload_len], framed[header.payload_len:]
    if given != _CRC.pack(crc16(payload)):
        metrics.error = "control payload CRC failed"
        return DecodedBurst(header=header, metrics=metrics)
    _set_evm(metrics, reference_evm(
        profile, symbols, framed, HEADER_MCS.modulation
    ), "reference")
    metrics.frame_ok = True
    return DecodedBurst(header=header, payload=payload, metrics=metrics)


def _decode_legacy_data(profile: OfdmProfile, receiver: BurstReceiver,
                        header: PhyHeader, cursor: int,
                        metrics: LinkMetrics) -> DecodedBurst:
    """The version-1 single-block path, retained for legacy reception."""
    modulation = mcs(header.mcs).modulation
    framed_bytes = header.payload_len + _CRC.size
    count = section_symbols(profile, framed_bytes, modulation)
    if receiver.available_data_symbols() < cursor + count:
        metrics.error = "legacy data payload is truncated"
        return DecodedBurst(header=header, metrics=metrics)
    symbols, variance, slope = receiver.data_symbols(cursor, count)
    metrics.phase_slope = slope
    framed = decode_section(symbols, variance, framed_bytes, modulation)
    payload, given = framed[:header.payload_len], framed[header.payload_len:]
    if given != _CRC.pack(crc16(payload)):
        metrics.error = f"payload CRC failed on block {header.block_seq}"
        return DecodedBurst(header=header, failed_blocks={header.block_seq},
                            block_order=(header.block_seq,), metrics=metrics)
    _set_evm(metrics, reference_evm(profile, symbols, framed, modulation),
             "reference")
    metrics.frame_ok = True
    return DecodedBurst(
        header=header, payload=payload, blocks={header.block_seq: payload},
        block_order=(header.block_seq,), metrics=metrics,
    )


def _decode_v2_data(profile: OfdmProfile, receiver: BurstReceiver,
                    header: PhyHeader, cursor: int,
                    metrics: LinkMetrics, *, soft_cache=None) -> DecodedBurst:
    """Decode a robust manifest then every independent sub-block section."""
    manifest_bytes = header.subblock_count * _MANIFEST_ENTRY.size + _CRC.size
    manifest_count = section_symbols(
        profile, manifest_bytes, HEADER_MCS.modulation
    )
    if receiver.available_data_symbols() < cursor + manifest_count:
        metrics.error = "burst manifest is truncated"
        return DecodedBurst(header=header, metrics=metrics)
    symbols, variance, slope = receiver.data_symbols(cursor, manifest_count)
    cursor += manifest_count
    metrics.phase_slope = slope
    raw_manifest = decode_section(
        symbols, variance, manifest_bytes, HEADER_MCS.modulation
    )
    try:
        entries = _decode_manifest(
            raw_manifest, header.subblock_count, header.block_count,
            max(profile.block_size, MAX_ARQ_BLOCK_BYTES),
        )
    except OfdmFrameError as exc:
        metrics.error = str(exc)
        return DecodedBurst(header=header, metrics=metrics)
    order = tuple(sequence for sequence, _ in entries)
    lengths_by_sequence = dict(entries)
    if sum(length for _, length in entries) != header.payload_len:
        metrics.error = "burst manifest byte count does not match the header"
        return DecodedBurst(header=header, block_order=order,
                            block_lengths=lengths_by_sequence, metrics=metrics)

    modulation = mcs(header.mcs).modulation
    blocks: dict[int, bytes] = {}
    failed: set[int] = set()
    slopes = [slope]
    reference_evms: list[float] = []
    reference_gmis: list[float] = []
    available = receiver.available_data_symbols()
    for position, (sequence, length) in enumerate(entries):
        framed_bytes = length + _CRC.size
        count = section_symbols(profile, framed_bytes, modulation, header.fec)
        if available < cursor + count:
            failed.update(item[0] for item in entries[position:])
            break
        data_symbols, data_var, data_slope = receiver.data_symbols(cursor, count)
        cursor += count
        slopes.append(data_slope)
        cache_key = (
            int(header.msg_id), int(sequence), int(header.mcs),
            int(framed_bytes),
        )
        prior = None if soft_cache is None else soft_cache.get(cache_key)
        framed_candidates, combined_soft = decode_section_with_soft(
            data_symbols, data_var, framed_bytes, modulation, header.fec, prior
        )
        if prior is not None:
            metrics.harq_combined_blocks += 1
        framed = framed_candidates[0]
        for iteration, candidate in enumerate(framed_candidates, start=1):
            candidate_payload = candidate[:length]
            if candidate[length:] == _CRC.pack(crc16(candidate_payload)):
                framed = candidate
                metrics.turbo_iterations = max(metrics.turbo_iterations, iteration)
                break
        payload, given = framed[:length], framed[length:]
        if given != _CRC.pack(crc16(payload)):
            failed.add(sequence)
            if soft_cache is not None:
                soft_cache[cache_key] = (header.fec, combined_soft)
            continue
        if soft_cache is not None:
            for key in [item for item in soft_cache
                        if item[0] == int(header.msg_id)
                        and item[1] == int(sequence)]:
                soft_cache.pop(key, None)
        blocks[sequence] = payload
        value = reference_evm(
            profile, data_symbols, framed, modulation, header.fec
        )
        if value is not None:
            reference_evms.append(value)
        information = reference_gmi(
            profile, data_symbols, data_var, framed, modulation, header.fec
        )
        if information is not None:
            reference_gmis.append(information)

    metrics.phase_slope = float(np.mean(slopes))
    if reference_evms:
        _set_evm(metrics, float(np.sqrt(np.mean(np.square(reference_evms)))),
                 "reference")
    if reference_gmis:
        metrics.gmi_bits_per_symbol = float(np.mean(reference_gmis))
    metrics.frame_ok = not failed and len(blocks) == len(entries)
    if not metrics.frame_ok:
        missing = sorted(failed | (set(order) - set(blocks)))
        metrics.error = f"sub-block CRC failed or truncated: {missing}"
    payload = (b"".join(blocks[sequence] for sequence in order)
               if metrics.frame_ok else None)
    return DecodedBurst(
        header=header, payload=payload, blocks=blocks,
        failed_blocks=failed | (set(order) - set(blocks)),
        block_order=order, block_lengths=lengths_by_sequence, metrics=metrics,
    )


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
