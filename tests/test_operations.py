import time
from types import SimpleNamespace

from guardian.config import StationConfig
from guardian.message import Folder, MailMessage, MessageStore, Status
from guardian.operations import (
    ALERT_SWEEP_BURSTS,
    ALERT_SWEEP_MAX_CHANNELS,
    PTT_TEST_MAX_SECONDS,
    Operations,
    control_mode_compatible,
    _ALERT_HISTORY,
)
from guardian.modem import make_modem
from guardian.protocol import ControlFrame, FrameType, Priority, encode_alert
from guardian.radio.base import RadioState
from guardian.routing import HeardStations, Route, RouteTable
from guardian.services import (
    EventBus,
    LogLevel,
    RadioSnapshot,
    SnapshotStore,
    WorkerPool,
)
from guardian.session import LoopbackBus, Orchestrator, SessionState
from guardian.session.orchestrator import (
    ACK_TIMEOUT,
    START_TIMEOUT,
    working_channel_token,
)


def test_idle_operations_never_transmit_and_keep_mail_queued(tmp_path) -> None:
    config = StationConfig(callsign="OK7PS", radio_backend="none")
    events = EventBus()
    snapshots = SnapshotStore()
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail")
    routes = RouteTable()
    operations = Operations(
        config,
        events,
        snapshots,
        workers,
        mailstore,
        routes,
        HeardStations(),
    )
    message = MailMessage(
        msg_id=mailstore.next_id(config.callsign),
        source=config.callsign,
        final_dest="OK1AAA",
        subject="Safe smoke",
        body="No RF without operator action.",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    try:
        operations.tick()
        assert operations.audio_transport is None
        assert not operations.send_queued(message.msg_id)
        assert mailstore.get(message.msg_id).status == Status.QUEUED
        assert not snapshots.read().network.control_channel_active
    finally:
        operations.close()
        workers.close(wait=True)


def _operations(tmp_path, **overrides) -> tuple[Operations, WorkerPool, MessageStore]:
    config = StationConfig(callsign="OK7PS", radio_backend="none", **overrides)
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail")
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        mailstore,
        RouteTable(),
        HeardStations(),
    )
    return operations, workers, mailstore


def _spy_transmissions(operations) -> list:
    """Capture what actually reaches the transport.

    Replacing an orchestrator method instead would create the attribute it is
    meant to exercise: 0.6.27 called a non-existent `send_beacon()` and the
    test passed anyway because it had just invented one.
    """
    sent = []
    transport = operations.net.transport
    original = transport.send

    def record(frame):
        sent.append(frame)
        return original(frame)

    transport.send = record
    return sent


def test_beacon_and_auto_delivery_stay_silent_without_a_control_channel(
    tmp_path,
) -> None:
    # Both behaviours key the radio with no operator asking. Neither may fire
    # while the control channel is down -- there is nothing to transmit on.
    operations, workers, mailstore = _operations(
        tmp_path, beacon_enabled=True, beacon_interval=15.0, auto_deliver=True
    )
    sent = _spy_transmissions(operations)
    operations.heard.record("OK1AAA", 1_000.0)
    mailstore.add(
        MailMessage(
            msg_id=1,
            source="OK7PS",
            final_dest="OK1AAA",
            body="waiting",
            folder=Folder.OUTBOX,
            status=Status.QUEUED,
        )
    )
    try:
        assert operations.audio_transport is None
        for _ in range(5):
            operations.tick()
        assert sent == []
        assert mailstore.get(1).status == Status.QUEUED
    finally:
        operations.close()
        workers.close(wait=True)


def test_the_beacon_switch_actually_beacons_on_its_interval(tmp_path) -> None:
    # beacon_enabled was a dead checkbox until 0.6.27: send_beacon() existed
    # but nothing ever called it.
    operations, workers, _ = _operations(
        tmp_path, beacon_enabled=True, beacon_interval=60.0
    )
    sent = _spy_transmissions(operations)
    operations.audio_transport = SimpleNamespace(pump=lambda: 0)
    try:
        operations._tick_beacon(1_000.0)
        assert len(sent) == 1
        assert sent[0].type is FrameType.BEACON
        assert sent[0].source == "OK7PS"

        operations._tick_beacon(1_030.0)      # inside the interval
        assert len(sent) == 1

        operations._tick_beacon(1_061.0)      # past it
        assert len(sent) == 2

        operations.config.beacon_enabled = False
        operations._tick_beacon(2_000.0)
        assert len(sent) == 2
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_auto_delivery_waits_for_the_next_hop_to_be_heard(tmp_path) -> None:
    operations, workers, mailstore = _operations(tmp_path, auto_deliver=True)
    attempts = []
    operations.send_queued = lambda msg_id: attempts.append(msg_id) or True
    operations.audio_transport = SimpleNamespace(pump=lambda: 0)
    for msg_id, dest, status in (
        (1, "OK1AAA", Status.QUEUED),
        (2, "OK9XXX", Status.QUEUED),
        (3, "OK1AAA", Status.FAILED),
    ):
        mailstore.add(
            MailMessage(
                msg_id=msg_id,
                source="OK7PS",
                final_dest=dest,
                body="waiting",
                folder=Folder.OUTBOX,
                status=status,
            )
        )
    try:
        # Nobody heard yet: nothing goes out.
        operations._tick_auto_deliver(1_000.0)
        assert attempts == []

        operations.heard.record("OK1AAA", 1_100.0)
        operations._tick_auto_deliver(1_100.0)
        assert attempts == [1]

        # #2's hop is still unheard and #3 failed, so the operator owns it.
        operations._tick_auto_deliver(1_200.0)
        assert attempts == [1]

        operations.config.auto_deliver = False
        operations.heard.record("OK9XXX", 1_300.0)
        operations._tick_auto_deliver(1_300.0)
        assert attempts == [1]
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_session_timeouts_follow_the_control_modem(tmp_path) -> None:
    # The budget must cover announce + reply on whichever modem is running.
    # MFSK was 5 s a frame at the original 31.25 baud and needed far more than
    # the 8 s floor; at 125 baud it fits again -- the rule is what matters, not
    # the number.
    operations, workers, _ = _operations(tmp_path)
    try:
        assert operations.net.ack_timeout == ACK_TIMEOUT
        assert operations.net.start_timeout == START_TIMEOUT

        for name in ("afsk1200", "mfsk16"):
            modem = make_modem(name, sample_rate=48000)
            transport = SimpleNamespace(modem=modem, on_frame=None)
            net = SimpleNamespace(
                ack_timeout=ACK_TIMEOUT, start_timeout=START_TIMEOUT
            )
            operations._scale_session_timeouts(net, transport)
            exchange = 2 * modem.airtime(48)
            assert net.ack_timeout >= exchange
            assert net.ack_timeout >= ACK_TIMEOUT

        # A slow modem does push the budget past the floor.
        slow = SimpleNamespace(
            modem=SimpleNamespace(airtime=lambda n: 12.0), on_frame=None
        )
        net = SimpleNamespace(ack_timeout=ACK_TIMEOUT, start_timeout=START_TIMEOUT)
        operations._scale_session_timeouts(net, slow)
        assert net.ack_timeout > 24.0

        # A transport with no modem (the idle NullTransport) is left alone.
        net = SimpleNamespace(ack_timeout=ACK_TIMEOUT, start_timeout=START_TIMEOUT)
        operations._scale_session_timeouts(net, SimpleNamespace())
        assert net.ack_timeout == ACK_TIMEOUT
    finally:
        operations.close()
        workers.close(wait=True)


