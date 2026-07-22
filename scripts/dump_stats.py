#!/usr/bin/env python3
"""Dump a full omniscient stats snapshot of the currently-running Civ6 game.

Standalone, no LLM involved. Connects to FireTuner on the active game
(EnableTuner must be 1 in AppOptions.txt, a game must be loaded — main menu
has no InGame/GameCore_Tuner Lua states to attach to), pulls every civ's
stats regardless of fog-of-war/contact status, the full map tile grid, and
the local player's own detailed overview, and writes one JSON file plus
(optionally) flat CSV tables and a rendered map PNG.

Usage:
    python3 scripts/dump_stats.py
    python3 scripts/dump_stats.py --out my_dump.json --csv --map-image
    python3 scripts/dump_stats.py --port 4319

Requires the civ6-mcp checkout at ../civ6-mcp relative to this script (for
its FireTuner wire-protocol client only — none of its player-facing,
fog-gated query builders are used here).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass

sys.stdout.reconfigure(line_buffering=True)  # real-time ordering when run as
# a subprocess (see load_save.py's --dump chaining) or backgrounded

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "civ6-mcp", "src"))

from civ_mcp.connection import GameConnection  # noqa: E402
from civ_mcp.game_state import GameState  # noqa: E402

DEFAULT_PORTS = (4318, 4319)

# ---------------------------------------------------------------------------
# Lua queries — all unconditional (no PlayersVisibility/HasMet gating), so
# they return complete data regardless of what the human player has explored
# or met. See docs/examples/turn14_omniscient_dump/README.md for why.
# ---------------------------------------------------------------------------

LUA_PLAYERS_ROSTER = """
for i = 0, 63 do
  local p = Players[i]
  if p ~= nil and p:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local name = cfg and Locale.Lookup(cfg:GetCivilizationShortDescription()) or "?"
    local kind = "MINOR"
    if p:IsMajor() then kind = "MAJOR" elseif p:IsBarbarian() then kind = "BARB" end
    print("PLAYER|"..i.."|"..kind.."|"..name)
  end
