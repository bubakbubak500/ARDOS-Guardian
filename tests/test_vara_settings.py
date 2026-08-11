from pathlib import Path

import pytest

from guardian.vara.settings import (
    configure_encryption,
    ini_path_for,
    read_encryption_status,
)


def test_vara_encryption_edit_preserves_unrelated_ini_settings(tmp_path: Path) -> None:
    executable = tmp_path / "VARAFM.exe"
    executable.write_bytes(b"")
    ini = ini_path_for(executable)
    ini.write_text(
        "[Soundcard]\nInput Device Name=Radio\n[Setup]\nEncryption=0\n"
        "Password encryption=OldKey1\nTCP Command Port=8300\n[Log]\nCommandsLog=1\n",
        encoding="utf-8",
    )

    configure_encryption(executable, enabled=True, password="SharedKey2026")

    text = ini.read_text(encoding="utf-8")
    assert "Input Device Name=Radio" in text
    assert "TCP Command Port=8300" in text
    assert "CommandsLog=1" in text
    assert "Encryption=1" in text
    assert "Password encryption=SharedKey2026" in text
    assert read_encryption_status(executable).enabled

    configure_encryption(executable, enabled=False)
    assert not read_encryption_status(executable).enabled
    assert "Password encryption=SharedKey2026" in ini.read_text(encoding="utf-8")


def test_vara_encryption_fails_closed_without_a_valid_key(tmp_path: Path) -> None:
    executable = tmp_path / "VARA.exe"
    executable.write_bytes(b"")

    with pytest.raises(ValueError, match="needs a configured password"):
        configure_encryption(executable, enabled=True)
    with pytest.raises(ValueError, match="1-32 ASCII"):
        configure_encryption(executable, enabled=True, password="not a key!")
