[CmdletBinding()]
param(
    [string]$OutputDir = "",
    [string]$PackageVersion = "",
    [string]$SigningCertificateThumbprint = $env:NULLA_WINDOWS_SIGNING_CERT_THUMBPRINT,
    [string]$TimestampServer = $env:NULLA_WINDOWS_TIMESTAMP_SERVER
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $ProjectRoot "dist\windows"
}
if ([string]::IsNullOrWhiteSpace($PackageVersion)) {
    $PackageVersion = "local-" + (Get-Date -Format "yyyyMMdd-HHmmss")
}
if ([string]::IsNullOrWhiteSpace($TimestampServer)) {
    $TimestampServer = "http://timestamp.digicert.com"
}

$PackageName = "NULLA-Windows-$PackageVersion"
$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) ($PackageName + "-" + [System.Guid]::NewGuid().ToString("N"))
$StageDir = Join-Path $StageRoot $PackageName
$ZipPath = Join-Path $OutputDir "$PackageName.zip"
$HashPath = Join-Path $OutputDir "$PackageName.sha256"
$ManifestPath = Join-Path $OutputDir "$PackageName.manifest.json"

function Get-GitOutput {
    param([string[]]$Arguments)
    try {
        $value = & git -C $ProjectRoot @Arguments 2>$null
        # A native command that produces no output yields AutomationNull, and
        # [string](AutomationNull) stays "nothing" (not ""), so a caller doing
        # .Trim() on the result hits InvokeMethodOnNull. This is exactly what
        # `git status --short` does on a clean tree - i.e. every CI checkout.
        # Wrapping in @(...) collapses AutomationNull to an empty array, so the
        # join always produces a real string the caller can .Trim() safely.
        return (@($value) -join "`n")
    }
    catch {
        return ""
    }
}

function Get-GitLines {
    param([string[]]$Arguments)
    try {
        return @(& git -C $ProjectRoot @Arguments 2>$null)
    }
    catch {
        return @()
    }
}

function Copy-TrackedFiles {
    New-Item -ItemType Directory -Path $StageDir -Force | Out-Null
    $files = @(Get-GitLines @("ls-files"))
    if ($files.Count -eq 0) {
        throw "Cannot build a clean Windows package because git ls-files returned no files."
    }
    $copied = 0
    foreach ($relative in $files) {
        $clean = [string]$relative.Trim()
        if ([string]::IsNullOrWhiteSpace($clean)) {
            continue
        }
        if ($clean -like "docs/archive/*") {
            continue
        }
        $src = Join-Path $ProjectRoot $clean
        if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
            continue
        }
        $dest = Join-Path $StageDir $clean
        $parent = Split-Path -Parent $dest
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $src -Destination $dest -Force
        $copied += 1
    }
    if (-not (Test-Path -LiteralPath (Join-Path $StageDir "Install_And_Run_NULLA.ps1"))) {
        throw "Staged Windows package is missing Install_And_Run_NULLA.ps1."
    }
    if ($copied -lt 50) {
        throw "Staged Windows package contains only $copied tracked file(s), refusing to create an incomplete package."
    }
}

function Sign-PackageScripts {
    if ([string]::IsNullOrWhiteSpace($SigningCertificateThumbprint)) {
        return @()
    }
    $cert = Get-ChildItem -Path Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -replace "\s", "" -eq ($SigningCertificateThumbprint -replace "\s", "") } |
        Select-Object -First 1
    if (-not $cert) {
        throw "Signing certificate not found for thumbprint $SigningCertificateThumbprint"
    }
    $signed = @()
    $scripts = Get-ChildItem -LiteralPath $StageDir -Recurse -File |
        Where-Object { $_.Extension -in @(".ps1", ".psm1", ".psd1") }
    foreach ($script in $scripts) {
        $signature = Set-AuthenticodeSignature -FilePath $script.FullName -Certificate $cert -TimestampServer $TimestampServer
        if ($signature.Status -ne "Valid") {
            throw "Failed to sign $($script.FullName): $($signature.StatusMessage)"
        }
        $signed += $script.FullName.Substring($StageDir.Length + 1).Replace("\", "/")
    }
    return $signed
}

function Write-PackageManifest {
    param([string[]]$SignedFiles)
    $zipHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $ZipPath).Hash.ToLowerInvariant()
    $commit = (Get-GitOutput @("rev-parse", "--short=12", "HEAD")).Trim()
    $dirty = -not [string]::IsNullOrWhiteSpace((Get-GitOutput @("status", "--short")).Trim())
    $manifest = [ordered]@{
        schema = "nulla.windows_package.v1"
        package = $PackageName
        version = $PackageVersion
        created_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
        source_commit = $commit
        source_dirty = $dirty
        entrypoint = "Install_And_Run_NULLA.ps1"
        zip_path = $ZipPath
        sha256 = $zipHash
        signed = ($SignedFiles.Count -gt 0)
        signed_files = $SignedFiles
        signing_certificate_thumbprint = $SigningCertificateThumbprint
    }
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
    "$zipHash  $(Split-Path -Leaf $ZipPath)" | Set-Content -LiteralPath $HashPath -Encoding ASCII
}

New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
if (Test-Path -LiteralPath $StageRoot) {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force
}
try {
    Copy-TrackedFiles
    $signedFiles = @(Sign-PackageScripts)
    if (Test-Path -LiteralPath $ZipPath) {
        Remove-Item -LiteralPath $ZipPath -Force
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory($StageDir, $ZipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    Write-PackageManifest -SignedFiles $signedFiles
    Write-Output "Package: $ZipPath"
    Write-Output "SHA256:  $HashPath"
    Write-Output "Manifest: $ManifestPath"
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