end
print("---END---")
"""

LUA_CIVS_AND_CITIES = """
for i = 0, 62 do
  if Players[i] and Players[i]:IsMajor() and Players[i]:IsAlive() then
    local cfg = PlayerConfigurations[i]
    local name = Locale.Lookup(cfg:GetCivilizationShortDescription())
    local leader = Locale.Lookup(cfg:GetLeaderName())
    local isHuman = false
    pcall(function() isHuman = cfg:IsHuman() end)
    local p = Players[i]
    local score = p:GetScore()
    local st = p:GetStats()
    local mil, techs, civics = 0, 0, 0
    pcall(function() mil = st:GetMilitaryStrength() end)
    pcall(function() techs = st:GetNumTechsResearched() end)
    pcall(function() civics = st:GetNumCivicsCompleted() end)
    local gold, goldYield, sciYield, culYield = 0, 0, 0, 0
    pcall(function() gold = p:GetTreasury():GetGoldBalance() end)
    pcall(function() goldYield = p:GetTreasury():GetGoldYield() end)
    pcall(function() sciYield = p:GetTechs():GetScienceYield() end)
    pcall(function() culYield = p:GetCulture():GetCultureYield() end)
    local govName = "NONE"
    pcall(function()
      local govIdx = p:GetCulture():GetCurrentGovernment()
      if govIdx and govIdx >= 0 then
        local row = GameInfo.Governments[govIdx]
        if row then govName = row.GovernmentType end
      end
    end)
    local atWar = false
    pcall(function() atWar = Players[Game.GetLocalPlayer()]:GetDiplomacy():IsAtWarWith(i) end)

    -- Full war graph: which OTHER majors civ i is at war with, checked
    -- unconditionally (not just relative to the local player, no HasMet
    -- gating). Barbarians excluded on purpose — everyone is permanently
    -- "at war" with them in Civ6, not a meaningful signal.
    local warWith = {}
    pcall(function()
      local diplo = p:GetDiplomacy()
      for k = 0, 62 do
        if k ~= i and Players[k] and Players[k]:IsAlive() and Players[k]:IsMajor() then
          local ok, atWarWithK = pcall(function() return diplo:IsAtWarWith(k) end)
          if ok and atWarWithK then table.insert(warWith, k) end
        end
      end
    end)

    -- Denunciations: directional, unlike war — GetDiplomaticStateIndex(i)
    -- is k's stance TOWARD i, and k's stance toward i can differ from i's
    -- stance toward k (confirmed live: one side read UNFRIENDLY while the
    -- other read NEUTRAL for the same pair). denouncedBy = every other
    -- major whose current diplomatic state toward civ i is DENOUNCED.
    -- State index -> name table matches civ6-mcp's own diplomacy.py query.
    local diploStates = {"ALLIED","DECLARED_FRIEND","FRIENDLY","NEUTRAL","UNFRIENDLY","DENOUNCED","WAR"}
    local denouncedBy = {}
    pcall(function()
      for k = 0, 62 do
        if k ~= i and Players[k] and Players[k]:IsAlive() and Players[k]:IsMajor() then
          local ok, stateIdx = pcall(function() return Players[k]:GetDiplomaticAI():GetDiplomaticStateIndex(i) end)
          if ok and diploStates[stateIdx + 1] == "DENOUNCED" then table.insert(denouncedBy, k) end
        end
      end
    end)

    print("CIV|"..i.."|"..name.."|"..leader.."|human="..tostring(isHuman)..
      "|score="..score.."|gold="..gold.."|goldpt="..goldYield.."|scipt="..sciYield..
      "|culpt="..culYield.."|mil="..mil.."|techs="..techs.."|civics="..civics..
      "|gov="..govName.."|atwar="..tostring(atWar).."|atwarids="..table.concat(warWith, ",")..
      "|denouncedby="..table.concat(denouncedBy, ","))

    local nCities, totalPop = 0, 0
    for _, c in p:GetCities():Members() do
      nCities = nCities + 1
      local pop = c:GetPopulation()
      totalPop = totalPop + pop
      local cap = 0
      pcall(function() cap = c:IsCapital() and 1 or 0 end)
      print("CITY|"..i.."|"..Locale.Lookup(c:GetName()).."|"..c:GetX()..","..c:GetY()..
        "|pop="..pop.."|cap="..cap)
    end
    print("CIVTOTALS|"..i.."|cities="..nCities.."|pop="..totalPop)
  end
end

-- City-states and Free Cities also own named cities (e.g. a major civ's
-- city rebelling from low loyalty becomes a Free City, sometimes later
-- annexed by a different major) — tracked separately from the majors loop
-- above since they don't have the full CIV stat block (score, techs, etc).
-- MINORCITY|ownerId|cityName|x,y|pop=N|cap=0/1
for i = 0, 62 do
  if Players[i] and not Players[i]:IsMajor() and not Players[i]:IsBarbarian() and Players[i]:IsAlive() then
    local ok = pcall(function()
      for _, c in Players[i]:GetCities():Members() do
        local pop = c:GetPopulation()
        local cap = c:IsCapital() and 1 or 0
        print("MINORCITY|"..i.."|"..Locale.Lookup(c:GetName()).."|"..c:GetX()..","..c:GetY()..
          "|pop="..pop.."|cap="..cap)
      end
    end)
  end
