import json

import numpy as np

from guardian.modem.audio import AudioControlTransport
from guardian.protocol import ControlFrame, FrameType


def test_rejected_candidate_is_classified_and_saved_without_delivery(tmp_path) -> None:
    audio_path = tmp_path / "last-bad-control.wav"
    logged: list[str] = []
    delivered: list[ControlFrame] = []
    transport = AudioControlTransport(
        sample_rate=8_000,
        diagnostic_audio_path=audio_path,
        on_log=logged.append,
    )
    transport.on_frame = delivered.append
    invalid = bytes(24)

    assert not transport._process_candidate(
        invalid,
        7.5,
        np.zeros(4_000, dtype=np.float32),
    )
    assert transport.pump() == 0
    assert delivered == []
    assert transport.rejected_control_candidates == 1
    assert "bad magic" in transport.last_rejected_control["reason"]
    assert transport.last_rejected_control["payload_hex"] == invalid.hex()
    assert audio_path.exists()
    metadata = json.loads(audio_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["snr_db"] == 7.5
    assert metadata["payload_length"] == len(invalid)
    assert any("RX rejected control candidate" in line for line in logged)


def test_valid_candidate_still_reaches_the_orchestrator_queue(tmp_path) -> None:
    transport = AudioControlTransport(
        sample_rate=8_000,
        diagnostic_audio_path=tmp_path / "last-bad-control.wav",
    )
    delivered: list[ControlFrame] = []
    transport.on_frame = delivered.append
    frame = ControlFrame(
        type=FrameType.BEACON,
        source="OK7PS",
        destination="ALL",
        next_hop="",
        message_id=1,
    )

    assert transport._process_candidate(
        frame.encode(),
        12.0,
        np.zeros(4_000, dtype=np.float32),
    )
    assert transport.pump() == 1
    assert delivered == [frame]
    assert transport.rejected_control_candidates == 0
