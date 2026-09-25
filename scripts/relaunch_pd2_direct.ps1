# relaunch_pd2_direct.ps1 -- Asset Studio variant of conformance/tools/relaunch_pd2.ps1:
# identical conformance launch (detours patch + D2Debugger on :8790) PLUS the -direct
# flag so Fog reads loose files under BASEPATH\data\ before the MPQs (the live overlay).
$ErrorActionPreference = 'Stop'
$root     = 'C:\Users\benam\source\cpp\D2MOO'
$game     = 'C:\Diablo2\ProjectD2\Game.exe'
$launcher = "$root\build-1.13c\external\D2.Detours\source\Release\D2.DetoursLauncher.exe"
$patchDir = "$root\build-1.13c\patch"

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File', "`"$PSCommandPath`"")
    return
}

# ---- ELEVATED from here ----
Write-Host '[1/3] stopping the running game + launcher...'
Get-Process Game               -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-Process D2.DetoursLauncher -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 30; $i++) {
    if (-not (Get-Process Game -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Write-Host '[2/3] launching Project Diablo 2 (conformance build + -direct, debugger on :8790)...'
$env:DIABLO2_PATCH = $patchDir
$env:D2_DEBUGGER   = '1'
Write-Host '[3/3] handing off to the launcher (this window blocks on the game)...'
& $launcher $game '--' '-w' '-direct'
