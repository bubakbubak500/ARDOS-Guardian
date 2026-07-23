from pathlib import Path

from guardian.qt.performance import UiResponsivenessProbe


def test_probe_records_only_stalls_above_threshold(tmp_path: Path) -> None:
    times = iter([0.0, 0.05, 0.25])
    output = tmp_path / "profile.jsonl"
    probe = UiResponsivenessProbe(
        interval_ms=50,
        stall_ms=100,
        clock=lambda: next(times),
        output_path=output,
    )

    probe.start()
    probe._heartbeat()
    assert not output.exists()

    probe._heartbeat()
    assert '"stall_ms":150.0' in output.read_text(encoding="utf-8")

    probe.stop()
    assert not probe.timer.isActive()