end
print("---END---")
"""

LUA_MAP_LOOKUP = """
for row in GameInfo.Terrains() do print("TERRAIN|"..row.Index.."|"..row.TerrainType) end
for row in GameInfo.Features() do print("FEATURE|"..row.Index.."|"..row.FeatureType) end
for row in GameInfo.Resources() do print("RESOURCE|"..row.Index.."|"..row.ResourceType.."|"..(row.ResourceClassType or "")) end
print("---END---")
"""

LUA_MAP_TILES = """
local w, h = Map.GetGridSize()
print("GRIDSIZE|"..w.."|"..h)
for y = 0, h - 1 do
  local parts = {}
  for x = 0, w - 1 do
    local plot = Map.GetPlot(x, y)
    if plot then
      local terrainIdx = plot:GetTerrainType()
      local ownerIdx = plot:GetOwner()
      local hills = plot:IsHills() and 1 or 0
      local mtn = plot:IsMountain() and 1 or 0
      local water = plot:IsWater() and 1 or 0
      local river = plot:IsRiver() and 1 or 0
      local coastal = plot:IsCoastalLand() and 1 or 0
      local featureIdx = plot:GetFeatureType()
      local resourceIdx = plot:GetResourceType()
      table.insert(parts, terrainIdx..","..ownerIdx..","..hills..","..mtn..","..water..","..river..","..coastal..","..featureIdx..","..resourceIdx)
    end
  end
  print("ROW|"..y.."|"..table.concat(parts, ";"))
