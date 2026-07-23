"""Renders a synthetic top-down, zero-fog map PNG from a dump_stats.py JSON dict.

Used optionally by dump_stats.py --map-image. Can also be run standalone on
an existing dump: python3 render_map_lib.py path/to/dump.json out.png
"""
from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

JERSEY_COLORS_PATH = Path(__file__).resolve().parent.parent / "assets" / "colors" / "jersey-colors.md"
FALLBACK_COLOR = (255, 0, 255)

# City/civ names routinely include Latin Extended characters (Bogotá,
# Meroë, ...) that PIL's built-in bitmap default font can't render at all
# (silently draws a tofu box instead) -- try a couple of common,
# widely-preinstalled TTFs with real Unicode coverage first.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
]


def _load_font(size: int):
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _hex_to_rgb(hex_str: str) -> tuple[int, int, int] | None:
    hex_str = (hex_str or "").strip().lstrip("#")
    if len(hex_str) != 6:
        return None
    try:
        return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
    except ValueError:
        return None


def _load_jersey_palette() -> list[tuple[int, int, int]]:
    """Parse assets/colors/jersey-colors.md's table into a flat list of RGB
    tuples -- a hand-picked, maximally-distinguishable palette used as a
    fallback for any civ with no real color to use (city-states,
    barbarians, or a log from before this field existed)."""
    if not JERSEY_COLORS_PATH.exists():
        return []
    palette = []
    for line in JERSEY_COLORS_PATH.read_text().splitlines():
        m = re.match(r"\|\s*[^|]+?\s*\|\s*\\?#([0-9a-fA-F]{6})\s*\|", line)
        if m:
            rgb = _hex_to_rgb(m.group(1))
            if rgb:
                palette.append(rgb)
    return palette


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# City-state "type" (the trait that determines its envoy bonus -- Culture/
# Science/Trade/Religious/Militaristic/Industrial) -> accent color, used for
# its striped border and territory wash instead of a civ-style solid color.
CITY_STATE_TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "CULTURE": (255, 0, 255),
    "SCIENCE": (116, 163, 243),
    "COMMERCIAL": (247, 216, 1),
    "RELIGIOUS": (249, 249, 249),
    "INDUSTRIAL": (255, 129, 18),
    "MILITARY": (120, 0, 1),
}


def _resolve_player_styles(
    civs: list[dict],
    players_roster: list[dict],
    player_ids_needed: set[int],
) -> dict[int, dict]:
    """Returns {pid: {"border": rgb, "fill": rgb, "striped": bool}}.

    Majors: border = real SECONDARY color, fill = real PRIMARY color (from
    StatsDumper.lua's UI.GetPlayerColors() dump) -- swapped from what the
    names suggest because that's how Civ6 itself actually uses them: the
    territory hue in-game is the primary color, and the border/outline is
    the secondary color. Civ6's own lobby already refuses to start a game
    with two players sharing a color, so no collision-avoidance is needed
    for these.

    City-states: both border and fill come from their city-state type (see
    CITY_STATE_TYPE_COLORS), with the border striped black/type-color
    instead of solid, to read as a different kind of territory at a glance
    rather than just another civ color.

    Anyone left over (a major with no real color at all, e.g. a log from
    before this field existed) gets whichever assets/colors/jersey-colors.md
    entry is farthest from every color already assigned in this render,
    solid border+fill. Processes ids in a fixed (sorted) order so the same
    game's civs/city-states get the same colors render after render, turn
    after turn.
    """
    jersey = _load_jersey_palette()
    real_by_id: dict[int, tuple[tuple[int, int, int], tuple[int, int, int]]] = {}
    for c in civs:
        primary = _hex_to_rgb(c.get("primary_color", ""))
        secondary = _hex_to_rgb(c.get("secondary_color", ""))
        if primary:
            real_by_id[c["id"]] = (primary, secondary or primary)

    cstype_by_id = {p["id"]: p["city_state_type"] for p in players_roster if p.get("city_state_type")}

    styles: dict[int, dict] = {}
    used: list[tuple[int, int, int]] = []
    for pid in sorted(player_ids_needed):
        if pid in cstype_by_id:
            accent = CITY_STATE_TYPE_COLORS.get(cstype_by_id[pid], FALLBACK_COLOR)
            styles[pid] = {"border": accent, "fill": accent, "striped": True}
            used.append(accent)
            continue
        if pid in real_by_id:
            primary, secondary = real_by_id[pid]
            border, fill = secondary, primary
            styles[pid] = {"border": border, "fill": fill, "striped": False}
            used.append(border)
            continue
        best, best_score = FALLBACK_COLOR, -1.0
        for rgb in jersey:
            score = min((_color_distance(rgb, u) for u in used), default=999999.0)
            if score > best_score:
                best_score, best = score, rgb
        styles[pid] = {"border": best, "fill": best, "striped": False}
        used.append(best)
    return styles


