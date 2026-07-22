# Civ6 pipeline scripts

Reference for every script in `scripts/` that makes up the Civ Historian
pipeline — parsing the mod's log output into an AI-generated newspaper
recap, posted to Discord.

The log itself is written by `mod/StatsDumper/StatsDumper.lua` during
play — an omniscient per-turn snapshot (civs and their real in-game colors,
cities, map, era, victory progress, religion, weather/disaster and
historic-moment notifications) logged via the game's own
`Automation.Log()`.

### `windows_log_pusher.ps1` (runs on the Windows gaming PC)

Polls the Civ6 Logs folder for `Automation.log`, pushes it to the Linux
box as soon as it appears/grows, then **exits** — run this once after
finishing a play session, not during the game. Always pushes to the
**same** remote filename (overwrite, not a timestamped copy) since this is
a cumulative per-session log, not discrete per-turn snapshots.

Delivery is atomic: each push `scp`'s to a `.partial` name first, then
renames it into its final name with a single remote `mv` over `ssh`. A
same-filesystem rename is atomic, so `log_watcher.py` polling the
destination path can never observe a half-written file — it's either not
there yet, or it's the complete log. That's what lets `log_watcher.py`
trigger the pipeline immediately on arrival with no "settle" wait of its
own. Was named `windows_log_watcher.ps1`; renamed once it stopped
behaving like a continuous watcher and became a one-shot pusher.

#### Params

| Param | Default | Meaning |
|---|---|---|
| `-LogDir` (alias `-Path`, or `--Path`) | `C:\Users\joris\AppData\Local\Firaxis Games\Sid Meier's Civilization VI\Logs` | Folder Civ6 writes its logs into (distinct from the `OneDrive\Documents\My Games\...` path Saves/Mods live under). `--Path` is handled manually since PowerShell's own parameter binder only recognizes a single leading dash. |
| `-RemoteHost` | `joris@192.168.2.2` | SSH target — the Linux box. |
| `-RemoteDir` | `/home/joris/civ6-pipeline/incoming/` | Where the pushed log lands. |
| `-PollSeconds` | `30` | How often to check `-LogDir` for growth. |
| `-LogFiles` | `Automation.log` | Which filename(s) in `-LogDir` to watch/push. |

#### Usage

```powershell
.\windows_log_pusher.ps1
.\windows_log_pusher.ps1 -PollSeconds 20
.\windows_log_pusher.ps1 -Path "D:\Custom\Logs"
.\windows_log_pusher.ps1 --Path "D:\Custom\Logs"
```

### `log_watcher.py` (runs on the Linux box)

Polls for `incoming/Automation.log` to exist, then runs `run_pipeline.py`
the moment it does. No settle/stability check is needed here —
`windows_log_pusher.ps1`'s atomic rename-on-delivery (see above) already
guarantees that whenever this file exists, it's the complete, final log,
never a partial one caught mid-transfer.

#### Params

| Param | Default | Meaning |
|---|---|---|
| `--poll-seconds` | `15.0` | How often to check whether the log has arrived. |
| `--idle-exit-minutes` | `0` | Exit after this many minutes with nothing new to process. `0` = never exit. |

#### Usage

```bash
python3 scripts/log_watcher.py
python3 scripts/log_watcher.py --poll-seconds 15
python3 scripts/log_watcher.py --idle-exit-minutes 30
```

Meant to run continuously rather than be started by hand each time — a
systemd **user** service works well:

```
systemctl --user status log-watcher
journalctl --user -u log-watcher -f
```

If set up this way, enable lingering (`loginctl enable-linger <user>`) so
it starts at boot without needing an active login session, and give the
service an `EnvironmentFile` for `OPENAI_API_KEY`/`DISCORD_WEBHOOK_URL` —
systemd services don't source `.bashrc`.

### `run_pipeline.py` (runs on the Linux box)

The actual worker — steps 3 through 8 of the Civ Historian pipeline, all in
one script:

