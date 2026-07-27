from guardian.modem.audio import match_device_index, match_device_name


DEVICES = [
    {
        "name": "Microphone Array (Internal Audio)",
        "hostapi": 0,
        "max_input_channels": 2,
        "max_output_channels": 0,
    },
    {
        "name": "Mikrofon (USB Audio CODEC)",
        "hostapi": 0,
        "max_input_channels": 1,
        "max_output_channels": 0,
    },
    {
        "name": "Speakers (USB Audio CODEC)",
        "hostapi": 0,
        "max_input_channels": 0,
        "max_output_channels": 2,
    },
]


def test_device_match_tolerates_saved_trailing_parenthesis_space() -> None:
    assert (
        match_device_index(
            DEVICES,
            "Mikrofon (USB Audio CODEC )",
            "input",
            default_api=0,
        )
        == 1
    )
    assert (
        match_device_name(
            ["Mikrofon (USB Audio CODEC)"],
            "Mikrofon (USB Audio CODEC )",
        )
        == "Mikrofon (USB Audio CODEC)"
    )


def test_device_match_respects_input_output_direction() -> None:
    assert (
        match_device_index(
            DEVICES,
            "Speakers (USB Audio CODEC)",
            "output",
            default_api=0,
        )
        == 2
    )


def test_device_match_refuses_ambiguous_hardware_identity() -> None:
    devices = DEVICES + [
        {
            "name": "Line (USB Audio CODEC)",
            "hostapi": 0,
            "max_input_channels": 1,
            "max_output_channels": 0,
        }
    ]
    assert (
        match_device_index(
            devices,
            "USB Audio CODEC",
            "input",
            default_api=0,
        )
        is None
    )
