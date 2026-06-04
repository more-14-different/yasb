$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$buildScript = Join-Path $repoRoot "src\build.py"
$shortcutScript = Join-Path $repoRoot "update-dev-shortcut.ps1"

if (-not (Test-Path $venvPython)) {
    throw "Missing venv python: $venvPython"
}

if (Test-Path (Join-Path $repoRoot "src\dist")) {
    Get-Process yasb -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like (Join-Path $repoRoot "src\dist\*") } |
        Stop-Process -Force
}

Push-Location (Join-Path $repoRoot "src")
try {
    & $venvPython $buildScript build
    & $shortcutScript
}
finally {
    Pop-Location
}
