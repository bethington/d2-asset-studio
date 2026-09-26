# _paths.ps1 -- shared locations for the launch scripts; dot-source it: . "$PSScriptRoot\_paths.ps1"
# Mirrors studio_config.py (keep the defaults in step). Every value is overridable by env var.
#   $root       D2MOO checkout with the build-1.13c conformance build   ($env:D2MOO_ROOT)
#   $game       Project Diablo 2 Game.exe                               ($env:PD2_GAME)
#   $workspace  Asset Studio workspace; exported to the game process so
#               D2Debugger finds autoload.txt there                     ($env:ASSET_STUDIO_WS)
$studioRoot = Split-Path -Parent $PSScriptRoot
$root       = if ($env:D2MOO_ROOT)      { $env:D2MOO_ROOT }      else { 'C:\Users\benam\source\cpp\D2MOO' }
$game       = if ($env:PD2_GAME)        { $env:PD2_GAME }        else { 'C:\Diablo2\ProjectD2\Game.exe' }
$workspace  = if ($env:ASSET_STUDIO_WS) { $env:ASSET_STUDIO_WS } else { Join-Path $studioRoot 'workspace' }
$launcher   = "$root\build-1.13c\external\D2.Detours\source\Release\D2.DetoursLauncher.exe"
$patchDir   = "$root\build-1.13c\patch"
$env:ASSET_STUDIO_WS = $workspace
