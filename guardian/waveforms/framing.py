"""Shared byte framing for the production SC-FTN physical layer.

The wire header, CRCs, FEC profiles, manifest and selective-repeat semantics are
the proven Guardian G2 format.  Only the mapping of coded constellation points
to audio is implemented by the SC-FTN modulator and receiver.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from ..ofdm.coding import (FecProfile, combine_harq_soft, decode_soft, encode_bits, encoded_bits,
                           iterative_decode_candidates,
                           fec_profile, fec_spec)
from ..ofdm.config import sc_mcs
from ..ofdm.framing import (
    HEADER_BYTES, LEGACY_FRAME_VERSION, AckBitmap,
    DecodedBurst, OfdmFrameError, OfdmFrameType, PhyHeader, SubBlock, _CRC,
    _decode_manifest, _encode_manifest, _normalise_blocks, _set_evm,
    max_arq_block_bytes,
    evm_to_snr_db, protocol_overhead_bytes,
)
from ..ofdm.interleaving import deinterleave, interleave
from ..protocol import crc16
from .config import WaveformProfile
from .constellation import (bits_per_symbol, coded_capacity, demap_llr,
                            map_bits,
                            reliability_gated_refine)
from .phy import correlation_candidates, make_modulator, make_receiver


def section_blocks(profile: WaveformProfile, byte_count: int, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> int:
    per_block = coded_capacity(profile.points_per_block, modulation)
    if per_block < 1:
        raise ValueError(f"profile block is too short for {modulation}")
    return int(math.ceil(encoded_bits(byte_count, fec) / per_block))


def header_blocks(profile: WaveformProfile) -> int:
    return section_blocks(profile, HEADER_BYTES, profile.bootstrap_modulation)


def _noise_prefix(noise_var, count: int):
    variance = np.asarray(noise_var, dtype=np.float64)
    return variance.reshape(-1)[:count] if variance.ndim else noise_var


def encode_section(profile: WaveformProfile, data: bytes, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> np.ndarray:
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    coded = encode_bits(bits, fec)
    per_block = coded_capacity(profile.points_per_block, modulation)
    blocks = int(math.ceil(len(coded) / per_block))
    padded = np.zeros(blocks * per_block, dtype=np.int8)
    padded[:len(coded)] = coded
    mapped = map_bits(interleave(padded), modulation)
    grid = np.zeros(blocks * profile.points_per_block, dtype=np.complex128)
    grid[:len(mapped)] = mapped
    return grid.reshape(blocks, profile.points_per_block)


def decode_section(profile: WaveformProfile, symbols, noise_var,
                   byte_count: int, modulation: str,
                   fec: FecProfile | int = FecProfile.FEC_1_2) -> bytes:
    refined = reliability_gated_refine(symbols, modulation)
    wanted_symbols = (
        (refined.size // profile.points_per_block)
        * (profile.points_per_block // 16) * 16
        if modulation == "pas64" else refined.size
    )
    llr = demap_llr(
        refined.reshape(-1)[:wanted_symbols], modulation,
        _noise_prefix(noise_var, wanted_symbols),
    )
    soft = deinterleave(llr)
    count = encoded_bits(byte_count, fec)
    bits = decode_soft(soft[:count], byte_count, fec)
    return np.packbits(bits[:byte_count * 8].astype(np.uint8)).tobytes()


def decode_section_with_soft(profile: WaveformProfile, symbols, noise_var,
                             byte_count: int, modulation: str,
                             fec: FecProfile | int = FecProfile.FEC_1_2,
                             prior_soft=None) -> tuple[list[bytes], np.ndarray]:
    refined = reliability_gated_refine(symbols, modulation)
    wanted_symbols = (
        (refined.size // profile.points_per_block)
        * (profile.points_per_block // 16) * 16
        if modulation == "pas64" else refined.size
    )
    llr = demap_llr(
        refined.reshape(-1)[:wanted_symbols], modulation,
        _noise_prefix(noise_var, wanted_symbols),
    )
    current_soft = deinterleave(llr)[:encoded_bits(byte_count, fec)]
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
        candidate_sets.append(iterative_decode_candidates(
            soft, byte_count, fec, accept=crc_accepts,
        ))
    candidates = [bits for group in candidate_sets for bits in group]
    return ([np.packbits(bits[:byte_count * 8].astype(np.uint8)).tobytes()
             for bits in candidates], soft)


def reference_evm(profile: WaveformProfile, symbols, data: bytes,
                  modulation: str,
                  fec: FecProfile | int = FecProfile.FEC_1_2) -> float | None:
    reference = encode_section(profile, data, modulation, fec)
    observed = reliability_gated_refine(symbols, modulation)
    if modulation == "pas64":
        # The fixed-composition matcher consumes complete 16-symbol groups.
        # Each physical row is zero-filled after its last complete group; those
        # silent padding points must not make the reported EVM look better.
        usable = (profile.points_per_block // 16) * 16
        reference = reference.reshape(-1, profile.points_per_block)[:, :usable]
        observed = observed.reshape(-1, profile.points_per_block)[:, :usable]
    reference = reference.reshape(-1)
    observed = observed.reshape(-1)
    count = min(len(reference), len(observed))
    if count < 1:
        return None
    error = observed[:count] - reference[:count]
    power = float(np.mean(np.abs(reference[:count]) ** 2))
    if power <= 0.0:
        return None
    return float(np.sqrt(np.mean(np.abs(error) ** 2) / power))


def reference_gmi(profile: WaveformProfile, symbols, noise_var, data: bytes,
                  modulation: str,
                  fec: FecProfile | int = FecProfile.FEC_1_2) -> float | None:
    raw = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    coded = encode_bits(raw, fec)
    per_block = coded_capacity(profile.points_per_block, modulation)
    padded = np.zeros(int(math.ceil(len(coded) / per_block)) * per_block,
                      dtype=np.int8)
    padded[:len(coded)] = coded
    reference = interleave(padded)
    observed = reliability_gated_refine(symbols, modulation)
    wanted_symbols = (
        (observed.size // profile.points_per_block)
        * (profile.points_per_block // 16) * 16
        if modulation == "pas64" else observed.size
    )
    llr = demap_llr(
        observed.reshape(-1)[:wanted_symbols], modulation,
        _noise_prefix(noise_var, wanted_symbols),
    )
    count = min(len(reference), len(llr))
    if count < 1:
        return None
    signed = (2.0 * reference[:count] - 1.0) * llr[:count]
    per_bit = 1.0 - float(np.mean(np.logaddexp(0.0, -signed)) / np.log(2.0))
    rate = (coded_capacity(16, modulation) / 16.0
            if modulation == "pas64" else bits_per_symbol(modulation))
    return max(0.0, min(1.0, per_bit)) * rate


class ExperimentalBurstCodec:
    """BurstCodec implementation for the production SC-FTN profile family."""

    @staticmethod
    def _acquisition_lead_samples(profile: WaveformProfile,
                                  header: PhyHeader) -> int:
        if header.frame_type is not OfdmFrameType.DATA:
            return 0
        return int(round(
            profile.sample_rate * profile.data_acquisition_lead_seconds
        ))

    @staticmethod
    def build_burst(profile: WaveformProfile, header: PhyHeader,
                    payload: bytes = b"", *,
                    blocks: list[SubBlock] | None = None) -> np.ndarray:
        bootstrap = profile.bootstrap_modulation
        grids = [encode_section(profile, header.encode(), bootstrap)]
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
                if any(len(block.payload) > max_arq_block_bytes(header.version)
                       for block in members):
                    raise ValueError("sub-block exceeds the protocol ARQ block size")
                if len({block.sequence for block in members}) != len(members):
                    raise ValueError("burst contains duplicate sub-block numbers")
                grids.append(encode_section(
                    profile, _encode_manifest(members), bootstrap
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
            grids.append(encode_section(profile, framed, bootstrap))
        waveform = make_modulator(profile).burst(np.vstack(grids))
        if (header.frame_type is OfdmFrameType.DATA
                and profile.data_acquisition_lead_seconds > 0.0):
            lead = np.zeros(ExperimentalBurstCodec._acquisition_lead_samples(
                profile, header
            ))
            waveform = np.concatenate([lead, waveform])
        return waveform

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
                        max_arq_block_bytes(header.version),
                        int(math.ceil(remaining / max(1, slots))),
                    )
                    lengths.append(length)
                    remaining -= length
            else:
                lengths = [int(length) for length in block_lengths]
            if header.version != LEGACY_FRAME_VERSION:
                manifest_bytes = header.subblock_count * 4 + _CRC.size
                blocks += section_blocks(
                    profile, manifest_bytes, profile.bootstrap_modulation
                )
            blocks += sum(
                section_blocks(profile, length + _CRC.size,
                               sc_mcs(header.mcs).modulation, header.fec)
                for length in lengths
            )
        elif header.payload_len:
            blocks += section_blocks(
                profile, header.payload_len + _CRC.size,
                profile.bootstrap_modulation
            )
        samples = make_modulator(profile).burst_samples(blocks)
        samples += ExperimentalBurstCodec._acquisition_lead_samples(
            profile, header
        )
        return samples

    @classmethod
    def burst_duration(cls, profile: WaveformProfile, header: PhyHeader,
                       block_lengths: list[int] | None = None) -> float:
        return cls.burst_samples(profile, header, block_lengths) / profile.sample_rate

    @classmethod
    def probe_burst_info(cls, profile: WaveformProfile, samples
                         ) -> tuple[int, int, bool] | None:
        """Return an exact span and deferred-ACK state from the robust prefix."""
        raw = np.asarray(samples, dtype=np.float64)
        modulator = make_modulator(profile)
        threshold = 0.20 if profile.is_single_carrier else 0.18
        for start in correlation_candidates(
            raw, modulator.reference(), threshold=threshold, limit=32
        ):
            try:
                receiver = make_receiver(profile, raw[start:], start_hint=0)
            except (ValueError, IndexError, np.linalg.LinAlgError):
                continue
            head_count = header_blocks(profile)
            if receiver.available_data_symbols() < head_count:
                continue
            values, variance, _ = receiver.data_symbols(0, head_count)
            try:
                header = PhyHeader.decode(
                    decode_section(
                        profile, values, variance, HEADER_BYTES,
                        profile.bootstrap_modulation,
                    ),
                    mcs_lookup=sc_mcs,
                )
            except OfdmFrameError:
                continue

            lengths = None
            if (header.frame_type is OfdmFrameType.DATA
                    and header.version != LEGACY_FRAME_VERSION):
                manifest_bytes = header.subblock_count * 4 + _CRC.size
                manifest_count = section_blocks(
                    profile, manifest_bytes, profile.bootstrap_modulation
                )
                if receiver.available_data_symbols() < head_count + manifest_count:
                    continue
                values, variance, _ = receiver.data_symbols(
                    head_count, manifest_count
                )
                try:
                    entries = _decode_manifest(
                        decode_section(
                            profile, values, variance, manifest_bytes,
                            profile.bootstrap_modulation,
                        ),
                        header.subblock_count,
                        header.block_count,
                        max(profile.block_size,
                            max_arq_block_bytes(header.version)),
                    )
                except OfdmFrameError:
                    continue
                lengths = [length for _, length in entries]
                if sum(lengths) != header.payload_len:
                    continue
            # Correlation starts at the real synchronisation preamble, after the
            # optional carrier-only acquisition lead.  The lead is physical TX
            # airtime but is not part of the framed span from this start index.
            span = (cls.burst_samples(profile, header, lengths)
                    - cls._acquisition_lead_samples(profile, header))
            return start, start + span, header.defer_ack
        return None

    @staticmethod
    def probe_burst_start(profile: WaveformProfile, samples) -> int | None:
        """Return a preamble hint before the complete CRC prefix is available.

        The live receiver treats this only as a bounded capture trigger; normal
        header and manifest CRCs still gate frame length and all ARQ data.
        """
        raw = np.asarray(samples, dtype=np.float64)
        modulator = make_modulator(profile)
        threshold = 0.20 if profile.is_single_carrier else 0.18
        found = correlation_candidates(
            raw, modulator.reference(), threshold=threshold, limit=1,
        )
        return int(found[0]) if found else None

    @classmethod
    def probe_burst_span(cls, profile: WaveformProfile, samples
                         ) -> tuple[int, int] | None:
        """Compatibility wrapper returning only the exact sample span."""
        found = cls.probe_burst_info(profile, samples)
        return None if found is None else found[:2]

    @staticmethod
    def decode_burst(profile: WaveformProfile, samples,
                     start_hint: int | None = None, *, soft_cache=None) -> DecodedBurst:
        try:
            receiver = make_receiver(profile, samples, start_hint)
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
            profile, head_symbols, head_var, HEADER_BYTES,
            profile.bootstrap_modulation
        )
        try:
            header = PhyHeader.decode(head_bytes, mcs_lookup=sc_mcs)
        except OfdmFrameError as exc:
            metrics.error = f"header rejected: {exc}"
            return DecodedBurst(metrics=metrics)

        _set_evm(metrics, reference_evm(
            profile, head_symbols, head_bytes, profile.bootstrap_modulation
        ), "reference_header")
        metrics.mcs = header.mcs
        cursor = head_count

        if header.frame_type is not OfdmFrameType.DATA:
            if header.payload_len == 0:
                metrics.frame_ok = True
                return DecodedBurst(header=header, payload=b"", metrics=metrics)
            framed_bytes = header.payload_len + _CRC.size
            count = section_blocks(
                profile, framed_bytes, profile.bootstrap_modulation
            )
            if receiver.available_data_symbols() < cursor + count:
                metrics.error = "control payload is truncated"
                return DecodedBurst(header=header, metrics=metrics)
            values, variance, slope = receiver.data_symbols(cursor, count)
            metrics.phase_slope = slope
            framed = decode_section(
                profile, values, variance, framed_bytes,
                profile.bootstrap_modulation
            )
            payload, given = framed[:header.payload_len], framed[header.payload_len:]
            if given != _CRC.pack(crc16(payload)):
                metrics.error = "control payload CRC failed"
                return DecodedBurst(header=header, metrics=metrics)
            _set_evm(metrics, reference_evm(
                profile, values, framed, profile.bootstrap_modulation
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
            framed = decode_section(
                profile, values, variance, framed_bytes, modulation
            )
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
            profile, manifest_bytes, profile.bootstrap_modulation
        )
        if receiver.available_data_symbols() < cursor + manifest_count:
            metrics.error = "burst manifest is truncated"
            return DecodedBurst(header=header, metrics=metrics)
        values, variance, slope = receiver.data_symbols(cursor, manifest_count)
        cursor += manifest_count
        raw_manifest = decode_section(
            profile, values, variance, manifest_bytes,
            profile.bootstrap_modulation
        )
        try:
            entries = _decode_manifest(
                raw_manifest, header.subblock_count, header.block_count,
                max(profile.block_size, max_arq_block_bytes(header.version)),
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

        if profile.symbol_clock_tracking and profile.is_single_carrier:
            # A capture can contain subsequent bursts with their own prefixes.
            # Those samples do not share this frame's pilot-row coordinates.
            # Once its CRC-checked manifest gives the exact span, constrain any
            # long trailing capture before estimating this frame's clock again.
            start = receiver.burst_start
            end = start + ExperimentalBurstCodec.burst_samples(
                profile, header, [length for _, length in entries]
            ) - ExperimentalBurstCodec._acquisition_lead_samples(profile, header)
            if len(samples) > end + int(profile.sample_rate * 0.1):
                return ExperimentalBurstCodec.decode_burst(
                    profile, np.asarray(samples)[:end], start_hint=start,
                    soft_cache=soft_cache)

        modulation = sc_mcs(header.mcs).modulation
        decoded: dict[int, bytes] = {}
        failed: set[int] = set()
        slopes = [slope]
        evms: list[float] = []
        gmis: list[float] = []
        available = receiver.available_data_symbols()
        jobs = []
        for position, (sequence, length) in enumerate(entries):
            framed_bytes = length + _CRC.size
            count = section_blocks(profile, framed_bytes, modulation, header.fec)
            if available < cursor + count:
                failed.update(item[0] for item in entries[position:])
                break
            values, variance, data_slope = receiver.data_symbols(cursor, count)
            cursor += count
            slopes.append(data_slope)
            cache_key = (
                int(header.msg_id), int(sequence), int(header.mcs),
                int(framed_bytes),
            )
            prior = None if soft_cache is None else soft_cache.get(cache_key)
            jobs.append((sequence, length, framed_bytes, values, variance,
                         cache_key, prior))

        def decode_job(job):
            (_sequence, _length, framed_bytes, values, variance,
             _cache_key, prior) = job
            return decode_section_with_soft(
                profile, values, variance, framed_bytes, modulation,
                header.fec, prior
            )

        if len(jobs) > 1 and fec_spec(header.fec).family == "ldpc":
            with ThreadPoolExecutor(
                max_workers=min(4, len(jobs)),
                thread_name_prefix="guardian-sc-ldpc",
            ) as executor:
                decoded_jobs = list(executor.map(decode_job, jobs))
        else:
            decoded_jobs = [decode_job(job) for job in jobs]

        metric_limit = int(profile.reference_metric_blocks)
        if metric_limit and metric_limit < len(jobs):
            metric_positions = set(np.linspace(
                0, len(jobs) - 1, metric_limit, dtype=np.int64
            ).tolist())
        else:
            metric_positions = set(range(len(jobs)))

        for job_position, (job, decoded_job) in enumerate(
            zip(jobs, decoded_jobs)
        ):
            (sequence, length, _framed_bytes, values, variance,
             cache_key, prior) = job
            framed_candidates, combined_soft = decoded_job
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
            decoded[sequence] = payload
            if job_position in metric_positions:
                value = reference_evm(
                    profile, values, framed, modulation, header.fec
                )
                if value is not None:
                    evms.append(value)
                information = reference_gmi(
                    profile, values, variance, framed, modulation, header.fec
                )
                if information is not None:
                    gmis.append(information)
        metrics.phase_slope = float(np.mean(slopes))
        if evms:
            _set_evm(metrics, float(np.sqrt(np.mean(np.square(evms)))), "reference")
        if gmis:
            metrics.gmi_bits_per_symbol = float(np.mean(gmis))
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

    @classmethod
    def decode_many(cls, profile: WaveformProfile, samples, *, limit: int = 32,
                    soft_cache=None
                    ) -> list[DecodedBurst]:
        """Decode each independently synchronised microburst in one RX window."""
        raw = np.asarray(samples, dtype=np.float64)
        result: list[DecodedBurst] = []
        modulator = make_modulator(profile)
        threshold = 0.20 if profile.is_single_carrier else 0.18
        starts = correlation_candidates(
            raw, modulator.reference(), threshold=threshold, limit=limit
        )
        consumed_until = -1
        for start in starts:
            if start < consumed_until:
                continue
            decoded = cls.decode_burst(
                profile, raw[start:], start_hint=0, soft_cache=soft_cache
            )
            if decoded.header is None:
                continue
            lengths = ([decoded.block_lengths[sequence]
                        for sequence in decoded.block_order]
                       if decoded.block_lengths else None)
            span = (cls.burst_samples(profile, decoded.header, lengths)
                    - cls._acquisition_lead_samples(profile, decoded.header))
            decoded.sample_start = start
            decoded.sample_end = min(len(raw), start + span)
            result.append(decoded)
            consumed_until = decoded.sample_end
        return result


__all__ = [
    "AckBitmap", "ExperimentalBurstCodec", "OfdmFrameType", "PhyHeader",
    "SubBlock", "header_blocks", "section_blocks",
]
