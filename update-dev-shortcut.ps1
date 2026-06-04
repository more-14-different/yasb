$ErrorActionPreference = "Stop"

$repoRoot = $PSScriptRoot
$distExe = Join-Path $repoRoot "src\dist\yasb.exe"
$iconPath = Join-Path $repoRoot "src\assets\images\app_icon.ico"
$programsDir = [Environment]::GetFolderPath("Programs")
$shortcutDir = Join-Path $programsDir "Scoop Apps"
$shortcutPath = Join-Path $shortcutDir "YASB Dev.lnk"

if (-not (Test-Path $distExe)) {
    throw "Missing dev executable: $distExe"
}

if (-not (Test-Path $iconPath)) {
    throw "Missing icon file: $iconPath"
}

New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $distExe
$shortcut.WorkingDirectory = (Split-Path -Parent $distExe)
$shortcut.IconLocation = $iconPath
$shortcut.Description = "YASB Dev"
$shortcut.Save()
