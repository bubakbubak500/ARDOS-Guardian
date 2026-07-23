# One-time setup on a fresh PC: create venv + install dependencies.
# Requires Python 3.11+ already installed (python.org or winget).
#   .\setup.ps1              # Python deps only
#   .\setup.ps1 -WithHamlib  # also download the Hamlib radio-control binaries
param([switch]$WithHamlib)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find a usable Python interpreter.
$pyExe = $null
foreach ($cand in @("py -3.12", "py -3", "python")) {
    $parts = $cand.Split(" ")
    $cmd = Get-Command $parts[0] -ErrorAction SilentlyContinue
    if ($cmd) { $pyExe = $cand; break }
}
if (-not $pyExe) {
    Write-Host "No Python found. Install Python 3.11 or newer from python.org and re-run." -ForegroundColor Red
    exit 1
}

Write-Host "Creating virtual environment..." -ForegroundColor Cyan
Invoke-Expression "$pyExe -m venv `"$root\.venv`""

$venvPy = Join-Path $root ".venv\Scripts\python.exe"
Write-Host "Installing dependencies..." -ForegroundColor Cyan
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -r (Join-Path $root "requirements.txt")

if ($WithHamlib) {
    Write-Host "Downloading Hamlib (radio control)..." -ForegroundColor Cyan
    Push-Location $root
    try {
        & $venvPy -m guardian.install.hamlib_installer
    } finally {
        Pop-Location
    }
}

Write-Host "Done. Launch with:  .\run.ps1" -ForegroundColor Green
if (-not $WithHamlib) {
    Write-Host "Tip: re-run with -WithHamlib to bundle radio control, or use the in-app button." -ForegroundColor DarkGray
}
