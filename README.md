# Civ6 Pipeline

Turns a real Civilization VI multiplayer game into an AI-generated
"newspaper" recap — a written article plus an illustrated front page —
automatically posted to Discord after each play session.

A custom Lua mod logs an omniscient snapshot of the game every turn (civs
and their real in-game colors, cities, map, era, victory progress,
religion, weather, historic moments).
Once a session ends, that log gets parsed, summarized into a news article
by Claude, illustrated by Gemini and OpenAI's image models, and posted to
Discord — no manual steps required.

## How it works

```
[Windows gaming PC]                              [Linux box]
mod/StatsDumper/StatsDumper.lua                    |
  (writes Automation.log during play)              |
        |                                           |
windows_log_pusher.ps1  --scp-->              incoming/Automation.log
  (run once after a session, then exits)            |
                                                      v (poll for arrival)
                                              log_watcher.py
                                        (systemd user service, always on)
                                                      |
                                                      v
                                              run_pipeline.py  <---------+
                              parse_mod_log.py -> claude -p x2 (article.md,  |
                       openai_image_prompt.txt) -> nano_banana.py (Gemini,   |
                        headliner.png) -> openai_image.py (OpenAI, newspaper.png)
                                            -> post_discord.py               |
                                                      |                      |
                                                      v                      |
                              sessions/<name>/{article.md, headliner.png,    |
                                     newspaper.png, turnNNN-*.json/.map.png, |
                                               map_timelapse.mp4}            |
                                                      |                      |
                                                      v                      |
                                                   Discord                   |
                                                                             |
                                        [webapp/, Docker container] --------+
                                        upload Automation.log + webhook URL
                                          via a browser, on-demand, same
                                              run_pipeline.py functions
```

1. **`mod/StatsDumper/`** — a read-only Civ6 mod that logs a full per-turn
   snapshot via the game's own `Automation.Log()`, no external tools or
   FireTuner connection required (safe for real multiplayer/anti-cheat).
2. **`windows_log_pusher.ps1`** — run once after finishing a game session
   on the machine that was playing. Delivers the accumulated log
   atomically (scp to a temp name, then a remote rename) to a Linux box,
   then exits.
3. **`log_watcher.py`** — runs continuously (as a systemd service) on the
   Linux side, watching for the log to arrive, then kicks off the
   pipeline as soon as it does — the atomic handoff above means it never
   has to guess whether a file it sees is still mid-transfer.
4. **`run_pipeline.py`** — parses the log into structured JSON, generates
   the article via two headless `claude -p` calls, the headliner
   illustration via Gemini, and the newspaper front page via OpenAI (each
   backend picked for whichever image it was judged better at), then posts
   the result to Discord.
5. **`webapp/`** — an on-demand alternative entry point into the same
   `run_pipeline.py` functions: a webpage to upload an `Automation.log`
   you already have on hand and paste a Discord webhook URL, packaged as a
   Docker container. `log_watcher.py` keeps running directly on the host,
   untouched — this is a separate path for testing or ad-hoc runs. See
   [`docs/webapp.md`](docs/webapp.md).

Full reference for every script (params, flags, behavior notes) lives in
[`docs/scripts.md`](docs/scripts.md).

## Project layout

- `mod/StatsDumper/` — the Civ6 mod (Lua).
- `scripts/` — the pipeline described above.
- `assets/` — leader portrait images (matched by filename against names
  mentioned in image prompts), `claude -p` prompt templates, a historic
  moments importance-scoring reference, a turn-number → in-game-year
  table, and a fallback color palette (`colors/jersey-colors.md`) for any
  civ with no real in-game color of its own (city-states, barbarians).
- `sessions/<name>/` — one directory per processed game session: parsed
  per-turn stats, a rendered map PNG per turn plus a `map_timelapse.mp4`
  assembled from them, the generated article, and the generated images.
  Gitignored.
