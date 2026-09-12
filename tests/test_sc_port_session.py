"""Focused SC-FTN control/session negotiation contracts."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from guardian.modem.afsk import AFSKModem
from guardian.modem.audio import (
    PTT_LEAD_SECONDS,
    PTT_TAIL_SECONDS,
    TX_GUARD_SECONDS,
    AudioControlTransport,
)
from guardian.protocol import (
    ControlFrame,
    Flags,
    FrameType,
    decode_g2_profile_capable,
    decode_ofdm_capable,
    encode_g2_profile_capable,
    encode_ofdm_capable,
)
from guardian.session import LoopbackBus, Orchestrator, SessionState
from guardian.session.orchestrator import g2_profile_token


def _drain(bus: LoopbackBus, *stations: Orchestrator) -> None:
    for _ in range(20):
        delivered = bus.pump()
        for station in stations:
            station.tick(1.0)
        if delivered == 0 and bus.idle:
            return
    raise AssertionError("loopback bus did not become idle")


def test_g2_profile_token_is_exact_for_all_sc_widths() -> None:
    expected = {
        "1K2": "G1T1",
        "2K7": "G1T2",
        "4K5": "G1T4",
        "5K": "G1T5",
        "10K": "G1TA",
        "20K": "G1TB",
    }
    assert {
        width: g2_profile_token("sc_ftn", width, 1)
        for width in expected
    } == expected

    with pytest.raises(ValueError):
        g2_profile_token("ofdm", "2K7", 1)
    with pytest.raises(ValueError):
        g2_profile_token("sc_fde_ftn", "2K7", 1)


def test_exact_profile_pair_enters_sc_payload_after_offer_ack() -> None:
    wire: list[ControlFrame] = []
    bus = LoopbackBus(monitor=lambda _sender, frame: wire.append(frame))
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator(
        "OK2IPW", bus.endpoint("receiver"), auto_complete=True, auto_route=False
    )
    token = g2_profile_token("sc_ftn", "2K7", 1)
    sender.ofdm_payload_request = lambda: True
    receiver.ofdm_payload_request = lambda: True
    sender.g2_profile = lambda: token
    receiver.g2_profile = lambda: token

    message = sender.send_message("OK2IPW", "hello", 11, next_hop="OK2IPW")
    _drain(bus, sender, receiver)

    assert message.state is SessionState.DELIVERED
    assert message.payload_transport == "ofdm_vhf"
    assert receiver.sessions[11].payload_transport == "ofdm_vhf"
    have = next(frame for frame in wire if frame.type is FrameType.HAVE_MSG)
    assert decode_g2_profile_capable(have.flags)
    assert not decode_ofdm_capable(have.flags)
    offers = [frame for frame in wire if frame.type is FrameType.G2_PROFILE_OFFER]
    acks = [frame for frame in wire if frame.type is FrameType.G2_PROFILE_ACK]
    assert offers and offers[0].destination == token
    assert acks and acks[0].destination == token


def test_legacy_one_bit_claim_cannot_enter_sc_payload() -> None:
    sent: list[ControlFrame] = []

    class Transport:
        on_frame = None

        def send(self, frame: ControlFrame) -> None:
            sent.append(frame)

    station = Orchestrator("OK2IPW", Transport(), auto_route=False)
    station.ofdm_payload_request = lambda: True
    station.g2_profile = lambda: g2_profile_token("sc_ftn", "2K7", 1)
    station._on_frame(ControlFrame(
        FrameType.HAVE_MSG,
        source="OK7PS",
        destination="OK2IPW",
        next_hop="OK2IPW",
        message_id=12,
        flags=encode_ofdm_capable(Flags.NONE, True),
    ))

    incoming = station.sessions[12]
    assert incoming.payload_transport == "vara_p2p"
    ack = next(frame for frame in sent if frame.type is FrameType.ACK_HAVE)
    assert not decode_ofdm_capable(ack.flags)
    assert not decode_g2_profile_capable(ack.flags)


def test_local_ofdm_callback_without_exact_sc_token_advertises_no_native_mode() -> None:
    sent: list[ControlFrame] = []

    class Transport:
        on_frame = None

        def send(self, frame: ControlFrame) -> None:
            sent.append(frame)

    station = Orchestrator("OK7PS", Transport(), auto_route=False)
    station.ofdm_payload_request = lambda: True
    message = station.send_message("OK2IPW", "hello", 13, next_hop="OK2IPW")

    assert not decode_ofdm_capable(message.flags)
    assert not decode_g2_profile_capable(message.flags)
    assert sent and sent[0].type is FrameType.HAVE_MSG


def test_unsupported_remote_profile_is_rejected_to_vara_explicitly() -> None:
    sent: list[ControlFrame] = []

    class Transport:
        on_frame = None

        def send(self, frame: ControlFrame) -> None:
            sent.append(frame)

    station = Orchestrator("OK2IPW", Transport(), auto_route=False)
    station.ofdm_payload_request = lambda: True
    station.g2_profile = lambda: g2_profile_token("sc_ftn", "2K7", 1)
    station._on_frame(ControlFrame(
        FrameType.HAVE_MSG,
        source="OK7PS",
        destination="OK2IPW",
        next_hop="OK2IPW",
        message_id=14,
        flags=encode_g2_profile_capable(Flags.NONE, True),
    ))
    station._on_frame(ControlFrame(
        FrameType.G2_PROFILE_OFFER,
        source="OK7PS",
        destination="G1F2",  # unsupported SC-FDE family token
        next_hop="OK2IPW",
        message_id=14,
        flags=Flags.G2_PROFILE,
    ))

    incoming = station.sessions[14]
    assert incoming.payload_transport == "vara_p2p"
    ack = [frame for frame in sent if frame.type is FrameType.G2_PROFILE_ACK][-1]
    assert ack.destination == "="


def test_calibration_frames_use_direct_hook_without_creating_mail_sessions() -> None:
    sent: list[ControlFrame] = []
    received: list[ControlFrame] = []

    class Transport:
        on_frame = None

        def send(self, frame: ControlFrame) -> None:
            sent.append(frame)

    station = Orchestrator("OK7PS", Transport(), auto_route=False)
    station.on_calibration_frame = received.append
    station.send_calibration_frame(FrameType.CAL_PROBE, "ok2ipw", 15, "G1T2")

    assert not station.sessions
    assert sent[0].type is FrameType.CAL_PROBE
    assert sent[0].destination == "OK2IPW"
    assert sent[0].next_hop == "G1T2"
    station._on_frame(ControlFrame(
        FrameType.CAL_REPORT,
        source="OK2IPW",
        destination="OK7PS",
        next_hop="",
        message_id=15,
    ))
    assert len(received) == 1


def test_afsk_uses_short_first_acquisition_and_extended_retry() -> None:
    modem = AFSKModem()
    payload = b"control"
    first = modem.modulate(payload)
    retry = modem.modulate_retry(payload)

    assert abs(len(first) / modem.fs - modem.airtime(len(payload))) < 1 / modem.fs
    assert abs(len(retry) / modem.fs - modem.retry_airtime(len(payload))) < 1 / modem.fs
    assert len(retry) > len(first)
    assert len(retry) - len(first) == (128 - 24) * 8 * int(modem.fs / modem.baud)


def test_afsk_audio_uses_short_edges_but_detected_legacy_leader_gets_long_quiet() -> None:
    modem = AFSKModem()
    transport = AudioControlTransport(modem=modem)
    assert (transport.tx_lead_seconds, transport.tx_tail_seconds) == (0.06, 0.06)

    frame = ControlFrame(FrameType.BEACON, source="OK7PS")
    encoded = frame.encode()
    modem.received_preamble_bytes[encoded] = 128.0
    transport._handle_payload(encoded)

    assert transport._peer_ready_at >= TX_GUARD_SECONDS + PTT_TAIL_SECONDS
    assert transport.tx_lead_seconds != PTT_LEAD_SECONDS


def test_legacy_duck_typed_afsk_name_keeps_g1_edges_without_retry_hook() -> None:
    modem = SimpleNamespace(name="afsk1200", modulate=lambda payload: payload)
    transport = AudioControlTransport(modem=modem)

    assert transport.tx_lead_seconds == PTT_LEAD_SECONDS
    assert transport.tx_tail_seconds == PTT_TAIL_SECONDS
