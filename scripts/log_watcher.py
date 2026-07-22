#!/usr/bin/env python3
"""Step 2 of the Civ Historian pipeline: watches incoming/Automation.log for
growth, and once it's been stable (no further growth) for --settle-seconds,
kicks off scripts/run_pipeline.py to turn the accumulated log into a fresh
session (article + images). Designed to run as a long-lived background
service (see start_log_watcher.sh) - same poll-loop pattern as mp_watcher.py.

windows_log_watcher.ps1 only pushes the log once a play session is
finished, not continuously turn-by-turn - so by the time a new/changed
Automation.log shows up here at all, it's already the final version for
that session. --settle-seconds is just a safety margin against reading the
file mid-scp-transfer, not a wait for the player to stop playing, so it can
be short (a couple of poll intervals is plenty).

Usage:
    python3 scripts/log_watcher.py
    python3 scripts/log_watcher.py --poll-seconds 15 --settle-seconds 10
    python3 scripts/log_watcher.py --idle-exit-minutes 30
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = REPO_ROOT / "incoming" / "Automation.log"


def main() -> None:
    ap = argparse.ArgumentParser(description="Trigger the Civ Historian pipeline when Automation.log settles")
    ap.add_argument("--poll-seconds", type=float, default=15.0)
    ap.add_argument("--settle-seconds", type=float, default=10.0,
                     help="How long Automation.log must stay unchanged before triggering a run")
    ap.add_argument("--idle-exit-minutes", type=float, default=0.0,
                     help="Exit after this many minutes with nothing new to process (0 = never exit)")
    args = ap.parse_args()

    last_size = -1
    last_grew_at: float | None = None
    triggered_for_size = -1
    idle_since: float | None = None

    print(f"Watching {LOG_PATH}")
    print(f"poll={args.poll_seconds}s settle={args.settle_seconds}s idle-exit={args.idle_exit_minutes}min")

    while True:
        time.sleep(args.poll_seconds)

        size = LOG_PATH.stat().st_size if LOG_PATH.exists() else -1

        if size != last_size:
            last_size = size
            last_grew_at = time.time()
            idle_since = None
            continue

        if size <= 0 or size == triggered_for_size:
            # Nothing pending: no file yet, or unchanged since last successful run.
            if args.idle_exit_minutes:
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= args.idle_exit_minutes * 60:
                    print(f"[{time.strftime('%H:%M:%S')}] idle for "
                          f"{args.idle_exit_minutes:.0f}min - exiting.")
                    return
            continue

        if last_grew_at is not None and time.time() - last_grew_at >= args.settle_seconds:
            print(f"[{time.strftime('%H:%M:%S')}] Automation.log settled at {size} bytes - running pipeline")
            result = subprocess.run(
                [sys.executable, str(REPO_ROOT / "scripts" / "run_pipeline.py")],
                cwd=REPO_ROOT,
            )
            if result.returncode == 0:
                triggered_for_size = size
                idle_since = None
                print(f"[{time.strftime('%H:%M:%S')}] pipeline finished")
            else:
                print(f"[{time.strftime('%H:%M:%S')}] pipeline FAILED "
                      f"(exit {result.returncode}) - will retry next settle")


if __name__ == "__main__":
    main()
