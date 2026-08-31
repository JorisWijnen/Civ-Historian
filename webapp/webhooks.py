"""Per-user Discord webhook storage: remembered name -> URL pairs, persisted
as one JSON file per Authentik user under WEBHOOKS_DIR (see app.py).

Keyed by the X-authentik-uid header injected by the Authentik reverse-proxy
that sits in front of this app -- not by anything the request body claims,
so one user can never read or overwrite another user's saved webhooks.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ANONYMOUS_UID = "anonymous"
# X-authentik-uid is normally a small int or a uuid, but treat it as
# untrusted input: it becomes part of a filename, so collapse anything
# outside a conservative safe set instead of trusting it verbatim.
_UNSAFE_UID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def uid_from_headers(headers) -> str:
    """Resolve the per-user storage key from request headers. Falls back to
    a shared 'anonymous' bucket (rather than erroring) when the header is
    absent, e.g. local dev without the Authentik proxy in front."""
    raw = (headers.get("X-authentik-uid") or "").strip()
    if not raw:
        return ANONYMOUS_UID
    return _UNSAFE_UID_CHARS.sub("_", raw)[:128] or ANONYMOUS_UID


class WebhookStore:
    """Flat JSON-file-per-user store of {name: url}. Single gunicorn worker
    (see docs/webapp.md) so no cross-process locking -- write via a
    temp-file-then-rename swap so a reader never sees a half-written file."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, uid: str) -> Path:
        return self.base_dir / f"{uid}.json"

    def load(self, uid: str) -> dict[str, str]:
        path = self._path(uid)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def _save_all(self, uid: str, webhooks: dict[str, str]) -> None:
        path = self._path(uid)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(webhooks, indent=2, ensure_ascii=False))
        tmp.replace(path)

    def add(self, uid: str, name: str, url: str) -> dict[str, str]:
        """Insert or overwrite (name -> url); re-saving an existing name
        just updates its URL rather than erroring or duplicating."""
        webhooks = self.load(uid)
        webhooks[name] = url
        self._save_all(uid, webhooks)
        return webhooks

    def delete(self, uid: str, name: str) -> dict[str, str]:
        webhooks = self.load(uid)
        webhooks.pop(name, None)
        self._save_all(uid, webhooks)
        return webhooks
