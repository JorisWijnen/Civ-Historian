# Civ6 Pipeline

Turns a real Civilization VI multiplayer game into an AI-generated
"newspaper" recap — a written article plus an illustrated front page —
automatically posted to Discord after each play session.

A custom Lua mod logs an omniscient snapshot of the game every turn (civs,
cities, map, era, victory progress, religion, weather, historic moments).
Once a session ends, that log gets parsed, summarized into a news article
by Claude, illustrated by OpenAI's image model, and posted to Discord —
no manual steps required.

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
                                              run_pipeline.py
                              parse_mod_log.py -> claude -p x2 (article.md,
                              openai_image_prompt.txt) -> openai_image.py x2
                              (headliner.png, newspaper.png) -> post_discord.py
                                                      |
                                                      v
                              sessions/<name>/{article.md, headliner.png,
                                               newspaper.png, turnNNN-*.json}
                                                      |
                                                      v
                                                   Discord
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
   the article and a front-page image via two headless `claude -p` calls
   and two OpenAI image calls, then posts the result to Discord.

Full reference for every script (params, flags, behavior notes) lives in
[`docs/scripts.md`](docs/scripts.md).

## Project layout

- `mod/StatsDumper/` — the Civ6 mod (Lua).
- `scripts/` — the pipeline described above.
- `assets/` — leader portrait images (matched by filename against names
  mentioned in image prompts), `claude -p` prompt templates, a historic
  moments importance-scoring reference, and a turn-number → in-game-year
  table.
- `sessions/<name>/` — one directory per processed game session: parsed
  per-turn stats, the generated article, and the generated images.
  Gitignored.
- `incoming/` — drop zone the Windows-side pusher delivers the log into.
  Gitignored.
- `logs/` — the log-watcher service's own log output.

## Setup

Requires:
- A Civ VI installation with a subscrition to the [`mod in the workshop`](https://steamcommunity.com/sharedfiles/filedetails/?id=3768059294) and having the mod enabled
- Claude code running on the pipeline machine
- An OpenAI API key (image generation)
- A Discord incoming webhook URL (for posting results)

Environment variables (used by `run_pipeline.py`):

| Variable | Purpose |
|---|---|
| `OPENAI_API_KEY` | Image generation (`gpt-image-1`) |
| `DISCORD_WEBHOOK_URL` | Posting the generated newspaper to Discord |
| `DISCORD_POST_ARTICLE_TEXT` | Optional; set to `0`/`false` to post the image only, without the article text follow-up |

`log_watcher.py` is meant to run as an always-on background service (a
systemd user service works well) so it's ready whenever a new log arrives.

## Known limitations

- **Log format is versioned** (`CIV6STATS_V2`, `CIV6UNITOPS_V2`,
  `CIV6EVENTS_V2` marker tags) so an out-of-date mod produces a loud
  "no turn blocks found" instead of silently parsing into wrong data.
- **Weather/disaster and historic-moment detection are unverified.** They're
  built on a best-effort reading of Civ6's notification API and haven't
  been confirmed to work correctly for anything beyond the local player in
  a real multiplayer session. Era, victory-condition, and religion tracking
  are solid by comparison.
- `windows_log_pusher.ps1` is meant to be run once after a session has ended, and before the game has rebooted clearing the log files.
