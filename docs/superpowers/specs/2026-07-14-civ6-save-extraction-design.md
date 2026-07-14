# Civ6 Save State Extraction — Design

Date: 2026-07-14
Status: Approved (sub-project 1 of the Civ6 news-article pipeline)

## Context

The end goal is an automated pipeline that turns a finished Civilization VI
session (played with friends) into a news-style article summarizing what
happened — city ownership changes ("Egypt annexes London from England"),
tech/civic races, military buildup, religion, wonders, etc.

Civ6's save file format is proprietary and only partially reverse-engineered
by the community, so this project reads state by running the actual game
headlessly with a mod/automation script rather than parsing the binary save
directly.

This is a multi-subsystem project. This document scopes **only the first
subsystem: extracting a full state snapshot (+ minimap screenshot) from a
single save file, running headlessly on the Ubuntu box.** Everything
downstream — diffing snapshots into events, generating narrative prose,
detecting/watching for new saves, delivery — is out of scope here and will
get its own design(s) later.

## Goals

- Given a save file path, produce one JSON state snapshot and one minimap
  PNG, without any human interaction.
- Cover: player yields (science/culture/gold/faith), techs/civics researched,
  military strength, city ownership, religion, wonders built — the fields
  needed to later detect "city changed hands," "new tech/wonder," and
  "who's leading X."
- Run once per autosave in a session (not just the final save), so
  downstream diffing can reconstruct a chronological sequence of events
  within a session, not just a net before/after.
- Runs on this headless Ubuntu box, Civ6 installed via Steam (Proton).

## Non-goals (deferred to later sub-projects)

- Watching a folder / share for new saves and auto-triggering. For now,
  invocation is **manual**: point the CLI at a folder containing dumped
  autosaves. (A later phase will point this at a network share that your
  main PC syncs autosaves to.)
- Diffing snapshots into narrated events.
- Generating article prose (LLM step).
- Any delivery mechanism (where the article ends up).

## Architecture

Two harnesses share one Lua module so game-state knowledge lives in exactly
one place:

- **`lua/dump_module.lua`** — pure Lua, no I/O. Given the live GameCore
  state, walks players/cities/techs/yields/military/religion/wonders and
  returns one Lua table matching the schema below. Does not know how its
  output leaves the process.

- **Dev harness (FireTuner path)** — used only to build/iterate on
  `dump_module.lua`. Launch Civ6 normally (under Xvfb), load a save, let it
  sit at the loaded state. `dev/firetuner_client.py` speaks the FireTuner
  wire protocol, sends `dump_module` plus a call that JSON-encodes its
  result and `print()`s it, and reads the result back off the debug socket.
  Fast iteration — no relaunching Civ6 per Lua tweak.

- **Prod harness (Automation path)** — `lua/automation_script.lua`,
  loaded by Civ6 via command-line at launch. Loads the target save, waits
  for the load-complete event, calls `dump_module`, writes the JSON result
  to disk (Automation Lua contexts have trusted file I/O, unlike normal mod
  sandboxes), triggers a screenshot, then invokes the Automation quit
  action. One process: save path in, JSON + PNG out, then exits. No network
  client needed at runtime.

## Environment

- Civ6 is installed via Steam (Proton) on this box.
- No monitor attached — Civ6 must run under Xvfb. Rendering is most likely
  software (llvmpipe) unless a virtual GPU (VirtualGL) is set up, which
  adds more complexity than assumed here.
- Whether Civ6 reaches a loaded-save state under Xvfb at all, and how long
  that takes per launch, is **unproven** — this is the first implementation
  spike (see Risks).

## State snapshot schema

```json
{
  "turn": 47,
  "gameTurnYear": "...",
  "players": [
    {
      "civ": "EGYPT",
      "leaderName": "Cleopatra",
      "isHuman": true,
      "score": 210,
      "gold": 340,
      "goldPerTurn": 12,
      "sciencePerTurn": 28,
      "culturePerTurn": 19,
      "faithPerTurn": 8,
      "techsResearched": 22,
      "civicsResearched": 18,
      "militaryStrength": 145,
      "unitCount": 9,
      "atWarWith": ["ENGLAND"],
      "religionFounded": "Osirianism",
      "cities": [
        { "id": 1001, "name": "Thebes", "population": 12, "originalOwner": "EGYPT" },
        { "id": 1044, "name": "London", "population": 8, "originalOwner": "ENGLAND" }
      ]
    }
  ],
  "cities": [
    {
      "id": 1044,
      "name": "London",
      "owner": "EGYPT",
      "x": 34,
      "y": 12,
      "population": 8,
      "majorityReligion": "Osirianism"
    }
  ],
  "wonders": [
    { "name": "Great Library", "city": "Thebes", "owner": "EGYPT" }
  ]
}
```

Design point: cities carry a current `owner`; comparing `owner` for the
same city `id` across two consecutive snapshots is exactly what lets a
downstream diff stage produce "Egypt annexes London from England" — no
in-game event log needed. Leaderboards ("who's leading science") are a sort
over `players` in a single snapshot, no diffing required.

This field list is a target shape, not a final spec — exact GameCore Lua
API calls per field are discovered during the FireTuner spike (see Risks),
and fields may be added (e.g. era score, victory progress) if cheaply
available once we're in there.

## Screenshot capture

Captured at the OS level against the Xvfb display (`import`/`scrot`/
`ffmpeg` against the virtual display), not via an in-game Lua screenshot
call. Simpler, and cropping to the minimap region is under our control once
the fixed UI layout at a known resolution is established. Trade-off:
brittle if UI layout shifts between game versions/resolutions — the crop
rectangle should be a single tunable constant.

## Invocation model

Manual CLI, no watcher/daemon:

```
python run.py --input-dir /path/to/dumped/autosaves
```

For each autosave newer than the last-processed turn recorded in
`state/last_processed.json` (tracked per game), runs the prod harness once,
in turn order. Each run's `state.json` is independent — sequencing/turn
order is what lets a later diffing stage reconstruct a chronological event
list, but that stage is out of scope here.

## Directory layout

```
src/civ6-pipeline/
  lua/dump_module.lua              # shared, no I/O
  lua/automation_script.lua        # prod harness
  dev/firetuner_client.py          # dev harness, FireTuner protocol client
  run.py                           # host CLI entrypoint
  data/
    snapshots/<save-basename>/state.json
    snapshots/<save-basename>/minimap.png
  state/last_processed.json        # last-processed turn per game
```

## Risks / validation spikes (first implementation tasks)

None of the following are proven yet; each becomes a spike task before the
real dump module is built:

1. Does Civ6 launch and reach a loaded-save state under Xvfb/software
   rendering at all? How long per launch?
2. Does the Automation framework's Lua context actually permit file writes
   (needed to get `dump_module`'s output onto disk without FireTuner at
   runtime)?
3. Exact GameCore Lua API calls for each schema field (yields, military
   strength, religion, wonders) — found via the FireTuner dev harness.
4. FireTuner wire protocol reachability under Proton — is the debug port
   exposed to the host, or only inside the Wine prefix's network namespace?
5. Minimap crop coordinates for the actual configured game resolution.
