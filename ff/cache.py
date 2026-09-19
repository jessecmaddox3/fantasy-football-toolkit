"""On-disk HTTP cache with per-call TTLs.

Every source module goes through here so that a draft-day session can re-run a
board a dozen times without re-pulling the 15 MB players dump.  Cache files live
under ``data/cache/`` (gitignored); override with ``FF_CACHE_DIR``.
"""

from __future__ import annotations

import json
import os
import time
import tempfile
from pathlib import Path
from typing import Any

import httpx


DAY = 86400
HOUR = 3600
WEEK = 7 * DAY

USER_AGENT = "fantasy-football-toolkit/0.2"


def cache_dir() -> Path:
    """Resolved at call time so tests and jobs can redirect it."""
    return Path(os.environ.get("FF_CACHE_DIR", Path.cwd() / "data" / "cache"))


def cache_path(key: str, suffix: str = ".json") -> Path:
    return cache_dir() / f"{key}{suffix}"


def _fresh(path: Path, ttl: float) -> bool:
    if ttl <= 0 or not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) < ttl


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique owner-only temporary files also avoid concurrent writers colliding.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", delete=False) as handle:
        tmp = Path(handle.name)
        try:
            handle.write(text)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)



def _request(
    url: str,
    *,
    params: dict | list | None = None,
    headers: dict | None = None,
    client: httpx.Client | None = None,
    timeout: float = 60.0,
) -> httpx.Response:
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    owned = client is None
    c = client or httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        resp = c.get(url, params=params, headers=hdrs)
        resp.raise_for_status()
        return resp
    finally:
        if owned:
            c.close()


def fetch_json(
    url: str,
    *,
    key: str,
    ttl: float,
    params: dict | list | None = None,
    headers: dict | None = None,
    client: httpx.Client | None = None,
) -> Any:
    """GET ``url`` as JSON, serving from disk while the cache entry is younger
    than ``ttl`` seconds.  ``ttl=0`` disables caching in both directions."""
    path = cache_path(key)
    if _fresh(path, ttl):
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass  # corrupt entry: fall through and refetch

    resp = _request(url, params=params, headers=headers, client=client)
    data = resp.json()
    if ttl > 0:
        _write(path, json.dumps(data))
    return data


def fetch_text(
    url: str,
    *,
    key: str,
    ttl: float,
    params: dict | list | None = None,
    headers: dict | None = None,
    client: httpx.Client | None = None,
) -> str:
    """Same contract as :func:`fetch_json` for CSV and other text payloads."""
    path = cache_path(key, ".txt")
    if _fresh(path, ttl):
        try:
            return path.read_text()
        except OSError:
            pass

    text = _request(url, params=params, headers=headers, client=client).text
    if ttl > 0:
        _write(path, text)
    return text
