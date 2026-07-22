#!/usr/bin/env python3
"""Parse the StatsDumper mod's Automation.log output into per-turn JSON files.

Reads CIV6STATS_V3|... lines (written by mod/StatsDumper/StatsDumper.lua via
Automation.Log() on every Events.TurnBegin) and writes JSON files per turn:

  turnNNN-civs.json         -- players roster + per-major-civ stats, including
                                full war graph and denunciations (no nested
                                cities)
  turnNNN-cities.json       -- every city (major-civ owned AND city-state/
                                Free City owned) as a flat list, including
                                yields and majority religion
  turnNNN-map.json          -- aggregated map/territory stats + the full
                                "_raw_tiles" grid needed by
                                render_map_lib.render_map
  turnNNN-demographics.json -- current era + per-civ era score/age, enabled
                                victory types, and per-civ victory progress
  turnNNN-religion.json     -- per-civ founded religion/pantheon/majority
                                religion/faith balance
  turnNNN-weather.json      -- disaster/weather notifications seen this turn
  turnNNN-moments.json      -- historic moment notifications seen this turn,
                                cross-referenced against
                                assets/historic_moments.csv for an importance
                                score

Usage:
    python3 scripts/parse_mod_log.py \
        --log "/path/to/Automation.log" \
        --out dumps/mod-session \
        --map-image
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from dump_stats import (  # noqa: E402
    _kv_fields,
    _num,
    compute_map_stats,
    parse_map_lookup,
    parse_map_tiles,
)

PREFIX = "CIV6STATS_V3|"
UNITOPS_PREFIX = "CIV6UNITOPS_V2|"
EVENTS_PREFIX = "CIV6EVENTS_V2|"
HISTORIC_MOMENTS_CSV = os.path.join(REPO_ROOT, "assets", "historic_moments.csv")

# StatsDumper.lua's ERA marker emits e.g. "ERA_INDUSTRIAL" -- the CSV's
# per-era importance columns are named to match 1:1 once that prefix is
# stripped and title-cased.
_ERA_TYPE_TO_CSV_COLUMN = {
    "ERA_ANCIENT": "Ancient",
    "ERA_CLASSICAL": "Classical",
    "ERA_MEDIEVAL": "Medieval",
    "ERA_RENAISSANCE": "Renaissance",
    "ERA_INDUSTRIAL": "Industrial",
    "ERA_MODERN": "Modern",
    "ERA_ATOMIC": "Atomic",
    "ERA_INFORMATION": "Information",
    "ERA_FUTURE": "Future",
}

# A CSV row needs at least this many of its Name's significant words present
# in a notification message to count as a match -- see score_moment_text().
_MIN_MATCH_WORDS = 2
_MIN_MATCH_FRACTION = 0.6
_STOPWORDS = {"a", "an", "the", "of", "in", "on", "to", "for", "your", "you",
              "our", "is", "and", "or", "new", "first"}

_MAP_LOOKUP_TAG_RENAME = {
    "MAPLOOKUP_TERRAIN": "TERRAIN",
    "MAPLOOKUP_FEATURE": "FEATURE",
    "MAPLOOKUP_RESOURCE": "RESOURCE",
}


def _strip_turn_field(body: str, rename_tag: str | None = None) -> str:
    """'MAPLOOKUP_TERRAIN|1|3|COAST' -> 'TERRAIN|3|COAST' (drops the turn field)."""
    parts = body.split("|")
    tag = rename_tag or parts[0]
    return "|".join([tag] + parts[2:])


def split_turn_blocks(path: str, prefix: str = PREFIX) -> dict[int, list[str]]:
    """Bucket every prefix-stripped line by its own embedded turn field.

    Every line the mod writes carries the turn number as its second field
    (right after the tag), regardless of which Automation.Log() call it
    came from -- DumpTurnStats/DumpMapLookup/DumpMapTiles/DumpDemographics/
    DumpReligion/DumpNotableEvents are separate calls, so a naive
    TURN|N...END|N bracket match would miss data logged in a later, separate
    call within the same turn. Keying directly off each line's own turn
    field sidesteps that. `prefix` selects which marker family to bucket
    (CIV6STATS_V3 or CIV6EVENTS_V2).
    """
    blocks: dict[int, list[str]] = {}
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.startswith(prefix):
                continue
            body = line[len(prefix):]
            parts = body.split("|")
            if len(parts) < 2:
                continue
            try:
                turn = int(parts[1])
            except ValueError:
                continue
            blocks.setdefault(turn, []).append(body)
    return blocks


def extract_map_lookup(blocks: dict[int, list[str]]) -> tuple[dict, dict, dict]:
    """Terrain/feature/resource name tables are only logged once (whichever
    turn the mod first ran on), not every turn -- scan all turns for them."""
    lookup_lines: list[str] = []
    for lines in blocks.values():
        for line in lines:
            tag = line.split("|", 1)[0]
            if tag in _MAP_LOOKUP_TAG_RENAME:
                lookup_lines.append(_strip_turn_field(line, _MAP_LOOKUP_TAG_RENAME[tag]))
    return parse_map_lookup(lookup_lines)


def _parse_city_yields(rest: str) -> dict:
    fields = _kv_fields(rest)
    return {
        "population": _num(fields.get("pop", "0")),
        "is_capital": fields.get("cap") == "1",
        "food": _num(fields.get("food", "0")),
        "production": _num(fields.get("prod", "0")),
        "gold": _num(fields.get("gold", "0")),
        "science": _num(fields.get("sci", "0")),
        "culture": _num(fields.get("cul", "0")),
        "faith": _num(fields.get("faith", "0")),
        "religion": fields.get("rel", "none"),
    }


def parse_turn_block(
    turn: int, lines: list[str]
) -> tuple[dict, list[dict], list[str], dict, dict]:
    """Returns (civs_data, cities_list, raw tile lines, demographics_data,
    religion_data) for this turn."""
    players: list[dict] = []
    civs: dict[int, dict] = {}
    order: list[int] = []
    cities: list[dict] = []
    tile_lines: list[str] = []
    era_type = "UNKNOWN"
    era_index = -1
    enabled_victories: list[str] = []
    civdemo: dict[int, dict] = {}
    civreligion: dict[int, dict] = {}

    for line in lines:
        tag = line.split("|", 1)[0]
        if tag == "PLAYER":
            _, _turn, pid, kind, rest = line.split("|", 4)
            rest_parts = rest.split("|")
            name = rest_parts[0]
            fields = _kv_fields("|".join(rest_parts[1:]))
            players.append({
                "id": int(pid),
                "kind": kind,
                "name": name,
                "city_state_type": fields.get("cstype", ""),
            })
        elif tag == "CIV":
            _, _turn, pid, name, leader, rest = line.split("|", 5)
            pid = int(pid)
            fields = _kv_fields(rest)
            civs[pid] = {
                "id": pid,
                "name": name,
                "leader": leader,
                "is_human": fields.get("human") == "true",
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
                "primary_color": fields.get("primary", "#999999"),
                "secondary_color": fields.get("secondary", "#333333"),
                "num_cities": 0,
                "population": 0,
            }
            order.append(pid)
        elif tag == "CITY":
            _, _turn, pid, name, coord, rest = line.split("|", 5)
            pid = int(pid)
            x, y = coord.split(",")
            city = {"owner_id": pid, "owner_kind": "MAJOR", "name": name,
                    "x": int(x), "y": int(y)}
            city.update(_parse_city_yields(rest))
            cities.append(city)
        elif tag == "CIVTOTALS":
            _, _turn, pid, rest = line.split("|", 3)
            pid = int(pid)
            fields = _kv_fields(rest)
            if pid in civs:
                civs[pid]["num_cities"] = _num(fields.get("cities", "0"))
                civs[pid]["population"] = _num(fields.get("pop", "0"))
        elif tag == "MINORCITY":
            _, _turn, pid, name, coord, rest = line.split("|", 5)
            pid = int(pid)
            x, y = coord.split(",")
            city = {"owner_id": pid, "owner_kind": "MINOR", "name": name,
                    "x": int(x), "y": int(y)}
            city.update(_parse_city_yields(rest))
            cities.append(city)
        elif tag in ("GRIDSIZE", "ROW"):
            tile_lines.append(_strip_turn_field(line))
        elif tag == "ERA":
            _, _turn, era_type, era_index_str = line.split("|", 3)
            era_index = _num(era_index_str)
        elif tag == "VICTORYENABLED":
            _, _turn, vtype = line.split("|", 2)
            enabled_victories.append(vtype)
        elif tag == "CIVDEMO":
            _, _turn, pid, rest = line.split("|", 3)
            pid = int(pid)
            fields = _kv_fields(rest)
            civdemo[pid] = {
                "id": pid,
                "era_score": _num(fields.get("erascore", "0")),
                "age": fields.get("age", "NORMAL"),
                "science_vp": _num(fields.get("scivp", "0")),
                "science_vp_needed": _num(fields.get("scineeded", "0")),
                "diplomatic_vp": _num(fields.get("diplovp", "0")),
                "tourism": _num(fields.get("tourism", "0")),
                "religion_cities": _num(fields.get("relcities", "0")),
                "spaceports": _num(fields.get("spaceports", "0")),
            }
        elif tag == "CIVRELIGION":
            _, _turn, pid, rest = line.split("|", 3)
            pid = int(pid)
            fields = _kv_fields(rest)
            civreligion[pid] = {
                "id": pid,
                "religion_founded": fields.get("created", "NONE"),
                "pantheon": fields.get("pantheon", "NONE"),
                "majority_religion": fields.get("majority", "NONE"),
                "faith_balance": _num(fields.get("faith", "0")),
            }

    name_by_id = {p["id"]: p["name"] for p in players}
    kind_by_id = {p["id"]: p["kind"] for p in players}
    for city in cities:
        city["owner_name"] = name_by_id.get(city["owner_id"], f"player{city['owner_id']}")
        # PLAYER roster kind (MAJOR/MINOR/BARB) is authoritative; the CITY
        # vs. MINORCITY line tag is really just "came from the civs loop or
        # the city-states loop", keep it in sync where we have better info.
        if city["owner_id"] in kind_by_id:
            city["owner_kind"] = kind_by_id[city["owner_id"]]

    civs_data = {
        "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
        "players_roster": players,
        "civs": [civs[pid] for pid in order],
    }

    # Join civ name/leader onto the demographics/religion rows (parsed from
    # separate Automation.Log() calls, keyed only by player id) so each
    # output file is self-contained without a turnNNN-civs.json lookup.
    for pid, entry in civdemo.items():
        entry["name"] = name_by_id.get(pid, f"player{pid}")
    for pid, entry in civreligion.items():
        entry["name"] = name_by_id.get(pid, f"player{pid}")

    demographics_data = {
        "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
        "era": {"type": era_type, "index": era_index},
        "enabled_victories": enabled_victories,
        "civs": [civdemo[pid] for pid in order if pid in civdemo],
    }
    religion_data = {
        "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
        "civs": [civreligion[pid] for pid in order if pid in civreligion],
    }
    return civs_data, cities, tile_lines, demographics_data, religion_data


def extract_unit_operations(path: str) -> list[str]:
    """Render CIV6UNITOPS_V2|... lines (written by StatsDumper.lua) into
    readable AutomationUnitOperations.log lines.

    This is a mod-generated replacement for the native engine
    UnitOperations.log, which only logs a unit the turn a NEW operation is
    queued for it -- a unit still fortified/healing/garrisoned from an
    earlier turn goes completely silent, so army size/composition can't
    actually be read off it, and it never records what a RANGE_ATTACK/etc
    operation actually targeted. The mod can only ever write to
    Logs/Automation.log (no Lua API exists for opening an arbitrary second
    log file), so this file is assembled here, at parse time, instead.
    """
    out_lines: list[str] = []
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.startswith(UNITOPS_PREFIX):
                continue
            body = line[len(UNITOPS_PREFIX):]
            parts = body.split("|")
            tag = parts[0]

            if tag == "UNIT":
                # UNIT|turn|ownerId|unitId|unitType|x,y|moves=..|fortifyturns=..|activity=..|garrisoned=..
                _tag, turn, owner, unit_id, unit_type, coord, *rest = parts
                fields = _kv_fields("|".join(rest))
                garrison_note = " (garrisoned)" if fields.get("garrisoned") == "1" else ""
                out_lines.append(
                    f"{int(turn):03d}, UNIT, player{owner}, {unit_type} ({unit_id}) @ {coord}, "
                    f"activity={fields.get('activity', '?')}, moves={fields.get('moves', '?')}, "
                    f"fortifyturns={fields.get('fortifyturns', '?')}{garrison_note}"
                )
            elif tag == "COMBAT":
                # COMBAT|turn|attacker=..|attackertype=..|attackerbarb=..|defender=..|defendertype=..|defenderbarb=..
                _tag, turn, *rest = parts
                fields = _kv_fields("|".join(rest))
                attacker_barb = " (barbarian)" if fields.get("attackerbarb") == "1" else ""
                defender_barb = " (barbarian)" if fields.get("defenderbarb") == "1" else ""
                out_lines.append(
                    f"{int(turn):03d}, COMBAT, "
                    f"attacker=player{fields.get('attacker')} {fields.get('attackertype')}{attacker_barb} -> "
                    f"defender=player{fields.get('defender')} {fields.get('defendertype')}{defender_barb}"
                )
            # UNITEND is just a per-turn sentinel in the raw log, nothing to render.
    return out_lines


def extract_notable_events(path: str) -> dict[int, list[str]]:
    """Bucket CIV6EVENTS_V2|... lines (weather + historic moments) by turn,
    same shape as split_turn_blocks() but for the separate marker family
    DumpNotableEvents() writes."""
    return split_turn_blocks(path, prefix=EVENTS_PREFIX)


def parse_weather_and_moments(lines: list[str]) -> tuple[list[dict], list[dict]]:
    """Returns (weather_events, moment_events) for one turn's CIV6EVENTS_V2 lines."""
    weather: list[dict] = []
    moments: list[dict] = []
    for line in lines:
        tag = line.split("|", 1)[0]
        if tag == "WEATHER":
            _, _turn, pid, type_name, coord, message = line.split("|", 5)
            x, y = coord.split(",")
            weather.append({
                "player_id": int(pid),
                "type": type_name,
                "x": int(x),
                "y": int(y),
                "message": message,
            })
        elif tag == "MOMENT":
            _, _turn, pid, message = line.split("|", 3)
            moments.append({"player_id": int(pid), "message": message})
        # END is just a per-turn sentinel, nothing to render.
    return weather, moments


