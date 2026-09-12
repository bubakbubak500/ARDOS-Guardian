# Validate the same frozen application that is shipped to users.
param([string]$Executable = "", [string]$ReportDirectory = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Executable) { $Executable = Join-Path $root "dist\Guardian\Guardian.exe" }
if (-not $ReportDirectory) { $ReportDirectory = Join-Path $root ".build-temp\frozen-tests" }
$Executable = (Resolve-Path -LiteralPath $Executable).Path
New-Item -ItemType Directory -Force -Path $ReportDirectory | Out-Null
$ReportDirectory = (Resolve-Path -LiteralPath $ReportDirectory).Path
foreach ($test in @("qt", "compression")) {
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
