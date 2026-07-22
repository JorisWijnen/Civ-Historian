# Pushes this Windows PC's Civ6 Automation.log (written by
# mod/StatsDumper/StatsDumper.lua) to a Linux box, then exits -- run this
# once after finishing a play session, not left running throughout it.
# Was named windows_log_watcher.ps1; renamed once it stopped behaving like
# a continuous watcher (see the one-shot exit-after-push logic below) and
# became a one-shot pusher instead. Supersedes windows_save_watcher.ps1 for
# the Civ Historian pipeline - we no longer need actual .Civ6Save files
# pushed at all, only this one log file. (windows_save_watcher.ps1 is left
# alone, unused by this flow, in case the save-reload approach is ever
# revisited.)
#
# UnitOperations.log (Civ6's own built-in unit-operation log) used to be
# pushed alongside Automation.log too, but StatsDumper.lua now writes its
# own richer unit-status/combat-target data (CIV6UNITOPS_V2|... lines)
# straight into Automation.log itself, superseding the need for the native
# log entirely - see mod/StatsDumper/StatsDumper.lua and
# scripts/parse_mod_log.py's extract_unit_operations().
#
# Delivery is atomic: each file is scp'd to a ".partial" name first, then
# renamed into its final name with a single remote `mv` over ssh. A rename
# on the same filesystem is atomic, so anything polling the destination
# path (log_watcher.py) can never observe a half-written file -- it either
# isn't there yet, or it's the complete log. That's what lets
# log_watcher.py trigger the pipeline the instant the file appears, with no
# "wait for it to settle" step of its own.
#
# Requires: Windows 10 1809+ / 11 (ssh.exe/scp.exe bundled via the OpenSSH
# client feature). Set up an SSH key (ssh-keygen, then copy the .pub into
# ~/.ssh/authorized_keys on the Linux box) so this can run unattended.
#
# Usage (PowerShell):
#   .\windows_log_pusher.ps1
#   .\windows_log_pusher.ps1 -PollSeconds 20
#   .\windows_log_pusher.ps1 --Path "D:\Custom\Logs"   (or -LogDir/-Path)

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
# no need for a stability check on this side -- we WANT to push a
# still-growing file here, not wait for it to stop changing).
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

        $tmpName = "$name.partial"
        Write-Host "[$( Get-Date -Format 'HH:mm:ss' )] Pushing $name ($size bytes)"
        try {
            & scp -q $path "${RemoteHost}:${RemoteDir}${tmpName}"
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "  scp exited with code $LASTEXITCODE - will retry next poll"
            } else {
                & ssh $RemoteHost "mv -f '${RemoteDir}${tmpName}' '${RemoteDir}${name}'"
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "  done"
                    $lastPushedSize[$name] = $size
                } else {
                    Write-Warning "  remote rename exited with code $LASTEXITCODE - will retry next poll"
                }
            }
        } catch {
            Write-Warning "  push failed: $_ - will retry next poll"
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
