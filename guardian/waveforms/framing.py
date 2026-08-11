"""Shared byte framing for SC-HS, SC-FTN and SEFDM physical layers.

The wire header, CRCs, FEC profiles, manifest and selective-repeat semantics are
the proven Guardian G2 format.  Only the mapping of coded constellation points
to audio differs from OFDM.
"""

from __future__ import annotations

import math

import numpy as np

from ..ofdm.coding import (FecProfile, decode_soft, encode_bits, encoded_bits,
                           fec_profile)
from ..ofdm.config import HEADER_MCS, sc_mcs
from ..ofdm.framing import (
    HEADER_BYTES, LEGACY_FRAME_VERSION, MAX_ARQ_BLOCK_BYTES, AckBitmap,
    DecodedBurst, OfdmFrameError, OfdmFrameType, PhyHeader, SubBlock, _CRC,
    _decode_manifest, _encode_manifest, _normalise_blocks, _set_evm,
    evm_to_snr_db, protocol_overhead_bytes,
)
from ..ofdm.interleaving import deinterleave, interleave
from ..protocol import crc16
from .config import WaveformProfile
from .constellation import bits_per_symbol, demap_llr, map_bits
from .phy import make_modulator, make_receiver


def section_blocks(profile: WaveformProfile, byte_count: int, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> int:
    per_block = profile.points_per_block * bits_per_symbol(modulation)
    return int(math.ceil(encoded_bits(byte_count, fec) / per_block))


def header_blocks(profile: WaveformProfile) -> int:
    return section_blocks(profile, HEADER_BYTES, HEADER_MCS.modulation)


def encode_section(profile: WaveformProfile, data: bytes, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    coded = encode_bits(bits, fec)
    per_block = profile.points_per_block * bits_per_symbol(modulation)
    blocks = int(math.ceil(len(coded) / per_block))
    padded = np.zeros(blocks * per_block, dtype=np.int8)
    padded[:len(coded)] = coded
    return map_bits(interleave(padded), modulation).reshape(
        blocks, profile.points_per_block
    )


def decode_section(symbols, noise_var, byte_count: int, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> bytes:
    llr = demap_llr(symbols, modulation, noise_var)
    soft = deinterleave(llr)
    count = encoded_bits(byte_count, fec)
    bits = decode_soft(soft[:count], byte_count, fec)
    return np.packbits(bits[:byte_count * 8].astype(np.uint8)).tobytes()


def reference_evm(profile: WaveformProfile, symbols, data: bytes,
                  modulation: str,
                  fec: FecProfile | int = FecProfile.FEC_1_2) -> float | None:
    reference = encode_section(profile, data, modulation, fec).reshape(-1)
    observed = np.asarray(symbols, dtype=np.complex128).reshape(-1)
    count = min(len(reference), len(observed))
    if count < 1:
        return None
    error = observed[:count] - reference[:count]
    power = float(np.mean(np.abs(reference[:count]) ** 2))
    if power <= 0.0:
        return None
    return float(np.sqrt(np.mean(np.abs(error) ** 2) / power))


class ExperimentalBurstCodec:
    """BurstCodec implementation for all non-OFDM Guardian G2 profiles."""

    @staticmethod
    def build_burst(profile: WaveformProfile, header: PhyHeader,
                    payload: bytes = b"", *,
                    blocks: list[SubBlock] | None = None) -> np.ndarray:
        grids = [encode_section(profile, header.encode(), HEADER_MCS.modulation)]
        if header.frame_type is OfdmFrameType.DATA:
            if header.version == LEGACY_FRAME_VERSION:
                if blocks is not None or len(payload) != header.payload_len:
                    raise ValueError("legacy DATA header does not match its payload")
                framed = payload + _CRC.pack(crc16(payload))
                grids.append(encode_section(
                    profile, framed, sc_mcs(header.mcs).modulation
                ))
            else:
                members = _normalise_blocks(header, payload, blocks)
                if any(len(block.payload) > MAX_ARQ_BLOCK_BYTES for block in members):
                    raise ValueError("sub-block exceeds the protocol ARQ block size")
                if len({block.sequence for block in members}) != len(members):
                    raise ValueError("burst contains duplicate sub-block numbers")
                grids.append(encode_section(
                    profile, _encode_manifest(members), HEADER_MCS.modulation
                ))
                for block in members:
                    framed = block.payload + _CRC.pack(crc16(block.payload))
                    grids.append(encode_section(
                        profile, framed, sc_mcs(header.mcs).modulation, header.fec
                    ))
        elif payload:
            if len(payload) != header.payload_len:
                raise ValueError("control header does not match its payload")
            framed = payload + _CRC.pack(crc16(payload))
            grids.append(encode_section(profile, framed, HEADER_MCS.modulation))
        return make_modulator(profile).burst(np.vstack(grids))

    @staticmethod
    def burst_samples(profile: WaveformProfile, header: PhyHeader,
                      block_lengths: list[int] | None = None) -> int:
        blocks = header_blocks(profile)
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
                manifest_bytes = header.subblock_count * 4 + _CRC.size
                blocks += section_blocks(
                    profile, manifest_bytes, HEADER_MCS.modulation
                )
            blocks += sum(
                section_blocks(profile, length + _CRC.size,
                               sc_mcs(header.mcs).modulation, header.fec)
                for length in lengths
            )
        elif header.payload_len:
            blocks += section_blocks(
                profile, header.payload_len + _CRC.size, HEADER_MCS.modulation
            )
        return make_modulator(profile).burst_samples(blocks)

    @classmethod
    def burst_duration(cls, profile: WaveformProfile, header: PhyHeader,
                       block_lengths: list[int] | None = None) -> float:
        return cls.burst_samples(profile, header, block_lengths) / profile.sample_rate

    @staticmethod
    def decode_burst(profile: WaveformProfile, samples) -> DecodedBurst:
        try:
            receiver = make_receiver(profile, samples)
        except (ValueError, IndexError, np.linalg.LinAlgError) as exc:
            from ..ofdm.metrics import LinkMetrics
            return DecodedBurst(metrics=LinkMetrics(error=str(exc)))

        metrics = receiver.metrics
        head_count = header_blocks(profile)
        if receiver.available_data_symbols() < head_count:
            metrics.error = "burst truncated before the header"
            return DecodedBurst(metrics=metrics)
        head_symbols, head_var, _ = receiver.data_symbols(0, head_count)
        head_bytes = decode_section(
            head_symbols, head_var, HEADER_BYTES, HEADER_MCS.modulation
        )
        try:
            header = PhyHeader.decode(head_bytes, mcs_lookup=sc_mcs)
        except OfdmFrameError as exc:
            metrics.error = f"header rejected: {exc}"
            return DecodedBurst(metrics=metrics)

        _set_evm(metrics, reference_evm(
            profile, head_symbols, head_bytes, HEADER_MCS.modulation
        ), "reference_header")
        metrics.mcs = header.mcs
        cursor = head_count

        if header.frame_type is not OfdmFrameType.DATA:
            if header.payload_len == 0:
                metrics.frame_ok = True
                return DecodedBurst(header=header, payload=b"", metrics=metrics)
            framed_bytes = header.payload_len + _CRC.size
            count = section_blocks(profile, framed_bytes, HEADER_MCS.modulation)
            if receiver.available_data_symbols() < cursor + count:
                metrics.error = "control payload is truncated"
                return DecodedBurst(header=header, metrics=metrics)
            values, variance, slope = receiver.data_symbols(cursor, count)
            metrics.phase_slope = slope
            framed = decode_section(
                values, variance, framed_bytes, HEADER_MCS.modulation
            )
            payload, given = framed[:header.payload_len], framed[header.payload_len:]
            if given != _CRC.pack(crc16(payload)):
                metrics.error = "control payload CRC failed"
                return DecodedBurst(header=header, metrics=metrics)
            _set_evm(metrics, reference_evm(
                profile, values, framed, HEADER_MCS.modulation
            ), "reference")
            metrics.frame_ok = True
            return DecodedBurst(header=header, payload=payload, metrics=metrics)

        if header.version == LEGACY_FRAME_VERSION:
            modulation = sc_mcs(header.mcs).modulation
            framed_bytes = header.payload_len + _CRC.size
            count = section_blocks(profile, framed_bytes, modulation)
            if receiver.available_data_symbols() < cursor + count:
                metrics.error = "legacy data payload is truncated"
                return DecodedBurst(header=header, metrics=metrics)
            values, variance, slope = receiver.data_symbols(cursor, count)
            metrics.phase_slope = slope
            framed = decode_section(values, variance, framed_bytes, modulation)
            payload, given = framed[:header.payload_len], framed[header.payload_len:]
            if given != _CRC.pack(crc16(payload)):
                metrics.error = f"payload CRC failed on block {header.block_seq}"
                return DecodedBurst(header=header, failed_blocks={header.block_seq},
                                    block_order=(header.block_seq,), metrics=metrics)
            _set_evm(metrics, reference_evm(profile, values, framed, modulation),
                     "reference")
            metrics.frame_ok = True
            return DecodedBurst(
                header=header, payload=payload, blocks={header.block_seq: payload},
                block_order=(header.block_seq,), metrics=metrics,
            )

        manifest_bytes = header.subblock_count * 4 + _CRC.size
        manifest_count = section_blocks(
            profile, manifest_bytes, HEADER_MCS.modulation
        )
        if receiver.available_data_symbols() < cursor + manifest_count:
            metrics.error = "burst manifest is truncated"
            return DecodedBurst(header=header, metrics=metrics)
        values, variance, slope = receiver.data_symbols(cursor, manifest_count)
        cursor += manifest_count
        raw_manifest = decode_section(
            values, variance, manifest_bytes, HEADER_MCS.modulation
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
        lengths = dict(entries)
        if sum(length for _, length in entries) != header.payload_len:
            metrics.error = "burst manifest byte count does not match the header"
            return DecodedBurst(header=header, block_order=order,
                                block_lengths=lengths, metrics=metrics)

        modulation = sc_mcs(header.mcs).modulation
        decoded: dict[int, bytes] = {}
        failed: set[int] = set()
        slopes = [slope]
        evms: list[float] = []
        available = receiver.available_data_symbols()
        for position, (sequence, length) in enumerate(entries):
            framed_bytes = length + _CRC.size
            count = section_blocks(profile, framed_bytes, modulation, header.fec)
            if available < cursor + count:
                failed.update(item[0] for item in entries[position:])
                break
            values, variance, data_slope = receiver.data_symbols(cursor, count)
            cursor += count
            slopes.append(data_slope)
            framed = decode_section(
                values, variance, framed_bytes, modulation, header.fec
            )
            payload, given = framed[:length], framed[length:]
            if given != _CRC.pack(crc16(payload)):
                failed.add(sequence)
                continue
            decoded[sequence] = payload
            value = reference_evm(profile, values, framed, modulation, header.fec)
            if value is not None:
                evms.append(value)
        metrics.phase_slope = float(np.mean(slopes))
        if evms:
            _set_evm(metrics, float(np.sqrt(np.mean(np.square(evms)))), "reference")
        metrics.frame_ok = not failed and len(decoded) == len(entries)
        if not metrics.frame_ok:
            missing = sorted(failed | (set(order) - set(decoded)))
            metrics.error = f"sub-block CRC failed or truncated: {missing}"
        payload = (b"".join(decoded[sequence] for sequence in order)
                   if metrics.frame_ok else None)
        return DecodedBurst(
            header=header, payload=payload, blocks=decoded,
            failed_blocks=failed | (set(order) - set(decoded)),
            block_order=order, block_lengths=lengths, metrics=metrics,
        )


__all__ = [
    "AckBitmap", "ExperimentalBurstCodec", "OfdmFrameType", "PhyHeader",
    "SubBlock", "header_blocks", "section_blocks",
]