- `incoming/` — drop zone the Windows-side pusher delivers the log into.
  Gitignored.
- `logs/` — the log-watcher service's own log output.
- `webapp/` — the Flask upload app (see Project layout below and
  [`docs/webapp.md`](docs/webapp.md)); `Dockerfile`, `docker-compose.yml`,
  `requirements.txt`, `.dockerignore`, `.env.example` (repo root) build and
  run it in a container.

## Setup

Requires:
- A Civ VI installation with a subscrition to the [`mod in the workshop`](https://steamcommunity.com/sharedfiles/filedetails/?id=3768059294) and having the mod enabled
- Claude code running on the pipeline machine
- A Gemini API key (headliner illustration) and an OpenAI API key (newspaper front page) — two different image backends, see `docs/scripts.md`
- A Discord incoming webhook URL (for posting results)

Environment variables (used by `run_pipeline.py`):

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Headliner illustration (`gemini-2.5-flash-image`, via `nano_banana.py`) |
| `OPENAI_API_KEY` | Newspaper front page (`gpt-image-2`, via `openai_image.py`) |
| `DISCORD_WEBHOOK_URL` | Posting the generated newspaper to Discord |
| `DISCORD_POST_ARTICLE_TEXT` | Optional; set to `0`/`false` to post the image only, without the article text follow-up |

`log_watcher.py` is meant to run as an always-on background service (a
systemd user service works well) so it's ready whenever a new log arrives.

### Web upload app (Docker)

For on-demand runs against a log you already have on hand — testing, a log
that didn't get auto-pushed, or running this from a machine other than the
always-on host — build and run the containerized upload app instead:

```bash
cp .env.example .env    # fill in GEMINI_API_KEY / OPENAI_API_KEY
export UID GID           # bash doesn't export these by default
docker compose up -d --build
```

Then visit `http://<host-ip>:8000/`, upload an `Automation.log`, and
(optionally) paste a Discord webhook URL. `log_watcher.py` is untouched by
this — it's a separate, on-demand path into the same `run_pipeline.py`
code. Full details, routes, and the `claude`-CLI-auth-in-container caveat
are in [`docs/webapp.md`](docs/webapp.md).

## Known limitations

- **Log format is versioned** (`CIV6STATS_V4`, `CIV6UNITOPS_V2`,
  `CIV6EVENTS_V2` marker tags) so an out-of-date mod produces a loud
  "no turn blocks found" instead of silently parsing into wrong data.
- **Weather/disaster and historic-moment detection are unverified.** They're
  built on a best-effort reading of Civ6's notification API and haven't
  been confirmed to work correctly for anything beyond the local player in
  a real multiplayer session. Era, victory-progress, and religion tracking
  are solid by comparison.
- **Victory *type* detection is a best-effort heuristic, not a direct API
  result.** `Game.GetWinningTeam()` (confirmed real) only says a team won,
  not which condition triggered it — the mod infers the type by checking
  science/diplomatic/culture/religious/domination conditions in a fixed
  priority order and falling back to `SCORE`. Untested against a real game
  ending; edge cases (e.g. two conditions completing the same turn) aren't
  disambiguated further.
- `windows_log_pusher.ps1` is meant to be run once after a session has ended, and before the game has rebooted clearing the log files.
- **The web upload app's `claude` CLI auth is unverified.** The container
  mounts the host's `~/.claude` read-only rather than using a separate
  `ANTHROPIC_API_KEY`, on the assumption that reuses whatever already works
  interactively — this hasn't been confirmed to actually work headlessly
  inside a container yet. See [`docs/webapp.md`](docs/webapp.md) for the
  smoke test and the `ANTHROPIC_API_KEY` fallback if it doesn't.
- **The web upload app is synchronous with no progress UI.** A real game
  log takes several minutes to process; `/run` is a plain HTML form POST
  that blocks until the run finishes, with no job queue, AJAX polling, or
  progress bar in this first version.
