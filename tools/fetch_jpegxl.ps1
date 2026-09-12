# Fetch the pinned official static libjxl tools used by Guardian's optional
# aggressive message compression path.
param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $Destination) {
    $Destination = Join-Path $root "codecs\vendor\jpegxl"
}
$bin = Join-Path $Destination "bin"
$cjxl = Join-Path $bin "cjxl.exe"
$djxl = Join-Path $bin "djxl.exe"

$version = "0.12.0"
$archiveName = "jxl-x64-windows-static.zip"
$expectedSha256 = "3025d7e308390796d20492322e606bc92decaee7b6bc99d3f7547870ae5db7de"
$url = "https://github.com/libjxl/libjxl/releases/download/v$version/$archiveName"
$work = Join-Path $root ".build-temp\jpegxl-$version"
$archive = Join-Path $work $archiveName
$expanded = Join-Path $work ("expanded-" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Force -Path $work | Out-Null
if (-not (Test-Path -LiteralPath $archive)) {
    Write-Host "Downloading pinned libjxl $version tools..." -ForegroundColor Cyan
    Invoke-WebRequest -Uri $url -OutFile $archive
}
$actualSha256 = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "libjxl archive checksum mismatch: expected $expectedSha256, got $actualSha256"
}

Expand-Archive -LiteralPath $archive -DestinationPath $expanded
$package = Join-Path $expanded "x64-windows-static"
$sourceBin = Join-Path $package "bin"
$sourceLicenses = Join-Path $package "licenses"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceBin "cjxl.exe") -Destination $cjxl
Copy-Item -LiteralPath (Join-Path $sourceBin "djxl.exe") -Destination $djxl
Copy-Item -LiteralPath $sourceLicenses -Destination $Destination -Recurse -Force
Write-Host "Verified libjxl $version tools staged in codecs\vendor\jpegxl." -ForegroundColor Green
