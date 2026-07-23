#!/usr/bin/env python3
"""Calls Gemini's image-generation model (nicknamed "Nano Banana") to turn a
text prompt plus reference images into a generated PNG.

Used for the headliner illustration specifically -- Gemini's output for
that (a single character-focused scene) was judged better than OpenAI's,
while OpenAI (openai_image.py) is used for the newspaper front-page
composite, where its handling of dense text/layout has won out instead.
Mixed-backend on purpose, not an oversight.

Requires:
    pip install --break-system-packages google-genai Pillow
    export GEMINI_API_KEY=...   (from https://aistudio.google.com/apikey)

Usage:
    python3 scripts/nano_banana.py --prompt-file sessions/X/openai_image_prompt.txt \\
        --ref-image assets/leaders/dido.webp --ref-image assets/leaders/wilhelmina.webp \\
        --out sessions/X/headliner.png
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

DEFAULT_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


def generate_image(
    prompt_text: str,
    reference_images: list[Path],
    out_path: Path,
    model: str = DEFAULT_MODEL,
) -> None:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set - get one at "
            "https://aistudio.google.com/apikey and set it in the environment."
        )

    from google import genai
    from PIL import Image

    client = genai.Client(api_key=api_key)

    contents: list = [prompt_text]
    for img_path in reference_images:
        if not img_path.exists():
            print(f"  warning: reference image not found, skipping: {img_path}", file=sys.stderr)
            continue
        contents.append(Image.open(img_path))

    response = client.models.generate_content(model=model, contents=contents)

    if not response.candidates:
        raise RuntimeError(
            f"Gemini returned no candidates at all (model={model}). "
            f"prompt_feedback={response.prompt_feedback!r}"
        )

    candidate = response.candidates[0]

    if candidate.content is None:
        # This is what a safety/policy block (or other refusal) looks like -
        # candidate.content is None rather than an empty parts list, so
        # accessing .parts directly raises a bare, unhelpful AttributeError.
        raise RuntimeError(
            f"Gemini returned an empty candidate, no image generated "
            f"(model={model}). finish_reason={candidate.finish_reason!r} "
            f"safety_ratings={candidate.safety_ratings!r} "
            f"prompt_feedback={response.prompt_feedback!r}"
        )

    for part in candidate.content.parts:
        if part.inline_data is not None:
            out_path.write_bytes(part.inline_data.data)
            return
        if getattr(part, "text", None):
            print(f"  Gemini returned text instead of an image: {part.text!r}", file=sys.stderr)

    raise RuntimeError(
        f"Gemini response had no image data (model={model}). "
        f"finish_reason={candidate.finish_reason!r} "
        f"safety_ratings={candidate.safety_ratings!r}"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate an image via Gemini/Nano Banana")
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
