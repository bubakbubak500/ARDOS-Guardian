"""Independent parity probes for the SC-FTN port.

The reference tree is intentionally imported in a different subprocess from
the working tree. Importing both implementations in one interpreter would
reuse the first ``guardian`` package and make a parity result meaningless.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
# The immutable reference is kept in this audit snapshot for local parity work.
# CI/clean checkouts may provide it elsewhere without changing the test source.
_configured_g2_root = os.environ.get("GUARDIAN_G2_REFERENCE_ROOT", "").strip()
G2_ROOT = (
    Path(_configured_g2_root).expanduser().resolve()
    if _configured_g2_root
    else ROOT / "output" / "g2-port-audit" / "g2-2.4.8"
)
PYTHON = sys.executable
_G2_REQUIRED_FILES = (
    G2_ROOT / "guardian",
    G2_ROOT / "tests" / "test_guardian_uart_radio.py",
    G2_ROOT / "tests" / "test_radio_independent_recovery.py",
)
_G2_REFERENCE_READY = all(path.exists() for path in _G2_REQUIRED_FILES)
pytestmark = pytest.mark.skipif(
    not _G2_REFERENCE_READY,
    reason=(
        "immutable G2 parity reference snapshot is unavailable at "
        f"{G2_ROOT}; set GUARDIAN_G2_REFERENCE_ROOT to a pinned reference "
        "checkout to enable parity probes"
    ),
)


def _probe(root: Path, body: str) -> dict:
    """Run one probe with exactly one source tree on the import path."""
    if not root.is_dir():
        raise AssertionError(f"parity source tree is missing: {root}")
    program = f"""
from pathlib import Path
import json
import sys

_root = Path({str(root)!r}).resolve()
sys.path.insert(0, str(_root))

def _assert_module_root(root, *modules):
    for module in modules:
        filename = Path(getattr(module, "__file__", "")).resolve()
        assert root == filename or root in filename.parents, filename

{body}
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [str(PYTHON), "-c", program],
        cwd=str(root),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"parity probe failed for {root}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"parity probe returned no JSON for {root}")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"parity probe returned non-JSON for {root}: {result.stdout!r}"
        ) from exc


def _assert_module_root(root: Path, *modules: object) -> None:
    for module in modules:
        filename = Path(getattr(module, "__file__", "")).resolve()
        assert root == filename or root in filename.parents, filename


@pytest.mark.parametrize("width", ("1K2", "2K7", "4K5", "5K", "10K", "20K"))
def test_sc_policy_inventory_matches_immutable_g2(width: str) -> None:
    """The automatic matrix and every production profile stay wire-identical."""
    body = f"""
from guardian.ofdm.automatic import automatic_g2_policy
from guardian.waveforms.config import profile_for
import guardian.ofdm.automatic as _automatic
import guardian.waveforms.config as _config

_assert_module_root(_root, _automatic, _config)
cases = (("generic", ""), ("guardian_k5", ""),
         ("generic", "UV-K5"), ("generic", "UV-K61"))
profile_description = profile_for("sc_ftn", {width!r}).describe()
# G2 reports a null SEFDM-only diagnostic key for every SC profile. G1 does not
# expose that unsupported family; compare the shared SC fields here.
profile_description.pop("sefdm_alpha", None)
result = {{"width": {width!r}, "policies": [],
          "profile": profile_description}}
for backend, model in cases:
    policy = automatic_g2_policy(
        "sc_ftn", {width!r}, radio_backend=backend, radio_model=model,
    )
    result["policies"].append({{
        "backend": backend,
        "model": model,
        "waveform": policy.waveform,
        "bandwidth": policy.bandwidth,
        "profile_name": policy.profile_name,
        "initial_mcs": policy.initial_mcs,
        "maximum_mcs": policy.maximum_mcs,
        "initial_fec": int(policy.initial_fec),
        "initial_burst_bytes": policy.initial_burst_bytes,
        "minimum_burst_bytes": policy.minimum_burst_bytes,
        "maximum_burst_bytes": policy.maximum_burst_bytes,
        "arq_block_bytes": policy.arq_block_bytes,
        "maximum_retries": policy.maximum_retries,
        "rescue_retries": policy.rescue_retries,
        "rescue_mcs": policy.rescue_mcs,
        "clean_bursts_to_upgrade": policy.clean_bursts_to_upgrade,
        "rapid_acquisition": policy.rapid_acquisition,
        "maximum_train_seconds": policy.maximum_train_seconds,
        "acquisition_lead_seconds": policy.acquisition_lead_seconds,
        "tx_guard_ms": policy.tx_guard_ms,
        "bootstrap_modulation": policy.bootstrap_modulation,
        "reference_metric_blocks": policy.reference_metric_blocks,
        "center_hz": policy.center_hz,
        "nyquist_symbol_rate": policy.nyquist_symbol_rate,
        "symbol_rate": policy.symbol_rate,
    }})
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
"""
    g1 = _probe(ROOT, body)
    g2 = _probe(G2_ROOT, body)
    assert g1 == g2


