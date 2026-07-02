[CmdletBinding()]
param(
    [ValidateSet("auto-recommended", "local-only", "local-max", "ollama-only", "ollama-max")]
    [string]$InstallProfile = "auto-recommended",
    [string]$NullaHome = "",
    [switch]$NoStart,
    [switch]$AutoYes,
    [switch]$SkipBenchmark
)

$ErrorActionPreference = "Stop"

$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$OneClick = Join-Path $ScriptRoot "installer\windows_one_click.ps1"

if (-not (Test-Path -LiteralPath $OneClick)) {
    throw "Missing Windows one-click installer: $OneClick"
}

$forward = @{
    InstallProfile = $InstallProfile
}
if (-not [string]::IsNullOrWhiteSpace($NullaHome)) {
    $forward["NullaHome"] = $NullaHome
}
if ($NoStart) {
    $forward["NoStart"] = $true
}
if ($AutoYes) {
    $forward["AutoYes"] = $true
}
if ($SkipBenchmark) {
    $forward["SkipBenchmark"] = $true
}

& $OneClick @forward
if ($AutoYes -and $null -ne $LASTEXITCODE) {
    exit $LASTEXITCODE
}
exit 0
