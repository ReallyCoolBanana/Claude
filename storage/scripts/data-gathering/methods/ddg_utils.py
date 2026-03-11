#!/usr/bin/env python3
"""
Shared DuckDuckGo search utilities.

Provides the DuckDuckGoParser HTML parser and search functions used across
multiple data-gathering methods. Centralises rate limiting and User-Agent
configuration so every caller behaves consistently.

This module was extracted from duplicated code in:
  - method_websearch.py  (DG-0001)
  - method_wikipedia.py  (DG-0002)
  - method_arxiv.py      (DG-0003)
  - method_iterative.py  (DG-0005)
"""

import re
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from html.parser import HTMLParser

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
DEFAULT_TIMEOUT = 10
RATE_LIMIT_SECONDS = 0.5

# Cache integration (graceful fallback if unavailable)
USE_CACHE = True
_cached_request = None
try:
    if USE_CACHE:
        from request_cache import cached_request as _cached_request
except ImportError:
    pass

# Module-level state for rate limiting
_last_request_time = 0.0
_rate_limit_lock = threading.Lock()
_config_lock = threading.Lock()


def set_user_agent(ua: str):
    """Override the default User-Agent for all DDG requests."""
    global DEFAULT_USER_AGENT
    with _config_lock:
        DEFAULT_USER_AGENT = ua


def set_rate_limit(seconds: float):
    """Override the delay between consecutive DDG requests."""
    global RATE_LIMIT_SECONDS
    with _config_lock:
        RATE_LIMIT_SECONDS = seconds


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------

class DuckDuckGoParser(HTMLParser):
    """Parse DuckDuckGo HTML search results page."""

    def __init__(self):
        super().__init__()
        self.results = []
        self._current = {}
        self._in_result_title = False
        self._in_snippet = False
        self._capture_text = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if tag == "a" and "result__a" in cls:
            self._in_result_title = True
            self._capture_text = ""
            href = attrs_dict.get("href", "")
            if "uddg=" in href:
                match = re.search(r'uddg=([^&]+)', href)
                if match:
                    href = urllib.parse.unquote(match.group(1))
            self._current["url"] = href
        if tag == "a" and "result__snippet" in cls:
            self._in_snippet = True
            self._capture_text = ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_result_title:
            self._in_result_title = False
            self._current["title"] = self._capture_text.strip()
        if tag == "a" and self._in_snippet:
            self._in_snippet = False
            self._current["snippet"] = self._capture_text.strip()
            if self._current.get("url") and self._current.get("title"):
                self.results.append(dict(self._current))
            self._current = {}

    def handle_data(self, data):
        if self._in_result_title or self._in_snippet:
            self._capture_text += data


# ---------------------------------------------------------------------------
# Search function
# ---------------------------------------------------------------------------

def _enforce_rate_limit():
    """Sleep if needed to respect the rate limit between requests."""
    global _last_request_time
    with _rate_limit_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if _last_request_time > 0 and elapsed < RATE_LIMIT_SECONDS:
            time.sleep(RATE_LIMIT_SECONDS - elapsed)
        _last_request_time = time.monotonic()


def search_duckduckgo(query, timeout=None, user_agent=None):
    """Search DuckDuckGo HTML and return parsed results.

    Parameters
    ----------
    query : str
        The search query string.
    timeout : int, optional
        Request timeout in seconds (default: DEFAULT_TIMEOUT).
    user_agent : str, optional
        Override the User-Agent header for this request.

    Returns
    -------
    list[dict]
        Parsed search results, each with 'url', 'title', and 'snippet' keys.
        On error returns a list containing a single dict with 'error' and
        'query' keys.
    """
    _enforce_rate_limit()

    _timeout = timeout if timeout is not None else DEFAULT_TIMEOUT
    _ua = user_agent if user_agent is not None else DEFAULT_USER_AGENT

    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    headers = {"User-Agent": _ua}
    try:
        # Use cache if available
        if _cached_request is not None:
            raw = _cached_request(url, headers=headers, timeout=_timeout)
            html = raw.decode("utf-8", errors="replace")
        else:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        parser = DuckDuckGoParser()
        parser.feed(html)
        return parser.results
    except urllib.error.HTTPError as e:
        return [{"error": f"HTTP {e.code}: {e.reason}", "query": query}]
    except urllib.error.URLError as e:
        return [{"error": f"URL error: {e.reason}", "query": query}]
    except OSError as e:
        return [{"error": f"Network error: {e}", "query": query}]


# Alias used by some modules
ddg_search = search_duckduckgo