def test_hf_bandwidth_is_only_sent_in_hf_mode(tmp_path) -> None:
    config = StationConfig(callsign="OK7PS", radio_backend="none")

    assert config.vara_hf_bandwidth == "BW2300"
    config.apply_vara_mode("HF")
    assert config.vara_mode == "HF"
    # FM has no bandwidth command in VARA's reference; the setting is HF-only.
    config.apply_vara_mode("FM")
    assert config.vara_hf_bandwidth == "BW2300"


def test_both_payload_modes_use_the_existing_backend_factory(tmp_path) -> None:
    for payload_backend in ("vara_p2p",):
        config = StationConfig(
            callsign="OK7PS",
            payload_backend=payload_backend,
        )
        workers = WorkerPool(max_workers=1)
        operations = Operations(
            config,
            EventBus(),
            SnapshotStore(),
            workers,
            MessageStore(tmp_path / payload_backend),
            RouteTable(),
            HeardStations(),
        )
        try:
            assert operations.net.payload is not None
            assert operations.config.payload_backend == payload_backend
        finally:
            operations.close()
            workers.close(wait=True)


def test_connect_vara_starts_the_selected_local_variant(
    tmp_path, monkeypatch
) -> None:
    executable = tmp_path / "VARAFM.exe"
    executable.touch()
    config = StationConfig(
        callsign="NOCALL",
        vara_mode="FM",
        vara_fm_path=str(executable),
    )
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail"),
        RouteTable(),
        HeardStations(),
    )
    attempts = []
    launches = []

    def connect(*, timeout):
        attempts.append(timeout)
        if len(attempts) == 1:
            raise ConnectionRefusedError("not running")

    class Process:
        def poll(self):
            return None

    monkeypatch.setattr(operations.vara, "connect", connect)
    monkeypatch.setattr(
        "guardian.operations.subprocess.Popen",
        lambda *args, **kwargs: launches.append((args, kwargs)) or Process(),
    )
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    try:
        assert operations.connect_vara()
        while workers.is_active("vara-control"):
            time.sleep(0.001)
        results = workers.drain()

        assert results[0].succeeded
        assert len(attempts) == 2
        assert launches[0][0][0] == [str(executable.resolve())]
    finally:
        operations.close()
        workers.close(wait=True)


def test_direct_route_qsy_happens_before_message_announcement(
    tmp_path, monkeypatch
) -> None:
    config = StationConfig(callsign="OK7PS", auto_qsy=True)
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail-qsy")
    message = MailMessage(
        msg_id=mailstore.next_id(config.callsign),
        source=config.callsign,
        final_dest="OK1AAA",
        subject="Direct",
        body="QSY first",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        mailstore,
        RouteTable([Route("OK1AAA", "", freq_hz=145_550_000, mode="FM")]),
        HeardStations(),
    )
    events = []

    class Radio:
        def get_state(self):
            return type("State", (), {"frequency_hz": 145_500_000})()

        def set_frequency(self, value):
            events.append(("frequency", value))

        def set_mode(self, value):
            events.append(("mode", value))

        def close(self):
            pass

    class Transport:
        def stop(self):
            pass

    operations.radio = Radio()
    operations.audio_transport = Transport()
    monkeypatch.setattr(
        operations.net,
        "send_message",
        lambda **kwargs: events.append(("announce", kwargs["final_dest"])),
    )
    try:
        assert operations.send_queued(message.msg_id)
        assert events[:3] == [
            ("frequency", 145_550_000),
            ("mode", "FM"),
            ("announce", "OK1AAA"),
        ]
        assert operations._qsy_previous == 145_500_000
        operations._session_event(
            SimpleNamespace(
                source="OK7PS",
                msg_id=message.msg_id,
                direction="out",
                state=SessionState.CONFIRMED,
            ),
            "peer confirmed RECEIVED",
        )
        assert events[-1] == ("frequency", 145_500_000)
        assert operations._qsy_previous is None
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_opt_in_working_channel_does_not_qsy_before_control_announcement(
    tmp_path, monkeypatch
) -> None:
    config = StationConfig(
        callsign="OK7PS",
        radio_backend="hamlib",
        rig_model=3073,
        auto_qsy=True,
        separate_working_channels=True,
    )
    workers = WorkerPool(max_workers=1)
    mailstore = MessageStore(tmp_path / "mail-working")
    message = MailMessage(
        msg_id=mailstore.next_id(config.callsign),
        source=config.callsign,
        final_dest="OK1AAA",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        mailstore,
        RouteTable(
            [
                Route(
                    "OK1AAA",
                    "",
                    "",
                    145_500_000,
                    "FM",
                    145_550_000,
                    "FM",
                )
            ]
        ),
        HeardStations(),
    )
    operations.radio = _SweepRadio()
    operations.audio_transport = SimpleNamespace(stop=lambda: None)
    announced = []
    monkeypatch.setattr(
        operations.net,
        "send_message",
        lambda **kwargs: announced.append(kwargs["final_dest"]),
    )
    try:
        assert operations.send_queued(message.msg_id)
        assert announced == ["OK1AAA"]
        assert operations.radio.commands == []
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_opt_in_payload_channel_is_matched_tuned_and_restored(
    tmp_path, monkeypatch
) -> None:
    config = StationConfig(
        callsign="OK7PS",
        radio_backend="hamlib",
        rig_model=3073,
        auto_qsy=True,
        separate_working_channels=True,
        vara_mode="FM",
    )
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail-working-qsy"),
        RouteTable(
            [Route("OK1AAA", "", "", 145_500_000, "FM", 145_550_000, "FM")]
        ),
        HeardStations(),
    )
    operations.radio = _SweepRadio(145_500_000, "FM")
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    try:
        channel = operations._working_channel_offer("OK1AAA")
        assert channel == (145_550_000, "FM")
        token = working_channel_token(*channel)
        assert operations._working_channel_accept("OK1AAA", token) == channel
        assert operations._working_channel_accept("OK1AAA", token + "X") is None

        message = SimpleNamespace(
            next_hop="OK1AAA",
            source="OK1AAA",
            working_frequency_hz=145_550_000,
            working_mode="FM",
        )
        assert operations._payload_send_qsy(message)
        assert operations.radio.commands[:2] == [
            ("frequency", 145_550_000),
            ("mode", "FM"),
        ]
        operations._payload_restore_calling()
        assert operations.radio.commands[-2:] == [
            ("frequency", 145_500_000),
            ("mode", "FM"),
        ]
        assert operations._qsy_previous is None
    finally:
        operations.close()
        workers.close(wait=True)