def load_historic_moments(path: str = HISTORIC_MOMENTS_CSV) -> list[dict]:
    """Load assets/historic_moments.csv into a list of rows with a parsed
    per-era importance score, e.g. {"Ancient": 10, "Classical": 9, ...}."""
    rows: list[dict] = []
    with open(path, newline="") as f:
        for raw_row in csv.DictReader(f, delimiter=";"):
            name = (raw_row.get("Name") or "").strip()
            if not name:
                continue
            era_scores = {}
            for era_col in _ERA_TYPE_TO_CSV_COLUMN.values():
                val = (raw_row.get(era_col) or "").strip()
                if val:
                    era_scores[era_col] = _num(val)
            rows.append({
                "name": name,
                "description": (raw_row.get("Description") or "").strip(),
                "base_score": _num((raw_row.get("Score") or "0").strip() or "0"),
                "era_scores": era_scores,
            })
    return rows


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-z']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def score_moment_text(message: str, csv_rows: list[dict], era_csv_column: str | None) -> dict | None:
    """Best-effort fuzzy match of a notification's free-text message against
    historic_moments.csv's Name/Description, scored for the current era.

    The Notification message is dynamically generated in-game (civ/leader
    names substituted in, third person, etc.) so it won't equal the CSV's
    Name or Description verbatim -- this instead picks whichever CSV row
    shares the most significant words with the message, requiring at least
    _MIN_MATCH_WORDS overlapping words and _MIN_MATCH_FRACTION of the row's
    own significant words to accept a match. Returns None (no confident
    match) rather than guessing, so callers can keep the raw message either
    way.
    """
    message_words = _significant_words(message)
    if not message_words:
        return None

    best_row = None
    best_overlap = 0
    for row in csv_rows:
        row_words = _significant_words(row["name"] + " " + row["description"])
        if not row_words:
            continue
        overlap = len(row_words & message_words)
        fraction = overlap / len(row_words)
        if overlap >= _MIN_MATCH_WORDS and fraction >= _MIN_MATCH_FRACTION and overlap > best_overlap:
            best_overlap = overlap
            best_row = row
    if best_row is None:
        return None

    score = best_row["era_scores"].get(era_csv_column) if era_csv_column else None
    if score is None:
        score = best_row["base_score"]
    return {
        "matched_name": best_row["name"],
        "score": score,
        "is_major": score is not None and score >= 7,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", required=True, help="Path to Automation.log")
    ap.add_argument("--out", required=True, help="Output directory for per-turn JSON files")
    ap.add_argument("--map-image", action="store_true", default=True,
                     help="Also render a no-fog map PNG per turn (default: on)")
    ap.add_argument("--no-map-image", dest="map_image", action="store_false",
                     help="Skip PNG rendering, JSON only")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    blocks = split_turn_blocks(args.log)
    if not blocks:
        print("No CIV6STATS_V3 turn blocks found in log.")
        return
    event_blocks = extract_notable_events(args.log)

    try:
        historic_moments = load_historic_moments()
    except FileNotFoundError:
        historic_moments = []
        print(f"Warning: {HISTORIC_MOMENTS_CSV} not found -- moments will be logged unscored.")

    terrains, features, resources = extract_map_lookup(blocks)
    if not terrains:
        print("Warning: no MAPLOOKUP_TERRAIN lines found -- map output will be unavailable "
              "(old mod version, or the log was truncated before turn 1).")

    render_map = None
    if args.map_image:
        try:
            from render_map_lib import render_map
        except ImportError:
            print("--map-image requires render_map_lib.py + Pillow -- skipping PNG output.")

    for turn in sorted(blocks):
        civs_data, cities, tile_lines, demographics_data, religion_data = parse_turn_block(turn, blocks[turn])

        civs_path = os.path.join(args.out, f"turn{turn:03d}-civs.json")
        with open(civs_path, "w") as f:
            json.dump(civs_data, f, indent=2)
        print(f"Wrote {civs_path} ({len(civs_data['civs'])} civs, {len(civs_data['players_roster'])} players)")

        cities_data = {
            "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
            "cities": cities,
        }
        cities_path = os.path.join(args.out, f"turn{turn:03d}-cities.json")
        with open(cities_path, "w") as f:
            json.dump(cities_data, f, indent=2)
        print(f"Wrote {cities_path} ({len(cities)} cities)")

        demographics_path = os.path.join(args.out, f"turn{turn:03d}-demographics.json")
        with open(demographics_path, "w") as f:
            json.dump(demographics_data, f, indent=2)
        print(f"Wrote {demographics_path} (era={demographics_data['era']['type']})")

        religion_path = os.path.join(args.out, f"turn{turn:03d}-religion.json")
        with open(religion_path, "w") as f:
            json.dump(religion_data, f, indent=2)
        print(f"Wrote {religion_path}")

        era_csv_column = _ERA_TYPE_TO_CSV_COLUMN.get(demographics_data["era"]["type"])
        weather_events, moment_events = parse_weather_and_moments(event_blocks.get(turn, []))
        for moment in moment_events:
            match = score_moment_text(moment["message"], historic_moments, era_csv_column)
            if match:
                moment.update(match)
            else:
                moment.update({"matched_name": None, "score": None, "is_major": False})

        weather_path = os.path.join(args.out, f"turn{turn:03d}-weather.json")
        with open(weather_path, "w") as f:
            json.dump({
                "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
                "events": weather_events,
            }, f, indent=2)
        print(f"Wrote {weather_path} ({len(weather_events)} events)")

        moments_path = os.path.join(args.out, f"turn{turn:03d}-moments.json")
        with open(moments_path, "w") as f:
            json.dump({
                "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
                "moments": moment_events,
            }, f, indent=2)
        print(f"Wrote {moments_path} ({len(moment_events)} moments, "
              f"{sum(1 for m in moment_events if m['is_major'])} major)")

        if tile_lines and terrains:
            width, height, grid = parse_map_tiles(tile_lines)
            map_stats = compute_map_stats(width, height, grid, terrains, resources, civs_data["players_roster"])
            map_data = {
                "meta": {"turn": turn, "source": "StatsDumper mod (Automation.log)"},
                "map": map_stats,
                "_raw_tiles": {
                    "terrains": terrains,
                    "features": features,
                    "resources": resources,
                    "grid": {f"{x},{y}": v for (x, y), v in grid.items()},
                },
            }
            map_path = os.path.join(args.out, f"turn{turn:03d}-map.json")
            with open(map_path, "w") as f:
                json.dump(map_data, f, indent=2)
            print(f"Wrote {map_path}")

            if render_map is not None:
                png_path = os.path.join(args.out, f"turn{turn:03d}.map.png")
                try:
                    render_data = {
                        **map_data,
                        "players_roster": civs_data["players_roster"],
                        "civs": civs_data["civs"],
                        "cities": cities,
                    }
                    render_map(render_data, png_path)
                    print(f"Wrote {png_path}")
                except Exception as e:
                    print(f"  map render failed for turn {turn}: {e!r}")
        else:
            print(f"  (no map data for turn {turn})")

    if render_map is not None:
        try:
            from make_map_video import make_map_video
            video_path = os.path.join(args.out, "map_timelapse.mp4")
            frame_count = make_map_video(Path(args.out), Path(video_path))
            print(f"Wrote {video_path} ({frame_count} frames)")
        except ImportError:
            print("  imageio/imageio-ffmpeg not installed -- skipping map_timelapse.mp4 "
                  "(pip install --break-system-packages imageio imageio-ffmpeg)")
        except Exception as e:
            print(f"  map_timelapse.mp4 failed: {e!r}")

    unit_op_lines = extract_unit_operations(args.log)
    if unit_op_lines:
        unitops_path = os.path.join(args.out, "AutomationUnitOperations.log")
        with open(unitops_path, "w") as f:
            f.write("Game Turn, Mode, Details\n")
            f.write("\n".join(unit_op_lines) + "\n")
        print(f"Wrote {unitops_path} ({len(unit_op_lines)} lines)")
    else:
        print("  (no CIV6UNITOPS_V2 lines found -- old mod version, or log truncated before turn 1)")

    if not event_blocks:
        print("  (no CIV6EVENTS_V2 lines found -- old mod version, or nothing notable happened)")


if __name__ == "__main__":
    main()
