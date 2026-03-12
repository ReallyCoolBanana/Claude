#!/usr/bin/env python3
"""Wikipedia Text Extractor — token-efficient article text via DBpedia + WebSearch.

Extracts Wikipedia article summaries and stores them as cached markdown or JSON.
Uses DBpedia Lookup API for short descriptions and WebSearch for full summaries.
Direct Wikipedia is blocked by egress policy (see KB-0045).

Data sources (in priority order):
  1. DBpedia Lookup API (lookup.dbpedia.org) — short descriptions, always available
  2. WebSearch — full article summaries (requires agent with WebSearch tool)

Usage:
    # Fetch descriptions from DBpedia Lookup for all articles (no agent needed)
    python3 wiki_text_extractor.py fetch-dbpedia [--limit N]

    # Extract text for one article (checks cache first)
    python3 wiki_text_extractor.py extract "Artificial_intelligence"

    # Batch status — see what's cached and what's missing
    python3 wiki_text_extractor.py batch [--limit N]

    # Export all cached articles as single markdown file
    python3 wiki_text_extractor.py export-md [--output FILE]

    # Export as token-efficient JSON
    python3 wiki_text_extractor.py export-json [--format compact|full]

    # Export in LLM-optimized format (most token-efficient)
    python3 wiki_text_extractor.py export-llm

    # Show extraction stats
    python3 wiki_text_extractor.py stats

    # List articles missing text
    python3 wiki_text_extractor.py gaps

API for other scripts:
    from wiki_text_extractor import WikiTextExtractor
    wte = WikiTextExtractor()
    text = wte.extract_article("Artificial_intelligence")
    wte.store_article("AI_safety", "Full summary text here...")
    wte.fetch_dbpedia_descriptions(["AI_safety", "Deep_learning"])
    md = wte.export_markdown()
    js = wte.export_json(compact=True)
"""

import json
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

CACHE_DIR = Path(__file__).parent.parent / "sources" / "wikipedia-ai" / "text-cache"
CATEGORY_PAGES = Path(__file__).parent.parent / "sources" / "wikipedia-ai" / "category-pages.json"
OUTPUT_DIR = Path(__file__).parent.parent / "sources" / "wikipedia-ai" / "exports"
DBPEDIA_LOOKUP = "https://lookup.dbpedia.org/api/search"

# Rate limiting
SEARCH_DELAY = 2.0
DBPEDIA_DELAY = 0.3  # DBpedia Lookup is fast, short delay
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


