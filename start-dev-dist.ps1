$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$distExe = Join-Path $repoRoot "src\dist\yasb.exe"
$env:YASB_CONFIG_HOME = "D:\C2D\dotfiles\yasb-dev"

if (-not (Test-Path $distExe)) {
    throw "Missing dev executable: $distExe"
}

Start-Process -FilePath $distExe -WorkingDirectory (Split-Path -Parent $distExe) -WindowStyle Hidden
