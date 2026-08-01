from guardian.radio import Channel, ChannelPlan, ChannelScanner


def _plan() -> ChannelPlan:
    return ChannelPlan(
        [
            Channel("Home", 145_500_000, "FM"),
            Channel("Channel 2", 145_550_000, "FM"),
        ]
    )


def test_scanner_advances_after_dwell_and_wraps() -> None:
    scanner = ChannelScanner(_plan(), dwell=3.0)

    assert scanner.start(10.0).name == "Home"
    assert scanner.tick(12.9) is None
    assert scanner.tick(13.0).name == "Channel 2"
    assert scanner.tick(16.0).name == "Home"


def test_activity_and_s_meter_restart_the_whole_dwell() -> None:
    scanner = ChannelScanner(_plan(), dwell=3.0, signal_threshold=-90)
    scanner.start(10.0)

    assert scanner.tick(13.0, signal=-80) is None
    assert scanner.holding
    assert scanner.tick(15.9, signal=-100) is None
    assert not scanner.holding
    assert scanner.tick(16.0, signal=-100).name == "Channel 2"

    assert scanner.tick(19.0, activity=True) is None
    assert scanner.holding
    assert scanner.tick(22.0).name == "Home"


def test_empty_plan_does_not_start() -> None:
    scanner = ChannelScanner(ChannelPlan())

    assert scanner.start(0.0) is None
    assert not scanner.enabled
