#!/usr/bin/env python3
"""Civ Historian webapp: upload an Automation.log + paste a Discord webhook
URL, run the pipeline once against it, and show the result.

This is the on-demand alternative to the always-on log_watcher.py systemd
service that runs directly on the host (untouched by this) -- see
docs/webapp.md. A real game log takes several minutes to process, so this
is a plain synchronous HTML form POST (no JS/AJAX, no job queue) -- the
browser's native spinner is enough for a single-user tool. Served by
gunicorn with --timeout 0 so a long request isn't reaped mid-run.

No auth in this iteration (local network only, Authentik SSO to be added
later in front of this by the operator) -- so no CSRF/SECRET_KEY either,
since no Flask session/flash state is used.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from flask import Flask, render_template, request, send_from_directory

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS = REPO_ROOT / "sessions"
SESSIONS.mkdir(exist_ok=True)

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from run_pipeline import setup_session, run_pipeline  # noqa: E402

app = Flask(__name__)
# Real raw Automation.log files observed so far run 2.8-24MB; 200MB is
# generous headroom, not an attempt at an unbounded upload.
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024


def _new_web_session_name() -> str:
    # _web suffix so browsing sessions/ makes it obvious which runs came
    # from here vs. the automatic windows_log_pusher.ps1 -> log_watcher.py
    # path -- nothing else parses the session-name format.
    return "session_" + time.strftime("%Y%m%d_%H%M%S") + "_web"


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/run")
def run():
    upload = request.files.get("log_file")
    if upload is None or not upload.filename:
        return render_template(
            "error.html", session_name=None,
            message="No Automation.log file was uploaded.",
        ), 400

    webhook_url = (request.form.get("webhook_url") or "").strip() or None
    post_article_text = "post_article_text" in request.form

    session_name = _new_web_session_name()
    # Saved next to sessions/ (same volume as the eventual session dir)
    # rather than /tmp, then copied in by setup_session() and removed below.
    tmp_upload = SESSIONS / f".upload-{session_name}.log"
    upload.save(tmp_upload)

    session_dir = None
    try:
        session_dir = setup_session(session_name, log_path=tmp_upload)
        discord_status = run_pipeline(
            session_dir, webhook_url=webhook_url, post_article_text=post_article_text,
        )
    except Exception as e:
        traceback.print_exc()
        return render_template(
            "error.html", session_name=session_name, message=str(e),
        ), 500
    finally:
        tmp_upload.unlink(missing_ok=True)

    return render_template(
        "result.html",
        session_name=session_name,
        headliner_exists=(session_dir / "headliner.png").exists(),
        newspaper_exists=(session_dir / "newspaper.png").exists(),
        article_exists=(session_dir / "article.md").exists(),
        discord_status=discord_status,
    )


@app.get("/sessions/<path:subpath>")
def sessions_static(subpath):
    return send_from_directory(SESSIONS, subpath)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
