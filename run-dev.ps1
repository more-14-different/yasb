$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$mainScript = Join-Path $repoRoot "src\main.py"

if (-not (Test-Path $venvPython)) {
    throw "Missing venv python: $venvPython"
}

& $venvPython $mainScript