def _working_operations(tmp_path, routes: RouteTable, **overrides):
    settings = {
        "callsign": "OK7PS",
        "radio_backend": "hamlib",
        "rig_model": 3073,
        "auto_qsy": True,
        "separate_working_channels": True,
        "vara_mode": "FM",
    }
    settings.update(overrides)
    config = StationConfig(**settings)
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail-working-follow"),
        routes,
        HeardStations(),
    )
    operations.radio = _SweepRadio(145_500_000, "FM")
    return operations, workers


def test_peer_working_channel_is_followed_when_the_local_route_differs(
    tmp_path, monkeypatch
) -> None:
    # The two operators typed different working frequencies for the same link,
    # which is the ordinary case. The station that opens the session names the
    # channel; this one goes there instead of failing the negotiation.
    routes = RouteTable(
        [Route("OK2IPW", "", "", 145_500_000, "FM", 145_300_000, "FM")]
    )
    operations, workers = _working_operations(tmp_path, routes)
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    try:
        proposed = working_channel_token(145_350_000, "FM")
        assert operations._working_channel_offer("OK2IPW") == (145_300_000, "FM")
        assert operations._working_channel_accept("OK2IPW", proposed) == (
            145_350_000,
            "FM",
        )

        message = SimpleNamespace(
            next_hop="OK2IPW",
            source="OK2IPW",
            working_frequency_hz=145_350_000,
            working_mode="FM",
        )
        assert operations._payload_receive_qsy(message)
        assert operations.radio.commands[:2] == [
            ("frequency", 145_350_000),
            ("mode", "FM"),
        ]
        operations._payload_restore_calling()
        assert operations.radio.commands[-2:] == [
            ("frequency", 145_500_000),
            ("mode", "FM"),
        ]
    finally:
        operations.close()
        workers.close(wait=True)


def test_two_stations_with_different_working_channels_agree_and_deliver(
    tmp_path,
) -> None:
    # The whole point, end to end: OK7PS has 145.350 configured for OK2IPW,
    # OK2IPW has 145.300 configured for OK7PS, and the session still runs --
    # on the proposer's 145.350, with both stations tuned there.
    here, here_workers = _working_operations(
        tmp_path / "here",
        RouteTable([Route("OK2IPW", "", "", 145_500_000, "FM", 145_350_000, "FM")]),
    )
    there, there_workers = _working_operations(
        tmp_path / "there",
        RouteTable([Route("OK7PS", "", "", 145_500_000, "FM", 145_300_000, "FM")]),
        callsign="OK2IPW",
    )
    bus = LoopbackBus()
    sender = Orchestrator("OK7PS", bus.endpoint("sender"), auto_route=False)
    receiver = Orchestrator(
        "OK2IPW", bus.endpoint("receiver"), auto_complete=True, auto_route=False
    )
    sender.working_channel_offer = here._working_channel_offer
    receiver.working_channel_accept = there._working_channel_accept
    try:
        assert here._working_channel_offer("OK2IPW") == (145_350_000, "FM")
        assert there._working_channel_offer("OK7PS") == (145_300_000, "FM")

        message = sender.send_message(
            "OK2IPW", "hello", msg_id=901, next_hop="OK2IPW"
        )
        now = 1.0
        for _ in range(20):
            delivered = bus.pump()
            sender.tick(now)
            receiver.tick(now)
            if delivered == 0 and bus.idle:
                break
            now += 0.25

        assert message.state is SessionState.DELIVERED
        inbound = receiver.sessions[901]
        assert (message.working_frequency_hz, message.working_mode) == (
            145_350_000,
            "FM",
        )
        assert (inbound.working_frequency_hz, inbound.working_mode) == (
            145_350_000,
            "FM",
        )
    finally:
        here.close()
        there.close()
        here_workers.close(wait=True)
        there_workers.close(wait=True)


def test_peer_may_not_move_this_station_to_another_band_or_mode(tmp_path) -> None:
    # Following a proposal is bounded automation, not remote control of the
    # dial: another band, a mode VARA cannot use here, and anything outside
    # the amateur service stay refused.
    routes = RouteTable(
        [Route("OK2IPW", "", "", 145_500_000, "FM", 145_300_000, "FM")]
    )
    operations, workers = _working_operations(tmp_path, routes)
    try:
        for frequency, mode in (
            (433_500_000, "FM"),        # another band
            (150_000_000, "FM"),        # outside the amateur service
            (145_350_000, "USB"),       # VARA FM cannot work this
        ):
            token = working_channel_token(frequency, mode)
            assert operations._working_channel_accept("OK2IPW", token) is None
        assert operations._working_channel_accept("OK2IPW", "!!") is None
    finally:
        operations.close()
        workers.close(wait=True)


def test_peer_working_channel_is_bounded_by_the_calling_frequency(tmp_path) -> None:
    # No working channel configured for this peer at all: the route's calling
    # frequency is what the proposal is judged against.
    routes = RouteTable([Route("OK2IPW", "", "", 145_500_000, "FM")])
    operations, workers = _working_operations(tmp_path, routes)
    try:
        assert operations._working_channel_offer("OK2IPW") is None
        assert operations._working_channel_accept(
            "OK2IPW", working_channel_token(145_350_000, "FM")
        ) == (145_350_000, "FM")
        assert operations._working_channel_accept(
            "OK2IPW", working_channel_token(29_500_000, "FM")
        ) is None
    finally:
        operations.close()
        workers.close(wait=True)


def test_following_a_peer_channel_still_needs_the_opt_in_and_a_cat_radio(
    tmp_path,
) -> None:
    routes = RouteTable(
        [Route("OK2IPW", "", "", 145_500_000, "FM", 145_300_000, "FM")]
    )
    token = working_channel_token(145_350_000, "FM")
    for overrides in (
        {"separate_working_channels": False},
        {"auto_qsy": False},
        {"rig_model": 1},
    ):
        operations, workers = _working_operations(tmp_path, routes, **overrides)
        try:
            assert operations._working_channel_accept("OK2IPW", token) is None
        finally:
            operations.close()
            workers.close(wait=True)


