# Omniscient extraction example (turn 14, 2026-07-16)

Example dump proving out the save-extraction approach in
`docs/superpowers/specs/2026-07-14-civ6-save-extraction-design.md`, captured
live via FireTuner against a real single-player game (Rome/Trajan, turn 14,
74x46 map, 6 majors + 9 city-states + Free Cities + Barbarians).

## Why this exists

`civ6-mcp` (cloned into `civ6-mcp/` at the repo root) is built for an honest
player-agent, so its `get_diplomacy()`/`get_rival_snapshot()`/map queries all
gate on `PlayersVisibility[me]:IsRevealed()` / `HasMet()` — i.e. they hide
anything the human player hasn't personally explored or met, matching real
fog-of-war rules. That's correct for civ6-mcp's use case but wrong for this
project: save-extraction for narrative generation should see everything in
the save regardless of what the player has explored, the same way a debug/
WorldBuilder view would. FireTuner's Lua execution has full engine access, so
custom queries that skip those gates entirely work fine and return complete
data — confirmed live on this turn-14 save where the player had met 0 of 5
rival civs and explored only a handful of tiles.

## Files

- `lua/civs_omniscient.lua` — full per-major-civ stats (score, cities, pop,
  military, techs, civics, gold), no `HasMet` check. Run in `InGame` context
  (`execute_write`) — `GetTreasury():GetGoldBalance()` errors in the
  read-only `GameCore` context.
- `lua/players_roster.lua` — every alive player including city-states and
  barbarians, with real names (normally hidden until first contact).
  GameCore-safe (`execute_read`).
- `lua/map_lookup_tables.lua` — terrain/feature/resource index → type-name
  tables (`GameInfo.Terrains()` etc.), needed to decode the numeric IDs in
  `full_map_tiles.lua`'s output. GameCore-safe.
- `lua/full_map_tiles.lua` — the whole map, every tile, no
  `PlayersVisibility`/`IsRevealed` check anywhere. One `print()` per map row
  (not per tile) to keep the FireTuner message count sane — 46 messages for a
  74x46 map instead of 3404. GameCore-safe.
- `map_lookup.txt`, `players.txt`, `map_rows.txt` — raw captured output of
  the three queries above, pipe-delimited.
- `render_map.py` — parses the three raw files into per-civ territory/
  resource stats (`civ_stats.txt`) and a synthetic top-down PNG render of the
  entire map with zero fog of war (`full_map_omniscient.png`). This is a
  data-driven visualization, not a screenshot of the game's own renderer —
  attempts to force the live renderer's fog off via
  `WorldBuilder.MapManager():SetAllRevealed()` failed because `WorldBuilder`
  Lua globals are only populated inside actual WorldBuilder mode, not
  reachable safely from a live FireTuner session (see spike 2 memory for the
  probe). Rendering our own map from the extracted tile data sidesteps that
  entirely and is arguably more useful anyway (consistent styling, exact
  ownership coloring, no camera/zoom issues).
- `civ_stats.txt` — stdout of `render_map.py`: global terrain histogram,
  land/water/hills/mountain counts, and a per-civ territory + top-resources
  breakdown for every major and city-state.
- `full_map_omniscient.png` — the rendered map. Each civ/city-state gets a
  distinct outline color on owned tiles; mountains are chevrons, hills are
  arcs, resources are yellow dots; base terrain is shaded by biome.

## Regenerating

With a live game running and FireTuner reachable on port 4318/4319:

```python
sys.path.insert(0, "civ6-mcp/src")
from civ_mcp.connection import GameConnection
conn = GameConnection(port=4318)
await conn.connect()
lines = await conn.execute_read(open("lua/map_lookup_tables.lua").read())
# ... etc, one call per .lua file above, save each to its .txt file
```

Then `python3 render_map.py` (needs Pillow — available in civ6-mcp's `uv`
venv, or `pip install --break-system-packages Pillow`).

## Key finding for `dump_module.lua`

When drafting the real extraction script, do **not** adapt civ6-mcp's
diplomacy/map Lua queries directly — copy the unconditional-loop pattern
from the `.lua` files here instead. See
`project_civ6_spike3_lua_api_findings.md` (agent memory) for the full
writeup.
