#!/usr/bin/env python3
"""
File-based HTTP request cache layer.

Caches HTTP GET responses on disk so that repeated requests for the same URL
(within a configurable TTL) are served from the local filesystem instead of
hitting the network.

Cache directory: storage/scripts/data-gathering/.cache/
Cache key:       SHA-256 of (URL + sorted relevant headers)
Storage format:  JSON file with {"timestamp": float, "url": str, "data": base64}

Usage:
    from request_cache import cached_request, clear_cache

    body = cached_request("https://example.com/api", ttl=3600)
    clear_cache()          # purge expired entries
    clear_cache(all=True)  # purge everything
"""

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CACHE_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", ".cache")
)

DEFAULT_TTL = 3600   # 1 hour
DEFAULT_TIMEOUT = 10


def _ensure_cache_dir():
    """Create the cache directory if it doesn't exist."""
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _cache_key(url: str, headers: dict | None = None) -> str:
    """Compute a deterministic SHA-256 cache key for a request."""
    parts = [url]
    if headers:
        for k in sorted(headers):
            parts.append(f"{k}:{headers[k]}")
    raw = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_path(key: str) -> str:
    """Return the filesystem path for a cache entry."""
    return os.path.join(_CACHE_DIR, f"{key}.json")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cached_request(
    url: str,
    headers: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    ttl: int = DEFAULT_TTL,
) -> bytes:
    """Fetch *url* via HTTP GET, returning the response body as bytes.

    If a cached copy exists and is younger than *ttl* seconds the network
    request is skipped entirely.

    Parameters
    ----------
    url : str
        The URL to fetch.
    headers : dict, optional
        Extra HTTP headers to send (also used in the cache key).
    timeout : int
        Network timeout in seconds (default 10).
    ttl : int
        Time-to-live in seconds for cached entries (default 3600).

    Returns
    -------
    bytes
        The raw response body.

    Raises
    ------
    urllib.error.HTTPError
        On HTTP error responses (4xx / 5xx).
    urllib.error.URLError
        On network-level failures.
    """
    _ensure_cache_dir()
    key = _cache_key(url, headers)
    path = _cache_path(key)

    # --- cache hit? ---
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                entry = json.load(fh)
            if time.time() - entry.get("timestamp", 0) < ttl:
                return base64.b64decode(entry["data"])
        except (json.JSONDecodeError, KeyError, OSError):
            # Corrupt cache file — delete and refetch
            try:
                os.remove(path)
            except OSError:
                pass

    # --- cache miss: fetch from network ---
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()

    # --- store in cache ---
    entry = {
        "timestamp": time.time(),
        "url": url,
        "data": base64.b64encode(data).decode("ascii"),
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
    except OSError:
        pass  # Non-fatal: we still have the data in memory

    return data


def clear_cache(all: bool = False) -> int:
    """Remove cache entries.

    Parameters
    ----------
    all : bool
        If True, remove every entry. If False (default), only remove entries
        whose TTL has expired (using DEFAULT_TTL as the threshold).

    Returns
    -------
    int
        Number of entries removed.
    """
    _ensure_cache_dir()
    removed = 0
    now = time.time()
    for fname in os.listdir(_CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(_CACHE_DIR, fname)
        if all:
            try:
                os.remove(fpath)
                removed += 1
            except OSError:
                pass
        else:
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    entry = json.load(fh)
                if now - entry.get("timestamp", 0) >= DEFAULT_TTL:
                    os.remove(fpath)
                    removed += 1
            except (json.JSONDecodeError, KeyError, OSError):
                try:
                    os.remove(fpath)
                    removed += 1
                except OSError:
                    pass
    return removed


def cache_stats() -> dict:
    """Return basic stats about the current cache contents."""
    _ensure_cache_dir()
    total = 0
    expired = 0
    total_bytes = 0
    now = time.time()
    for fname in os.listdir(_CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(_CACHE_DIR, fname)
        total += 1
        try:
            size = os.path.getsize(fpath)
            total_bytes += size
            with open(fpath, "r", encoding="utf-8") as fh:
                entry = json.load(fh)
            if now - entry.get("timestamp", 0) >= DEFAULT_TTL:
                expired += 1
        except (json.JSONDecodeError, KeyError, OSError):
            expired += 1
    return {
        "total_entries": total,
        "expired_entries": expired,
        "active_entries": total - expired,
        "total_bytes": total_bytes,
        "cache_dir": _CACHE_DIR,
    }