def test_sc_wire_fec_and_audio_lengths_match_immutable_g2() -> None:
    """Headers, FEC lengths and clean decoded samples share one wire contract."""
    body = r'''
import hashlib
import json
import numpy as np

from guardian.ofdm.coding import FEC_SPECS, decode_soft, encode_bits
from guardian.ofdm.config import sc_mcs
from guardian.ofdm.framing import (
    AckBitmap, OfdmFrameType, PhyHeader, SubBlock,
)
from guardian.waveforms.config import profile_for
from guardian.waveforms.framing import ExperimentalBurstCodec
import guardian.ofdm.framing as _framing
import guardian.waveforms.framing as _waveform_framing

_assert_module_root(_root, _framing, _waveform_framing)
rng = np.random.default_rng(0x5C)
raw_bits = rng.integers(0, 2, 512 * 8, dtype=np.int8)
fec_rows = []
for spec in FEC_SPECS:
    coded = encode_bits(raw_bits, spec.profile)
    decoded = decode_soft((2.0 * coded - 1.0) * 20.0, 512, spec.profile)
    fec_rows.append({
        "profile": int(spec.profile),
        "family": spec.family,
        "encoded_bits": len(coded),
        "sha256": hashlib.sha256(np.asarray(coded, dtype=np.int8).tobytes()).hexdigest(),
        "round_trip": bool(np.array_equal(decoded[:len(raw_bits)], raw_bits)),
    })

header = PhyHeader(
    OfdmFrameType.DATA, 0x10203040, block_seq=7, block_count=9,
    mcs=17, fec=6, payload_len=51, subblock_count=2,
    retransmission=True, version=2,
)
encoded_header = header.encode()
decoded_header = PhyHeader.decode(encoded_header, mcs_lookup=sc_mcs)
header_row = {
    "wire": encoded_header.hex(),
    "version": decoded_header.version,
    "frame_type": int(decoded_header.frame_type),
    "msg_id": decoded_header.msg_id,
    "block_seq": decoded_header.block_seq,
    "block_count": decoded_header.block_count,
    "mcs": decoded_header.mcs,
    "fec": int(decoded_header.fec),
    "payload_len": decoded_header.payload_len,
    "subblock_count": decoded_header.subblock_count,
    "retransmission": decoded_header.retransmission,
}
ack = AckBitmap(130, frozenset({0, 1, 63, 64, 129}),
                remote_snr_db=12.25, remote_evm_rms=0.135)
ack_decoded = AckBitmap.decode(ack.encode_compact())
ack_row = {
    "wire": ack.encode_compact().hex(),
    "total_blocks": ack_decoded.total_blocks,
    "received": sorted(ack_decoded.received),
    "remote_snr_db": ack_decoded.remote_snr_db,
    "remote_evm_rms": round(ack_decoded.remote_evm_rms or 0.0, 6),
}

blocks = [SubBlock(4, bytes(range(32))), SubBlock(6, bytes(range(31, 50)))]
bursts = []
for width in ("1K2", "2K7", "4K5", "5K", "10K", "20K"):
    profile = profile_for("sc_ftn", width)
    burst = ExperimentalBurstCodec.build_burst(profile, header, blocks=blocks)
    decoded = ExperimentalBurstCodec.decode_burst(profile, burst)
    bursts.append({
        "width": width,
        "samples": len(burst),
        "expected_samples": ExperimentalBurstCodec.burst_samples(
            profile, header, [len(item.payload) for item in blocks],
        ),
        "sha256": hashlib.sha256(
            np.asarray(burst, dtype="<f8").tobytes()
        ).hexdigest(),
        "ok": bool(decoded.ok),
        "block_order": list(decoded.block_order),
        "payload_sha256": hashlib.sha256(
            b"".join(decoded.blocks[index] for index in decoded.block_order)
        ).hexdigest() if decoded.ok else "",
        "error": decoded.metrics.error,
    })
print(json.dumps({"fec": fec_rows, "header": header_row,
                  "ack": ack_row, "bursts": bursts},
                 sort_keys=True, separators=(",", ":")))
'''
    g1 = _probe(ROOT, body)
    g2 = _probe(G2_ROOT, body)
    assert g1 == g2
    assert all(row["ok"] for row in g1["bursts"])
    assert all(row["samples"] == row["expected_samples"] for row in g1["bursts"])


