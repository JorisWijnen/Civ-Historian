#!/usr/bin/env python3
"""Post a Civ Historian session's newspaper image + article text to Discord
via an incoming webhook.

Requires:
    export DISCORD_WEBHOOK_URL=...  (Server Settings -> Integrations ->
                                      Webhooks -> New Webhook -> Copy URL)

Usage:
    python3 scripts/post_discord.py --image sessions/X/newspaper.png \
        --article sessions/X/article.md --label session_X
    python3 scripts/post_discord.py --image sessions/X/newspaper.png --label session_X
        # (no --article -> image only, no text follow-up)
"""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path

import requests

DISCORD_MESSAGE_LIMIT = 2000
_RATE_LIMIT_RETRIES = 3


def _strip_markdown_hr(text: str) -> str:
    """Drop standalone markdown horizontal-rule lines ('---', '***', '___')
    -- Discord renders these as literal text rather than a divider, so a
    lone blank line between sections reads better than the raw dashes."""
    lines = [ln for ln in text.split("\n") if not re.fullmatch(r"[-*_]{3,}", ln.strip())]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def _post_with_retry(webhook_url: str, **kwargs) -> None:
    """POST to the webhook, retrying once per Discord-signaled rate limit
    (429 + Retry-After) rather than failing the whole post over a transient
    burst -- a handful of chunked messages sent back-to-back can trip this."""
    for attempt in range(_RATE_LIMIT_RETRIES + 1):
        resp = requests.post(webhook_url, timeout=30, **kwargs)
        if resp.status_code != 429:
            resp.raise_for_status()
            return
        if attempt == _RATE_LIMIT_RETRIES:
            resp.raise_for_status()
        retry_after = 1.0
        try:
            retry_after = float(resp.json().get("retry_after", 1.0))
        except Exception:
            pass
        time.sleep(retry_after)


def _chunk_text(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> list[str]:
    """Split article text into <=limit-char chunks, preferring paragraph
    breaks so a chunk boundary doesn't land mid-sentence. Falls back to a
    hard slice only if a single paragraph itself exceeds the limit."""
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(para) > limit:
            chunks.append(para[:limit])
            para = para[limit:]
        current = para
    if current:
        chunks.append(current)
    return chunks


def post_to_discord(
    webhook_url: str,
    image_path: Path,
    article_text: str,
    label: str | None = None,
    include_article_text: bool = True,
) -> None:
    """Post the newspaper image (as a file attachment), optionally followed
    by the full article text (chunked to Discord's 2000-char message limit)
    as one or more follow-up messages on the same webhook. Now that OpenAI's
    newspaper.png actually renders the article legibly, the text follow-up
    is redundant for some setups -- include_article_text=False skips it."""
    if not webhook_url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL is not set -- create an incoming webhook "
            "(Server Settings -> Integrations -> Webhooks) and set it in "
            "the environment."
        )
    if not image_path.exists():
        raise RuntimeError(f"{image_path} not found -- nothing to post")

    caption = f"\U0001f5de️ {label}" if label else "\U0001f5de️ New Civ Historian session"
    with open(image_path, "rb") as f:
        _post_with_retry(
            webhook_url,
            data={"content": caption},
            files={"file": (image_path.name, f, "image/png")},
        )

    if not include_article_text:
        return

    for chunk in _chunk_text(_strip_markdown_hr(article_text)):
        _post_with_retry(webhook_url, json={"content": chunk})


def main() -> None:
    ap = argparse.ArgumentParser(description="Post a newspaper image + article to Discord")
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--article", type=Path, default=None,
                     help="Path to article.md -- if given, its text is posted as follow-up "
                          "message(s) after the image; if omitted, only the image is posted")
    ap.add_argument("--label", default=None)
    ap.add_argument("--webhook-url", default=os.environ.get("DISCORD_WEBHOOK_URL"))
    args = ap.parse_args()

    article_text = args.article.read_text() if args.article else ""
    post_to_discord(
        args.webhook_url, args.image, article_text,
        label=args.label, include_article_text=args.article is not None,
    )
    print("Posted to Discord.")


if __name__ == "__main__":
    main()