def test_separate_working_channel_refuses_no_cat_automation(tmp_path) -> None:
    config = StationConfig(
        callsign="OK7PS",
        radio_backend="hamlib",
        rig_model=1,
        auto_qsy=True,
        separate_working_channels=True,
    )
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail-working-nocat"),
        RouteTable(
            [Route("OK1AAA", "", "", 145_500_000, "FM", 145_550_000, "FM")]
        ),
        HeardStations(),
    )
    try:
        try:
            operations._working_channel_offer("OK1AAA")
        except RuntimeError as exc:
            assert "CAT" in str(exc)
        else:
            raise AssertionError("No-CAT working channel was accepted")
    finally:
        operations.close()
        workers.close(wait=True)


def test_no_cat_qsy_waits_for_operator_and_cancel_keeps_message_queued(
    tmp_path, monkeypatch
) -> None:
    operations, workers, mailstore = _operations(
        tmp_path,
        rig_model=1,
        manual_frequency_hz=145_500_000,
        auto_qsy=True,
    )
    operations.config.radio_backend = "hamlib"
    operations.routes.add(
        Route("OK1AAA", "", freq_hz=145_550_000, mode="FM")
    )
    message = MailMessage(
        msg_id=mailstore.next_id("OK7PS"),
        source="OK7PS",
        final_dest="OK1AAA",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    operations.audio_transport = SimpleNamespace()
    announced: list[str] = []
    monkeypatch.setattr(operations.config, "save", lambda *a, **k: None)
    monkeypatch.setattr(
        operations.net,
        "send_message",
        lambda **kwargs: announced.append(kwargs["final_dest"]),
    )
    prompts: list[tuple[str, int, str]] = []
    operations.confirm_manual_qsy = lambda call, freq, mode: (
        prompts.append((call, freq, mode)) or False
    )
    try:
        assert not operations.send_queued(message.msg_id)
        assert prompts == [("OK1AAA", 145_550_000, "FM")]
        assert announced == []
        assert mailstore.get(message.msg_id).status == Status.QUEUED
        assert operations.current_frequency() == 145_500_000
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_confirmed_no_cat_qsy_updates_the_reported_dial_before_sending(
    tmp_path, monkeypatch
) -> None:
    operations, workers, mailstore = _operations(
        tmp_path,
        rig_model=1,
        manual_frequency_hz=145_500_000,
        auto_qsy=True,
    )
    operations.config.radio_backend = "hamlib"
    operations.routes.add(
        Route("OK1AAA", "", freq_hz=145_550_000, mode="FM")
    )
    message = MailMessage(
        msg_id=mailstore.next_id("OK7PS"),
        source="OK7PS",
        final_dest="OK1AAA",
        created=time.time(),
        folder=Folder.OUTBOX,
        status=Status.QUEUED,
    )
    mailstore.add(message)
    operations.audio_transport = SimpleNamespace()
    monkeypatch.setattr(operations.config, "save", lambda *a, **k: None)
    announced: list[tuple[str, int | None]] = []
    monkeypatch.setattr(
        operations.net,
        "send_message",
        lambda **kwargs: announced.append(
            (kwargs["final_dest"], operations.current_frequency())
        ),
    )
    operations.confirm_manual_qsy = lambda *_args: True
    try:
        assert operations.send_queued(message.msg_id)
        assert announced == [("OK1AAA", 145_550_000)]
        assert operations.config.manual_frequency_hz == 145_550_000
        assert operations._qsy_previous is None, "a no-CAT dial cannot auto-restore"
        assert operations.alert_sweep_channels() == []
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


class _FakeVara:
    """Records the command lines Guardian sends to VARA."""

    def __init__(self, link_state: str = "DISCONNECTED") -> None:
        self.connected = True
        self.commands: list[str] = []
        self.state = SimpleNamespace(link_state=link_state)

    def send_command(self, command: str) -> None:
        self.commands.append(command)


def test_hf_bandwidth_reaches_vara_when_the_operator_changes_it(tmp_path) -> None:
    # Before 0.6.33 the bandwidth was only ever sent inside connect_vara(), so
    # switching 2300 -> 2750 in Settings left the modem on 2300 with nothing in
    # the log to say so.
    operations, workers, _ = _operations(tmp_path, vara_mode="HF")
    operations.vara = _FakeVara()
    try:
        assert operations.apply_vara_session_settings()
        assert "BW2300" in operations.vara.commands
        assert "P2P SESSION" in operations.vara.commands

        operations.vara.commands.clear()
        operations.config.vara_hf_bandwidth = "BW2750"
        assert operations.apply_vara_session_settings()
        assert "BW2750" in operations.vara.commands
    finally:
        operations.close()
        workers.close(wait=True)


def test_fm_is_never_sent_a_bandwidth_or_p2p_command(tmp_path) -> None:
    # Both are HF/SAT only; VARA FM answers WRONG.
    operations, workers, _ = _operations(tmp_path, vara_mode="FM")
    operations.vara = _FakeVara()
    try:
        assert operations.apply_vara_session_settings()
        assert not [c for c in operations.vara.commands if c.startswith("BW")]
        assert "P2P SESSION" not in operations.vara.commands
        assert "CHAT OFF" in operations.vara.commands
    finally:
        operations.close()
        workers.close(wait=True)


def test_session_settings_are_not_pushed_into_a_live_link(tmp_path) -> None:
    # Session-level commands mid-connection drop the link, per the reference.
    operations, workers, _ = _operations(tmp_path, vara_mode="HF")
    operations.vara = _FakeVara(link_state="CONNECTED")
    try:
        assert not operations.apply_vara_session_settings()
        assert operations.vara.commands == []
    finally:
        operations.close()
        workers.close(wait=True)


def test_endpoint_and_tuning_changes_are_told_apart(tmp_path) -> None:
    # A bandwidth edit can be re-sent to a live VARA; a mode or port change
    # means a different modem instance and needs a reconnect.
    operations, workers, _ = _operations(tmp_path, vara_mode="HF")
    try:
        endpoint, tuning = operations.vara_endpoint(), operations.vara_tuning()

        operations.config.vara_hf_bandwidth = "BW500"
        assert operations.vara_endpoint() == endpoint
        assert operations.vara_tuning() != tuning

        operations.config.apply_vara_mode("FM")
        assert operations.vara_endpoint() != endpoint
    finally:
        operations.close()
        workers.close(wait=True)


def test_alert_is_not_broadcast_while_the_control_channel_is_down(tmp_path) -> None:
    # An alert keys the radio the moment the operator confirms it. With no
    # control channel there is nothing to key, and the refusal has to be
    # visible rather than a silent no-op.
    operations, workers, _ = _operations(tmp_path)
    sent = _spy_transmissions(operations)
    try:
        assert operations.audio_transport is None
        assert not operations.send_alert(0x01, "POZAR")
        for _ in range(5):
            operations.tick()
        assert sent == []
        assert operations.alerts == []
    finally:
        operations.close()
        workers.close(wait=True)


def test_heard_alert_is_recorded_for_the_banner(tmp_path) -> None:
    operations, workers, _ = _operations(tmp_path)
    frame = ControlFrame(
        type=FrameType.ALERT,
        source="OK2IPW",
        destination=encode_alert(0x02, "ZRANENI U MOSTU", "OK2IPW"),
        next_hop="",
        message_id=4242,
        priority=Priority.EMERGENCY,
        ttl=3,
    )
    try:
        operations.net._on_frame(frame)
        assert len(operations.alerts) == 1
        record = operations.alerts[0]
        assert (record.code, record.note, record.source) == (
            0x02,
            "ZRANENI U MOSTU",
            "OK2IPW",
        )
        assert not record.mine
        assert record.priority is Priority.EMERGENCY
        # A repeat of the same alert must not stack up in the banner.
        operations.net._on_frame(frame)
        assert len(operations.alerts) == 1
    finally:
        operations.close()
        workers.close(wait=True)


def test_alert_history_is_bounded(tmp_path) -> None:
    operations, workers, _ = _operations(tmp_path)
    try:
        for msg_id in range(_ALERT_HISTORY + 5):
            operations.net._on_frame(
                ControlFrame(
                    type=FrameType.ALERT,
                    source="OK1AAA",
                    destination=encode_alert(0x20, f"drill {msg_id}", "OK1AAA"),
                    next_hop="",
                    message_id=msg_id,
                    priority=Priority.ROUTINE,
                    ttl=1,
                )
            )
        assert len(operations.alerts) == _ALERT_HISTORY
        assert operations.alerts[0].note.endswith(str(_ALERT_HISTORY + 4))
    finally:
        operations.close()
        workers.close(wait=True)


class _SweepRadio:
    """A rig that records what it was told, and can fail on chosen channels."""

    name = "test-radio"

    def __init__(
        self,
        frequency_hz: int = 145_500_000,
        mode: str = "FM",
        failing=(),
    ) -> None:
        self.frequency_hz = frequency_hz
        self.mode = mode
        self.failing = set(failing)
        self.commands: list[tuple[str, object]] = []

    def get_state(self):
        return RadioState(
            connected=True, frequency_hz=self.frequency_hz, mode=self.mode
        )

    def set_frequency(self, value: int) -> None:
        if value in self.failing:
            raise OSError(f"rig refused {value}")
        self.frequency_hz = value
        self.commands.append(("frequency", value))

    def set_mode(self, value: str) -> None:
        self.mode = value
        self.commands.append(("mode", value))

    def set_ptt(self, on: bool) -> None:
        pass

    def close(self) -> None:
        pass


class _SweepTransport:
    """Enough of AudioControlTransport for the sweep to run against."""

    def __init__(self) -> None:
        self.sent: list = []
        self.on_frame = None
        self.last_frame_snr = None

    def send(self, frame) -> None:
        self.sent.append(frame)

    def wait_tx_idle(self, timeout: float = 5.0) -> bool:
        return True

    def pump(self) -> int:
        return 0

    def stop(self) -> None:
        pass


def _sweeping_operations(tmp_path, routes, radio=None):
    config = StationConfig(callsign="OK7PS", radio_backend="none")
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail"),
        routes,
        HeardStations(),
    )
    transport = _SweepTransport()
    operations.radio = radio or _SweepRadio()
    operations.audio_transport = transport
    operations.net.transport = transport
    return operations, workers, transport


