from pathlib import Path

from guardian.config import StationConfig
from guardian.install.dependencies import (
    DependencyKind,
    find_vara_fm,
    find_vara_hf,
    inspect_dependencies,
)


def test_explicit_vara_executables_are_detected(tmp_path: Path) -> None:
    vara_fm = tmp_path / "VARAFM.exe"
    vara_hf = tmp_path / "VARA.exe"
    vara_fm.touch()
    vara_hf.touch()

    assert find_vara_fm(str(vara_fm)) == str(vara_fm.resolve())
    assert find_vara_hf(str(vara_hf)) == str(vara_hf.resolve())


def test_dependency_report_contains_all_required_components(
    tmp_path: Path,
) -> None:
    vara_fm = tmp_path / "VARAFM.exe"
    vara_fm.touch()
    config = StationConfig(
        rigctld_path=str(tmp_path / "missing-rigctld.exe"),
        vara_fm_path=str(vara_fm),
        vara_hf_path=str(tmp_path / "missing-VARA.exe"),
    )

    statuses = inspect_dependencies(config)
    by_kind = {status.kind: status for status in statuses}

    assert set(by_kind) == set(DependencyKind)
    assert by_kind[DependencyKind.VARA_FM].available
    assert not by_kind[DependencyKind.VARA_HF].available
    assert by_kind[DependencyKind.VARA_HF].official_url.startswith("https://")
    assert by_kind[DependencyKind.HAMLIB].can_install
