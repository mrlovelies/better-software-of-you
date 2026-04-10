#Requires -Version 5.1
<#
.SYNOPSIS
    GPU Handoff Watcher — monitors for GPU-heavy processes (games) and hands off
    the GPU from Ollama to the game, notifying the Razer routing layer.

.DESCRIPTION
    Runs as a Windows scheduled task at login. Polls every 5 seconds for game
    processes. On detection:
      1. Waits for any in-flight Ollama inference to complete
      2. Stops Ollama (unloads models, frees VRAM)
      3. Flags Legion as offline in Razer's research_machines table via SSH
    On game exit:
      1. Restarts Ollama
      2. Waits for it to be ready
      3. Flags Legion as online in Razer's research_machines table

.NOTES
    Install: gpu-install.ps1 (sets up the scheduled task)
    Sunshine is ignored — it holds minimal GPU allocation and coexists with Ollama.
#>

# --- Configuration ---

$PollIntervalSec = 5
$OllamaApiBase = "http://localhost:11434"
$InferenceWaitMaxSec = 60
$OllamaStartWaitSec = 15

# Razer SSH config (Tailscale)
$RazerSSH = "mrlovelies@100.91.234.67"
$RazerSSHPort = 22
$RazerDBPath = "~/.local/share/software-of-you/soy.db"

# Game processes to watch for. Add new ones as needed.
# These are exe names without extension.
$GameProcesses = @(
    # Launchers that indicate active gaming (not just idle in tray)
    # We watch for actual game engines / renderers instead

    # Steam games (common engines)
    "hl2"                    # Source engine
    "csgo"                   # CS:GO / CS2
    "cs2"
    "dota2"
    "RocketLeague"
    "Cyberpunk2077"
    "bg3"                    # Baldur's Gate 3
    "bg3_dx11"
    "starfield"
    "GTA5"
    "GTAV"
    "ForzaHorizon5"
    "eldenring"

    # Epic / other
    "FortniteClient-Win64-Shipping"
    "RDR2"
    "HogwartsLegacy"

    # General GPU-heavy indicators
    "UE4-Win64-Shipping"     # Unreal Engine 4 games (generic)
    "UE5-Win64-Shipping"     # Unreal Engine 5 games (generic)

    # Sunshine streaming (IGNORED — listed here as documentation only)
    # "sunshine"  # Do NOT add — coexists with Ollama
)

# Processes to always ignore even if they use GPU
$IgnoreProcesses = @(
    "sunshine"
    "SunshineService"
    "dwm"                    # Desktop Window Manager
    "explorer"
    "ShellExperienceHost"
)

$LogFile = "$env:LOCALAPPDATA\gpu-watcher\gpu-watcher.log"

# --- Functions ---

function Write-Log {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$timestamp] $Message"
    Write-Host $line

    # Ensure log directory exists
    $logDir = Split-Path $LogFile -Parent
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }

    Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue

    # Rotate log if > 1MB
    if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) {
        $content = Get-Content $LogFile -Tail 500
        Set-Content -Path $LogFile -Value $content
    }
}