def test_sc_2k7_long_clock_tracking_matches_immutable_g2() -> None:
    """A long burst survives seeded noise, CFO and a sample-clock offset."""
    body = r'''
import hashlib
import json
import numpy as np

from guardian.ofdm.channel import Channel, ChannelSpec
from guardian.ofdm.coding import FecProfile
from guardian.ofdm.framing import OfdmFrameType, PhyHeader, SubBlock
from guardian.waveforms.config import profile_for
from guardian.waveforms.framing import ExperimentalBurstCodec
import guardian.ofdm.channel as _channel
import guardian.waveforms.framing as _waveform_framing

_assert_module_root(_root, _channel, _waveform_framing)
profile = profile_for("sc_ftn", "2K7")
payload = np.random.default_rng(0x2A7).integers(
    0, 256, 4096, dtype=np.uint8,
).tobytes()
blocks = [SubBlock(index, payload[index * 256:(index + 1) * 256])
          for index in range(16)]
header = PhyHeader(
    OfdmFrameType.DATA, 0x2A7, block_count=16, block_seq=3, mcs=1,
    fec=FecProfile.FEC_1_2, payload_len=len(payload),
    subblock_count=len(blocks), version=3,
)
waveform = ExperimentalBurstCodec.build_burst(profile, header, blocks=blocks)
aired = Channel(profile, ChannelSpec(
    snr_db=22.0, freq_offset_hz=2.0, phase_offset=0.2,
    ppm=40.0, trailing=1000,
), seed=0x2A7)(waveform)
decoded = ExperimentalBurstCodec.decode_burst(profile, aired)
metrics = decoded.metrics
print(json.dumps({
    "waveform_samples": len(waveform),
    "aired_samples": len(aired),
    "ok": bool(decoded.ok),
    "payload_sha256": hashlib.sha256(
        b"".join(decoded.blocks[index] for index in decoded.block_order)
    ).hexdigest() if decoded.ok else "",
    "block_order": list(decoded.block_order),
    "sample_clock_ppm": round(float(metrics.sample_clock_ppm or 0.0), 6),
    "cfo_hz": round(float(metrics.cfo_hz or 0.0), 6),
    "error": metrics.error,
}, sort_keys=True, separators=(",", ":")))
'''
    g1 = _probe(ROOT, body)
    g2 = _probe(G2_ROOT, body)
    assert g1["ok"] and g2["ok"]
    assert g1["payload_sha256"] == g2["payload_sha256"]
    assert g1["block_order"] == g2["block_order"]
    assert g1["waveform_samples"] == g2["waveform_samples"]
    assert g1["aired_samples"] == g2["aired_samples"]
    assert g1["sample_clock_ppm"] == pytest.approx(g2["sample_clock_ppm"], abs=1e-3)
    assert g1["cfo_hz"] == pytest.approx(g2["cfo_hz"], abs=1e-3)


