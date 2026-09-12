"""Focused contracts for the SC-FTN payload port.

These tests intentionally exercise the public payload factory and dispatch
boundary.  The DSP and wire-codec coverage remains in the shared modem tests;
this file makes the SC-only product boundary explicit.
"""

from __future__ import annotations

import io
import json
import lzma
import time
import zipfile
from types import SimpleNamespace

import pytest

from guardian.ofdm.automatic import automatic_g2_policy
from guardian.payload import NegotiatedPayload, OfdmVhfBackend, make_backend
from guardian.payload.base import PayloadBackend
import guardian.payload.vara_p2p as vara_module
from guardian.payload.vara_p2p import (
    TransferContext,
    VaraP2PBackend,
    transfer_identity_from_bundle,
)
from guardian.session import Message
from guardian.vara import TransferResult


def test_factory_forwards_radio_model_into_the_automatic_policy() -> None:
    """A generic audio backend still gets the K5 model policy branch."""
    kwargs = {
        "g2_waveform": "sc_ftn",
        "g2_bandwidth": "4K5",
        "radio_backend": "generic",
        "radio_model": "UV-K5",
    }

    via_factory = make_backend("ofdm_vhf", **kwargs)
    direct = OfdmVhfBackend(**kwargs)
    expected = automatic_g2_policy(
        "sc_ftn", "4K5", radio_backend="generic", radio_model="UV-K5"
    )

    assert via_factory.policy == direct.policy == expected
    assert via_factory.policy is not None
    assert via_factory.policy.initial_mcs == 1
    assert via_factory.policy.maximum_mcs == 3
    assert via_factory.policy.arq_block_bytes == 256


@pytest.mark.parametrize("bandwidth", ("1K2", "2K7", "4K5", "5K", "10K", "20K"))
def test_factory_exposes_each_audited_sc_ftn_width(bandwidth: str) -> None:
    backend = make_backend(
        "ofdm_vhf", g2_waveform="sc_ftn", g2_bandwidth=bandwidth
    )

    assert backend.waveform_family == "sc_ftn"
    assert backend.g2_bandwidth == bandwidth


@pytest.mark.parametrize("waveform", ("ofdm", "sc_fde_ftn", "sc_hs", "sefdm"))
def test_factory_rejects_waveforms_outside_the_sc_port(waveform: str) -> None:
    with pytest.raises(ValueError, match="SC-FTN only"):
        make_backend("ofdm_vhf", g2_waveform=waveform)


def test_factory_rejects_an_unrecognised_sc_width() -> None:
    with pytest.raises(ValueError, match="unsupported SC-FTN bandwidth"):
        make_backend("ofdm_vhf", g2_waveform="sc_ftn", g2_bandwidth="2700")


class _RecordingBackend(PayloadBackend):
    def __init__(self, name: str):
        self.name = name
        self.calls: list[str] = []

    def start_send(self, msg, done) -> None:
        self.calls.append("send")
        done(True)

    def start_receive(self, msg, done) -> None:
        self.calls.append("receive")
        done(True)

    def cancel(self, msg) -> None:
        self.calls.append("cancel")


def test_negotiated_payload_dispatches_vara_and_sc_tokens_without_normalising() -> None:
    vara = _RecordingBackend("vara_p2p")
    sc = _RecordingBackend("ofdm_vhf")
    negotiated = NegotiatedPayload(
        vara,
        backends={"vara_p2p": vara, "ofdm_vhf": sc},
    )

    negotiated.start_send(SimpleNamespace(payload_transport="vara_p2p"), lambda ok: None)
    negotiated.start_send(SimpleNamespace(payload_transport="ofdm_vhf"), lambda ok: None)

    assert vara.calls == ["send"]
    assert sc.calls == ["send"]

    with pytest.raises(ValueError, match="unsupported"):
        negotiated.backend_for(SimpleNamespace(payload_transport="sc_fde_ftn"))


