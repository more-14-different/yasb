$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$mainScript = Join-Path $repoRoot "src\main.py"
$configGuard = Join-Path $repoRoot "assert-dev-config.ps1"
$env:YASB_CONFIG_HOME = & $configGuard

if (-not (Test-Path $venvPython)) {
    throw "Missing venv python: $venvPython"
}

& $venvPython $mainScript
