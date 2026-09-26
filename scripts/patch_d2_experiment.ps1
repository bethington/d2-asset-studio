# Phase 0 experiment (elevated): stop the elevated game, inject the green-potion
# overlay into patch_d2.mpq (priority 5000), relaunch PD2 with -direct + debugger.
# Reversible: patch_d2.mpq.assetstudio-bak holds the original. Self-elevates (1 UAC).
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\_paths.ps1"   # $root $game $launcher $patchDir $workspace
$tool     = Split-Path -Parent $PSScriptRoot   # d2-asset-studio repo root

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile','-ExecutionPolicy','Bypass','-File', "`"$PSCommandPath`"")
    return
}

Write-Host '[1/4] stopping game + launcher (elevated)...'
Get-Process Game,'Diablo II',D2.DetoursLauncher -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-Process Game -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
}
Start-Sleep -Seconds 1

Write-Host '[2/4] injecting green potions into patch_d2.mpq...'
Push-Location $tool
& python 'scripts\inject_patch_d2_test.py'
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0) { Write-Host "INJECT FAILED (rc=$rc) -- aborting relaunch"; return }

Write-Host '[3/4] launching PD2 (-direct, debugger :8790)...'
$env:DIABLO2_PATCH = $patchDir
$env:D2_DEBUGGER   = '1'
Write-Host '[4/4] handing off to launcher (blocks on the game)...'
& $launcher $game '--' '-w' '-direct'