def test_vara_handoff_timeout_defers_qsy_release_and_completion() -> None:
    """A native RF tail cannot race a CANCEL/control restart."""

    class HandoffVara:
        connected = True

        def __init__(self) -> None:
            self.idle = False
            self.commands: list[tuple] = []
            self.state = SimpleNamespace(
                tx_buffer_bytes=None,
                data_socket_generation=1,
                tx_bitrate_bps=None,
                ptt_keyings=1,
                transport_lost=False,
            )

        def connect_to(self, callsign: str) -> None:
            self.commands.append(("connect", callsign))

        def wait_link(self, state: str, timeout: float, **_kwargs) -> bool:
            return state == "CONNECTED" or state == "DISCONNECTED"

        def wait_data_ready(self) -> None:
            return None

        def prepare_data_transfer(self) -> None:
            return None

        def write_data(self, data: bytes) -> None:
            self.state.tx_buffer_bytes = len(data)

        def wait_transfer_complete(self, timeout: float) -> TransferResult:
            return TransferResult.DRAINED

        def disconnect_link(self) -> None:
            self.commands.append(("disconnect",))

        def wait_radio_idle(self, timeout: float = 30.0) -> bool:
            return self.idle

    vara = HandoffVara()
    events: list[object] = []
    result: list[bool] = []
    pending: list[object] = []
    backend = VaraP2PBackend(
        vara,
        on_acquire=lambda: events.append("acquire"),
        on_unqsy=lambda: events.append("restore"),
        on_release=lambda: events.append("release"),
        on_handoff_failed=lambda resume: pending.append(resume),
    )

    backend._send(
        Message(801, "OK7PS", "OK1AAA", "OK1AAA", payload_bytes=b"x"),
        result.append,
    )

    assert events == ["acquire"]
    assert result == []
    assert len(pending) == 1

    vara.idle = True
    assert pending[0]() is True
    assert events == ["acquire", "restore", "release"]
    assert result == [True]


def test_vara_context_reads_aggressive_manifest_without_attachment_restore() -> None:
    from guardian.message.aggressive import FORMAT_VERSION, MAGIC

    manifest = {
        "format": FORMAT_VERSION,
        "msg_id": 802,
        "source": "OK1AAA",
        "final_dest": "OK2BBB",
        "attachments": [],
    }
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest).encode("utf-8"))
        archive.writestr("body.txt", "relay")
    aggressive = MAGIC + lzma.compress(inner.getvalue(), format=lzma.FORMAT_XZ)

    assert transfer_identity_from_bundle(aggressive).source == "OK1AAA"
    backend = VaraP2PBackend()
    context = backend._send_context(
        Message(802, "OK3CCC", "OK2BBB", "OK3CCC"),
        aggressive,
        raw_payload=False,
    )
    assert context == TransferContext("OK1AAA", "OK2BBB", "OK3CCC")


def test_vara_handoff_resume_is_invalidated_on_shutdown() -> None:
    class BusyVara:
        def wait_radio_idle(self, timeout: float = 30.0) -> bool:
            return False

    pending: list[object] = []
    events: list[str] = []
    result: list[bool] = []
    backend = VaraP2PBackend(
        BusyVara(),
        on_unqsy=lambda: events.append("restore"),
        on_release=lambda: events.append("release"),
        on_handoff_failed=lambda resume: pending.append(resume),
    )

    backend._handoff_ready.clear()
    backend._defer_handoff(result.append, False)
    assert len(pending) == 1

    backend.shutdown()
    assert pending[0]() is False
    assert events == []
    assert result == []


def test_vara_default_handoff_recovery_has_a_bounded_worker_budget(monkeypatch) -> None:
    class BusyVara:
        def wait_radio_idle(self, timeout: float = 30.0) -> bool:
            return False

    logs: list[str] = []
    result: list[bool] = []
    backend = VaraP2PBackend(BusyVara(), on_log=logs.append)
    monkeypatch.setattr(vara_module, "HANDOFF_RECOVERY_MAX_SECONDS", 0.03)

    backend._handoff_ready.clear()
    backend._defer_handoff(result.append, False)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and not any(
        "budget expired" in line for line in logs
    ):
        time.sleep(0.01)

    assert [line for line in logs if "budget expired" in line] == [
        "VARA P2P: control handoff recovery budget expired; control ownership "
        "remains suspended"
    ]
    assert result == []
    backend.shutdown()