def _alert(code: int = 0x01, note: str = "POZAR") -> ControlFrame:
    return ControlFrame(
        type=FrameType.ALERT,
        source="OK7PS",
        destination=encode_alert(code, note, "OK7PS"),
        next_hop="",
        message_id=0xA11E,
        priority=Priority.EMERGENCY,
        ttl=3,
    )


def _wait_for_worker(operations: Operations, name: str) -> None:
    deadline = time.monotonic() + 2.0
    while operations.workers.is_active(name) and time.monotonic() < deadline:
        time.sleep(0.001)
    operations.workers.drain()


def _scanning_operations(tmp_path):
    routes = RouteTable(
        [
            Route("OK1FM", "", "", 145_550_000, "FM"),
            Route("OK1HF", "", "", 7_100_000, "USB"),
        ]
    )
    operations, workers, transport = _sweeping_operations(tmp_path, routes)
    operations.config.radio_backend = "hamlib"
    operations.config.rig_model = 3073
    operations.snapshots.update(
        radio=RadioSnapshot(
            connected=True,
            name="hamlib",
            frequency_hz=145_500_000,
            mode="FM",
            signal=-110,
        )
    )
    return operations, workers, transport


def test_scanner_uses_only_channels_compatible_with_the_live_modem(tmp_path) -> None:
    operations, workers, _transport = _scanning_operations(tmp_path)
    try:
        assert control_mode_compatible("afsk1200", "FM")
        assert not control_mode_compatible("afsk1200", "USB")
        assert control_mode_compatible("mfsk16", "USB")
        assert not control_mode_compatible("mfsk16", "FM")
        assert [channel.freq_hz for channel in operations.scanner_channels()] == [
            145_500_000,
            145_550_000,
        ]
        assert operations.alert_sweep_channels() == [(145_550_000, "FM")]
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_scanner_tunes_off_thread_reports_state_and_returns_home(tmp_path) -> None:
    operations, workers, _transport = _scanning_operations(tmp_path)
    operations.config.scan_dwell = 1.0
    try:
        assert operations.start_scanner()
        snapshot = operations.snapshots.read().network
        assert snapshot.scanner_active
        assert snapshot.scanner_channels == 2
        assert snapshot.scanner_frequency_hz == 145_500_000

        operations._tick_scanner(operations.scanner.last_change + 1.0)
        _wait_for_worker(operations, "scanner-tune")
        assert operations.radio.frequency_hz == 145_550_000
        assert operations.snapshots.read().network.scanner_frequency_hz == 145_550_000

        assert operations.stop_scanner()
        _wait_for_worker(operations, "scanner-home")
        assert operations.radio.frequency_hz == 145_500_000
        assert not operations.snapshots.read().network.scanner_active
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_scanner_pauses_for_payload_and_blocks_unsynchronised_transmit(tmp_path) -> None:
    operations, workers, transport = _scanning_operations(tmp_path)
    try:
        assert operations.start_scanner()
        operations._payload_active.set()
        operations._tick_scanner(operations.scanner.last_change + 60.0)
        operations._update_network_snapshot()
        assert operations.snapshots.read().network.scanner_paused
        assert operations.radio.commands == []
        operations._payload_active.clear()

        assert not operations.send_alert(0x01, "POZAR")
        assert transport.sent == []
    finally:
        operations._payload_active.clear()
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_scanner_refuses_no_cat_and_survives_one_failed_qsy(tmp_path) -> None:
    operations, workers, _transport = _scanning_operations(tmp_path)
    try:
        operations.config.rig_model = 1  # Hamlib Dummy
        assert not operations.start_scanner()

        operations.config.rig_model = 3073
        operations.radio.failing.add(145_550_000)
        operations.config.scan_dwell = 1.0
        assert operations.start_scanner()
        operations._tick_scanner(operations.scanner.last_change + 1.0)
        _wait_for_worker(operations, "scanner-tune")

        assert operations.scanner is not None
        assert operations.scanner.enabled
        assert operations.scanner.current.freq_hz == 145_550_000
        assert any(
            "could not tune" in event.message
            for event in operations.events.history()
        )
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_alert_sweep_repeats_the_same_alert_on_every_known_frequency(
    tmp_path, monkeypatch
) -> None:
    # An alert only reaches whoever is listening where we are tuned. The route
    # table is the only record of where the rest of the net is.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    routes = RouteTable(
        [
            Route("OK1AAA", "", "", 145_500_000, "FM"),
            Route("OK1BBB", "", "", 7_100_000, "USB"),
            Route("OK1CCC", "", "", 14_105_000, "USB"),
        ]
    )
    operations, workers, transport = _sweeping_operations(tmp_path, routes)
    frame = _alert()
    try:
        reached = operations._alert_sweep(
            frame,
            [(7_100_000, "USB"), (14_105_000, "USB")],
            operations.net,
            transport,
        )

        assert reached == 2
        # Back where the operator left it, on the mode they left it in: a rig
        # returned to an FM channel still set to USB would be deaf there.
        assert operations.radio.commands == [
            ("frequency", 7_100_000),
            ("mode", "USB"),
            ("frequency", 14_105_000),
            ("mode", "USB"),
            ("frequency", 145_500_000),
            ("mode", "FM"),
        ]
        assert len(transport.sent) == 2 * ALERT_SWEEP_BURSTS
        # One alert, not several: the id is what makes receivers dedupe it.
        assert {copy.message_id for copy in transport.sent} == {frame.message_id}
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_a_channel_that_will_not_tune_costs_only_that_channel(
    tmp_path, monkeypatch
) -> None:
    # The whole point of the sweep is reach; losing the remaining frequencies
    # because one QSY failed would defeat it.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    radio = _SweepRadio(failing={7_100_000})
    operations, workers, transport = _sweeping_operations(
        tmp_path, RouteTable(), radio=radio
    )
    try:
        reached = operations._alert_sweep(
            _alert(),
            [(7_100_000, "USB"), (14_105_000, "USB")],
            operations.net,
            transport,
        )

        assert reached == 1
        assert len(transport.sent) == ALERT_SWEEP_BURSTS
        assert radio.commands[-2:] == [("frequency", 145_500_000), ("mode", "FM")]
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_the_sweep_stops_when_the_control_channel_goes_away(
    tmp_path, monkeypatch
) -> None:
    # VARA takes the codec mid-sweep, or the operator stops the channel: there
    # is nothing to transmit with any more, and the radio must still come home.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    operations, workers, transport = _sweeping_operations(tmp_path, RouteTable())
    try:
        operations._payload_active.set()
        reached = operations._alert_sweep(
            _alert(),
            [(7_100_000, "USB"), (14_105_000, "USB")],
            operations.net,
            transport,
        )

        assert reached == 0
        assert transport.sent == []
        assert operations.radio.commands == [
            ("frequency", 145_500_000),
            ("mode", "FM"),
        ]
    finally:
        operations._payload_active.clear()
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_the_sweep_skips_the_current_frequency_and_stays_bounded(tmp_path) -> None:
    routes = RouteTable(
        [
            Route(f"OK1A{index:02d}", "", "", 145_000_000 + index * 1_000, "FM")
            for index in range(ALERT_SWEEP_MAX_CHANNELS + 5)
        ]
    )
    operations, workers, transport = _sweeping_operations(tmp_path, routes)
    try:
        operations.snapshots.update(
            radio=RadioSnapshot(connected=True, frequency_hz=145_001_000, mode="FM")
        )
        channels = operations.alert_sweep_channels()

        assert len(channels) == ALERT_SWEEP_MAX_CHANNELS
        assert 145_001_000 not in [freq for freq, _mode in channels]
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_an_alert_without_a_sweep_never_touches_the_radio(tmp_path) -> None:
    routes = RouteTable([Route("OK1AAA", "", "", 7_100_000, "USB")])
    operations, workers, transport = _sweeping_operations(tmp_path, routes)
    try:
        assert operations.send_alert(0x12, "QRV", sweep=False)
        assert not workers.is_active("alert-sweep")
        assert operations.radio.commands == []
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_the_sweep_waits_for_the_home_repeats_before_tuning_away(
    tmp_path, monkeypatch
) -> None:
    # The home copies are spaced by the tick loop. Tuning away while they are
    # still queued would put them on a channel the radio has already left.
    monkeypatch.setattr("guardian.operations.ALERT_SWEEP_HOME_WAIT", 0.3)
    operations, workers, transport = _sweeping_operations(tmp_path, RouteTable())
    net = operations.net
    try:
        net.tick(1_000.0)
        net.send_alert(0x01, "POZAR")
        assert net.alerts_pending() > 0

        started = time.monotonic()
        operations._wait_for_queued_alerts(net, transport)
        waited = time.monotonic() - started

        # Nothing drained the queue, so the sweep waited out its bound rather
        # than tuning away immediately -- and rather than waiting forever.
        assert waited >= 0.3
        assert net.alerts_pending() > 0

        net.tick(1_100.0)                    # copies are on the air now
        started = time.monotonic()
        operations._wait_for_queued_alerts(net, transport)
        assert time.monotonic() - started < 0.3
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


