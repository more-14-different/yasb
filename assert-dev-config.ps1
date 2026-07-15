[CmdletBinding()]
param(
    [string]$ConfigHome = "D:\C2D\dotfiles\yasb-dev"
)

$ErrorActionPreference = "Stop"
$expectedRole = "yasb-fork"
$markerPath = Join-Path $ConfigHome ".yasb-config-role"

if (-not (Test-Path -LiteralPath $ConfigHome -PathType Container)) {
    throw "Missing YASB fork config directory: $ConfigHome"
}

if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
    throw "Missing YASB config role marker: $markerPath"
}

$actualRole = (Get-Content -LiteralPath $markerPath -Raw).Trim()
if ($actualRole -ne $expectedRole) {
    throw "Refusing to start yasb-fork with config role '$actualRole' from $ConfigHome; expected '$expectedRole'."
}

foreach ($requiredFile in @("config.yaml", "styles.css")) {
    $requiredPath = Join-Path $ConfigHome $requiredFile
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Missing required YASB fork config file: $requiredPath"
    }
}

(Resolve-Path -LiteralPath $ConfigHome).Path