end
print("---END---")
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _kv_fields(s: str) -> dict:
    """Parse trailing 'key=value|key2=value2' segments into a dict."""
    out = {}
    for part in s.split("|"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _num(v: str):
    try:
        if "." in v:
            return float(v)
        return int(v)
    except (ValueError, TypeError):
        return v


def parse_players_roster(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        if not line.startswith("PLAYER|"):
            continue
        _, pid, kind, name = line.split("|", 3)
        out.append({"id": int(pid), "kind": kind, "name": name})
    return out


def parse_civs_and_cities(lines: list[str]) -> list[dict]:
    civs: dict[int, dict] = {}
    order: list[int] = []
    for line in lines:
        if line.startswith("CIV|"):
            _, pid, name, leader, rest = line.split("|", 4)
            pid = int(pid)
            fields = _kv_fields(rest)
            civs[pid] = {
                "id": pid,
                "name": name,
                "leader": leader,
                "is_human": fields.get("human") == "true",
                "at_war_with_local_player": fields.get("atwar") == "true",
                "government": fields.get("gov", "NONE"),
                "score": _num(fields.get("score", "0")),
                "gold": _num(fields.get("gold", "0")),
                "gold_per_turn": _num(fields.get("goldpt", "0")),
                "science_per_turn": _num(fields.get("scipt", "0")),
                "culture_per_turn": _num(fields.get("culpt", "0")),
                "military_strength": _num(fields.get("mil", "0")),
                "techs_researched": _num(fields.get("techs", "0")),
                "civics_completed": _num(fields.get("civics", "0")),
                "at_war_with": [int(x) for x in fields.get("atwarids", "").split(",") if x],
                "denounced_by": [int(x) for x in fields.get("denouncedby", "").split(",") if x],
                "num_cities": 0,
                "population": 0,
                "cities": [],
            }
            order.append(pid)
        elif line.startswith("CITY|"):
            _, pid, name, coord, rest = line.split("|", 4)
            pid = int(pid)
            x, y = coord.split(",")
            fields = _kv_fields(rest)
            if pid in civs:
                civs[pid]["cities"].append({
                    "name": name,
                    "x": int(x),
                    "y": int(y),
                    "population": _num(fields.get("pop", "0")),
                    "is_capital": fields.get("cap") == "1",
                })
        elif line.startswith("CIVTOTALS|"):
            _, pid, rest = line.split("|", 2)
            pid = int(pid)
            fields = _kv_fields(rest)
            if pid in civs:
                civs[pid]["num_cities"] = _num(fields.get("cities", "0"))
                civs[pid]["population"] = _num(fields.get("pop", "0"))
    return [civs[pid] for pid in order]


def parse_minor_cities(lines: list[str], players: list[dict]) -> list[dict]:
    """City-states and Free Cities (owner id 62) own named cities too —
    tracked separately from parse_civs_and_cities since they don't have a
    CIV stat block. This is what makes a major-civ city rebelling into a
    Free City (or later being annexed by a different major) visible by
    name rather than just as an aggregate territory tile-count shift."""
    name_by_id = {p["id"]: p["name"] for p in players}
    out = []
    for line in lines:
        if not line.startswith("MINORCITY|"):
            continue
        _, pid, name, coord, rest = line.split("|", 4)
        pid = int(pid)
        x, y = coord.split(",")
        fields = _kv_fields(rest)
        out.append({
            "owner_id": pid,
            "owner_name": name_by_id.get(pid, f"player{pid}"),
            "name": name,
            "x": int(x),
            "y": int(y),
            "population": _num(fields.get("pop", "0")),
            "is_capital": fields.get("cap") == "1",
        })
    return out


def parse_map_lookup(lines: list[str]) -> tuple[dict, dict, dict]:
    terrains, features, resources = {}, {}, {}
    for line in lines:
        parts = line.split("|")
        if parts[0] == "TERRAIN":
            terrains[int(parts[1])] = parts[2]
        elif parts[0] == "FEATURE":
            features[int(parts[1])] = parts[2]
        elif parts[0] == "RESOURCE":
            resources[int(parts[1])] = {"type": parts[2], "class": parts[3]}
    return terrains, features, resources


def parse_map_tiles(lines: list[str]) -> tuple[int, int, dict]:
    width = height = 0
    grid: dict[tuple[int, int], dict] = {}
    for line in lines:
        if line.startswith("GRIDSIZE|"):
            _, w, h = line.split("|")
            width, height = int(w), int(h)
        elif line.startswith("ROW|"):
            _, y_str, data = line.split("|", 2)
            y = int(y_str)
            if not data:
                continue
            for x, cell in enumerate(data.split(";")):
                if not cell:
                    continue
                t, owner, hills, mtn, water, river, coastal, feat, res = cell.split(",")
                grid[(x, y)] = {
                    "terrain": int(t), "owner": int(owner), "hills": int(hills),
                    "mountain": int(mtn), "water": int(water), "river": int(river),
                    "coastal": int(coastal), "feature": int(feat), "resource": int(res),
                }
    return width, height, grid


def compute_map_stats(width: int, height: int, grid: dict, terrains: dict,
                       resources: dict, players: list[dict]) -> dict:
    terrain_hist = Counter(terrains.get(t["terrain"], f"UNK_{t['terrain']}") for t in grid.values())
    mountain_count = sum(1 for t in grid.values() if t["mountain"])
    water_count = sum(1 for t in grid.values() if t["water"])
    land_count = len(grid) - water_count
    hills_count = sum(1 for t in grid.values() if t["hills"] and not t["mountain"])

    per_owner = defaultdict(lambda: {"land": 0, "water": 0, "hills": 0, "mountain": 0, "resources": Counter()})
    for t in grid.values():
        owner = t["owner"]
        if owner < 0:
            continue
        d = per_owner[owner]
        if t["water"]:
            d["water"] += 1
        else:
            d["land"] += 1
        if t["hills"] and not t["mountain"]:
            d["hills"] += 1
        if t["mountain"]:
            d["mountain"] += 1
        if t["resource"] >= 0:
            rname = resources.get(t["resource"], {}).get("type", f"UNK_{t['resource']}")
            d["resources"][rname] += 1

    name_by_id = {p["id"]: p["name"] for p in players}
    kind_by_id = {p["id"]: p["kind"] for p in players}
    territory = []
    for owner in sorted(per_owner):
        d = per_owner[owner]
        territory.append({
            "id": owner,
            "name": name_by_id.get(owner, f"player{owner}"),
            "kind": kind_by_id.get(owner, "?"),
            "land_tiles": d["land"],
            "water_tiles": d["water"],
            "hills_tiles": d["hills"],
            "mountain_tiles": d["mountain"],
            "resources": dict(d["resources"]),
        })

    unowned_land = sum(1 for t in grid.values() if t["owner"] < 0 and not t["water"])
    unowned_water = sum(1 for t in grid.values() if t["owner"] < 0 and t["water"])

    return {
        "width": width,
        "height": height,
        "total_tiles": len(grid),
        "land_tiles": land_count,
        "water_tiles": water_count,
        "hills_tiles": hills_count,
        "mountain_tiles": mountain_count,
        "unowned_land_tiles": unowned_land,
        "unowned_water_tiles": unowned_water,
        "terrain_histogram": dict(terrain_hist),
        "territory": territory,
    }


def overview_to_dict(ov) -> dict:
    if is_dataclass(ov):
        d = asdict(ov)
    else:
        d = dict(ov.__dict__)
    # sets aren't JSON-serializable
    for k, v in list(d.items()):
        if isinstance(v, set):
            d[k] = sorted(v)
    return d


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def connect_any_port(ports: tuple[int, ...]) -> GameConnection:
    last_err = None
    for port in ports:
        conn = GameConnection(port=port)
        try:
            await conn.connect()
            print(f"Connected on port {port} "
                  f"(gamecore_index={conn.gamecore_index}, ingame_index={conn.ingame_index})")
            if conn.ingame_index is None:
                raise ConnectionError(
                    "Connected, but no InGame Lua state found — is a game actually "
                    "loaded (not just sitting at the main menu)?"
                )
            return conn
        except (ConnectionError, OSError) as e:
            last_err = e
            print(f"  port {port}: {e}")
    raise ConnectionError(
        f"Could not connect to Civ6's FireTuner on any of {ports}. "
        f"Is the game running with EnableTuner=1 and a game loaded? Last error: {last_err}"
    )


async def gather(port: int | None) -> dict:
    ports = (port,) if port else DEFAULT_PORTS
    conn = await connect_any_port(ports)
    gs = GameState(conn)

    print("Fetching local player overview...")
    ov = await gs.get_game_overview()

    print("Fetching player roster...")
    roster_lines = await conn.execute_read(LUA_PLAYERS_ROSTER, timeout=15.0)
    players = parse_players_roster(roster_lines)

    print("Fetching all civs + cities (omniscient, no fog gating)...")
    civ_lines = await conn.execute_write(LUA_CIVS_AND_CITIES, timeout=20.0)
    civs = parse_civs_and_cities(civ_lines)
    minor_cities = parse_minor_cities(civ_lines, players)

    print("Fetching map lookup tables...")
    lookup_lines = await conn.execute_read(LUA_MAP_LOOKUP, timeout=15.0)
    terrains, features, resources = parse_map_lookup(lookup_lines)

    print("Fetching full map tile grid (omniscient, no fog gating)...")
    tile_lines = await conn.execute_read(LUA_MAP_TILES, timeout=60.0)
    width, height, grid = parse_map_tiles(tile_lines)

    map_stats = compute_map_stats(width, height, grid, terrains, resources, players)

    await conn.disconnect()

    return {
        "meta": {
            "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "turn": ov.turn,
            "era_name": ov.era_name,
            "difficulty": ov.difficulty,
            "max_turns": ov.max_turns,
        },
        "local_player": overview_to_dict(ov),
        "civs": civs,
        "minor_cities": minor_cities,
        "players_roster": players,
        "map": map_stats,
        "_raw_tiles": {
            "terrains": terrains,
            "features": features,
            "resources": resources,
            "grid": {f"{x},{y}": v for (x, y), v in grid.items()},
        },
    }


def write_csv_tables(data: dict, base_path: str) -> list[str]:
    written = []

    civs_path = f"{base_path}.civs.csv"
    with open(civs_path, "w", newline="") as f:
        fields = ["id", "name", "leader", "is_human", "at_war_with_local_player",
                   "at_war_with", "denounced_by", "government", "score", "gold",
                   "gold_per_turn", "science_per_turn", "culture_per_turn",
                   "military_strength", "techs_researched", "civics_completed",
                   "num_cities", "population"]
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in data["civs"]:
            row = dict(c)
            row["at_war_with"] = ";".join(str(x) for x in c["at_war_with"])
            row["denounced_by"] = ";".join(str(x) for x in c["denounced_by"])
            w.writerow(row)
    written.append(civs_path)

    cities_path = f"{base_path}.cities.csv"
    with open(cities_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["civ_id", "civ_name", "city_name", "x", "y", "population", "is_capital"])
        for c in data["civs"]:
            for city in c["cities"]:
                w.writerow([c["id"], c["name"], city["name"], city["x"], city["y"],
                            city["population"], city["is_capital"]])
    written.append(cities_path)

    minor_cities_path = f"{base_path}.minor_cities.csv"
    with open(minor_cities_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["owner_id", "owner_name", "city_name", "x", "y", "population", "is_capital"])
        for city in data.get("minor_cities", []):
            w.writerow([city["owner_id"], city["owner_name"], city["name"], city["x"], city["y"],
                        city["population"], city["is_capital"]])
    written.append(minor_cities_path)

    territory_path = f"{base_path}.territory.csv"
    with open(territory_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "kind", "land_tiles", "water_tiles",
                     "hills_tiles", "mountain_tiles", "top_resources"])
        for t in data["map"]["territory"]:
            top_res = ", ".join(f"{k}:{v}" for k, v in
                                 sorted(t["resources"].items(), key=lambda kv: -kv[1])[:5])
            w.writerow([t["id"], t["name"], t["kind"], t["land_tiles"], t["water_tiles"],
                        t["hills_tiles"], t["mountain_tiles"], top_res])
    written.append(territory_path)

    return written


def print_summary(data: dict) -> None:
    meta = data["meta"]
    print(f"\n=== Turn {meta['turn']} ({meta['era_name']}, {meta['difficulty']}) ===")
    print(f"{'Civ':<14}{'Leader':<18}{'Score':>6}{'Cities':>7}{'Pop':>5}"
          f"{'Mil':>6}{'Techs':>6}{'Gold':>7}")
    for c in data["civs"]:
        print(f"{c['name']:<14}{c['leader']:<18}{c['score']:>6}{c['num_cities']:>7}"
              f"{c['population']:>5}{c['military_strength']:>6}{c['techs_researched']:>6}"
              f"{c['gold']:>7.0f}")
    m = data["map"]
    print(f"\nMap: {m['width']}x{m['height']} ({m['total_tiles']} tiles) — "
          f"land={m['land_tiles']} water={m['water_tiles']} "
          f"hills={m['hills_tiles']} mountains={m['mountain_tiles']}")
    print(f"Players known (roster): {len(data['players_roster'])} "
          f"(majors={sum(1 for p in data['players_roster'] if p['kind']=='MAJOR')}, "
          f"minors={sum(1 for p in data['players_roster'] if p['kind']=='MINOR')})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None,
                     help="Output JSON path (default: dumps/civ6_turn<N>_<timestamp>.json)")
    ap.add_argument("--port", type=int, default=None,
                     help="FireTuner port (default: try 4318 then 4319)")
    ap.add_argument("--csv", action="store_true",
                     help="Also write flat CSV tables (civs, cities, territory) next to the JSON")
    ap.add_argument("--map-image", action="store_true",
                     help="Also render a full no-fog map PNG next to the JSON "
                          "(requires Pillow: pip install --break-system-packages Pillow)")
    ap.add_argument("--no-raw-tiles", action="store_true",
                     help="Omit the full per-tile grid from the JSON output (much smaller file, "
                          "keeps only the aggregated map stats)")
    args = ap.parse_args()

    data = asyncio.run(gather(args.port))

    if args.no_raw_tiles:
        del data["_raw_tiles"]

    if args.out:
        out_path = args.out
    else:
        dumps_dir = os.path.join(REPO_ROOT, "dumps")
        os.makedirs(dumps_dir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(dumps_dir, f"civ6_turn{data['meta']['turn']:03d}_{ts}.json")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nWrote {out_path}")

    if args.csv:
        base = os.path.splitext(out_path)[0]
        for p in write_csv_tables(data, base):
            print(f"Wrote {p}")

    if args.map_image:
        try:
            from render_map_lib import render_map
        except ImportError:
            print("--map-image requires render_map_lib.py next to this script "
                  "(and Pillow installed) — skipping.")
        else:
            img_path = os.path.splitext(out_path)[0] + ".map.png"
            render_map(data, img_path)
            print(f"Wrote {img_path}")

    print_summary(data)


if __name__ == "__main__":
    main()