def test_sending_an_alert_hands_the_sweep_to_a_worker(tmp_path) -> None:
    # The operator's dialog must not block on ten QSYs and twenty bursts.
    routes = RouteTable([Route("OK1AAA", "", "", 145_550_000, "FM")])
    operations, workers, transport = _sweeping_operations(tmp_path, routes)
    swept: list[tuple] = []
    operations._alert_sweep = (
        lambda frame, channels, net, tport: swept.append((frame, channels)) or 1
    )
    try:
        assert operations.send_alert(0x01, "POZAR")
        while workers.is_active("alert-sweep"):
            time.sleep(0.001)
        workers.drain()

        assert len(swept) == 1
        frame, channels = swept[0]
        assert channels == [(145_550_000, "FM")]
        assert frame.type is FrameType.ALERT
        assert operations.alerts[0].note == "POZAR"
    finally:
        operations.audio_transport = None
        operations.close()
        workers.close(wait=True)


class _PttRadio:
    """A rig that logs keying and can misbehave the way real ones do.

    `reports_ptt` is the driver capability: a Hamlib rig answers for itself,
    while a VOX/serial line only ever echoes the wire we asserted.
    """

    name = "fake-rig"

    def __init__(
        self,
        *,
        reports_ptt=True,
        reports_tx=True,
        sticks_keyed=False,
        fail_on=None,
    ) -> None:
        self.reports_ptt = reports_ptt
        self.reports_tx = reports_tx
        self.sticks_keyed = sticks_keyed
        self.fail_on = fail_on          # True/False: raise when keyed/unkeyed
        self.keyings: list[bool] = []
        self.ptt = False
        self.opened = False

    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.opened = True

    def set_ptt(self, on: bool) -> None:
        self.keyings.append(on)
        if self.fail_on is on:
            raise OSError("PTT command failed")
        self.ptt = on or self.sticks_keyed

    def get_state(self):
        return RadioState(
            connected=True, ptt=self.ptt and self.reports_tx, frequency_hz=145_500_000
        )

    def close(self) -> None:
        self.opened = False


