"""Parses the raw dump files in this directory (map_lookup.txt, map_rows.txt,
players.txt) into per-civ territory/resource stats and a synthetic top-down
map render with zero fog of war. Run from this directory: python3 render_map.py
Requires Pillow (available in civ6-mcp's uv venv: `uv run python3 render_map.py`
from the civ6-mcp checkout, or `pip install --break-system-packages Pillow`).
"""
import os
from collections import Counter, defaultdict

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))

# --- parse lookup tables ---
terrains = {}
features = {}
resources = {}
with open(f"{HERE}/map_lookup.txt") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if parts[0] == "TERRAIN":
            terrains[int(parts[1])] = parts[2]
        elif parts[0] == "FEATURE":
            features[int(parts[1])] = parts[2]
        elif parts[0] == "RESOURCE":
            resources[int(parts[1])] = (parts[2], parts[3])

players = {}
with open(f"{HERE}/players.txt") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        _, pid, kind, name = line.split("|")
        players[int(pid)] = (kind, name)

# --- parse map rows ---
grid = {}  # (x,y) -> dict
with open(f"{HERE}/map_rows.txt") as f:
    for line in f:
        line = line.strip()
        if not line.startswith("ROW|"):
            continue
        _, y_str, data = line.split("|", 2)
        y = int(y_str)
        if not data:
            continue
        for x, cell in enumerate(data.split(";")):
            if not cell:
                continue
            t, owner, hills, mtn, water, river, coastal, feat, res, cont = cell.split(",")
            grid[(x, y)] = dict(
                terrain=int(t), owner=int(owner), hills=int(hills), mtn=int(mtn),
                water=int(water), river=int(river), coastal=int(coastal),
                feature=int(feat), resource=int(res), continent=int(cont),
            )

width = max(x for x, y in grid) + 1
height = max(y for x, y in grid) + 1
print(f"Parsed {len(grid)} tiles, grid {width}x{height}")

# --- aggregate stats ---
terrain_hist = Counter(terrains.get(t["terrain"], f"UNK_{t['terrain']}") for t in grid.values())
mountain_count = sum(1 for t in grid.values() if t["mtn"])
water_count = sum(1 for t in grid.values() if t["water"])
land_count = len(grid) - water_count
hills_count = sum(1 for t in grid.values() if t["hills"] and not t["mtn"])

per_civ = defaultdict(lambda: {"land": 0, "water": 0, "hills": 0, "mountain": 0, "resources": Counter()})
for t in grid.values():
    owner = t["owner"]
    if owner < 0:
        continue
    d = per_civ[owner]
    if t["water"]:
        d["water"] += 1
    else:
        d["land"] += 1
    if t["hills"] and not t["mtn"]:
        d["hills"] += 1
    if t["mtn"]:
        d["mountain"] += 1
    if t["resource"] >= 0:
        rname = resources.get(t["resource"], (f"UNK_{t['resource']}",))[0]
        d["resources"][rname] += 1

print("\n=== GLOBAL TERRAIN HISTOGRAM ===")
for name, count in terrain_hist.most_common():
    print(f"  {name}: {count}")
print(f"\nTotal tiles: {len(grid)}  Land: {land_count}  Water: {water_count}  "
      f"Hills(non-mtn): {hills_count}  Mountains: {mountain_count}")

print("\n=== PER-CIV TERRITORY ===")
for owner in sorted(per_civ):
    kind, name = players.get(owner, ("?", f"player{owner}"))
    d = per_civ[owner]
    total = d["land"] + d["water"]
    top_res = ", ".join(f"{k}:{v}" for k, v in d["resources"].most_common(5))
    print(f"  [{owner}] {name} ({kind}): {total} tiles total "
          f"(land={d['land']}, water={d['water']}, hills={d['hills']}, mountain={d['mountain']}) "
          f"top resources: {top_res or 'none'}")

unowned_land = sum(1 for t in grid.values() if t["owner"] < 0 and not t["water"])
unowned_water = sum(1 for t in grid.values() if t["owner"] < 0 and t["water"])
print(f"\nUnowned: land={unowned_land} water={unowned_water}")

# --- render map ---
civ_colors = {
    0: (220, 30, 30),     # Rome - red
    1: (240, 210, 40),    # Egypt - gold
    2: (60, 110, 230),    # Norway - blue
    3: (150, 90, 40),     # Ethiopia - brown
    4: (230, 150, 30),    # Nubia - orange
    5: (170, 70, 200),    # Sumeria - purple
    6: (0, 170, 140),     # Venice - teal
    7: (255, 255, 255),   # Kabul - white
    8: (0, 220, 0),       # Cardiff - bright green
    9: (0, 90, 200),      # Jerusalem - deep blue
    10: (255, 120, 180),  # Johannesburg - pink
    11: (255, 140, 0),    # Brussels - amber
    12: (120, 0, 0),      # Yerevan - maroon
    13: (0, 200, 220),    # Ngazargamu - cyan
    14: (200, 200, 0),    # Hong Kong - olive/yellow
    62: (150, 150, 150),  # Free cities - gray
    63: (30, 30, 30),     # Barbarians - near black
}


def terrain_base_color(terrain_name: str, water: bool) -> tuple[int, int, int]:
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


TILE = 14
img_w = width * TILE + TILE // 2
img_h = int(height * TILE * 0.78) + TILE
img = Image.new("RGB", (img_w, img_h), (10, 10, 15))
draw = ImageDraw.Draw(img)

for (x, y), t in grid.items():
    px = x * TILE + (TILE // 2 if y % 2 else 0)
    py = int(y * TILE * 0.78)
    terrain_name = terrains.get(t["terrain"], "")
    base = terrain_base_color(terrain_name, t["water"])
    draw.rectangle([px, py, px + TILE - 1, py + TILE - 1], fill=base)

    owner = t["owner"]
    if owner >= 0:
        oc = civ_colors.get(owner, (255, 0, 255))
        draw.rectangle([px, py, px + TILE - 1, py + TILE - 1], outline=oc, width=2)

    if t["mtn"]:
        draw.line([px + 2, py + TILE - 2, px + TILE // 2, py + 2], fill=(60, 60, 60), width=2)
        draw.line([px + TILE // 2, py + 2, px + TILE - 2, py + TILE - 2], fill=(60, 60, 60), width=2)
    elif t["hills"]:
        draw.arc([px + 2, py + 4, px + TILE - 2, py + TILE + 4], 180, 360, fill=(70, 70, 40), width=2)

    if t["resource"] >= 0:
        draw.ellipse([px + TILE // 2 - 2, py + TILE // 2 - 2, px + TILE // 2 + 2, py + TILE // 2 + 2],
                      fill=(255, 255, 0))

legend_h = 60
final = Image.new("RGB", (img_w, img_h + legend_h), (10, 10, 15))
final.paste(img, (0, 0))
ldraw = ImageDraw.Draw(final)
lx, ly = 4, img_h + 4
for pid in sorted(civ_colors):
    if pid not in per_civ:
        continue
    kind, name = players.get(pid, ("?", f"p{pid}"))
    color = civ_colors[pid]
    ldraw.rectangle([lx, ly, lx + 10, ly + 10], fill=color, outline=(255, 255, 255))
    label = name
    ldraw.text((lx + 14, ly - 1), label, fill=(255, 255, 255))
    lx += 14 + len(label) * 6 + 14
    if lx > img_w - 80:
        lx = 4
        ly += 14
        legend_h += 14

out_path = f"{HERE}/full_map_omniscient.png"
final.save(out_path)
print(f"\nSaved map render to {out_path} ({final.size})")
