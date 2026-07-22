#!/usr/bin/env python3
"""Calls OpenAI's image-generation model (gpt-image-1) to turn a text
prompt plus reference images into a generated PNG. Used to call Gemini
instead (nicknamed "Nano Banana") -- switched to OpenAI because its output
was noticeably better for this newspaper/illustration use case.

Requires:
    export OPENAI_API_KEY=...   (https://platform.openai.com/api-keys)

Usage:
    python3 scripts/openai_image.py --prompt-file sessions/X/openai_image_prompt.txt \\
        --ref-image assets/leaders/dido.webp --ref-image assets/leaders/wilhelmina.webp \\
        --out sessions/X/headliner.png
"""
from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import sys
from pathlib import Path

import requests

DEFAULT_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
DEFAULT_SIZE = os.environ.get("OPENAI_IMAGE_SIZE", "1024x1024")
GENERATIONS_URL = "https://api.openai.com/v1/images/generations"
EDITS_URL = "https://api.openai.com/v1/images/edits"


def generate_image(
    prompt_text: str,
    reference_images: list[Path],
    out_path: Path,
    model: str = DEFAULT_MODEL,
) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set - get one at "
            "https://platform.openai.com/api-keys and set it in the environment."
        )
    headers = {"Authorization": f"Bearer {api_key}"}

    existing_refs: list[Path] = []
    for img_path in reference_images:
        if not img_path.exists():
            print(f"  warning: reference image not found, skipping: {img_path}", file=sys.stderr)
            continue
        existing_refs.append(img_path)

    # With reference images (leader portraits, or headliner.png when
    # composing the newspaper front page): /images/edits, which gpt-image-1
    # treats as guidance images -- it supports several input images per
    # call, sent as repeated "image[]" multipart fields. With none at all
    # (e.g. no leader portrait matched a headliner prompt): plain
    # /images/generations, which is text-only and has no "image" field.
    if existing_refs:
        opened = [open(p, "rb") for p in existing_refs]
        try:
            files = [
                ("image[]", (p.name, f, mimetypes.guess_type(p.name)[0] or "application/octet-stream"))
                for p, f in zip(existing_refs, opened)
            ]
            data = {"model": model, "prompt": prompt_text, "size": DEFAULT_SIZE}
            resp = requests.post(EDITS_URL, headers=headers, data=data, files=files, timeout=180)
        finally:
            for f in opened:
                f.close()
    else:
        # Unlike /edits (which needs multipart for the file upload),
        # /generations only accepts a JSON body -- a form-encoded POST here
        # gets rejected outright with "unsupported_content_type".
        data = {"model": model, "prompt": prompt_text, "size": DEFAULT_SIZE}
        resp = requests.post(GENERATIONS_URL, headers=headers, json=data, timeout=180)

    if resp.status_code != 200:
        raise RuntimeError(
            f"OpenAI image API returned {resp.status_code} (model={model}): {resp.text[:1000]}"
        )

    payload = resp.json()
    items = payload.get("data") or []
    if not items or not items[0].get("b64_json"):
        raise RuntimeError(f"OpenAI response had no image data (model={model}): {payload!r}")

    out_path.write_bytes(base64.b64decode(items[0]["b64_json"]))


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate an image via OpenAI (gpt-image-1)")
    ap.add_argument("--prompt-file", required=True, type=Path)
    ap.add_argument("--ref-image", action="append", default=[], type=Path, dest="ref_images")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    prompt_text = args.prompt_file.read_text()
    generate_image(prompt_text, args.ref_images, args.out, model=args.model)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