def test_sc_arq_adaptation_history_and_block_retention_match_immutable_g2() -> None:
    """Scripted DATA/ACK/POLL feedback keeps rescue and held blocks stable."""
    body = r'''
import json
import numpy as np
from guardian.ofdm import BENCH
from guardian.ofdm.adaptation import AdaptationConfig, LinkAdaptationController
from guardian.ofdm.coding import FecProfile
from guardian.ofdm.framing import AckBitmap, OfdmFrameType, PhyHeader
from guardian.ofdm.link import BurstTxState, OfdmLink, RxBurstState
from guardian.ofdm.metrics import LinkMetrics

controller = LinkAdaptationController(AdaptationConfig(), mcs_index=3)
history = []
for _ in range(3):
    controller.report_burst(
        sent_blocks=4, acked_blocks=4, unique_bytes=2048,
        elapsed_seconds=10.0, remote_snr_db=14.0, remote_evm_rms=0.12,
    )
    history.append((int(controller.profile.fec), controller.profile.burst_bytes))
for _ in range(3):
    controller.report_burst(sent_blocks=4, acked_blocks=4,
                            unique_bytes=2048, elapsed_seconds=9.0)
    history.append((int(controller.profile.fec), controller.profile.burst_bytes))
controller.report_burst(sent_blocks=8, acked_blocks=7,
                        retransmitted_bytes=512, unique_bytes=3584,
                        elapsed_seconds=12.0)
history.append((int(controller.profile.fec), controller.profile.burst_bytes))
retry_fec = [int(controller.fec_for_retry(index)) for index in range(5)]

link = OfdmLink(BENCH, None, mcs_index=3, max_train_seconds=60.0,
                rescue_after_attempt=2, rescue_mcs_index=0,
                controller=LinkAdaptationController(
                    AdaptationConfig(modern_ldpc=True), mcs_index=3,
                ))
rescue = [(int(link._fec_for_attempt(index)), link._mcs_for_attempt(index))
          for index in range(4)]
state = BurstTxState(
    msg_id=0x958, burst_id=4, total_blocks=5,
    blocks={index: bytes([index]) * 512 for index in range(5)},
    pending=set(range(5)), retry_count={index: 0 for index in range(5)},
)
first_header = link._window_data_header(
    state, [0, 1], FecProfile.FEC_1_2, retransmission=True,
)
link.max_train_seconds = link._burst_duration(
    first_header, [512, 512]
) + 1e-6
batches = link._bounded_window_batches(
    state, list(range(5)), FecProfile.FEC_1_2, retransmission=True,
)

# The receiver has already retained blocks 0 and 2. A DATA report and a POLL
# for the same window repeat that state; a different POLL cannot reuse stale
# quality measurements. This is the compact ACK path used during rescue.
receiver = OfdmLink(BENCH, None)
rx_state = RxBurstState(0x958, 4, blocks={0: b"x", 2: b"z"}, train_seen=True)
replies = []
def build(header, payload=b"", **_kwargs):
    replies.append((header.frame_type.name, AckBitmap.decode(payload)))
    return np.ones(1)
receiver._build_burst = build
receiver._transmit = lambda *args, **kwargs: None
receiver._guard_peer_receiver = lambda: None
data = PhyHeader(OfdmFrameType.DATA, 0x958, block_seq=7, block_count=4)
poll = PhyHeader(OfdmFrameType.POLL, 0x958, block_seq=7, block_count=4)
receiver._answer_bitmap(OfdmFrameType.ACK, data, rx_state,
                        LinkMetrics(residual_snr_db=12, evm_rms=0.2))
receiver._answer_bitmap(OfdmFrameType.ACK, poll, rx_state,
                        LinkMetrics(residual_snr_db=25, evm_rms=0.01))
receiver._answer_bitmap(OfdmFrameType.ACK,
                        PhyHeader(OfdmFrameType.POLL, 0x958,
                                  block_seq=8, block_count=4),
                        rx_state, LinkMetrics(residual_snr_db=25, evm_rms=0.01))
reply_summary = [
    (kind, sorted(item.received), item.remote_snr_db,
     round(item.remote_evm_rms or 0.0, 3))
    for kind, item in replies
]
print(json.dumps({"adaptation": history, "retry_fec": retry_fec,
                  "rescue": rescue, "batches": batches,
                  "reply_history": reply_summary,
                  "held_blocks": sorted(rx_state.blocks)},
                 sort_keys=True, separators=(",", ":")))
'''
    g1 = _probe(ROOT, body)
    g2 = _probe(G2_ROOT, body)
    assert g1 == g2
    assert g1["held_blocks"] == [0, 2]
    assert [item[0] for item in g1["reply_history"]] == ["ACK", "ACK", "ACK"]


def _run_reference_tests(root: Path, test_file: Path, selection: str) -> None:
    """Run a G2 regression file with a selected import root."""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root)
    result = subprocess.run(
        [
            str(PYTHON), "-m", "pytest", "-q", "-p", "no:cacheprovider",
            "--confcutdir", str(root), str(test_file), "-k", selection,
            "--disable-warnings",
        ],
        cwd=str(root),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise AssertionError(
            f"reference regression failed under {root}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


def test_g1_runs_selected_g2_uart_aioc_and_recovery_regressions() -> None:
    """Run immutable G2 fixture files once per import root.

    The fixture paths remain in the audit tree; changing ``cwd`` and
    ``PYTHONPATH`` makes the same test code validate G1 and G2 independently.
    """
    uart = G2_ROOT / "tests" / "test_guardian_uart_radio.py"
    recovery = G2_ROOT / "tests" / "test_radio_independent_recovery.py"
    uart_selection = (
        "crc_and_both_uart_envelopes or "
        "profiles_read_both_vfos_and_switch_working_then_control or "
        "aioc_ptt_uses_safe_complementary_order_and_uart_never_keys_on_open or "
        "k5_waits_for_firmware_uart_guard_and_status_poll_uses_cache or "
        "k5_filter_profile_can_be_changed_transiently_without_a_state_poll or "
        "definite_radio_rejections_are_reported_without_retry or "
        "tuning_is_refused_while_aioc_ptt_is_active or "
        "unconfirmed_vfo_readback_is_never_reported_as_a_success"
    )
    for root in (ROOT, G2_ROOT):
        _run_reference_tests(root, uart, uart_selection)
        _run_reference_tests(
            root, recovery,
            "test_keying_failure_always_closes_the_audio_stream or "
            "test_audio_is_drained_while_keyed_and_drain_failure_still_unkeys or "
            "test_failed_control_send_is_not_a_successful_flush_and_can_be_retried",
        )
