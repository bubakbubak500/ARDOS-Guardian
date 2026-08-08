from pathlib import Path

import pytest

from guardian.assets import icon


ROOT = Path(__file__).resolve().parents[1]


def test_pyinstaller_spec_is_portable_and_versioned() -> None:
    spec = (ROOT / "Guardian.spec").read_text(encoding="utf-8")

    assert "SPECPATH" in spec
    assert "guardian_launch.py" in spec
    assert "build\" / \"version_info.txt" in spec
    assert "C:\\Users\\" not in spec


def test_installer_is_per_user_upgrade_safe_and_preserves_data() -> None:
    script = (ROOT / "installer" / "Guardian.iss").read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in script
    assert "DefaultDirName={localappdata}\\Programs\\{#MyAppDirName}" in script
    assert "#define MyAppDirName \"Guardian-G2\"" in script
    assert "UsePreviousAppDir=yes" in script
    assert "%APPDATA%\\Guardian-G2" in script
    assert "VARAFM.exe" in script
    assert "VARA.exe" in script
    assert "rigctld.exe" in script


G2_APP_ID = "D9090316-F68C-4DAE-AF02-B433658A8F15"
G1_APP_ID = "CF48D1B9-ABC0-4DC5-A97E-00334B9DF040"


def test_installer_is_a_separate_product_from_g1() -> None:
    """G1 and G2 must be installable side by side, not upgrades of each other."""
    script = (ROOT / "installer" / "Guardian.iss").read_text(encoding="utf-8")

    assert f"AppId={{{{{G2_APP_ID}}}" in script
    assert f"AppId={{{{{G1_APP_ID}}}" not in script
    # The upgrade-detection key must look at G2's own uninstall entry.
    assert f"Uninstall\\{{{G2_APP_ID}}}_is1" in script
    assert f"Uninstall\\{{{G1_APP_ID}}}_is1" not in script
    assert "#define MyAppName \"Guardian G2\"" in script
    assert "#define MyAppUserModelID \"OK7PS.ARDOSGuardian.G2\"" in script
    assert "AppUserModelID: \"{#MyAppUserModelID}\"" in script
    assert "DefaultGroupName={#MyAppName}" in script
    assert "UninstallDisplayName={#MyAppName}" in script
    assert "OutputBaseFilename=Guardian-G2-{#MyAppVersion}-setup-win-x64" in script


def test_the_g2_executable_name_is_consistent_across_the_build() -> None:
    spec = (ROOT / "Guardian.spec").read_text(encoding="utf-8")
    script = (ROOT / "installer" / "Guardian.iss").read_text(encoding="utf-8")
    build = (ROOT / "build.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "build_installer.ps1").read_text(encoding="utf-8")
    shortcut = (ROOT / "make_shortcut.ps1").read_text(encoding="utf-8")
    version_info = (ROOT / "tools" / "write_version_info.py").read_text(encoding="utf-8")

    assert spec.count('name="Guardian-G2"') == 2      # EXE and COLLECT
    assert "#define MyAppExeName \"Guardian-G2.exe\"" in script
    assert "Source: \"..\\dist\\{#MyAppDirName}\\*\"" in script
    for text in (build, installer, shortcut):
        assert "dist\\Guardian-G2\\Guardian-G2.exe" in text
    assert "'Guardian-G2.exe'" in version_info


def test_the_icon_is_regenerated_at_build_time(tmp_path: Path) -> None:
    """A stale .ico in the tree would otherwise be shipped unchanged."""
    build = (ROOT / "build.ps1").read_text(encoding="utf-8")

    assert "ensure_ico(" in build
    assert "overwrite=True" in build

    target = tmp_path / "guardian.ico"
    target.write_bytes(b"stale")
    assert icon.ensure_ico(target) == target
    assert target.read_bytes() == b"stale"          # idempotent by default
    icon.ensure_ico(target, overwrite=True)
    assert target.read_bytes()[:4] == b"\x00\x00\x01\x00"   # a real ICO header


def test_the_icon_shows_a_red_g2_mark_at_tray_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mark has to survive the 16px frame the Windows tray uses."""
    pixels = list(icon.build_image(16).convert("RGB").getdata())
    red = [p for p in pixels if p[0] > 150 and p[0] > p[2] + 60]

    assert len(red) >= 25, "the G2 mark is too small or too dark to read at 16px"

    # The cached filename carries the revision, so new artwork is not masked by
    # an icon an operator generated with an earlier build.
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cached = icon.get_ico_path()

    assert cached.name == f"guardian-g2-r{icon.ICON_REVISION}.ico"
    assert cached.parent == tmp_path / "Guardian-G2"
    assert cached.is_file()


def test_release_workflow_builds_manifest_checksums_and_attestation() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert 'tags:' in workflow
    assert '"v*.*.*"' in workflow
    assert "build_installer.ps1" in workflow
    assert "release-manifest.json" in workflow
    assert "SHA256SUMS.txt" in workflow
    assert "actions/attest@v4" in workflow
    # Actions is disabled on G2, but the artefact names must not drift from the
    # ones build_installer.ps1 actually produces.
    assert "release\\Guardian-G2-$version-setup-win-x64.exe" in workflow
    assert "dist\\Guardian-G2\\*" in workflow


def test_build_scripts_support_ci_python_without_local_venv() -> None:
    build = (ROOT / "build.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "build_installer.ps1").read_text(encoding="utf-8")

    for script in (build, installer):
        assert "GUARDIAN_BUILD_PYTHON" in script
        assert "Get-Command python" in script
