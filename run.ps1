# Dev launcher — runs Guardian from source using the project venv.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "venv not found. Run setup.ps1 first." -ForegroundColor Red
    exit 1
}
& $py -m guardian
