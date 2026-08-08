# Create a Desktop (and Start Menu) shortcut to the built Guardian-G2.exe.
# Run after .\build.ps1 has produced dist\Guardian-G2\Guardian-G2.exe.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root "dist\Guardian-G2\Guardian-G2.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Guardian-G2.exe not found. Run .\build.ps1 first." -ForegroundColor Red
    exit 1
}
$icon = Join-Path $root "guardian\assets\guardian.ico"
$workdir = Split-Path -Parent $exe
$shell = New-Object -ComObject WScript.Shell

foreach ($dir in @([Environment]::GetFolderPath("Desktop"),
                   (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    # A distinct .lnk name so a G1 shortcut on the same Desktop survives.
    $lnk = Join-Path $dir "Guardian G2.lnk"
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = $workdir
    $s.Description = "Guardian G2 - ARDOS control and routing layer for VARA"
    if (Test-Path $icon) { $s.IconLocation = $icon }
    $s.Save()
    Write-Host "Shortcut created: $lnk" -ForegroundColor Green
}
