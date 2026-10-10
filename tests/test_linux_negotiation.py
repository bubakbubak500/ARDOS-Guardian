"""Stations without VARA must agree on a native modem before any payload."""

from unittest.mock import Mock

import pytest

from guardian.protocol import ControlFrame, Flags, FrameType
from guardian.session import LoopbackBus, Message, Orchestrator, SessionState


class RecordingTransport:
    on_frame = None

    def __init__(self):
        self.sent = []

    def send(self, frame):
        self.sent.append(frame)


def station(token="G1T2", *, callsign="OK7PS"):
    transport = RecordingTransport()
    payload = Mock()
    owner = Orchestrator(
        callsign, transport, auto_route=False, payload=payload,
        allow_vara_fallback=False,
    )
    owner.ofdm_payload_request = lambda: True
    owner.g2_profile = lambda: token
    return owner, transport, payload


def assert_rejected(message, transport, payload):
    assert message.state is SessionState.FAILED
    assert "VARA is unavailable" in message.error
    assert "Select the same SC-FTN or ARDOP profile" in message.error
    assert transport.sent[-1].type is FrameType.CANCEL
    assert not any(frame.type is FrameType.START_VARA for frame in transport.sent)
    payload.start_send.assert_not_called()
    payload.start_receive.assert_not_called()


def drain(bus, *owners):
    for _ in range(20):
        delivered = bus.pump()
        for owner in owners:
            owner.tick(1.0)
        if delivered == 0 and bus.idle:
            return
    raise AssertionError("loopback bus did not become idle")


@pytest.mark.parametrize("flags", [Flags.NONE, Flags.OFDM_PAYLOAD])
def test_incoming_peer_without_profile_capability_is_cancelled(flags):
    owner, transport, payload = station(callsign="OK2IPW")
    owner._on_frame(ControlFrame(
        FrameType.HAVE_MSG, source="OK7PS", destination="OK2IPW",
        next_hop="OK2IPW", message_id=1, flags=flags,
    ))

    assert_rejected(owner.sessions[1], transport, payload)
    assert not any(frame.type is FrameType.ACK_HAVE for frame in transport.sent)


@pytest.mark.parametrize("flags", [
    Flags.NONE, Flags.OFDM_PAYLOAD, Flags.G2_PROFILE,
])
def test_ack_without_both_agreement_capabilities_is_cancelled(flags):
    owner, transport, payload = station()
    message = owner.send_message("OK2IPW", "hello", 2, next_hop="OK2IPW")
    owner._on_frame(ControlFrame(
        FrameType.ACK_HAVE, source="OK2IPW", destination="OK2IPW",
        next_hop="OK2IPW", message_id=2, flags=flags,
    ))

    assert_rejected(message, transport, payload)


@pytest.mark.parametrize("token", [None, "G1F2"])
def test_invalid_local_profile_does_not_accept_a_native_peer(token):
    owner, transport, payload = station(token, callsign="OK2IPW")
    owner._on_frame(ControlFrame(
        FrameType.HAVE_MSG, source="OK7PS", destination="OK2IPW",
        next_hop="OK2IPW", message_id=3, flags=Flags.G2_PROFILE,
    ))

    assert_rejected(owner.sessions[3], transport, payload)


def test_local_profile_disappearing_during_agreement_is_cancelled():
    owner, transport, payload = station(None)
    message = owner.send_message("OK2IPW", "hello", 4, next_hop="OK2IPW")
    message.payload_transport = "ofdm_vhf"

    owner._negotiate_g2_profile_or_start(message, "OK2IPW")

    assert_rejected(message, transport, payload)


@pytest.mark.parametrize("remote_token", ["G1T4", "G1F2", "="])
def test_incompatible_profile_offer_is_cancelled_without_fallback_ack(remote_token):
    owner, transport, payload = station(callsign="OK2IPW")
    owner._on_frame(ControlFrame(
        FrameType.HAVE_MSG, source="OK7PS", destination="OK2IPW",
        next_hop="OK2IPW", message_id=5, flags=Flags.G2_PROFILE,
    ))
    owner._on_frame(ControlFrame(
        FrameType.G2_PROFILE_OFFER, source="OK7PS", destination=remote_token,
        next_hop="OK2IPW", message_id=5, flags=Flags.G2_PROFILE,
    ))

    assert_rejected(owner.sessions[5], transport, payload)
    assert not any(frame.type is FrameType.G2_PROFILE_ACK for frame in transport.sent)


