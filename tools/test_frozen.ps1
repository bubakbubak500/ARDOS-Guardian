# Validate the same frozen application that is shipped to users.
param([string]$Executable = "", [string]$ReportDirectory = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Executable) { $Executable = Join-Path $root "dist\Guardian\Guardian.exe" }
if (-not $ReportDirectory) { $ReportDirectory = Join-Path $root ".build-temp\frozen-tests" }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
New-Item -ItemType Directory -Force -Path $ReportDirectory | Out-Null
$ReportDirectory = (Resolve-Path -LiteralPath $ReportDirectory).Path
foreach ($test in @("qt", "compression", "ardop")) {
    $report = Join-Path $ReportDirectory ("$test-" + [guid]::NewGuid().ToString("N") + ".txt")
    $process = Start-Process -FilePath $Executable -ArgumentList @(
        "--$test-self-test", "--$test-self-test-report", ('"' + $report + '"')
    ) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) {
        Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
        throw "Frozen $test self-test timed out."
    }
    if (-not (Test-Path -LiteralPath $report)) { throw "Missing frozen $test report." }
    $result = Get-Content -LiteralPath $report -Raw
    Write-Host $result
    if ($process.ExitCode -ne 0 -or $result -notmatch '^PASS') {
        throw "Frozen $test self-test failed (exit $($process.ExitCode))."
    }
}
$labReport = Join-Path $ReportDirectory ("lab-" + [guid]::NewGuid().ToString("N") + ".json")
$labProcess = Start-Process -FilePath $Executable -ArgumentList @(
    "--lab", "self-test", "--output", ('"' + $labReport + '"')
) -WindowStyle Hidden -PassThru
if (-not $labProcess.WaitForExit(180000)) {
    Stop-Process -Id $labProcess.Id -ErrorAction SilentlyContinue
    throw "Frozen LAB parity self-test timed out."
}
if (-not (Test-Path -LiteralPath $labReport)) { throw "Missing frozen LAB parity report." }
$labResult = Get-Content -LiteralPath $labReport -Raw | ConvertFrom-Json
if ($labProcess.ExitCode -ne 0 -or -not $labResult.passed -or $labResult.rf_started) {
    throw "Frozen LAB parity self-test failed."
}
Write-Host "PASS LAB: two isolated production processes, matching runtime identity, no RF."
