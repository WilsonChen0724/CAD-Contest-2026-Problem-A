param(
    [string]$InstallDir = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"

function Resolve-InstallDir {
    param([string]$InputDir)

    if ($InputDir) {
        return [System.IO.Path]::GetFullPath($InputDir)
    }

    $repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    return Join-Path $repoRoot "third_party\yosys"
}

function Select-WindowsAsset {
    param($Assets)

    $candidates = @($Assets | Where-Object {
        $_.name -match "windows-x64" -and
        ($_.name -match "\.exe$" -or $_.name -match "\.zip$")
    })
    if ($candidates.Count -gt 0) {
        return $candidates[0]
    }

    $assetNames = ($Assets | ForEach-Object { $_.name }) -join ", "
    throw "Could not find a Windows x64 .exe or .zip asset in the latest OSS CAD Suite release. Available assets: $assetNames"
}

$installRoot = Resolve-InstallDir $InstallDir
$toolRoot = Join-Path $installRoot "oss-cad-suite"
$yosysExe = Join-Path $toolRoot "bin\yosys.exe"

if ((Test-Path $yosysExe) -and -not $Force) {
    Write-Host "Yosys is already installed at $yosysExe"
    & $yosysExe -V
    Write-Host ""
    Write-Host "For this PowerShell session, run:"
    Write-Host "`$env:PATH = `"$($toolRoot)\bin;`$env:PATH`""
    exit 0
}

if ((Test-Path $toolRoot) -and $Force) {
    Remove-Item -LiteralPath $toolRoot -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null

$apiUrl = "https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest"
Write-Host "Fetching latest OSS CAD Suite release metadata..."
$release = Invoke-RestMethod -Uri $apiUrl -Headers @{ "User-Agent" = "cada-yosys-installer" }
$asset = Select-WindowsAsset $release.assets

$archivePath = Join-Path $installRoot $asset.name
Write-Host "Downloading $($asset.name)..."
Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archivePath -Headers @{ "User-Agent" = "cada-yosys-installer" }

Write-Host "Extracting to $installRoot..."
if ($asset.name -match "\.zip$") {
    Expand-Archive -LiteralPath $archivePath -DestinationPath $installRoot -Force
} elseif ($asset.name -match "\.exe$") {
    $process = Start-Process -FilePath $archivePath -WorkingDirectory $installRoot -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "OSS CAD Suite self-extracting installer failed with exit code $($process.ExitCode)."
    }
} else {
    throw "Unsupported Windows OSS CAD Suite asset type: $($asset.name)"
}

if (-not (Test-Path $yosysExe)) {
    throw "Install finished, but yosys.exe was not found at $yosysExe"
}

Remove-Item -LiteralPath $archivePath -Force

Write-Host ""
Write-Host "Yosys installed successfully:"
& $yosysExe -V
Write-Host ""
Write-Host "For this PowerShell session, run:"
Write-Host "`$env:PATH = `"$($toolRoot)\bin;`$env:PATH`""
