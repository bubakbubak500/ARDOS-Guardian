param(
    [string]$Compiler = "",
    [string]$SignTool = "",
    [string]$CertificateThumbprint = "",
    [string]$ReleaseBaseUrl = ""
)

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
$script = Join-Path $root "installer\Guardian.iss"
$application = Join-Path $root "dist\Guardian\Guardian.exe"

if (-not $python -or -not (Test-Path -LiteralPath $python)) {
    throw "Build Python not found. Run setup.ps1 or set GUARDIAN_BUILD_PYTHON."
}
$version = & $python -c "from guardian import __version__; print(__version__)"
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Guardian version must use major.minor.patch format."
}
if (-not (Test-Path -LiteralPath $application)) {
    throw "Build Guardian first with .\build.ps1."
}

if (-not $Compiler) {
    $candidates = @(
        (Join-Path $root "tools\Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe")
    )
    $Compiler = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler)) {
    throw "ISCC.exe was not found. Install Inno Setup from https://jrsoftware.org/isdl.php."
}

if ($SignTool) {
    if (-not (Test-Path -LiteralPath $SignTool)) {
        throw "SignTool was not found."
    }
    if ($CertificateThumbprint -notmatch '^[0-9A-Fa-f]{40}$') {
        throw "Provide a 40-character SHA-1 code-signing certificate thumbprint."
    }
    $signCommand = '$q' + $SignTool + '$q sign /fd SHA256 /sha1 ' +
        $CertificateThumbprint + ' /td SHA256 /tr http://timestamp.digicert.com $f'
    & $Compiler "/DMyAppVersion=$version" "/Sguardiansign=$signCommand" "/DEnableSigning=1" $script
} else {
    & $Compiler "/DMyAppVersion=$version" $script
}
if ($LASTEXITCODE -ne 0) {
    throw "Guardian installer build failed."
}

$installer = Join-Path $root "release\Guardian-$version-setup-win-x64.exe"
if (-not (Test-Path -LiteralPath $installer)) {
    throw "Inno Setup completed without producing the expected installer."
}
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()

if ($ReleaseBaseUrl) {
    if ($ReleaseBaseUrl -notmatch '^https://') {
        throw "ReleaseBaseUrl must use HTTPS."
    }
    $base = $ReleaseBaseUrl.TrimEnd("/")
    $manifest = [ordered]@{
        version = $version
        installer_url = "$base/Guardian-$version-setup-win-x64.exe"
        sha256 = $hash
        notes_url = "https://github.com/bubakbubak500/ARDOS-Guardian/releases/tag/v$version"
    }
    $manifestJson = $manifest | ConvertTo-Json
    $manifestPath = Join-Path $root "release\release-manifest.json"
    [IO.File]::WriteAllText(
        $manifestPath,
        $manifestJson + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
}

Get-FileHash -LiteralPath $installer -Algorithm SHA256
