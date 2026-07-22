#!/usr/bin/env python3
"""Step 2 of the Civ Historian pipeline: watches for incoming/Automation.log
to appear, then kicks off scripts/run_pipeline.py to turn it into a fresh
session (article + images). Designed to run as a long-lived background
service (a systemd user service works well).

windows_log_pusher.ps1 delivers the log atomically -- it scp's to a
".partial" name, then renames it into place with a single remote `mv` over
ssh -- so by the time Automation.log exists at this path, it's guaranteed
to be the complete, final log for that session (rename is atomic on the
same filesystem, so this can never observe a half-written file). No
"settle" wait for the file to stop growing is needed here as a result.

Usage:
    python3 scripts/log_watcher.py
    python3 scripts/log_watcher.py --poll-seconds 15
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
    ap = argparse.ArgumentParser(description="Trigger the Civ Historian pipeline when Automation.log arrives")
    ap.add_argument("--poll-seconds", type=float, default=15.0)
    ap.add_argument("--idle-exit-minutes", type=float, default=0.0,
                     help="Exit after this many minutes with nothing new to process (0 = never exit)")
    args = ap.parse_args()

    idle_since: float | None = None

    print(f"Watching {LOG_PATH}")
    print(f"poll={args.poll_seconds}s idle-exit={args.idle_exit_minutes}min")

    while True:
        time.sleep(args.poll_seconds)

        if not LOG_PATH.exists():
            if args.idle_exit_minutes:
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= args.idle_exit_minutes * 60:
                    print(f"[{time.strftime('%H:%M:%S')}] idle for "
                          f"{args.idle_exit_minutes:.0f}min - exiting.")
                    return
            continue

        idle_since = None
        size = LOG_PATH.stat().st_size
        print(f"[{time.strftime('%H:%M:%S')}] Automation.log arrived ({size} bytes) - running pipeline")
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "run_pipeline.py")],
            cwd=REPO_ROOT,
        )
        if result.returncode == 0:
            print(f"[{time.strftime('%H:%M:%S')}] pipeline finished")
        else:
            print(f"[{time.strftime('%H:%M:%S')}] pipeline FAILED (exit {result.returncode})")
        # run_pipeline.py's setup_session() archives Automation.log out of
        # incoming/ (into the new session's raw_logs/) as one of its first
        # steps, so incoming/ is empty again on the next poll in the normal
        # case. If it failed before reaching that point (e.g. parse_mod_log.py
        # itself errored), the file is still here and this will retry on
        # the very next poll -- same "keep retrying until something
        # succeeds or the file is gone" behavior as before, just without a
        # separate settle timer gating it.


if __name__ == "__main__":
    main()
