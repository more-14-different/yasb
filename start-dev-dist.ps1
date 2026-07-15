$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$distExe = Join-Path $repoRoot "src\dist\yasb.exe"
$configGuard = Join-Path $repoRoot "assert-dev-config.ps1"
$env:YASB_CONFIG_HOME = & $configGuard

if (-not (Test-Path $distExe)) {
    throw "Missing dev executable: $distExe"
}

Start-Process -FilePath $distExe -WorkingDirectory (Split-Path -Parent $distExe) -WindowStyle Hidden
