# Web upload app

An on-demand alternative to the always-on automatic path
(`windows_log_pusher.ps1` -> `log_watcher.py`, see [`scripts.md`](scripts.md)):
a webpage where you upload an `Automation.log` you already have on hand and
paste a Discord webhook URL, and it runs the pipeline once against that log.

Useful for testing, a log that didn't get auto-pushed, or running a game log
through the pipeline from a machine that isn't the always-on host —
`log_watcher.py` keeps running directly on the host exactly as before; this
is a separate, on-demand entry point into the same `run_pipeline.py` code,
packaged as a Docker container so it can run anywhere.

## Routes

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Upload form: `Automation.log` file + optional Discord webhook URL + "post article text" checkbox. |
| `/run` | POST | Runs `setup_session()` then `run_pipeline()` against the upload, synchronously. Renders `result.html` on success, `error.html` on failure. |
| `/sessions/<path>` | GET | Serves files out of the bind-mounted `sessions/` volume (used by `result.html` to embed the generated images). |

There is no auth on any of this (local network only for now — front it with
your own reverse proxy/SSO if exposing it more broadly) and no job
queue/progress UI: a real game log takes several minutes, so `/run` is a
plain HTML form POST that blocks until the run finishes and then renders the
result page. Leave the tab open; there's no AJAX polling to watch.

## Build and run

```bash
cp .env.example .env    # fill in GEMINI_API_KEY / OPENAI_API_KEY
export UID GID           # bash doesn't export these by default
docker compose build
docker compose up -d
```

Then visit `http://<host-ip>:8000/`. `DISCORD_WEBHOOK_URL` and
`DISCORD_POST_ARTICLE_TEXT` are **not** container-level config — the form
takes them per-request instead, so different runs can post to different
webhooks without touching the container.

Uploaded logs land in a normal `sessions/session_<timestamp>_web/` directory
(the `_web` suffix just makes it obvious in a directory listing which runs
came from here vs. the automatic push pipeline — nothing else depends on
the naming).

## `claude` CLI auth in the container — needs a live smoke test

The Dockerfile installs Node.js + `npm install -g @anthropic-ai/claude-code`
so `run_pipeline.py`'s `claude -p` calls work inside the container. The
chosen approach is to mount the host's `~/.claude` directory read-only at
`/app/.claude` (`docker-compose.yml` does this; `ENV HOME=/app` in the
Dockerfile makes `claude` look there), reusing whatever credentials already
work interactively on the host rather than provisioning a separate
`ANTHROPIC_API_KEY`.

This has **not yet been confirmed to work headlessly in a container** — verify
before relying on it:

```bash
docker compose run --rm civ6-webapp claude --version
docker compose run --rm civ6-webapp claude -p "reply OK" --output-format json
```

If that fails, the fallback is setting `ANTHROPIC_API_KEY` in `.env` instead
of the `~/.claude` mount (billed separately from an interactive Claude Code
subscription — check which applies before switching).

## Behavior notes

- Single gunicorn worker (`--workers 1`) — this is a single-user tool, and
  running two pipeline invocations concurrently would mean two interleaved
  `claude -p`/image-generation calls competing for the same API keys and
  session-naming clock. `--timeout 0` so gunicorn doesn't kill a multi-minute
  request.
- `MAX_CONTENT_LENGTH` is capped at 200MB (real raw logs observed so far run
  2.8-24MB — generous headroom, not unbounded).
- If image generation or the Discord post fails, the result page still
  shows whatever local artifacts *did* get produced (article/images) plus
  the Discord failure reason — matches `run_pipeline.py`'s existing
  "local artifacts complete even if Discord hiccups" behavior on the CLI
  side.
- No progress UI in v1 — the browser's native spinner during the POST is
  the only feedback while a run is in progress.
