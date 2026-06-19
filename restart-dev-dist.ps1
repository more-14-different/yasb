$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$buildScript = Join-Path $repoRoot "build-dev.ps1"
$distExe = Join-Path $repoRoot "src\dist\yasb.exe"
$shortcutScript = Join-Path $repoRoot "update-dev-shortcut.ps1"
$env:YASB_CONFIG_HOME = "D:\C2D\dotfiles\yasb-dev"

& $buildScript
& $shortcutScript

Get-Process yasb -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -eq $distExe } |
    Stop-Process -Force

Start-Process -FilePath $distExe -WorkingDirectory (Split-Path -Parent $distExe) -WindowStyle Hidden
