#!/usr/bin/env python3
"""Assembles a session's per-turn map PNGs (turnNNN.map.png, written by
render_map_lib.render_map) into an MP4 timelapse, in turn order, at a fixed
duration per frame.

Requires:
    pip install --break-system-packages imageio imageio-ffmpeg
    (imageio-ffmpeg bundles its own static ffmpeg binary -- no system
    ffmpeg install needed.)

Usage:
    python3 scripts/make_map_video.py sessions/session_X
    python3 scripts/make_map_video.py sessions/session_X --seconds-per-turn 0.5
    python3 scripts/make_map_video.py sessions/session_X --out sessions/session_X/map_timelapse.mp4
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_SECONDS_PER_TURN = 0.5
_TURN_RE = re.compile(r"turn(\d+)\.map\.png$")


def make_map_video(
    session_dir: Path,
    out_path: Path,
    seconds_per_turn: float = DEFAULT_SECONDS_PER_TURN,
) -> int:
    """Writes out_path, returns the number of frames written. Raises
    RuntimeError if no turn*.map.png files exist in session_dir."""
    import imageio.v2 as imageio

    frames = sorted(
        (p for p in session_dir.glob("turn*.map.png") if _TURN_RE.search(p.name)),
        key=lambda p: int(_TURN_RE.search(p.name).group(1)),
    )
    if not frames:
        raise RuntimeError(f"no turn*.map.png files found in {session_dir}")

    fps = 1.0 / seconds_per_turn
    # macro_block_size=2 (rather than imageio-ffmpeg's default 16, or 1/off)
    # rounds dimensions up to the nearest even number -- the minimum
    # libx264's yuv420p output actually requires. Our map images are sized
    # from tile*width/height arithmetic with no guarantee of landing on
    # *any* particular multiple, and an odd height/width makes libx264
    # refuse to encode at all ("height not divisible by 2").
    writer = imageio.get_writer(str(out_path), fps=fps, macro_block_size=2)
    try:
        for frame_path in frames:
            writer.append_data(imageio.imread(frame_path))
    finally:
        writer.close()
    return len(frames)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session_dir", type=Path)
    ap.add_argument("--seconds-per-turn", type=float, default=DEFAULT_SECONDS_PER_TURN)
    ap.add_argument("--out", type=Path, default=None,
                     help="Output path (default: <session_dir>/map_timelapse.mp4)")
    args = ap.parse_args()

    out_path = args.out or (args.session_dir / "map_timelapse.mp4")
    count = make_map_video(args.session_dir, out_path, args.seconds_per_turn)
    print(f"Wrote {out_path} ({count} frames, {args.seconds_per_turn}s/turn)")


if __name__ == "__main__":
    main()