@pytest.mark.parametrize("remote_token", ["G1T4", "="])
def test_incompatible_profile_ack_is_cancelled(remote_token):
    owner, transport, payload = station()
    message = owner.send_message("OK2IPW", "hello", 6, next_hop="OK2IPW")
    owner._on_frame(ControlFrame(
        FrameType.ACK_HAVE, source="OK2IPW", destination="OK2IPW",
        next_hop="OK2IPW", message_id=6,
        flags=Flags.OFDM_PAYLOAD | Flags.G2_PROFILE,
    ))
    assert message.state is SessionState.NEGOTIATING_PROFILE
    owner._on_frame(ControlFrame(
        FrameType.G2_PROFILE_ACK, source="OK2IPW", destination=remote_token,
        next_hop="OK7PS", message_id=6, flags=Flags.G2_PROFILE,
    ))

    assert_rejected(message, transport, payload)


@pytest.mark.parametrize("native_only_endpoint", ["sender", "receiver", "both"])
def test_width_mismatch_never_starts_payload_with_a_native_only_endpoint(
    native_only_endpoint,
):
    wire = []
    bus = LoopbackBus(monitor=lambda _sender, frame: wire.append(frame))
    sender = Orchestrator(
        "OK7PS", bus.endpoint("sender"), auto_route=False,
        allow_vara_fallback=native_only_endpoint == "receiver",
    )
    receiver = Orchestrator(
        "OK2IPW", bus.endpoint("receiver"), auto_route=False, auto_complete=True,
        allow_vara_fallback=native_only_endpoint == "sender",
    )
    sender.ofdm_payload_request = receiver.ofdm_payload_request = lambda: True
    sender.g2_profile = lambda: "G1T2"
    receiver.g2_profile = lambda: "G1T4"
    message = sender.send_message("OK2IPW", "hello", 7, next_hop="OK2IPW")

    drain(bus, sender, receiver)

    assert message.state.terminal
    assert receiver.sessions[7].state.terminal
    assert any(frame.type is FrameType.CANCEL for frame in wire)
    assert not any(frame.type is FrameType.START_VARA for frame in wire)


@pytest.mark.parametrize("token", ["G1T2", "A500"])
def test_matching_native_profiles_still_complete(token):
    wire = []
    bus = LoopbackBus(monitor=lambda _sender, frame: wire.append(frame))
    sender = Orchestrator(
        "OK7PS", bus.endpoint("sender"), auto_route=False,
        allow_vara_fallback=False,
    )
    receiver = Orchestrator(
        "OK2IPW", bus.endpoint("receiver"), auto_route=False, auto_complete=True,
        allow_vara_fallback=False,
    )
    sender.ofdm_payload_request = receiver.ofdm_payload_request = lambda: True
    sender.g2_profile = receiver.g2_profile = lambda: token
    message = sender.send_message("OK2IPW", "hello", 8, next_hop="OK2IPW")

    drain(bus, sender, receiver)

    assert message.state is SessionState.DELIVERED
    expected = "ardop" if token == "A500" else "ofdm_vhf"
    assert message.payload_transport == receiver.sessions[8].payload_transport == expected
    assert not any(frame.type is FrameType.CANCEL for frame in wire)


def test_payload_send_cannot_bypass_native_agreement():
    owner, transport, payload = station()
    message = owner.send_message("OK2IPW", "hello", 9, next_hop="OK2IPW")

    owner._start_payload(message, "OK2IPW")

    assert_rejected(message, transport, payload)


def test_payload_receive_cannot_bypass_native_agreement():
    owner, transport, payload = station(callsign="OK2IPW")
    message = Message(
        msg_id=10, source="OK7PS", final_dest="OK2IPW", next_hop="OK2IPW",
        direction="in", state=SessionState.ACKED,
    )
    owner.sessions[10] = message
    owner._on_frame(ControlFrame(
        FrameType.START_VARA, source="OK7PS", destination="OK2IPW",
        next_hop="OK2IPW", message_id=10,
    ))

    assert_rejected(message, transport, payload)