def _ptt_operations(tmp_path, radio, backend="hamlib"):
    config = StationConfig(callsign="OK7PS", radio_backend=backend)
    workers = WorkerPool(max_workers=1)
    operations = Operations(
        config,
        EventBus(),
        SnapshotStore(),
        workers,
        MessageStore(tmp_path / "mail"),
        RouteTable(),
        HeardStations(),
    )
    operations.radio = radio
    operations.rigctld = SimpleNamespace(
        ensure=lambda *a, **k: "", stop=lambda: None
    )
    return operations, workers


def _await_worker(workers, name="radio-control") -> None:
    while workers.is_active(name):
        time.sleep(0.001)
    workers.drain()


def test_ptt_test_keys_the_radio_and_confirms_it_transmitted(
    tmp_path, monkeypatch
) -> None:
    # "No exception" is not proof: the rig is asked whether it is actually in
    # TX while it is keyed.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    radio = _PttRadio()
    operations, workers = _ptt_operations(tmp_path, radio)
    results: list[tuple[bool, str]] = []
    try:
        assert operations.run_ptt_test(on_result=lambda ok, msg: results.append((ok, msg)))
        _await_worker(workers)

        assert radio.keyings == [True, False]
        assert radio.opened, "the test brings the radio up if it is not open"
        assert results[0][0]
        assert "passed" in results[0][1]
    finally:
        operations.close()
        workers.close(wait=True)


def test_a_radio_left_keyed_is_reported_as_a_fault(tmp_path, monkeypatch) -> None:
    # An interface that keys but never releases is the fault this test exists
    # to catch, and it must not read as a pass.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    radio = _PttRadio(sticks_keyed=True)
    operations, workers = _ptt_operations(tmp_path, radio)
    results: list[tuple[bool, str]] = []
    try:
        operations.run_ptt_test(on_result=lambda ok, msg: results.append((ok, msg)))
        _await_worker(workers)

        assert "still asserted" in results[0][1]
        assert "passed" not in results[0][1]
    finally:
        operations.close()
        workers.close(wait=True)


def test_a_backend_that_cannot_read_back_says_so_instead_of_passing(
    tmp_path, monkeypatch
) -> None:
    # A VOX/serial line has no telemetry: get_state() hands back the RTS/DTR
    # wire we just asserted (this fake does exactly that), which says nothing
    # about the transmitter. Reporting it as a verified pass would be a lie the
    # operator might rely on in an emergency.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    operations, workers = _ptt_operations(
        tmp_path, _PttRadio(reports_ptt=False), backend="vox"
    )
    results: list[tuple[bool, str]] = []
    try:
        operations.run_ptt_test(on_result=lambda ok, msg: results.append((ok, msg)))
        _await_worker(workers)

        assert results[0][0], "the command itself succeeded"
        assert "cannot confirm TX" in results[0][1]
    finally:
        operations.close()
        workers.close(wait=True)


def test_the_radio_is_unkeyed_even_when_the_test_itself_fails(
    tmp_path, monkeypatch
) -> None:
    # If reading the state mid-test throws, the carrier must still stop.
    monkeypatch.setattr("guardian.operations.time.sleep", lambda _seconds: None)
    radio = _PttRadio()
    radio.get_state = lambda: (_ for _ in ()).throw(OSError("CAT dropped"))
    operations, workers = _ptt_operations(tmp_path, radio)
    results: list[tuple[bool, str]] = []
    try:
        operations.run_ptt_test(on_result=lambda ok, msg: results.append((ok, msg)))
        _await_worker(workers)

        assert radio.keyings == [True, False], "unkeyed despite the failure"
        assert not results[0][0]
        assert "CAT dropped" in results[0][1]
    finally:
        operations.close()
        workers.close(wait=True)


def test_ptt_test_refuses_without_radio_control_or_during_a_transfer(
    tmp_path,
) -> None:
    radio = _PttRadio()
    operations, workers = _ptt_operations(tmp_path, radio, backend="none")
    results: list[tuple[bool, str]] = []
    try:
        assert not operations.run_ptt_test(
            on_result=lambda ok, msg: results.append((ok, msg))
        )
        assert radio.keyings == []
        assert not results[0][0]

        operations.config.radio_backend = "hamlib"
        operations._payload_active.set()
        assert not operations.run_ptt_test(
            on_result=lambda ok, msg: results.append((ok, msg))
        )
        assert radio.keyings == [], "a payload transfer owns the radio"
        assert not results[1][0]
    finally:
        operations._payload_active.clear()
        operations.close()
        workers.close(wait=True)


def test_the_keying_time_is_capped_however_it_is_called(tmp_path, monkeypatch) -> None:
    # A carrier is only ever as long as the guard allows.
    slept: list[float] = []
    monkeypatch.setattr("guardian.operations.time.sleep", slept.append)
    operations, workers = _ptt_operations(tmp_path, _PttRadio())
    try:
        operations.run_ptt_test(seconds=600.0)
        _await_worker(workers)

        assert sum(slept) == PTT_TEST_MAX_SECONDS
    finally:
        operations.close()
        workers.close(wait=True)