function Test-OllamaRunning {
    try {
        $response = Invoke-RestMethod -Uri "$OllamaApiBase/api/tags" -TimeoutSec 3 -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

function Test-OllamaInference {
    <# Returns $true if a model is actively generating (mid-inference). #>
    try {
        $response = Invoke-RestMethod -Uri "$OllamaApiBase/api/ps" -TimeoutSec 3 -ErrorAction Stop
        if ($response.models -and $response.models.Count -gt 0) {
            # Check if any model has an active request (size_vram > 0 means loaded, but
            # we need to check if it's actually generating vs just loaded idle)
            # The /api/ps endpoint shows running models. If a model is mid-generation,
            # it appears here. We treat any loaded model as potentially active and wait.
            foreach ($model in $response.models) {
                if ($model.size_vram -gt 0) {
                    return $true
                }
            }
        }
        return $false
    } catch {
        return $false
    }
}

function Wait-OllamaIdle {
    <# Waits for Ollama to finish any in-flight inference before we stop it. #>
    $waited = 0
    while ($waited -lt $InferenceWaitMaxSec) {
        if (-not (Test-OllamaInference)) {
            return $true
        }
        Write-Log "  Ollama mid-inference, waiting... ($waited/$InferenceWaitMaxSec sec)"
        Start-Sleep -Seconds 2
        $waited += 2
    }
    Write-Log "  WARNING: Ollama still busy after ${InferenceWaitMaxSec}s, proceeding anyway"
    return $false
}

function Stop-OllamaForGaming {
    Write-Log "Stopping Ollama for GPU handoff..."

    # Wait for in-flight inference to complete
    Wait-OllamaIdle

    # Stop Ollama via WSL
    try {
        & wsl ollama stop 2>&1 | Out-Null
        Write-Log "  Ollama stopped"
    } catch {
        Write-Log "  WARNING: ollama stop failed: $_"
        # Try harder — kill the process
        try {
            & wsl pkill -f "ollama serve" 2>&1 | Out-Null
            Write-Log "  Ollama force-killed"
        } catch {
            Write-Log "  ERROR: Could not stop Ollama: $_"
        }
    }

    # Notify Razer: flag Legion as offline
    Set-RazerFlag -Active 0
}

function Start-OllamaAfterGaming {
    Write-Log "Restarting Ollama after gaming session..."

    # Start Ollama via WSL (background)
    # OLLAMA_KEEP_ALIVE=0 ensures models unload immediately after inference,
    # so the watcher doesn't see idle-but-loaded models as "active inference"
    try {
        & wsl bash -c "OLLAMA_KEEP_ALIVE=0 nohup ollama serve > /tmp/ollama.log 2>&1 &" 2>&1 | Out-Null
        Write-Log "  Ollama serve started (KEEP_ALIVE=0)"
    } catch {
        Write-Log "  ERROR: Could not start Ollama: $_"
        return
    }

    # Wait for Ollama to be ready
    $waited = 0
    while ($waited -lt $OllamaStartWaitSec) {
        Start-Sleep -Seconds 2
        $waited += 2
        if (Test-OllamaRunning) {
            Write-Log "  Ollama ready (took ${waited}s)"
            # Notify Razer: flag Legion as online
            Set-RazerFlag -Active 1
            return
        }
    }

    Write-Log "  WARNING: Ollama not ready after ${OllamaStartWaitSec}s — Razer NOT notified"
}

function Set-RazerFlag {
    param([int]$Active)

    $state = if ($Active) { "online" } else { "offline (gaming)" }
    Write-Log "  Notifying Razer: Legion -> $state"

    $sql = "UPDATE research_machines SET active=$Active, updated_at=datetime('now') WHERE name='legion';"

    try {
        $result = & wsl ssh -o ConnectTimeout=5 -o BatchMode=yes -p $RazerSSHPort $RazerSSH "sqlite3 $RazerDBPath `"$sql`"" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Log "  Razer notified successfully"
        } else {
            Write-Log "  WARNING: Razer SSH returned exit code $LASTEXITCODE : $result"
        }
    } catch {
        Write-Log "  ERROR: Could not reach Razer via SSH: $_"
    }

    # Also update local DB as backup
    try {
        & wsl sqlite3 ~/.local/share/software-of-you/soy.db "$sql" 2>&1 | Out-Null
    } catch {
        # Non-critical — Razer is the authority
    }
}

function Find-GameProcess {
    <# Returns the first detected game process, or $null if none found. #>
    $running = Get-Process -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessName -Unique

    foreach ($game in $GameProcesses) {
        if ($running -contains $game) {
            return $game
        }
    }
    return $null
}

function Wait-GameExit {
    param([string]$GameName)
    <# Blocks until no game processes are found (checks all, not just the trigger). #>
    while ($true) {
        Start-Sleep -Seconds $PollIntervalSec
        $game = Find-GameProcess
        if (-not $game) {
            return
        }
        # Different game might have launched — keep waiting
        if ($game -ne $GameName) {
            Write-Log "  Game changed: $GameName -> $game (still waiting)"
            $GameName = $game
        }
    }
}

# --- Main Loop ---

Write-Log "=== GPU Handoff Watcher started ==="
Write-Log "  Monitoring for: $($GameProcesses.Count) game process patterns"
Write-Log "  Ignoring: $($IgnoreProcesses -join ', ')"
Write-Log "  Ollama status: $(if (Test-OllamaRunning) { 'running' } else { 'not running' })"

# Ensure Legion is flagged as available on startup
if (Test-OllamaRunning) {
    Set-RazerFlag -Active 1
}

$gaming = $false

while ($true) {
    Start-Sleep -Seconds $PollIntervalSec

    if (-not $gaming) {
        # Check for game launch
        $game = Find-GameProcess
        if ($game) {
            Write-Log "GAME DETECTED: $game"
            $gaming = $true

            if (Test-OllamaRunning) {
                Stop-OllamaForGaming
            } else {
                Write-Log "  Ollama already stopped — just flagging Razer"
                Set-RazerFlag -Active 0
            }

            Write-Log "  Waiting for game to exit..."
            Wait-GameExit -GameName $game

            Write-Log "GAME EXITED: $game"
            $gaming = $false

            Start-OllamaAfterGaming
        }
    }
}
