[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory,
    [Parameter(Mandatory = $true)]
    [string]$LinkPath
)

$ErrorActionPreference = "Stop"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($LinkPath)
$shortcut.TargetPath = $TargetPath
$shortcut.WorkingDirectory = $WorkingDirectory
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,220"
$shortcut.Save()

Write-Output $LinkPath
