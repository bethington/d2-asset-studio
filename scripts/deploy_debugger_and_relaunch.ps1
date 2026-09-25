# Deploy the freshly-built D2Debugger.dll into build-1.13c\patch and relaunch PD2
# (-direct + debugger on :8790). Self-elevates (1 UAC). Also restores patch_d2.mpq
# from the Asset Studio backup if one exists (belt-and-suspenders: leave stock MPQs clean).
$ErrorActionPreference = 'Stop'
$root     = 'C:\Users\benam\source\cpp\D2MOO'
$game     = 'C:\Diablo2\ProjectD2\Game.exe'
$launcher = "$root\build-1.13c\external\D2.Detours\source\Release\D2.DetoursLauncher.exe"
$patchDir = "$root\build-1.13c\patch"
$built    = "$root\build-1.13c\source\D2Debugger\Release\D2Debugger.dll"
$builtPdb = "$root\build-1.13c\source\D2Debugger\Release\D2Debugger.pdb"
$pd2patch = 'C:\Diablo2\ProjectD2\patch_d2.mpq'
$pd2bak   = 'C:\Diablo2\ProjectD2\patch_d2.mpq.assetstudio-bak'

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File', "`"$PSCommandPath`"")
    return
}

Write-Host '[1/4] stopping game + launcher...'
Get-Process Game,'Diablo II',D2.DetoursLauncher -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-Process Game -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 1

Write-Host '[2/4] deploying D2Debugger.dll -> patch...'
Copy-Item $built "$patchDir\D2Debugger.dll" -Force
if (Test-Path $builtPdb) { Copy-Item $builtPdb "$patchDir\D2Debugger.pdb" -Force }
Write-Host "      deployed ($((Get-Item "$patchDir\D2Debugger.dll").Length) bytes)"

if (Test-Path $pd2bak) {
    Copy-Item $pd2bak $pd2patch -Force
    Write-Host '      (restored stock patch_d2.mpq from backup)'
}

Write-Host '[3/4] launching PD2 (-direct, debugger :8790)...'
$env:DIABLO2_PATCH = $patchDir
$env:D2_DEBUGGER   = '1'
Write-Host '[4/4] handing off to launcher (blocks on the game)...'
& $launcher $game '--' '-w' '-direct'
