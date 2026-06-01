# Build a standalone Guardian.exe with PyInstaller.
# Produces dist\Guardian\Guardian.exe (one-folder) — fast start, easy to zip.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv not found. Run setup.ps1 first." -ForegroundColor Red
    exit 1
}

# Generate the app icon (.ico) for the executable.
$icon = Join-Path $root "guardian\assets\guardian.ico"
& $py -c "from guardian.assets.icon import ensure_ico; from pathlib import Path; ensure_ico(Path(r'$icon'))"

Write-Host "Building Guardian.exe ..." -ForegroundColor Cyan
& $py -m PyInstaller `
    --name Guardian `
    --noconfirm `
    --clean `
    --windowed `
    --icon "$icon" `
    --collect-all customtkinter `
    --collect-all sounddevice `
    --collect-submodules pystray `
    --collect-submodules PIL `
    --collect-submodules numpy `
    "$root\guardian\__main__.py"

Write-Host "Build complete: dist\Guardian\Guardian.exe" -ForegroundColor Green
