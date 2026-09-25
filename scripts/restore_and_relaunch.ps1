# Restore patch_d2.mpq from the Asset Studio backup and relaunch the standard
# conformance game (no -direct). Self-elevates (1 UAC).
$ErrorActionPreference = 'Stop'
$root     = 'C:\Users\benam\source\cpp\D2MOO'
$game     = 'C:\Diablo2\ProjectD2\Game.exe'
$launcher = "$root\build-1.13c\external\D2.Detours\source\Release\D2.DetoursLauncher.exe"
$patchDir = "$root\build-1.13c\patch"
$patch    = 'C:\Diablo2\ProjectD2\patch_d2.mpq'
$bak      = 'C:\Diablo2\ProjectD2\patch_d2.mpq.assetstudio-bak'

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File', "`"$PSCommandPath`"")
    return
}

Write-Host '[1/3] stopping game + launcher...'
Get-Process Game,'Diablo II',D2.DetoursLauncher -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-Process Game -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 1

Write-Host '[2/3] restoring original patch_d2.mpq from backup...'
if (Test-Path $bak) {
    Copy-Item $bak $patch -Force
    Write-Host "      restored ($(Get-Item $patch | Select-Object -ExpandProperty Length) bytes)"
} else {
    Write-Host "      WARNING: backup not found at $bak"
}

Write-Host '[3/3] relaunching standard conformance game (no -direct)...'
$env:DIABLO2_PATCH = $patchDir
$env:D2_DEBUGGER   = '1'
& $launcher $game '--' '-w'
