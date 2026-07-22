# Watches this Windows PC's Civ6 Logs folder for Automation.log (written by
# mod/StatsDumper/StatsDumper.lua), pushes it to the GPU box over scp as
# soon as it appears/grows, then exits -- run this once after finishing a
# play session, not left running throughout it. Supersedes
# windows_save_watcher.ps1 for the Civ Historian pipeline - we no longer
# need actual .Civ6Save files pushed at all, only this one log file.
# (windows_save_watcher.ps1 is left alone, unused by this flow, in case the
# save-reload approach is ever revisited.)
#
# UnitOperations.log (Civ6's own built-in unit-operation log) used to be
# pushed alongside Automation.log too, but StatsDumper.lua now writes its
# own richer unit-status/combat-target data (CIV6UNITOPS_V2|... lines)
# straight into Automation.log itself, superseding the need for the native
# log entirely - see mod/StatsDumper/StatsDumper.lua and
# scripts/parse_mod_log.py's extract_unit_operations().
#
# Unlike a save file, this log is a single ever-growing file for the
# whole session, not discrete per-turn snapshots - so this script always
# pushes to the SAME remote filename (overwriting), rather than a
# timestamped copy per push. The GPU-box side (log_watcher.py) is
# responsible for deciding when the accumulated log is "settled" enough to
# run the pipeline, not this script.
#
# Requires: Windows 10 1809+ / 11 (ssh.exe/scp.exe bundled via the OpenSSH
# client feature). Set up an SSH key (ssh-keygen, then copy the .pub into
# ~/.ssh/authorized_keys on the GPU box) so this can run unattended.
#
# Usage (PowerShell):
#   .\windows_log_watcher.ps1
#   .\windows_log_watcher.ps1 -PollSeconds 20
#   .\windows_log_watcher.ps1 --Path "D:\Custom\Logs"   (or -LogDir/-Path)

param(
    [Alias("Path")]
    [string]$LogDir = "C:\Users\joris\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\Logs",
    [string]$RemoteHost = "joris@192.168.2.2",
    [string]$RemoteDir = "/home/joris/civ6-pipeline/incoming/",
    [int]$PollSeconds = 30,
    [string[]]$LogFiles = @("Automation.log"),
    # Catches anything PowerShell's own binder didn't match by name (e.g. a
    # literal "--Path", which -- unlike "-Path"/"-LogDir" -- isn't valid
    # PowerShell parameter syntax) so it can be parsed manually below.
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

# Manual "--Path <dir>" / "--Path=<dir>" support, for anyone invoking this
# the GNU/argparse way instead of PowerShell's native "-LogDir"/"-Path".
if ($RemainingArgs) {
    for ($i = 0; $i -lt $RemainingArgs.Count; $i++) {
        if ($RemainingArgs[$i] -eq "--Path" -and ($i + 1) -lt $RemainingArgs.Count) {
            $LogDir = $RemainingArgs[++$i]
        } elseif ($RemainingArgs[$i] -like "--Path=*") {
            $LogDir = $RemainingArgs[$i].Substring(7)
        }
    }
}

if (-not (Test-Path $LogDir)) {
    Write-Error "Log directory not found: $LogDir"
    exit 1
}

Write-Host "Watching: $LogDir ($($LogFiles -join ', '))"
Write-Host "Pushing to: ${RemoteHost}:${RemoteDir}"
Write-Host "Poll interval: ${PollSeconds}s. Exits once every file above has been pushed (Ctrl+C to stop early)."
Write-Host ""

# filename -> last pushed size (a log only ever grows within a session, so
# "size changed since last push" is enough to decide whether to re-push;
# no need for the two-consecutive-polls stability check the save watcher
# used, since we WANT to push a still-growing file here, not wait for it to
# stop changing).
$lastPushedSize = @{}
foreach ($name in $LogFiles) {
    $lastPushedSize[$name] = -1
}

while ($true) {
    foreach ($name in $LogFiles) {
        $path = Join-Path $LogDir $name
        if (-not (Test-Path $path)) {
            continue
        }

        $size = (Get-Item $path).Length
        if ($size -eq $lastPushedSize[$name]) {
            continue
        }

        Write-Host "[$( Get-Date -Format 'HH:mm:ss' )] Pushing $name ($size bytes)"
        try {
            & scp -q $path "${RemoteHost}:${RemoteDir}${name}"
            if ($LASTEXITCODE -eq 0) {
                Write-Host "  done"
                $lastPushedSize[$name] = $size
            } else {
                Write-Warning "  scp exited with code $LASTEXITCODE - will retry next poll"
            }
        } catch {
            Write-Warning "  scp failed: $_ - will retry next poll"
        }
    }

    # Exit once every configured file has been pushed at least once, rather
    # than polling forever -- this is meant to be run after a play session
    # ends, not left running throughout it.
    $allPushed = $true
    foreach ($name in $LogFiles) {
        if ($lastPushedSize[$name] -lt 0) {
            $allPushed = $false
            break
        }
    }
    if ($allPushed) {
        Write-Host "All file(s) pushed - exiting."
        exit 0
    }

    Start-Sleep -Seconds $PollSeconds
}
