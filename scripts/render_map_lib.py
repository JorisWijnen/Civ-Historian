"""Renders a synthetic top-down, zero-fog map PNG from a dump_stats.py JSON dict.

Used optionally by dump_stats.py --map-image. Can also be run standalone on
an existing dump: python3 render_map_lib.py path/to/dump.json out.png
"""
from __future__ import annotations

import json
import math
import sys

from PIL import Image, ImageDraw

# Stable color per player id so repeated dumps of the same game stay
# visually consistent across turns.
CIV_COLORS = {
    0: (220, 30, 30), 1: (240, 210, 40), 2: (60, 110, 230), 3: (150, 90, 40),
    4: (230, 150, 30), 5: (170, 70, 200), 6: (0, 170, 140), 7: (255, 255, 255),
    8: (0, 220, 0), 9: (0, 90, 200), 10: (255, 120, 180), 11: (255, 140, 0),
    12: (120, 0, 0), 13: (0, 200, 220), 14: (200, 200, 0), 15: (0, 100, 100),
    16: (180, 180, 255), 17: (255, 200, 200), 18: (100, 255, 200),
    62: (150, 150, 150), 63: (30, 30, 30),
}
FALLBACK_COLOR = (255, 0, 255)


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


def render_map(data: dict, out_path: str, tile: int = 14) -> None:
    raw = data.get("_raw_tiles")
    if raw is None:
        raise ValueError(
            "dump has no '_raw_tiles' section (was it written with --no-raw-tiles?) "
            "— can't render a map without the per-tile grid."
        )
    terrains = {int(k): v for k, v in raw["terrains"].items()}
    grid = {}
    for coord, t in raw["grid"].items():
        x_str, y_str = coord.split(",")
        grid[(int(x_str), int(y_str))] = t

    m = data["map"]
    width, height = m["width"], m["height"]
    name_by_id = {p["id"]: p["name"] for p in data["players_roster"]}

    img_w = width * tile + tile // 2
    img_h = int(height * tile * 0.78) + tile
    img = Image.new("RGB", (img_w, img_h), (10, 10, 15))
    draw = ImageDraw.Draw(img)

    # Circumradius sized so a hex's flat left/right edges exactly span
    # `tile` pixels, matching the horizontal center-to-center spacing below -
    # same-row hexagons touch with no gap, same as the squares did before.
    hex_size = tile / math.sqrt(3)

    for (x, y), t in grid.items():
        px = x * tile + (tile // 2 if y % 2 else 0)
        # Civ6's plot Y=0 is the map's SOUTH edge, increasing northward - the
        # opposite of image row order (row 0 = top). Flip here so north ends
        # up at the top of the rendered image, matching the in-game minimap.
        py = int((height - 1 - y) * tile * 0.78)
        cx, cy = px + tile / 2, py + tile / 2
        hexagon = _hex_vertices(cx, cy, hex_size)

        terrain_name = terrains.get(t["terrain"], "")
        draw.polygon(hexagon, fill=_terrain_base_color(terrain_name))

        owner = t["owner"]
        if owner >= 0:
            draw.polygon(hexagon, outline=CIV_COLORS.get(owner, FALLBACK_COLOR), width=2)

        if t["mountain"]:
            draw.line([px + 2, py + tile - 2, px + tile // 2, py + 2], fill=(60, 60, 60), width=2)
            draw.line([px + tile // 2, py + 2, px + tile - 2, py + tile - 2], fill=(60, 60, 60), width=2)
        elif t["hills"]:
            draw.arc([px + 2, py + 4, px + tile - 2, py + tile + 4], 180, 360, fill=(70, 70, 40), width=2)

        if t["resource"] >= 0:
            draw.ellipse([cx - 2, cy - 2, cx + 2, cy + 2], fill=(255, 255, 0))

    legend_h = 60
    final = Image.new("RGB", (img_w, img_h + legend_h), (10, 10, 15))
    final.paste(img, (0, 0))
    ldraw = ImageDraw.Draw(final)
    lx, ly = 4, img_h + 4
    territory_ids = {t["id"] for t in m["territory"]}
    for pid in sorted(CIV_COLORS):
        if pid not in territory_ids:
            continue
        name = name_by_id.get(pid, f"p{pid}")
        color = CIV_COLORS[pid]
        ldraw.rectangle([lx, ly, lx + 10, ly + 10], fill=color, outline=(255, 255, 255))
        ldraw.text((lx + 14, ly - 1), name, fill=(255, 255, 255))
        lx += 14 + len(name) * 6 + 14
        if lx > img_w - 80:
            lx = 4
            ly += 14
            legend_h += 14
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
