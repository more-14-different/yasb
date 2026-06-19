$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$mainScript = Join-Path $repoRoot "src\main.py"
$env:YASB_CONFIG_HOME = "D:\C2D\dotfiles\yasb-dev"

if (-not (Test-Path $venvPython)) {
    throw "Missing venv python: $venvPython"
}

& $venvPython $mainScript