def test_changed_radio_settings_rebuild_the_driver(tmp_path) -> None:
    # make_driver ran once in __init__, so switching backend or PTT wiring in
    # Settings kept the old driver (old port, old reports_ptt) until restart.
    operations, workers, _ = _operations(tmp_path)
    try:
        before = operations.radio_settings()
        assert operations.radio.name == "none"

        operations.config.radio_backend = "hamlib"
        operations.config.rig_model = 1
        operations.config.ptt_type = "RTS"
        assert operations.radio_settings() != before

        operations.reconfigure_radio()
        assert operations.radio.name == "hamlib"
        assert operations.radio.reports_ptt is False, "dummy + serial PTT"

        operations.config.rig_model = 3073
        operations.config.ptt_type = "RIG"
        operations.reconfigure_radio()
        assert operations.radio.reports_ptt is True
    finally:
        operations.close()
        workers.close(wait=True)


def test_slow_keying_is_only_requested_on_fm_and_stays_capped(tmp_path) -> None:
    # HF radios do not need the crutch; the request must never leave an HF
    # configuration however the operator set the spinner.
    operations, workers, _ = _operations(
        tmp_path, vara_mode="FM", vara_ptt_delay_ms=400
    )
    try:
        assert operations._vara_keying_delay_request() == 400
        assert operations.net.ptt_delay_request() == 400

        operations.config.vara_ptt_delay_ms = 9_999
        assert operations._vara_keying_delay_request() == 700   # wire cap

        operations.config.apply_vara_mode("HF")
        assert operations._vara_keying_delay_request() == 0
    finally:
        operations.close()
        workers.close(wait=True)


def test_vara_releases_hold_the_negotiated_tail_but_keyups_stay_immediate(
    tmp_path, monkeypatch
) -> None:
    # Watched on a spectrum display: unkeying the instant VARA says PTT OFF
    # cuts the tail off the burst and the peer answers into the gap. The tail
    # belongs on the release. Key-up must stay immediate — VARA starts
    # modulating on its own clock, and keying late clips the leader instead.
    slept: list[float] = []
    monkeypatch.setattr("guardian.operations.time.sleep", slept.append)
    operations, workers, _ = _operations(tmp_path, vara_host_ptt=True)
    keyed: list[bool] = []
    operations._radio_ptt = keyed.append
    operations.configure_vara_host_ptt()
    try:
        assert operations.vara.on_ptt == operations._vara_ptt

        operations.vara.on_ptt(True)
        operations.vara.on_ptt(False)
        assert keyed == [True, False]
        assert slept == [], "no negotiation, no delay: today's behaviour"

        operations._payload_ptt_delay_ms = 400
        operations.vara.on_ptt(True)
        assert slept == [], "key-up never waits"
        operations.vara.on_ptt(False)
        assert keyed == [True, False, True, False]
        assert slept == [0.4], "the tail is held on release only"
    finally:
        operations.close()
        workers.close(wait=True)


def test_the_negotiated_gap_follows_the_session_and_dies_with_it(tmp_path) -> None:
    operations, workers, _ = _operations(tmp_path)
    try:
        message = SimpleNamespace(
            source="OK7PS", msg_id=9, direction="out",
            state=SessionState.STARTING_VARA, ptt_delay_ms=300,
        )
        operations._session_event(message, "starting VARA")
        assert operations._payload_ptt_delay_ms == 300

        # The receiving side adopts it the same way.
        inbound = SimpleNamespace(
            source="OK2IPW", msg_id=10, direction="in", payload_bytes=None,
            state=SessionState.RECEIVING, ptt_delay_ms=500,
        )
        operations._session_event(inbound, "receiving payload over VARA")
        assert operations._payload_ptt_delay_ms == 500

        inbound.state = SessionState.FAILED
        operations._session_event(inbound, "failed")
        assert operations._payload_ptt_delay_ms == 0, "never leaks to the next session"
    finally:
        operations.close()
        workers.close(wait=True)


def test_guardian_keys_for_vara_out_of_the_box(tmp_path) -> None:
    # OK2IPW's evening: rigctld owns the CAT port, so with host PTT off VARA
    # had no port left to key through. The session looked perfect -- CONNECT,
    # BITRATE, PTT ON, DISCONNECTED -- and not one watt reached the antenna.
    # Keying through Guardian is the only configuration that works with our
    # own rigctld holding the port.
    assert StationConfig().vara_host_ptt is True

    operations, workers, _ = _operations(tmp_path)
    try:
        assert operations.vara.on_ptt is not None, "wired at startup"
    finally:
        operations.close()
        workers.close(wait=True)


def test_a_profile_that_already_chose_its_own_keying_is_left_alone(
    tmp_path,
) -> None:
    # A station may be keying through VARA deliberately; taking that over
    # behind the operator's back could double-key the radio.
    path = tmp_path / "config.json"
    path.write_text('{"callsign": "OK7PS", "vara_host_ptt": false}', encoding="utf-8")

    assert StationConfig.load(path).vara_host_ptt is False


def test_a_station_that_cannot_key_for_vara_is_warned_before_the_handoff(
    tmp_path,
) -> None:
    # The one log line that would have ended the search in a minute.
    operations, workers, _ = _operations(tmp_path, vara_host_ptt=False)
    operations.config.radio_backend = "hamlib"
    operations.config.cat_port = "COM3"
    try:
        assert operations._warn_if_nothing_can_key_vara()
        warning = operations.events.history()[-1]
        assert warning.level is LogLevel.WARNING
        assert "COM3" in warning.message

        # Keying through Guardian, or no rigctld holding a port: no warning.
        operations.config.vara_host_ptt = True
        assert not operations._warn_if_nothing_can_key_vara()
        operations.config.vara_host_ptt = False
        operations.config.cat_port = ""
        assert not operations._warn_if_nothing_can_key_vara()
    finally:
        operations.close()
        workers.close(wait=True)


def test_a_negotiated_delay_nobody_can_apply_says_so(tmp_path) -> None:
    # The peer holds its tail believing we hold ours; silence here would make
    # a half-applied agreement look like a working one.
    operations, workers, _ = _operations(tmp_path, vara_host_ptt=False)
    try:
        operations._session_event(
            SimpleNamespace(
                source="OK7PS", msg_id=1, direction="out",
                state=SessionState.STARTING_VARA, ptt_delay_ms=400,
            ),
            "starting VARA",
        )
        warning = operations.events.history()[-1]
        assert warning.level is LogLevel.WARNING
        assert "400" in warning.message
    finally:
        operations.close()
        workers.close(wait=True)
