$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$distExe = Join-Path $repoRoot "src\dist\yasb.exe"
$iconPath = Join-Path $repoRoot "src\assets\images\app_icon.ico"
$launcherScript = Join-Path $repoRoot "start-dev-dist.ps1"
$programsDir = [Environment]::GetFolderPath("Programs")
$shortcutDir = Join-Path $programsDir "Scoop Apps"
$shortcutPath = Join-Path $shortcutDir "YASB Dev.lnk"

if (-not (Test-Path $distExe)) {
    throw "Missing dev executable: $distExe"
}

if (-not (Test-Path $iconPath)) {
    throw "Missing icon file: $iconPath"
}

if (-not (Test-Path $launcherScript)) {
    throw "Missing launcher script: $launcherScript"
}

New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null

$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) {
    $pwsh = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pwsh
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcherScript`""
$shortcut.WorkingDirectory = $repoRoot
$shortcut.IconLocation = $iconPath
$shortcut.Description = "YASB Dev"
$shortcut.Save()
