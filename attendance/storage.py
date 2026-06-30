"""Archive uploaded files + run summaries for later analysis.

Two backends, chosen at runtime:
- **Vercel Blob** when `BLOB_READ_WRITE_TOKEN` is set (httpx calls to
  blob.vercel-storage.com).
- **Local folder** (`data/uploads/`) otherwise, so local dev works.

Kept intentionally small: save bytes/text under a timestamped key, list records,
read a local file back for download.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import List

import httpx

import config

_BLOB_API = "https://blob.vercel-storage.com"


def _ts() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def using_blob() -> bool:
    return bool(config.BLOB_TOKEN)


# ---------------------------------------------------------------------------
def save_bytes(category: str, name: str, data: bytes,
               content_type: str = "application/octet-stream") -> dict:
    key = f"{category}/{_ts()}_{name}"
    if using_blob():
        try:
            r = httpx.put(
                f"{_BLOB_API}/{key}", content=data, timeout=20,
                headers={"authorization": f"Bearer {config.BLOB_TOKEN}",
                         "x-content-type": content_type,
                         "x-add-random-suffix": "0",
                         "x-api-version": "7"})
            r.raise_for_status()
            url = r.json().get("url", "")
            return {"key": key, "url": url, "size": len(data),
                    "uploaded_at": _ts(), "category": category, "name": name}
        except Exception:
            pass  # fall through to local
    path = config.UPLOADS_DIR / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"key": key, "url": f"/api/download?key={key}", "size": len(data),
            "uploaded_at": _ts(), "category": category, "name": name}


def save_text(category: str, name: str, text: str,
              content_type: str = "text/csv") -> dict:
    return save_bytes(category, name, text.encode("utf-8"), content_type)


def save_json(category: str, name: str, obj) -> dict:
    return save_text(category, name, json.dumps(obj, default=str), "application/json")


def list_records(limit: int = 200) -> List[dict]:
    if using_blob():
        try:
            r = httpx.get(_BLOB_API, timeout=20,
                          headers={"authorization": f"Bearer {config.BLOB_TOKEN}"},
                          params={"limit": limit})
            r.raise_for_status()
            blobs = r.json().get("blobs", [])
            return [{"key": b.get("pathname"), "url": b.get("url"),
                     "size": b.get("size"), "uploaded_at": b.get("uploadedAt")}
                    for b in blobs]
        except Exception:
            return []
    out = []
    base = config.UPLOADS_DIR
    if base.exists():
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out.append({"key": str(p.relative_to(base)),
                            "url": f"/api/download?key={p.relative_to(base)}",
                            "size": p.stat().st_size,
                            "uploaded_at": _dt.datetime.fromtimestamp(
                                p.stat().st_mtime, _dt.timezone.utc).isoformat()})
    return sorted(out, key=lambda r: r["uploaded_at"], reverse=True)[:limit]


def read_local(key: str) -> bytes:
    """Read a locally-stored file (used by the /api/download fallback)."""
    path = (config.UPLOADS_DIR / key).resolve()
    if not str(path).startswith(str(config.UPLOADS_DIR.resolve())) or not path.is_file():
        raise FileNotFoundError(key)
    return path.read_bytes()


def read_content(key: str) -> bytes:
    """Read stored file bytes by key, works with both Blob and local backends."""
    if using_blob():
        recs = list_records()
        rec = next((r for r in recs if r.get("key") == key), None)
        if rec is None:
            raise FileNotFoundError(key)
        r = httpx.get(rec["url"], timeout=20)
        r.raise_for_status()
        return r.content
    return read_local(key)
