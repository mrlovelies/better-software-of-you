#Requires -Version 5.1
<#
.SYNOPSIS
    GPU Watcher Recovery — runs at boot to reset state after crashes/reboots.

.DESCRIPTION
    If the watcher or Windows crashed mid-game, Legion stays flagged as offline
    on Razer. This script:
      1. Ensures Ollama is running
      2. Flags Legion as online in Razer's research_machines table
      3. Starts the main gpu-watcher.ps1

    Install as a scheduled task that runs at login, BEFORE gpu-watcher.ps1.
    Or just have gpu-watcher.ps1 call this on startup (it already does the
    Set-RazerFlag on init, so this is a safety net for edge cases).
#>

$OllamaApiBase = "http://localhost:11434"
$RazerSSH = "mrlovelies@100.91.234.67"
$RazerSSHPort = 22
$RazerDBPath = "~/.local/share/software-of-you/soy.db"
$LogFile = "$env:LOCALAPPDATA\gpu-watcher\gpu-watcher.log"

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message"
    Write-Host $line

    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
}

function Test-OllamaRunning {
    try {
        Invoke-RestMethod -Uri "$OllamaApiBase/api/tags" -TimeoutSec 3 -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

Write-Log "=== GPU Recovery: Boot check ==="

# Step 1: Ensure Ollama is running
if (-not (Test-OllamaRunning)) {
    Write-Log "  Ollama not running — starting..."
    try {
        & wsl bash -c "nohup ollama serve > /tmp/ollama.log 2>&1 &" 2>&1 | Out-Null
        Start-Sleep -Seconds 10

        if (Test-OllamaRunning) {
            Write-Log "  Ollama started successfully"
        } else {
            Write-Log "  WARNING: Ollama failed to start"
        }
    } catch {
        Write-Log "  ERROR: Could not start Ollama: $_"
    }
} else {
    Write-Log "  Ollama already running"
}

# Step 2: Flag Legion as online on Razer
$sql = "UPDATE research_machines SET active=1, updated_at=datetime('now') WHERE name='legion';"
Write-Log "  Resetting Razer flag: Legion -> online"

try {
    & wsl ssh -o ConnectTimeout=5 -o BatchMode=yes -p $RazerSSHPort $RazerSSH "sqlite3 $RazerDBPath `"$sql`"" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Log "  Razer notified: Legion online"
    } else {
        Write-Log "  WARNING: Could not reach Razer (may be offline) — will retry when watcher starts"
    }
} catch {
    Write-Log "  WARNING: SSH to Razer failed: $_"
}

# Also update local DB
try {
    & wsl sqlite3 ~/.local/share/software-of-you/soy.db "$sql" 2>&1 | Out-Null
} catch { }

Write-Log "=== GPU Recovery: Complete ==="