class WikiTextExtractor:
    """Extract and cache Wikipedia article text via WebSearch."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._stats = {
            "searches": 0,
            "cache_hits": 0,
            "extractions": 0,
            "failures": 0,
        }

    # ── Cache helpers ────────────────────────────────────────────────

    @staticmethod
    def _cache_safe(title: str) -> str:
        """Make title safe for use as filename."""
        return (
            title.replace("/", "__SLASH__")
            .replace(":", "__COLON__")
            .replace("?", "__Q__")
            .replace(" ", "_")
        )

    def _cache_path(self, title: str) -> Path:
        return self.cache_dir / f"text_{self._cache_safe(title)}.json"

    def _load_cache(self, title: str) -> Optional[dict]:
        p = self._cache_path(title)
        if p.exists():
            self._stats["cache_hits"] += 1
            return json.loads(p.read_text())
        return None

    def _save_cache(self, title: str, data: dict):
        self._cache_path(title).write_text(json.dumps(data, separators=(",", ":")))

    # ── WebSearch-based extraction ───────────────────────────────────

    def _websearch(self, query: str) -> str:
        """Run a WebSearch query via subprocess calling this script's helper."""
        # We use curl to a search API or fall back to formatted results
        # In the agent environment, WebSearch is a tool — for CLI we simulate
        # by using the cached result or returning empty
        return ""

    def extract_article(self, title: str, use_cache: bool = True) -> dict:
        """Extract article text for a single Wikipedia article.

        Returns dict with keys: title, summary, sections, source, cached.
        This method is designed to be called by agents who have WebSearch access.
        For CLI batch use, use batch_extract_with_searcher().
        """
        if use_cache:
            cached = self._load_cache(title)
            if cached:
                return {**cached, "cached": True}

        # Return empty — caller (agent) should populate via WebSearch
        return {
            "title": title,
            "summary": "",
            "sections": [],
            "source": "pending",
            "cached": False,
        }

    def store_article(self, title: str, summary: str, sections: Optional[list] = None,
                      source: str = "websearch") -> dict:
        """Store extracted article text in cache.

        Called by agents after they use WebSearch to get article text.
        """
        data = {
            "title": title,
            "summary": self._clean_text(summary),
            "sections": sections or [],
            "source": source,
            "extracted": time.strftime("%Y-%m-%d"),
        }
        self._save_cache(title, data)
        self._stats["extractions"] += 1
        return data

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean extracted text — remove HTML, normalize whitespace."""
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()

    # ── DBpedia Lookup integration ───────────────────────────────────

    def fetch_dbpedia_description(self, title: str) -> Optional[str]:
        """Fetch short description from DBpedia Lookup API.

        Returns description text or None if not found.
        DBpedia Lookup returns ~100-200 char descriptions for most articles.
        """
        query = title.replace("_", " ")
        params = urllib.parse.urlencode({
            "query": query,
            "format": "json",
            "maxResults": "3",
        })
        url = f"{DBPEDIA_LOOKUP}?{params}"

        for attempt in range(MAX_RETRIES):
            try:
                result = subprocess.run(
                    ["curl", "-sL", "--max-time", "10", url],
                    capture_output=True, text=True, timeout=15,
                )
                if not result.stdout.strip():
                    continue
                data = json.loads(result.stdout)
                target_uri = f"http://dbpedia.org/resource/{title}"

                for doc in data.get("docs", []):
                    resources = doc.get("resource", [])
                    if target_uri in resources:
                        comment = doc.get("comment", [""])[0]
                        if comment:
                            return self._clean_text(comment)

                # If exact match not found, use first result's comment
                if data.get("docs"):
                    comment = data["docs"][0].get("comment", [""])[0]
                    if comment:
                        return self._clean_text(comment)

            except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError):
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue

        return None

    def fetch_dbpedia_descriptions(self, titles: list[str], skip_cached: bool = True) -> dict:
        """Batch fetch descriptions from DBpedia Lookup for multiple articles.

        Updates cache for articles that don't have text yet.
        Returns {title: description} for successfully fetched articles.
        """
        results = {}
        for i, title in enumerate(titles):
            if skip_cached:
                cached = self._load_cache(title)
                if cached and cached.get("summary"):
                    results[title] = cached["summary"]
                    continue

            desc = self.fetch_dbpedia_description(title)
            if desc:
                self.store_article(title, desc, source="dbpedia-lookup")
                results[title] = desc
                self._stats["searches"] += 1

            if i < len(titles) - 1:
                time.sleep(DBPEDIA_DELAY)

            # Progress reporting
            if (i + 1) % 10 == 0:
                print(f"  Progress: {i+1}/{len(titles)} ({len(results)} found)", file=sys.stderr)

        return results

    # ── Batch operations ─────────────────────────────────────────────

    def get_all_titles(self) -> list[str]:
        """Get all article titles from category-pages.json."""
        if not CATEGORY_PAGES.exists():
            raise FileNotFoundError(f"Category pages not found: {CATEGORY_PAGES}")
        data = json.loads(CATEGORY_PAGES.read_text())
        return [p["title"].replace(" ", "_") for p in data["pages"]]

    def get_cached_titles(self) -> list[str]:
        """Get titles that already have cached text."""
        cached = []
        for f in self.cache_dir.glob("text_*.json"):
            title = f.stem.replace("text_", "")
            title = (
                title.replace("__SLASH__", "/")
                .replace("__COLON__", ":")
                .replace("__Q__", "?")
            )
            cached.append(title)
        return sorted(cached)

    def get_missing_titles(self) -> list[str]:
        """Get titles that don't have cached text yet."""
        all_titles = set(self.get_all_titles())
        cached = set(self.get_cached_titles())
        return sorted(all_titles - cached)

    def batch_status(self) -> dict:
        """Get batch extraction status."""
        all_t = self.get_all_titles()
        cached = self.get_cached_titles()
        missing = self.get_missing_titles()
        return {
            "total": len(all_t),
            "cached": len(cached),
            "missing": len(missing),
            "coverage": f"{len(cached)/len(all_t)*100:.1f}%" if all_t else "0%",
        }

    # ── Export formats ───────────────────────────────────────────────

    def export_markdown(self, max_articles: int = 0) -> str:
        """Export all cached articles as a single markdown document.

        Token-efficient: uses compact headers, strips redundancy.
        """
        lines = ["# Wikipedia AI Articles — Extracted Text\n"]
        cached = self.get_cached_titles()
        if max_articles:
            cached = cached[:max_articles]

        for title in sorted(cached):
            data = self._load_cache(title)
            if not data or not data.get("summary"):
                continue
            lines.append(f"## {title.replace('_', ' ')}\n")
            lines.append(data["summary"])
            if data.get("sections"):
                for sec in data["sections"]:
                    name = sec.get("name", "")
                    text = sec.get("text", "")
                    if name and text:
                        lines.append(f"\n### {name}\n")
                        lines.append(text)
            lines.append("")  # blank line separator

        return "\n".join(lines)

    def export_json(self, compact: bool = True, max_articles: int = 0) -> str:
        """Export all cached articles as JSON.

        compact=True uses short keys: t=title, s=summary, x=sections
        """
        cached = self.get_cached_titles()
        if max_articles:
            cached = cached[:max_articles]

        articles = []
        for title in sorted(cached):
            data = self._load_cache(title)
            if not data or not data.get("summary"):
                continue
            if compact:
                entry = {"t": title, "s": data["summary"]}
                if data.get("sections"):
                    entry["x"] = [
                        {"n": s.get("name", ""), "t": s.get("text", "")}
                        for s in data["sections"]
                        if s.get("text")
                    ]
            else:
                entry = data
            articles.append(entry)

        if compact:
            return json.dumps(articles, separators=(",", ":"))
        return json.dumps(articles, indent=2)

    def export_llm(self, max_articles: int = 0) -> str:
        """Export in ultra-compact LLM format.

        Format:
            === Article_Title ===
            <summary text>
            --- Section Name ---
            <section text>
        """
        cached = self.get_cached_titles()
        if max_articles:
            cached = cached[:max_articles]

        lines = []
        for title in sorted(cached):
            data = self._load_cache(title)
            if not data or not data.get("summary"):
                continue
            lines.append(f"=== {title} ===")
            lines.append(data["summary"])
            if data.get("sections"):
                for sec in data["sections"]:
                    if sec.get("text"):
                        lines.append(f"--- {sec.get('name', 'Section')} ---")
                        lines.append(sec["text"])
            lines.append("")

        return "\n".join(lines)

    # ── Agent task generation ────────────────────────────────────────

    def generate_agent_tasks(self, batch_size: int = 30) -> list[list[str]]:
        """Split missing articles into batches for parallel agent processing.

        Returns list of title batches, each suitable for one agent.
        """
        missing = self.get_missing_titles()
        batches = []
        for i in range(0, len(missing), batch_size):
            batches.append(missing[i : i + batch_size])
        return batches

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get session and cache statistics."""
        status = self.batch_status()
        return {**self._stats, **status, "cache_dir": str(self.cache_dir)}


# ── CLI ─────────────────────────────────────────────────────────────

def cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    wte = WikiTextExtractor()
    args = sys.argv[2:]

    def get_arg(flag: str, default: str = "") -> str:
        for i, a in enumerate(args):
            if a == flag and i + 1 < len(args):
                return args[i + 1]
        return default

    if cmd == "extract":
        title = args[0] if args else "Artificial_intelligence"
        result = wte.extract_article(title)
        if result.get("cached"):
            print(json.dumps(result, indent=2))
        else:
            print(f"No cached text for '{title}'. Use an agent with WebSearch to extract.")
            print(f"Agent should call: wte.store_article('{title}', summary_text)")

    elif cmd == "batch":
        limit = int(get_arg("--limit", "0"))
        status = wte.batch_status()
        print(f"Total articles: {status['total']}")
        print(f"Already cached: {status['cached']}")
        print(f"Missing: {status['missing']}")
        print(f"Coverage: {status['coverage']}")

        if status["missing"] > 0:
            batches = wte.generate_agent_tasks()
            if limit:
                batches = batches[:limit]
            print(f"\nAgent task batches needed: {len(batches)}")
            for i, batch in enumerate(batches):
                print(f"  Batch {i+1}: {len(batch)} articles ({batch[0]}...{batch[-1]})")

    elif cmd == "export-md":
        output = get_arg("--output", "")
        limit = int(get_arg("--limit", "0"))
        md = wte.export_markdown(max_articles=limit)
        if output:
            Path(output).write_text(md)
            print(f"Exported to {output} ({len(md)} bytes)")
        else:
            print(md)

    elif cmd == "export-json":
        fmt = get_arg("--format", "compact")
        limit = int(get_arg("--limit", "0"))
        js = wte.export_json(compact=(fmt == "compact"), max_articles=limit)
        print(js)

    elif cmd == "export-llm":
        limit = int(get_arg("--limit", "0"))
        print(wte.export_llm(max_articles=limit))

    elif cmd == "stats":
        print(json.dumps(wte.get_stats(), indent=2))

    elif cmd == "gaps":
        missing = wte.get_missing_titles()
        print(f"Articles missing text ({len(missing)}):")
        for t in missing:
            print(f"  {t}")

    elif cmd == "fetch-dbpedia":
        limit = int(get_arg("--limit", "0"))
        titles = wte.get_all_titles()
        if limit:
            titles = titles[:limit]
        print(f"Fetching DBpedia descriptions for {len(titles)} articles...", file=sys.stderr)
        results = wte.fetch_dbpedia_descriptions(titles)
        print(f"\nFetched {len(results)} descriptions from DBpedia Lookup")
        status = wte.batch_status()
        print(f"Coverage: {status['coverage']} ({status['cached']}/{status['total']})")

    elif cmd == "store":
        # Store article text from stdin (for piping from agents)
        title = args[0] if args else ""
        if not title:
            print("Usage: echo 'text' | python3 wiki_text_extractor.py store Article_Title")
            sys.exit(1)
        text = sys.stdin.read()
        result = wte.store_article(title, text)
        print(f"Stored {title}: {len(result['summary'])} chars")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    cli()
