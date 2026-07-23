# Build a standalone, one-folder Guardian distribution with bundled Python.
param([switch]$SkipTests)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
$python = $env:GUARDIAN_BUILD_PYTHON
if (-not $python -and (Test-Path -LiteralPath $venvPython)) {
    $python = $venvPython
}
if (-not $python) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) { $python = $pythonCommand.Source }
}
$spec = Join-Path $root "Guardian.spec"
$versionInfo = Join-Path $root "build\version_info.txt"
$executable = Join-Path $root "dist\Guardian\Guardian.exe"
$buildTemp = Join-Path $root ".build-temp"

if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "Build Python not found. Run setup.ps1 or set GUARDIAN_BUILD_PYTHON."
}

Push-Location $root
$guardianPreviousPythonPath = $env:PYTHONPATH
try {
    # Scripts launched from tools\ otherwise see that directory as sys.path[0]
    # and cannot import the adjacent guardian package.
    $env:PYTHONPATH = if ($guardianPreviousPythonPath) {
        "$root;$guardianPreviousPythonPath"
    } else {
        $root
    }
    New-Item -ItemType Directory -Force -Path $buildTemp | Out-Null
    $env:TEMP = $buildTemp
    $env:TMP = $buildTemp

    if (-not $SkipTests) {
        Write-Host "Running characterization tests..." -ForegroundColor Cyan
        # A fixed pytest directory can be left owned by another Windows build
        # account. A unique path also lets concurrent local/CI builds coexist.
        $pytestTemp = Join-Path $buildTemp ("pytest-" + [guid]::NewGuid().ToString("N"))
        & $python -m pytest -p no:cacheprovider --basetemp $pytestTemp
        if ($LASTEXITCODE -ne 0) { throw "Tests failed; build was stopped." }
    }

    Write-Host "Generating application icon and Windows version metadata..." -ForegroundColor Cyan
    & $python -c "from guardian.assets.icon import ensure_ico; from pathlib import Path; ensure_ico(Path(r'guardian/assets/guardian.ico'))"
    if ($LASTEXITCODE -ne 0) { throw "Application icon generation failed." }
    & $python tools\write_version_info.py --output $versionInfo
    if ($LASTEXITCODE -ne 0) { throw "Version metadata generation failed." }

    Write-Host "Building Guardian application..." -ForegroundColor Cyan
    & $python -m PyInstaller --noconfirm --clean $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "PyInstaller completed without producing Guardian.exe."
    }

    $version = & $python -c "from guardian import __version__; print(__version__)"
    $hash = (Get-FileHash -LiteralPath $executable -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host "Build complete: dist\Guardian\Guardian.exe" -ForegroundColor Green
    Write-Host "Version: $version" -ForegroundColor DarkGray
    Write-Host "SHA-256: $hash" -ForegroundColor DarkGray
} finally {
    $env:PYTHONPATH = $guardianPreviousPythonPath
    Pop-Location
}
