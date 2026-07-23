from pathlib import Path


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
    assert "DefaultDirName={localappdata}\\Programs\\{#MyAppName}" in script
    assert "UsePreviousAppDir=yes" in script
    assert "AppId={{CF48D1B9-ABC0-4DC5-A97E-00334B9DF040}" in script
    assert "AppUserModelID: \"OK7PS.ARDOSGuardian\"" in script
    assert "%APPDATA%\\Guardian" in script
    assert "VARAFM.exe" in script
    assert "VARA.exe" in script
    assert "rigctld.exe" in script


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


def test_build_scripts_support_ci_python_without_local_venv() -> None:
    build = (ROOT / "build.ps1").read_text(encoding="utf-8")
    installer = (ROOT / "build_installer.ps1").read_text(encoding="utf-8")

    for script in (build, installer):
        assert "GUARDIAN_BUILD_PYTHON" in script
        assert "Get-Command python" in script
