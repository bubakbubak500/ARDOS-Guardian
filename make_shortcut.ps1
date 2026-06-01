# Create a Desktop (and Start Menu) shortcut to the built Guardian.exe.
# Run after .\build.ps1 has produced dist\Guardian\Guardian.exe.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe = Join-Path $root "dist\Guardian\Guardian.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Guardian.exe not found. Run .\build.ps1 first." -ForegroundColor Red
    exit 1
}
$icon = Join-Path $root "guardian\assets\guardian.ico"
$workdir = Split-Path -Parent $exe
$shell = New-Object -ComObject WScript.Shell

foreach ($dir in @([Environment]::GetFolderPath("Desktop"),
                   (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    $lnk = Join-Path $dir "Guardian.lnk"
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = $workdir
    $s.Description = "Guardian - ARDOS control and routing layer for VARA"
    if (Test-Path $icon) { $s.IconLocation = $icon }
    $s.Save()
    Write-Host "Shortcut created: $lnk" -ForegroundColor Green
}
