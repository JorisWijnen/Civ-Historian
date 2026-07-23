#!/usr/bin/env python3
"""Civ Historian pipeline, steps 3-8: given the pushed Automation.log
sitting in incoming/, parse it into a fresh sessions/<name>/ folder,
generate the article + image prompt via two headless `claude -p` calls,
renders headliner.png via Gemini (nano_banana.py) and newspaper.png via
OpenAI (gpt-image-2, see openai_image.py) -- mixed backend on purpose, each
picked for whichever image it was judged better at -- and posts
newspaper.png + article.md to Discord.

Posting to Discord requires an incoming webhook URL:
    export DISCORD_WEBHOOK_URL=...  (Server Settings -> Integrations ->
                                      Webhooks -> New Webhook -> Copy URL)
If unset, or the post otherwise fails, the pipeline logs a warning and
still exits successfully -- article.md/newspaper.png are already on disk
by that point.

By default the full article.md text is also posted as follow-up message(s)
after the image. Set DISCORD_POST_ARTICLE_TEXT=0 (or false/no) to post the
image only -- OpenAI's newspaper.png renders the article text legibly
enough that the separate text post is often redundant now.

Usage:
    python3 scripts/run_pipeline.py
    python3 scripts/run_pipeline.py --session-name session_20260718_220000
    python3 scripts/run_pipeline.py --skip-images
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INCOMING = REPO_ROOT / "incoming"
SESSIONS = REPO_ROOT / "sessions"
PROMPTS = REPO_ROOT / "assets" / "prompts"
LEADERS = REPO_ROOT / "assets" / "leaders"

LOG_FILES = ["Automation.log"]
PROMPT_TEMPLATES = ["article.prompt.txt", "prompt_gen.prompt.txt", "newspaper_image.prompt.txt"]
IMAGE_PROMPT_NAME = "openai_image_prompt.txt"


def new_session_name() -> str:
    return "session_" + time.strftime("%Y%m%d_%H%M%S")


def setup_session(session_name: str) -> Path:
    """Step 3: parse the incoming log, create the session folder, copy in
    the prompt templates with SESSIONX substituted, and archive the raw
    source logs out of incoming/ (so the *next* run always starts from a
    clean log - a leftover earlier game's data bleeding into a later run's
    parse has bitten us twice already doing this by hand)."""
    session_dir = SESSIONS / session_name
    session_dir.mkdir(parents=True, exist_ok=False)

    automation_log = INCOMING / "Automation.log"
    if not automation_log.exists():
        raise FileNotFoundError(f"{automation_log} not found - nothing to process")

    print(f"[{session_name}] parsing {automation_log} ...")
    subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "scripts" / "parse_mod_log.py"),
            "--log", str(automation_log),
            "--out", str(session_dir),
        ],
        check=True, cwd=REPO_ROOT,
    )

    for template_name in PROMPT_TEMPLATES:
        text = (PROMPTS / template_name).read_text()
        text = text.replace("SESSIONX", session_name)
        (session_dir / template_name).write_text(text)

    raw_dir = session_dir / "raw_logs"
    raw_dir.mkdir()
    for name in LOG_FILES:
        src = INCOMING / name
        if src.exists():
            shutil.move(str(src), str(raw_dir / name))

    return session_dir


def run_claude(prompt_path: Path) -> None:
    """Steps 4/5: run a prompt file through the local claude CLI, headless.
    --dangerously-skip-permissions is required since nothing is here to
    approve tool calls interactively; acceptable because the only inputs
    are our own generated stats/prompt files, not untrusted external data."""
    prompt_text = prompt_path.read_text()
    print(f"  calling claude -p on {prompt_path.name} ...")
    result = subprocess.run(
        ["claude", "-p", "--dangerously-skip-permissions", "--output-format", "json", prompt_text],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed on {prompt_path.name}:\n{result.stderr}")


def match_leader_images(prompt_text: str) -> list[Path]:
    """The image-generation prompts mention leaders by name but can't
    browse the filesystem themselves - this matches those names against
    assets/leaders/ so the actual portrait files can be attached as
    reference images."""
    matches = []
    if not LEADERS.exists():
        return matches
    for img in sorted(LEADERS.iterdir()):
        if not img.is_file():
            continue
        words = img.stem.replace("-", " ").split()
        if all(re.search(rf"\b{re.escape(w)}\b", prompt_text, re.IGNORECASE) for w in words):
            matches.append(img)
    return matches


def generate_images(session_dir: Path) -> None:
    """Steps 6/7: image generation calls -- mixed backend on purpose.
    headliner.png uses Gemini (nano_banana.py) since its output won out for
    a single character-focused scene; newspaper.png uses OpenAI
    (openai_image.py) since its text/layout handling won out for the dense
    front-page composite. Raises RuntimeError (caller decides what to do)
    if GEMINI_API_KEY isn't set yet -- that failure happens on the very
    first call below, before there's anything to fall back to."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from nano_banana import generate_image as generate_image_gemini  # noqa: E402
    from openai_image import generate_image as generate_image_openai  # noqa: E402

    image_prompt_path = session_dir / IMAGE_PROMPT_NAME
    if not image_prompt_path.exists():
        raise FileNotFoundError(f"{image_prompt_path} missing - step 5 didn't produce it")

    prompt_text = image_prompt_path.read_text()
    ref_images = match_leader_images(prompt_text)
    print(f"  leader portraits matched: {[p.name for p in ref_images]}")

    headliner_path = session_dir / "headliner.png"
    print("  generating headliner.png (Gemini) ...")
    generate_image_gemini(prompt_text, ref_images, headliner_path)

    newspaper_prompt_path = session_dir / "newspaper_image.prompt.txt"
    newspaper_text = newspaper_prompt_path.read_text()
    article_text = (session_dir / "article.md").read_text()
    # Full article wrapped in explicit BEGIN/END markers, with the actual
    # instruction *after* the content (not before) -- matches a manually
    # tested prompt structure that produced a genuinely good full front
    # page with gpt-image-2, vs. the truncated/garbled results earlier
    # attempts (instructions-first, excerpt-only) gave with gpt-image-1.
    full_prompt = (
        "----- START ARTICLE.MD -----\n"
        + article_text
        + "\n----- END ARTICLE.MD -----\n"
        + newspaper_text
    )

    print("  generating newspaper.png (OpenAI) ...")
    newspaper_path = session_dir / "newspaper.png"
    discord_image_path = headliner_path
    try:
        generate_image_openai(full_prompt, [headliner_path], newspaper_path)
        discord_image_path = newspaper_path
    except Exception as e:
        # headliner.png already exists at this point -- post that instead
        # of skipping Discord entirely over a front-page composite failure.
        print(f"  newspaper.png generation failed, falling back to headliner.png: {e}",
              file=sys.stderr)

    from post_discord import post_to_discord  # noqa: E402

    include_article_text = os.environ.get("DISCORD_POST_ARTICLE_TEXT", "1").strip().lower() \
        not in ("0", "false", "no")
    try:
        suffix = " + article.md" if include_article_text else " (article.md text skipped)"
        print(f"  posting {discord_image_path.name}{suffix} to Discord ...")
        post_to_discord(
            os.environ.get("DISCORD_WEBHOOK_URL"),
            discord_image_path,
            article_text,
            label=session_dir.name,
            include_article_text=include_article_text,
        )
    except Exception as e:
        # Local artifacts (article.md, headliner.png/newspaper.png) are
        # already done at this point -- a Discord hiccup (missing webhook,
        # network blip) shouldn't take down an otherwise-successful run.
        print(f"  Discord post skipped: {e}", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Civ Historian pipeline end-to-end")
    ap.add_argument("--session-name", default=None,
                     help="Resume/target a specific sessions/<name> (default: new timestamped session)")
    ap.add_argument("--skip-images", action="store_true",
                     help=f"Stop after article.md/{IMAGE_PROMPT_NAME} (steps 3-5 only)")
    args = ap.parse_args()

    if args.session_name:
        session_dir = SESSIONS / args.session_name
        if session_dir.exists():
            print(f"Resuming existing session: {session_dir}")
        else:
            session_dir = setup_session(args.session_name)
    else:
        session_dir = setup_session(new_session_name())

    if not (session_dir / "article.md").exists():
        run_claude(session_dir / "article.prompt.txt")
    else:
        print("  article.md already exists, skipping step 4")

    if not (session_dir / IMAGE_PROMPT_NAME).exists():
        run_claude(session_dir / "prompt_gen.prompt.txt")
    else:
        print(f"  {IMAGE_PROMPT_NAME} already exists, skipping step 5")

    if args.skip_images:
        print(f"Done (images skipped). Session: {session_dir}")
        return

    try:
        generate_images(session_dir)
    except RuntimeError as e:
        print(f"Image generation skipped: {e}", file=sys.stderr)
        print(
            f"article.md and {IMAGE_PROMPT_NAME} are ready in {session_dir}. "
            f"Re-run with --session-name {session_dir.name} once GEMINI_API_KEY/"
            f"OPENAI_API_KEY are set to pick up from here.",
            file=sys.stderr,
        )
        return

    print(f"Done. Session: {session_dir}")


if __name__ == "__main__":
    main()
