#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs the GPU Handoff Watcher as a Windows scheduled task.

.DESCRIPTION
    Creates two scheduled tasks:
      1. gpu-recovery — runs at login, resets state
      2. gpu-watcher — runs at login (delayed 15s), monitors for games

    Run this from an elevated PowerShell prompt.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$WatcherScript = Join-Path $ScriptDir "gpu-watcher.ps1"
$RecoveryScript = Join-Path $ScriptDir "gpu-recovery.ps1"

if (-not (Test-Path $WatcherScript)) {
    Write-Error "gpu-watcher.ps1 not found in $ScriptDir"
    exit 1
}

# Remove existing tasks if they exist
Unregister-ScheduledTask -TaskName "SoY-GPU-Recovery" -Confirm:$false -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "SoY-GPU-Watcher" -Confirm:$false -ErrorAction SilentlyContinue

# Task 1: Recovery (runs immediately at login)
$recoveryAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RecoveryScript`""

$recoveryTrigger = New-ScheduledTaskTrigger -AtLogOn
$recoverySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "SoY-GPU-Recovery" `
    -Action $recoveryAction `
    -Trigger $recoveryTrigger `
    -Settings $recoverySettings `
    -Description "Resets Legion GPU status after boot/crash" `
    -RunLevel Limited

Write-Host "Registered: SoY-GPU-Recovery (runs at login)" -ForegroundColor Green

# Task 2: Watcher (runs at login with 15s delay so recovery finishes first)
$watcherAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$WatcherScript`""

$watcherTrigger = New-ScheduledTaskTrigger -AtLogOn
$watcherTrigger.Delay = "PT15S"
$watcherSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "SoY-GPU-Watcher" `
    -Action $watcherAction `
    -Trigger $watcherTrigger `
    -Settings $watcherSettings `
    -Description "Monitors for games and hands off GPU from Ollama" `
    -RunLevel Limited

Write-Host "Registered: SoY-GPU-Watcher (runs at login + 15s delay)" -ForegroundColor Green

Write-Host ""
Write-Host "Installation complete. Tasks will start on next login." -ForegroundColor Cyan
Write-Host "To start now:  Start-ScheduledTask -TaskName 'SoY-GPU-Watcher'" -ForegroundColor Cyan
Write-Host "To view logs:  Get-Content `$env:LOCALAPPDATA\gpu-watcher\gpu-watcher.log -Tail 50" -ForegroundColor Cyan
