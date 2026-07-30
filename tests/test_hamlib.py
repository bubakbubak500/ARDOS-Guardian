from guardian.radio.hamlib import HamlibRadio


class ChunkedSocket:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        return self.chunks.pop(0)


def test_hamlib_consumes_complete_multiline_mode_reply() -> None:
    radio = HamlibRadio()
    sock = ChunkedSocket([
        b"145237500\n",
        b"FM\n15000\n",
        b"0\n",
        b"-54\n",
    ])
    radio._sock = sock

    state = radio.get_state()

    assert state.frequency_hz == 145_237_500
    assert state.mode == "FM"
    assert state.ptt is False
    assert state.signal == -54
    assert state.error is None
    assert sock.sent == [b"f\n", b"m\n", b"t\n", b"l STRENGTH\n"]


def test_hamlib_line_reader_preserves_partial_and_following_lines() -> None:
    radio = HamlibRadio()
    sock = ChunkedSocket([b"F", b"M\n150", b"00\n"])
    radio._sock = sock

    with radio._lock:
        reply = radio._command("m", reply_lines=2)

    assert reply == ["FM", "15000"]


def test_only_a_driver_that_asks_the_radio_claims_to_report_ptt() -> None:
    # The PTT test trusts this flag to decide whether a keyed read-back means
    # the transmitter came up. VOX reads back the wire it just asserted, so it
    # must not claim otherwise.
    from guardian.radio.base import NullRadio
    from guardian.radio.generic_vox import VoxRadio

    assert HamlibRadio.reports_ptt is True
    assert VoxRadio.reports_ptt is False
    assert NullRadio.reports_ptt is False


def test_rigctld_command_gives_a_no_cat_radio_the_port_as_the_ptt_device() -> None:
    # The dummy rig is a simulator: it never opens the -r rig device. Handing
    # it the AIOC's COM port there looked configured while the port was never
    # touched, so PTT "worked" in every log and keyed nothing. Confirmed
    # against rigctld 4.7.2: `-m 1 -r COM99` starts happily with a port that
    # does not even exist and answers T/t from the simulator.
    from guardian.radio.presets import DUMMY_MODEL
    from guardian.radio.rigctld_launcher import RigctldProcess

    launcher = RigctldProcess.__new__(RigctldProcess)

    dummy = launcher.command(DUMMY_MODEL, "COM7", 4532, 0, ptt_type="RTS")
    assert "-r" not in dummy, "the dummy rig has no rig device to open"
    assert dummy[dummy.index("-P") + 1] == "RTS"
    assert dummy[dummy.index("-p") + 1] == "COM7"

    # A real CAT rig keeps the port as its rig device; PTT flags only appear
    # when the operator explicitly keys over a serial line.
    cat = launcher.command(3073, "COM7", 4532, 19200)
    assert cat[cat.index("-r") + 1] == "COM7"
    assert cat[cat.index("-s") + 1] == "19200"
    assert "-P" not in cat and "-p" not in cat

    dtr = launcher.command(3073, "COM7", 4532, 0, ptt_type="DTR")
    assert dtr[dtr.index("-P") + 1] == "DTR"
    assert dtr[dtr.index("-p") + 1] == "COM7"
    assert dtr[dtr.index("-r") + 1] == "COM7"


def test_our_own_rigctld_is_restarted_when_its_command_line_goes_stale(
    monkeypatch,
) -> None:
    # A changed PTT line or COM port exists only on the rigctld command line.
    # ensure() used to reuse any responsive instance, so the operator's change
    # silently kept the old wiring until the next reboot.
    from guardian.radio import rigctld_launcher as module
    from guardian.radio.rigctld_launcher import RigctldProcess

    monkeypatch.setattr(module, "port_in_use", lambda *a, **k: True)
    monkeypatch.setattr(module, "responds", lambda *a, **k: True)

    launcher = RigctldProcess.__new__(RigctldProcess)
    launcher.exe = "rigctld.exe"

    class Child:
        def poll(self):
            return None

    started: list[list[str]] = []
    stopped: list[bool] = []

    def fake_start(model, com_port, tcp_port=4532, baud=0, ptt_type="RIG"):
        started.append(launcher.command(model, com_port, tcp_port, baud, ptt_type))
        return "started"

    launcher.start = fake_start
    launcher.stop = lambda: stopped.append(True)

    # Someone else's rigctld: reused, whatever it was started with.
    launcher.proc = None
    launcher.args = []
    assert "reusing" in launcher.ensure(1, "COM7", ptt_type="RTS")
    assert stopped == [] and started == []

    # Ours, same arguments: reused.
    launcher.proc = Child()
    launcher.args = launcher.command(1, "COM7", 4532, 0, "RTS")
    assert "reusing" in launcher.ensure(1, "COM7", ptt_type="RTS")
    assert stopped == []

    # Ours, the operator switched RTS -> DTR: restarted with the new line.
    monkeypatch.setattr(module, "port_in_use", lambda *a, **k: bool(stopped == []))
    assert launcher.ensure(1, "COM7", ptt_type="DTR") == "started"
    assert stopped == [True]
    assert started[-1][started[-1].index("-P") + 1] == "DTR"


def test_rprt_errors_come_with_an_explanation() -> None:
    # "RPRT -6" sends the operator to a lookup table at exactly the moment
    # they are debugging PTT wiring.
    radio = HamlibRadio()
    sock = ChunkedSocket([b"RPRT -6\n"])
    radio._sock = sock

    try:
        radio.set_ptt(True)
    except IOError as exc:
        assert "RPRT -6" in str(exc)
        assert "serial port" in str(exc)
    else:
        raise AssertionError("a failed PTT command must raise")


def test_the_driver_only_claims_ptt_readback_for_a_real_cat_rig() -> None:
    # The dummy model echoes T back and a serial PTT line reads our own wire;
    # only a real rig asked over CAT may confirm the PTT test.
    from guardian.config import StationConfig
    from guardian.radio import make_driver

    def driver(**kw):
        return make_driver(StationConfig(radio_backend="hamlib", **kw))

    assert driver(rig_model=3073).reports_ptt is True
    assert driver(rig_model=1).reports_ptt is False, "dummy echoes"
    assert driver(rig_model=3073, ptt_type="RTS").reports_ptt is False
    assert driver(rig_model=1, ptt_type="DTR").reports_ptt is False