1. **Parse**: runs `parse_mod_log.py` against `incoming/Automation.log`
   into a fresh `sessions/<name>/` directory, including a per-turn map PNG
   and (once every turn's map is rendered) a `map_timelapse.mp4` assembled
   from all of them via `make_map_video.py`.
2. **Template setup**: copies the three prompt templates from `assets/prompts/`
   (`article.prompt.txt`, `prompt_gen.prompt.txt`, `newspaper_image.prompt.txt`)
   into the session directory, substituting the literal string `SESSIONX`
   for the real session name in each, so their file-path references (e.g.
   `sessions/SESSIONX/article.md`) resolve correctly.
3. **Archive**: moves the source log files out of `incoming/` into
   `sessions/<name>/raw_logs/` — both to keep every file for this run
   together in one place, and so the *next* run always starts from a clean
   log (a leftover earlier game's data bleeding into a later run's parse via
   an un-cleared `Automation.log` has happened twice doing this by hand).
4. **Article**: runs `sessions/<name>/article.prompt.txt` through the local
   `claude` CLI, headless (`claude -p --dangerously-skip-permissions`) — it
   needs real file read/write access (the parsed stats JSON,
   `assets/turn_to_year.md`, writing `article.md`), which a plain API call
   wouldn't provide.
5. **Image prompt**: same mechanism, for `prompt_gen.prompt.txt` ->
   `openai_image_prompt.txt`.
6. **Headliner image**: matches leader names mentioned in
   `openai_image_prompt.txt` against filenames in `assets/leaders/` (all-words
   whole-word match, e.g. `frederick-barbarossa.webp` needs both "frederick"
   and "barbarossa" present), attaches the matched portraits as reference
   images, and calls OpenAI (`gpt-image-1`, via `openai_image.py`) to produce
   `headliner.png`.
7. **Newspaper composite**: calls OpenAI again with
   `newspaper_image.prompt.txt` plus a short excerpt of `article.md` (just
   the masthead/headline/opening line — asking the model to typeset the
   *entire* article onto one image reliably failed) and `headliner.png`
   attached as a reference image, producing the final `newspaper.png`. If
   this step fails for any reason, `headliner.png` is posted to Discord
   instead rather than skipping the post entirely.
8. **Discord**: posts the image (`newspaper.png`, or the `headliner.png`
   fallback) via `post_discord.py`, followed by `article.md`'s full text as
   one or more follow-up messages — unless `DISCORD_POST_ARTICLE_TEXT=0`
   (see Behavior notes), in which case only the image is posted.

#### Params

| Param | Default | Meaning |
|---|---|---|
| `--session-name` | new `session_<start timestamp>` | Target session directory under `sessions/`. If it already exists, **resumes** it instead of re-parsing: skips step 4 if `article.md` already exists, skips step 5 if `openai_image_prompt.txt` already exists, and always (re-)runs steps 6/7 unless `--skip-images` is given. |
| `--skip-images` | off | Stop after steps 3-5 (article + image prompt only) — no OpenAI calls. |

#### Usage

```bash
python3 scripts/run_pipeline.py
python3 scripts/run_pipeline.py --skip-images
python3 scripts/run_pipeline.py --session-name session_20260718_222916   # resume/retry
```

#### Behavior notes

- Requires `incoming/Automation.log` to exist when starting a **new**
  session (raises and exits otherwise) — resuming an existing session
  doesn't re-check this, since parsing already happened.
- The `claude -p` calls run with `--dangerously-skip-permissions` since
  there's no human available to approve tool calls in an unattended
  pipeline — acceptable here because the only inputs are our own generated
  stats/prompt files, not untrusted external data.
- If `OPENAI_API_KEY` isn't set, step 6 (the first image call) raises a
  `RuntimeError` that's caught and reported clearly rather than crashing:
  `article.md`/`openai_image_prompt.txt` are left in place, and the message
  tells you to re-run with `--session-name <name>` once the key is set,
  which picks up exactly where it left off (steps 3-5 are skipped as
  already done).
- Only needs the `requests` package for steps 6/7 (not needed for
  `--skip-images` runs) — no image SDK/`pip install` required.
- `DISCORD_POST_ARTICLE_TEXT` (default on; `0`/`false`/`no` disables) —
  whether step 8 also posts `article.md`'s full text after the image.
  Defaults to on, but since OpenAI's `newspaper.png` renders the article
  legibly on its own, the text follow-up is often redundant now.

### `openai_image.py`

Not normally run directly — the OpenAI (`gpt-image-1`) image-generation
call `run_pipeline.py` uses internally for steps 6/7. Used to be called
`nano_banana.py` back when this called Gemini instead (nicknamed "Nano
Banana"); renamed once the backend switched to OpenAI. Standalone CLI also
available for one-off testing:

#### Params

| Param | Default | Meaning |
|---|---|---|
| `--prompt-file` | required | Path to a text file with the image prompt. |
| `--ref-image` | none (repeatable) | Path to a reference image to attach; pass multiple times for multiple images. |
| `--out` | required | Output PNG path. |
| `--model` | `gpt-image-1` (or `$OPENAI_IMAGE_MODEL`) | OpenAI model ID to call. |

#### Usage

```bash
python3 scripts/openai_image.py --prompt-file sessions/X/openai_image_prompt.txt \
    --ref-image assets/leaders/dido.webp --ref-image assets/leaders/wilhelmina.webp \
    --out sessions/X/headliner.png
```

Requires `OPENAI_API_KEY` in the environment (get one at
https://platform.openai.com/api-keys). With one or more `--ref-image`s it
calls `/v1/images/edits` (gpt-image-1 accepts several reference images per
call, sent as repeated `image[]` multipart fields); with none at all it
calls `/v1/images/generations` (text-only) instead. Both return a
`b64_json`-encoded PNG directly, decoded and written to `--out`.

### `post_discord.py`

Not normally run directly — `run_pipeline.py` uses it internally for step
8. Posts an image as a file attachment to a Discord incoming webhook,
optionally followed by article text chunked to Discord's 2000-character
message limit (splitting on paragraph breaks where possible). Standalone
CLI also available:

#### Params

| Param | Default | Meaning |
|---|---|---|
| `--image` | required | Path to the image to post. |
| `--article` | none | Path to article text to post as follow-up message(s). If omitted, only the image is posted. |
| `--label` | none | Short caption shown above the image. |
| `--webhook-url` | `$DISCORD_WEBHOOK_URL` | Discord incoming webhook URL. |

#### Usage

```bash
python3 scripts/post_discord.py --image sessions/X/newspaper.png \
    --article sessions/X/article.md --label session_X
python3 scripts/post_discord.py --image sessions/X/newspaper.png --label session_X
```

Requires a Discord incoming webhook URL (Server Settings -> Integrations
-> Webhooks -> New Webhook -> Copy URL). Removing "Send Messages"
permission for a channel's roles does **not** block webhook posts — that's
a separate mechanism from normal member permissions. Deleting/regenerating
the webhook, or deleting the channel, does.

## Standalone tools

`dump_stats.py` and `render_map_lib.py` are also used directly by
`parse_mod_log.py` as shared library functions, but remain independently
useful on their own — e.g. pulling a live stats snapshot from a
single-player game over FireTuner, or rendering a map PNG from an existing
dump by hand.

### `dump_stats.py`

Takes a full omniscient stats snapshot of whatever Civ6 game is currently
loaded — every civ's stats regardless of fog-of-war/contact status, the
full map tile grid, minor civs/Free Cities, and the local player's own
detailed overview. Requires a live FireTuner connection to a loaded game
(the main menu has no InGame/GameCore_Tuner Lua state to attach to).

#### Params

| Param | Default | Meaning |
|---|---|---|
| `--out` | `dumps/civ6_turn<N>_<timestamp>.json` | Output JSON path. |
| `--port` | tries `4318` then `4319` | FireTuner port. |
| `--csv` | off | Also write flat CSV tables (civs, cities, territory) next to the JSON. |
| `--map-image` | off | Also render a full no-fog map PNG next to the JSON (needs Pillow). |
| `--no-raw-tiles` | off | Omit the full per-tile grid from the JSON (much smaller file, keeps only aggregated map stats). |

#### Usage

```bash
python3 scripts/dump_stats.py
python3 scripts/dump_stats.py --out my_dump.json --csv --map-image
python3 scripts/dump_stats.py --port 4319
```

### `render_map_lib.py`

Not a CLI tool in normal use — a library `dump_stats.py --map-image` and
`parse_mod_log.py` import for rendering a synthetic top-down, zero-fog map
PNG from a dump's JSON. Can also be run standalone against an existing
dump:

```bash
python3 scripts/render_map_lib.py path/to/dump.json out.png
```

No other params — takes exactly a JSON path and an output PNG path,
positionally.

**Territory rendering**: owned tiles get a translucent color wash, with a
solid border drawn only on the *outer* edge of each territory (an edge is
bordered only where the neighboring tile belongs to someone else, or is
off the map) — not on every edge of every owned hex, which is how
Civ6 itself renders borders in-game.

**Colors**: uses each civ's real in-game primary color (`primary_color` on
each entry in the JSON's `civs` list, populated from `StatsDumper.lua`'s
`UI.GetPlayerColors()` dump) directly — Civ6's own lobby already refuses
to start a game with two players assigned conflicting colors, so no
collision-checking is needed here. Anyone without a real color at all
(city-states, barbarians, or an older log predating this field) falls back
to `assets/colors/jersey-colors.md`, a hand-picked palette chosen for
maximum distinguishability, picking whichever entry is farthest from every
color already used. Assignment is order-stable (sorted by player id) so
the same game's civs keep the same colors turn after turn.

**Cities**: every city gets a marker (pentagon for capitals, circle
otherwise) filled in the owner's color plus a name label, using a
Unicode-capable TTF (DejaVu Sans, falling back to Liberation Sans, falling
back to PIL's plain bitmap font if neither is installed) — PIL's default
font can't render accented names like "Bogotá" or "Meroë" at all.

### `make_map_video.py`

Not normally run directly — `parse_mod_log.py` calls it automatically
after rendering every turn's map PNG, assembling `sessions/<name>/turnNNN.map.png`
into a `map_timelapse.mp4` timelapse in turn order. Requires
`pip install --break-system-packages imageio imageio-ffmpeg` (the latter
bundles its own static ffmpeg binary, no system `ffmpeg` install needed);
if missing, `parse_mod_log.py` logs a warning and skips the video rather
than failing the whole run. Standalone CLI also available:

#### Params

| Param | Default | Meaning |
|---|---|---|
| `session_dir` | required, positional | Directory containing `turnNNN.map.png` files. |
| `--seconds-per-turn` | `0.5` | Duration each frame is shown for. |
| `--out` | `<session_dir>/map_timelapse.mp4` | Output MP4 path. |

#### Usage

```bash
python3 scripts/make_map_video.py sessions/session_X
python3 scripts/make_map_video.py sessions/session_X --seconds-per-turn 0.25
```