def _draw_striped_line(draw: ImageDraw.ImageDraw, p0, p1, color_a, color_b, width: int, dash_len: float) -> None:
    """A two-color dashed line from p0 to p1 -- used for city-state borders
    (alternating black/type-color) since PIL has no native dash support."""
    x0, y0 = p0
    x1, y1 = p1
    length = math.hypot(x1 - x0, y1 - y0)
    if length == 0:
        return
    n = max(1, round(length / dash_len))
    for i in range(n):
        t0, t1 = i / n, (i + 1) / n
        seg = [(x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
               (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)]
        draw.line(seg, fill=(color_a if i % 2 == 0 else color_b), width=width)


def _hex_vertices(cx: float, cy: float, s: float) -> list[tuple[float, float]]:
    """Vertices of a pointy-top hexagon (point at top/bottom, flat left/right
    sides) centered at (cx, cy) with circumradius s - matches Civ6's own hex
    grid orientation (rows offset horizontally, the layout already used for
    tile positioning below)."""
    return [
        (cx + s * math.cos(math.radians(90 - 60 * i)),
         cy - s * math.sin(math.radians(90 - 60 * i)))
        for i in range(6)
    ]


def _pentagon_vertices(cx: float, cy: float, s: float) -> list[tuple[float, float]]:
    """Vertices of a point-up regular pentagon centered at (cx, cy) with
    circumradius s -- used for the capital city marker."""
    return [
        (cx + s * math.cos(math.radians(90 - 72 * i)),
         cy - s * math.sin(math.radians(90 - 72 * i)))
        for i in range(5)
    ]


# Edge i (connecting vertex i to vertex (i+1)%6, per _hex_vertices' vertex
# angles 90/30/-30/-90/-150/150 degrees) faces this Civ6 neighbor direction.
_EDGE_DIRECTIONS = ["NE", "E", "SE", "SW", "W", "NW"]

# Civ6 plot neighbor offsets for a horizontally-offset (odd/even row) hex
# grid -- standard formulas for this coordinate system, keyed by the
# current tile's row parity.
_NEIGHBOR_DELTAS_EVEN_Y = {
    "NE": (0, 1), "E": (1, 0), "SE": (0, -1),
    "SW": (-1, -1), "W": (-1, 0), "NW": (-1, 1),
}
_NEIGHBOR_DELTAS_ODD_Y = {
    "NE": (1, 1), "E": (1, 0), "SE": (1, -1),
    "SW": (0, -1), "W": (-1, 0), "NW": (0, 1),
}


def _neighbor(x: int, y: int, direction: str) -> tuple[int, int]:
    deltas = _NEIGHBOR_DELTAS_ODD_Y if y % 2 else _NEIGHBOR_DELTAS_EVEN_Y
    dx, dy = deltas[direction]
    return (x + dx, y + dy)


def _terrain_base_color(terrain_name: str) -> tuple[int, int, int]:
    if "OCEAN" in terrain_name:
        return (18, 45, 90)
    if "COAST" in terrain_name:
        return (45, 95, 150)
    if "SNOW" in terrain_name:
        return (235, 235, 240)
    if "TUNDRA" in terrain_name:
        return (140, 140, 110)
    if "DESERT" in terrain_name:
        return (215, 195, 120)
    if "PLAINS" in terrain_name:
        return (170, 165, 90)
    if "GRASS" in terrain_name:
        return (90, 140, 70)
    return (120, 120, 120)


def render_map(data: dict, out_path: str, tile: int = 28) -> None:
    raw = data.get("_raw_tiles")
    if raw is None:
        raise ValueError(
            "dump has no '_raw_tiles' section (was it written with --no-raw-tiles?) "
            "— can't render a map without the per-tile grid."
        )
    terrains = {int(k): v for k, v in raw["terrains"].items()}
    grid: dict[tuple[int, int], dict] = {}
    for coord, t in raw["grid"].items():
        x_str, y_str = coord.split(",")
        grid[(int(x_str), int(y_str))] = t

    m = data["map"]
    width, height = m["width"], m["height"]
    name_by_id = {p["id"]: p["name"] for p in data["players_roster"]}

    owned_ids = {t["owner"] for t in grid.values() if t["owner"] >= 0}
    styles = _resolve_player_styles(data.get("civs", []), data["players_roster"], owned_ids)

    img_w = width * tile + tile // 2
    img_h = int(height * tile * 0.78) + tile

    # Circumradius sized so a hex's flat left/right edges exactly span
    # `tile` pixels, matching the horizontal center-to-center spacing below -
    # same-row hexagons touch with no gap, same as the squares did before.
    hex_size = tile / math.sqrt(3)

    def _plot_geometry(x: int, y: int) -> tuple[float, float, int, int]:
        px = x * tile + (tile // 2 if y % 2 else 0)
        # Civ6's plot Y=0 is the map's SOUTH edge, increasing northward - the
        # opposite of image row order (row 0 = top). Flip here so north ends
        # up at the top of the rendered image, matching the in-game minimap.
        py = int((height - 1 - y) * tile * 0.78)
        return px + tile / 2, py + tile / 2, px, py

    # Pass 1: opaque terrain fill.
    img = Image.new("RGB", (img_w, img_h), (10, 10, 15))
    draw = ImageDraw.Draw(img)
    for (x, y), t in grid.items():
        cx, cy, px, py = _plot_geometry(x, y)
        hexagon = _hex_vertices(cx, cy, hex_size)
        terrain_name = terrains.get(t["terrain"], "")
        draw.polygon(hexagon, fill=_terrain_base_color(terrain_name))

    # Pass 2: translucent territory wash over owned tiles, composited on
    # top of terrain -- closer to how territory looks in-game (a colored
    # tint over the land) than a hard-filled tile.
    TERRITORY_ALPHA = 90
    overlay = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for (x, y), t in grid.items():
        owner = t["owner"]
        if owner < 0:
            continue
        cx, cy, _px, _py = _plot_geometry(x, y)
        hexagon = _hex_vertices(cx, cy, hex_size)
        fill_color = styles.get(owner, {}).get("fill", FALLBACK_COLOR)
        odraw.polygon(hexagon, fill=fill_color + (TERRITORY_ALPHA,))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Pass 3: terrain decorations, then only the OUTER edges of each
    # territory (an edge is drawn only where the neighbor across it belongs
    # to someone else, or is off the map entirely) -- not every edge of
    # every owned hex, which is what let two adjacent same-owner tiles'
    # fills silently paint over each other's border along their shared
    # edge. Borders are drawn last so they're never overpainted by anything.
    deco_inset = max(2, int(tile * 0.14))
    deco_width = max(2, int(tile * 0.12))
    resource_r = max(2, int(tile * 0.14))
    border_width = max(2, int(tile * 0.12))
    for (x, y), t in grid.items():
        cx, cy, px, py = _plot_geometry(x, y)

        if t["mountain"]:
            draw.line([px + deco_inset, py + tile - deco_inset, px + tile // 2, py + deco_inset],
                       fill=(60, 60, 60), width=deco_width)
            draw.line([px + tile // 2, py + deco_inset, px + tile - deco_inset, py + tile - deco_inset],
                       fill=(60, 60, 60), width=deco_width)
        elif t["hills"]:
            draw.arc([px + deco_inset, py + deco_inset * 2, px + tile - deco_inset, py + tile + deco_inset * 2],
                      180, 360, fill=(70, 70, 40), width=deco_width)

        if t["resource"] >= 0:
            draw.ellipse([cx - resource_r, cy - resource_r, cx + resource_r, cy + resource_r], fill=(255, 255, 0))

        owner = t["owner"]
        if owner < 0:
            continue
        hexagon = _hex_vertices(cx, cy, hex_size)
        style = styles.get(owner, {"border": FALLBACK_COLOR, "striped": False})
        for edge_idx, direction in enumerate(_EDGE_DIRECTIONS):
            neighbor_coord = _neighbor(x, y, direction)
            neighbor_tile = grid.get(neighbor_coord)
            neighbor_owner = neighbor_tile["owner"] if neighbor_tile else -1
            if neighbor_owner != owner:
                v0 = hexagon[edge_idx]
                v1 = hexagon[(edge_idx + 1) % 6]
                if style["striped"]:
                    _draw_striped_line(draw, v0, v1, (0, 0, 0), style["border"],
                                        border_width, dash_len=max(3, tile * 0.18))
                else:
                    draw.line([v0, v1], fill=style["border"], width=border_width)

    # Pass 4: city markers + name labels, drawn last so they always sit on
    # top of terrain/territory/borders. Capitals get a pentagon, other
    # cities a circle, both filled in the owner's color with a white
    # outline for contrast against any background.
    city_font_size = max(10, int(tile * 0.5))
    city_font = _load_font(city_font_size)
    marker_r = tile * 0.32
    for city in data.get("cities", []):
        cx, cy, _px, _py = _plot_geometry(city["x"], city["y"])
        color = styles.get(city.get("owner_id", -1), {}).get("border", FALLBACK_COLOR)
        if city.get("is_capital"):
            pentagon = _pentagon_vertices(cx, cy, marker_r * 1.15)
            draw.polygon(pentagon, fill=color, outline=(255, 255, 255))
        else:
            draw.ellipse([cx - marker_r, cy - marker_r, cx + marker_r, cy + marker_r],
                         fill=color, outline=(255, 255, 255))

        name = city.get("name", "")
        if not name:
            continue
        text_x, text_y = cx + marker_r + 3, cy - city_font_size / 2
        for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            draw.text((text_x + ox, text_y + oy), name, font=city_font, fill=(0, 0, 0))
        draw.text((text_x, text_y), name, font=city_font, fill=(255, 255, 255))

    # Turn indicator, top-left corner -- drawn last so it always sits on
    # top of everything else, same outline treatment as city labels.
    turn = data.get("meta", {}).get("turn")
    if turn is not None:
        turn_font_size = max(14, int(tile * 0.6))
        turn_font = _load_font(turn_font_size)
        label = f"Turn {turn}"
        tx, ty = 6, 6
        for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
            draw.text((tx + ox, ty + oy), label, font=turn_font, fill=(0, 0, 0))
        draw.text((tx, ty), label, font=turn_font, fill=(255, 255, 255))

    legend_font_size = max(11, int(tile * 0.45))
    legend_font = _load_font(legend_font_size)
    swatch = legend_font_size
    row_h = swatch + 8
    legend_h = row_h
    final = Image.new("RGB", (img_w, img_h + legend_h), (10, 10, 15))
    final.paste(img, (0, 0))
    ldraw = ImageDraw.Draw(final)
    lx, ly = 4, img_h + 4
    territory_ids = {t["id"] for t in m["territory"]}
    for pid in sorted(styles):
        if pid not in territory_ids:
            continue
        name = name_by_id.get(pid, f"p{pid}")
        color = styles[pid]["border"]
        ldraw.rectangle([lx, ly, lx + swatch, ly + swatch], fill=color, outline=(255, 255, 255))
        ldraw.text((lx + swatch + 4, ly), name, font=legend_font, fill=(255, 255, 255))
        lx += swatch + 4 + len(name) * (legend_font_size * 0.6) + 14
        if lx > img_w - 100:
            lx = 4
            ly += row_h
            legend_h += row_h
            final_h_needed = img_h + legend_h
            if final_h_needed > final.height:
                bigger = Image.new("RGB", (img_w, final_h_needed), (10, 10, 15))
                bigger.paste(final, (0, 0))
                final = bigger
                ldraw = ImageDraw.Draw(final)

    final.save(out_path)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: render_map_lib.py <dump.json> <out.png>")
        sys.exit(1)
    with open(sys.argv[1]) as f:
        data = json.load(f)
    render_map(data, sys.argv[2])
    print(f"Wrote {sys.argv[2]}")
