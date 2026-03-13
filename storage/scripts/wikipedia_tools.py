#!/usr/bin/env python3
"""Wikipedia Research Toolkit — optimized for egress-restricted environments.

Uses DBpedia SPARQL + WebSearch as primary data sources since direct
Wikipedia access is blocked by egress policy. See KB-0045 for methodology.

Usage:
    # Get all links for a single article
    python3 wikipedia_tools.py links "Artificial_intelligence"

    # Batch get links for articles from category-pages.json
    python3 wikipedia_tools.py batch [--type external|internal|both] [--limit N]

    # Get article metadata (categories, description, related articles)
    python3 wikipedia_tools.py metadata "Artificial_intelligence"

    # Batch metadata for all articles
    python3 wikipedia_tools.py batch-meta [--limit N]

    # Get article summary via search
    python3 wikipedia_tools.py summary "Artificial_intelligence"

    # Export all data (multiple formats)
    python3 wikipedia_tools.py export [--format compact|full|llm]

    # Stats on what's available
    python3 wikipedia_tools.py stats

API for other scripts:
    from wikipedia_tools import WikipediaTools
    wt = WikipediaTools()
    links = wt.get_external_links("Artificial_intelligence")
    internal = wt.get_internal_links("Artificial_intelligence")
    meta = wt.get_metadata("Artificial_intelligence")
    batch = wt.batch_links(["AI_safety", "Deep_learning"], link_type="both")
    batch_meta = wt.batch_metadata(["AI_safety", "Deep_learning"])
"""

import json
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Optional

SPARQL_ENDPOINT = "https://dbpedia.org/sparql"
DEFAULT_GRAPH = "http://dbpedia.org"
DEFAULT_DATA_DIR = Path(__file__).parent.parent / "sources" / "wikipedia-ai"
CATEGORY_PAGES = DEFAULT_DATA_DIR / "category-pages.json"
CACHE_DIR = DEFAULT_DATA_DIR / "cache"
BATCH_SIZE = 40  # articles per SPARQL query (tested safe at 50, use 40 for margin)

# URL compression — ordered longest-prefix-first for correct matching
_URL_PREFIX_MAP = [
    ("https://web.archive.org/web/", "wa:"),
    ("https://archive.org/details/", "ard:"),
    ("https://archive.org/", "ar:"),
    ("https://www.bbc.co.uk/", "bbc:"),
    ("https://www.nytimes.com/", "nyt:"),
    ("https://www.theguardian.com/", "gdn:"),
    ("https://www.washingtonpost.com/", "wp:"),
    ("https://www.theatlantic.com/", "atl:"),
    ("https://www.economist.com/", "econ:"),
    ("https://www.forbes.com/", "fbs:"),
    ("https://www.zdnet.com/", "zd:"),
    ("https://www.wired.com/", "wrd:"),
    ("https://books.google.com/", "gb:"),
    ("https://doi.org/", "doi:"),
    ("https://arxiv.org/abs/", "axa:"),
    ("https://arxiv.org/pdf/", "axp:"),
    ("https://arxiv.org/", "ax:"),
    ("https://www.researchgate.net/publication/", "rgp:"),
    ("https://www.researchgate.net/", "rg:"),
    ("https://papers.ssrn.com/", "ssrn:"),
    ("https://dl.acm.org/doi/", "acm:"),
    ("https://link.springer.com/", "spr:"),
    ("https://www.nature.com/", "nat:"),
    ("https://www.science.org/", "sci:"),
    ("http://", "h:"),
    ("https://", "s:"),
]

# Reverse map for decompression (shortest prefix first to avoid partial match)
_URL_DECOMPRESS_MAP = {short: full for full, short in reversed(_URL_PREFIX_MAP)}


class WikipediaTools:
    """Core toolkit for Wikipedia research in egress-restricted environments."""

    def __init__(self, cache_dir: Optional[Path] = None, data_dir: Optional[Path] = None):
        if data_dir:
            self.data_dir = Path(data_dir)
            self.cache_dir = self.data_dir / "cache"
            self.category_pages = self.data_dir / "category-pages.json"
        else:
            self.data_dir = DEFAULT_DATA_DIR
            self.cache_dir = cache_dir or CACHE_DIR
            self.category_pages = CATEGORY_PAGES
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._stats = {"sparql_calls": 0, "cache_hits": 0, "total_links": 0}

    # ── SPARQL helpers ──────────────────────────────────────────────

    def _sparql_query(self, query: str, timeout: int = 30) -> dict:
        """Execute a SPARQL query against DBpedia. Returns parsed JSON."""
        params = {
            "default-graph-uri": DEFAULT_GRAPH,
            "query": query,
            "format": "application/sparql-results+json",
        }
        url = f"{SPARQL_ENDPOINT}?{urllib.parse.urlencode(params)}"
        self._stats["sparql_calls"] += 1

        result = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), url],
            capture_output=True, text=True, timeout=timeout + 5
        )
        if not result.stdout.strip():
            return {"results": {"bindings": []}}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"results": {"bindings": []}}

    @staticmethod
    def _clean_url(url: str) -> str:
        """Strip wiki markup artifacts from DBpedia URLs."""
        for sep in ["%7C", "|", "%7c"]:
            if sep in url:
                url = url.split(sep)[0]
        return url.rstrip("/").strip()

    @staticmethod
    def _resource_uri(title: str) -> str:
        """Convert article title to DBpedia resource URI."""
        safe = urllib.parse.quote(title.replace(" ", "_"), safe="()_-.")
        return f"http://dbpedia.org/resource/{safe}"

    @staticmethod
    def _title_from_uri(uri: str) -> str:
        """Extract article title from DBpedia resource URI."""
        return urllib.parse.unquote(uri.split("/resource/")[-1])

    @staticmethod
    def _cat_from_uri(uri: str) -> str:
        """Extract category name from DBpedia category URI."""
        return urllib.parse.unquote(uri.split("/")[-1]).replace("Category:", "")

    @staticmethod
    def _cache_safe(title: str) -> str:
        """Make title safe for use as filename."""
        return title.replace("/", "__SLASH__").replace(":", "__COLON__").replace("?", "__Q__")

    # ── Single article queries ──────────────────────────────────────

    def get_external_links(self, title: str, use_cache: bool = True) -> list[str]:
        """Get external citation links for a Wikipedia article via DBpedia."""
        cache_key = self.cache_dir / f"ext_{self._cache_safe(title)}.json"
        if use_cache and cache_key.exists():
            self._stats["cache_hits"] += 1
            return json.loads(cache_key.read_text())

        uri = self._resource_uri(title)
        query = f'SELECT ?link WHERE {{ <{uri}> <http://dbpedia.org/ontology/wikiPageExternalLink> ?link }}'
        data = self._sparql_query(query)
        links = [self._clean_url(b["link"]["value"]) for b in data["results"]["bindings"]]
        links = sorted(set(links))
        self._stats["total_links"] += len(links)

        if use_cache:
            cache_key.write_text(json.dumps(links))
        return links

    def get_internal_links(self, title: str, use_cache: bool = True) -> list[str]:
        """Get internal wiki links (other Wikipedia articles) via DBpedia."""
        cache_key = self.cache_dir / f"int_{self._cache_safe(title)}.json"
        if use_cache and cache_key.exists():
            self._stats["cache_hits"] += 1
            return json.loads(cache_key.read_text())

        uri = self._resource_uri(title)
        query = f'SELECT ?link WHERE {{ <{uri}> <http://dbpedia.org/ontology/wikiPageWikiLink> ?link }}'
        data = self._sparql_query(query)
        links = [self._title_from_uri(b["link"]["value"]) for b in data["results"]["bindings"]]
        links = sorted(set(links))
        self._stats["total_links"] += len(links)

        if use_cache:
            cache_key.write_text(json.dumps(links))
        return links

    def get_metadata(self, title: str, use_cache: bool = True) -> dict:
        """Get article metadata: categories, description, seeAlso, label."""
        cache_key = self.cache_dir / f"meta_{self._cache_safe(title)}.json"
        if use_cache and cache_key.exists():
            self._stats["cache_hits"] += 1
            return json.loads(cache_key.read_text())

        uri = self._resource_uri(title)
        query = (
            f'SELECT ?p ?o WHERE {{ '
            f'<{uri}> ?p ?o . '
            f'FILTER(?p IN ('
            f'<http://purl.org/dc/terms/subject>, '
            f'<http://dbpedia.org/ontology/description>, '
            f'<http://www.w3.org/2000/01/rdf-schema#seeAlso>, '
            f'<http://www.w3.org/2000/01/rdf-schema#label>, '
            f'<http://purl.org/linguistics/gold/hypernym>'
            f')) . '
            f'FILTER(!isLiteral(?o) || lang(?o) = "en")'
            f' }}'
        )
        data = self._sparql_query(query)

        meta: dict = {"categories": [], "seeAlso": [], "label": "", "description": "", "hypernym": ""}
        for b in data["results"]["bindings"]:
            p = b["p"]["value"]
            o = b["o"]["value"]
            if "subject" in p:
                meta["categories"].append(self._cat_from_uri(o))
            elif "seeAlso" in p:
                meta["seeAlso"].append(self._title_from_uri(o))
            elif "label" in p:
                meta["label"] = o
            elif "description" in p:
                meta["description"] = o
            elif "hypernym" in p:
                meta["hypernym"] = self._title_from_uri(o)

        meta["categories"] = sorted(set(meta["categories"]))
        meta["seeAlso"] = sorted(set(meta["seeAlso"]))

        if use_cache:
            cache_key.write_text(json.dumps(meta))
        return meta

    # ── Batch queries ───────────────────────────────────────────────

    def batch_links(
        self,
        titles: list[str],
        link_type: str = "both",
        use_cache: bool = True,
    ) -> dict:
        """Batch fetch links for multiple articles. Returns {title: {external: [...], internal: [...]}}."""
        result = {}
        uncached_ext = []
        uncached_int = []

        for title in titles:
            result[title] = {}
            if link_type in ("external", "both"):
                cache_key = self.cache_dir / f"ext_{self._cache_safe(title)}.json"
                if use_cache and cache_key.exists():
                    result[title]["external"] = json.loads(cache_key.read_text())
                    self._stats["cache_hits"] += 1
                else:
                    uncached_ext.append(title)

            if link_type in ("internal", "both"):
                cache_key = self.cache_dir / f"int_{self._cache_safe(title)}.json"
                if use_cache and cache_key.exists():
                    result[title]["internal"] = json.loads(cache_key.read_text())
                    self._stats["cache_hits"] += 1
                else:
                    uncached_int.append(title)

        if uncached_ext:
            ext_data = self._batch_sparql(uncached_ext, "wikiPageExternalLink")
            for title, links in ext_data.items():
                cleaned = sorted(set(self._clean_url(l) for l in links))
                if title not in result:
                    result[title] = {}
                result[title]["external"] = cleaned
                if use_cache:
                    (self.cache_dir / f"ext_{self._cache_safe(title)}.json").write_text(json.dumps(cleaned))
                self._stats["total_links"] += len(cleaned)

        if uncached_int:
            int_data = self._batch_sparql(uncached_int, "wikiPageWikiLink", extract_title=True)
            for title, links in int_data.items():
                deduped = sorted(set(links))
                if title not in result:
                    result[title] = {}
                result[title]["internal"] = deduped
                if use_cache:
                    (self.cache_dir / f"int_{self._cache_safe(title)}.json").write_text(json.dumps(deduped))
                self._stats["total_links"] += len(deduped)

        for title in titles:
            if link_type in ("external", "both") and "external" not in result[title]:
                result[title]["external"] = []
            if link_type in ("internal", "both") and "internal" not in result[title]:
                result[title]["internal"] = []

        return result

    def batch_metadata(self, titles: list[str], use_cache: bool = True) -> dict:
        """Batch fetch metadata for multiple articles."""
        result = {}
        uncached = []

        for title in titles:
            cache_key = self.cache_dir / f"meta_{self._cache_safe(title)}.json"
            if use_cache and cache_key.exists():
                result[title] = json.loads(cache_key.read_text())
                self._stats["cache_hits"] += 1
            else:
                uncached.append(title)

        if uncached:
            meta_data = self._batch_metadata_sparql(uncached)
            for title, meta in meta_data.items():
                result[title] = meta
                if use_cache:
                    (self.cache_dir / f"meta_{self._cache_safe(title)}.json").write_text(json.dumps(meta))

        # Fill empty entries
        for title in titles:
            if title not in result:
                result[title] = {"categories": [], "seeAlso": [], "label": "", "description": "", "hypernym": ""}

        return result

    def _batch_metadata_sparql(self, titles: list[str]) -> dict[str, dict]:
        """Batch SPARQL for metadata (categories, description, seeAlso, label, hypernym)."""
        title_set = set(titles)
        all_results: dict[str, dict] = {
            t: {"categories": [], "seeAlso": [], "label": "", "description": "", "hypernym": ""}
            for t in titles
        }

        # Use smaller batches for metadata (returns many rows per article)
        meta_batch = min(BATCH_SIZE, 15)
        for i in range(0, len(titles), meta_batch):
            batch = titles[i : i + meta_batch]
            values = " ".join(f"<{self._resource_uri(t)}>" for t in batch)
            query = (
                f"SELECT ?article ?p ?o WHERE {{ "
                f"VALUES ?article {{ {values} }} . "
                f"?article ?p ?o . "
                f"FILTER(?p IN ("
                f"<http://purl.org/dc/terms/subject>, "
                f"<http://dbpedia.org/ontology/description>, "
                f"<http://www.w3.org/2000/01/rdf-schema#seeAlso>, "
                f"<http://www.w3.org/2000/01/rdf-schema#label>, "
                f"<http://purl.org/linguistics/gold/hypernym>"
                f")) . "
                f'FILTER(!isLiteral(?o) || lang(?o) = "en")'
                f" }}"
            )
            data = self._sparql_query(query, timeout=60)
            for b in data["results"]["bindings"]:
                art = self._title_from_uri(b["article"]["value"])
                if art not in title_set:
                    continue
                p = b["p"]["value"]
                o = b["o"]["value"]
                if "subject" in p:
                    all_results[art]["categories"].append(self._cat_from_uri(o))
                elif "seeAlso" in p:
                    all_results[art]["seeAlso"].append(self._title_from_uri(o))
                elif "label" in p:
                    all_results[art]["label"] = o
                elif "description" in p:
                    all_results[art]["description"] = o
                elif "hypernym" in p:
                    all_results[art]["hypernym"] = self._title_from_uri(o)

            if i + meta_batch < len(titles):
                time.sleep(0.5)

        # Dedupe lists
        for meta in all_results.values():
            meta["categories"] = sorted(set(meta["categories"]))
            meta["seeAlso"] = sorted(set(meta["seeAlso"]))

        return all_results

    def _batch_sparql(
        self,
        titles: list[str],
        predicate: str,
        extract_title: bool = False,
    ) -> dict[str, list[str]]:
        """Run batched SPARQL queries for multiple articles.

        Only returns results for titles that were requested (filters out
        DBpedia redirect noise).
        """
        title_set = set(titles)
        all_results: dict[str, list[str]] = {t: [] for t in titles}

        for i in range(0, len(titles), BATCH_SIZE):
            batch = titles[i : i + BATCH_SIZE]
            values = " ".join(f"<{self._resource_uri(t)}>" for t in batch)
            query = (
                f"SELECT ?article ?link WHERE {{ "
                f"VALUES ?article {{ {values} }} . "
                f"?article <http://dbpedia.org/ontology/{predicate}> ?link }}"
            )
            data = self._sparql_query(query)
            for b in data["results"]["bindings"]:
                art = self._title_from_uri(b["article"]["value"])
                val = self._title_from_uri(b["link"]["value"]) if extract_title else b["link"]["value"]
                if art in title_set:
                    all_results[art].append(val)

            if i + BATCH_SIZE < len(titles):
                time.sleep(0.5)

        return all_results

    # ── Category page helpers ───────────────────────────────────────

    def load_category_pages(self) -> list[dict]:
        """Load the article index from category-pages.json."""
        cp = self.category_pages
        if not cp.exists():
            raise FileNotFoundError(f"Category pages not found: {cp}")
        data = json.loads(cp.read_text())
        return data["pages"]

    def get_all_titles(self) -> list[str]:
        """Get all article titles from the category index, normalized."""
        return [p["title"].replace(" ", "_") for p in self.load_category_pages()]

    # ── Export (token-efficient formats) ────────────────────────────

    def export_compact(self, data: dict, max_links: int = 0) -> str:
        """Export as compact JSON — short keys, compressed URLs.

        Format: {"Article": {"e": [urls], "i": [titles]}}
        """
        compact = {}
        for title, links in data.items():
            entry = {}
            ext = links.get("external", [])
            internal = links.get("internal", [])

            if max_links > 0:
                ext = ext[:max_links]
                internal = internal[:max_links]

            if ext:
                entry["e"] = self._compress_urls(ext)
            if internal:
                entry["i"] = internal
            if entry:
                compact[title] = entry

        return json.dumps(compact, separators=(",", ":"))

    def export_llm(self, data: dict, meta: Optional[dict] = None, max_links: int = 0) -> str:
        """Export in token-optimized newline format for LLM consumption.

        ~40-50% fewer tokens than full JSON. Format:
            === Article_Title ===
            d: Short description
            c: Category1|Category2
            e: compressed_url1
            e: compressed_url2
            i: Internal_Link1|Internal_Link2|...
        """
        lines = []
        for title, links in data.items():
            ext = links.get("external", [])
            internal = links.get("internal", [])

            if max_links > 0:
                ext = ext[:max_links]
                internal = internal[:max_links]

            if not ext and not internal:
                continue

            lines.append(f"=== {title} ===")

            # Add metadata if available
            if meta and title in meta:
                m = meta[title]
                if m.get("description"):
                    lines.append(f"d: {m['description']}")
                if m.get("categories"):
                    lines.append(f"c: {'|'.join(m['categories'])}")

            # External links — one per line with prefix compression
            if ext:
                compressed = self._compress_urls(ext)
                for url in compressed:
                    lines.append(f"e: {url}")

            # Internal links — pipe-separated on one line (saves newline tokens)
            if internal:
                lines.append(f"i: {'|'.join(internal)}")

        return "\n".join(lines)

    @staticmethod
    def _compress_urls(urls: list[str]) -> list[str]:
        """Compress URLs by stripping common prefixes to save tokens."""
        compressed = []
        for url in urls:
            for prefix, short in _URL_PREFIX_MAP:
                if url.startswith(prefix):
                    url = short + url[len(prefix):]
                    break
            compressed.append(url)
        return compressed

    @staticmethod
    def decompress_url(short: str) -> str:
        """Expand a compressed URL back to full form."""
        for short_prefix, full_prefix in _URL_DECOMPRESS_MAP.items():
            if short.startswith(short_prefix):
                return full_prefix + short[len(short_prefix):]
        return short

    # ── Stats ───────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get session statistics."""
        cached_ext = len(list(self.cache_dir.glob("ext_*.json")))
        cached_int = len(list(self.cache_dir.glob("int_*.json")))
        cached_meta = len(list(self.cache_dir.glob("meta_*.json")))
        return {
            **self._stats,
            "cached_external": cached_ext,
            "cached_internal": cached_int,
            "cached_metadata": cached_meta,
            "cache_dir": str(self.cache_dir),
        }

    def clear_cache(self):
        """Clear all cached data."""
        for f in self.cache_dir.glob("*.json"):
            f.unlink()


# ── CLI ─────────────────────────────────────────────────────────────

def cli():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    args = sys.argv[2:]

    def get_arg(flag: str, default: str = "") -> str:
        for i, a in enumerate(args):
            if a == flag and i + 1 < len(args):
                return args[i + 1]
        return default

    data_dir = get_arg("--data-dir", "")
    wt = WikipediaTools(data_dir=Path(data_dir) if data_dir else None)

    if cmd == "links":
        title = args[0] if args else "Artificial_intelligence"
        ext = wt.get_external_links(title)
        internal = wt.get_internal_links(title)
        print(json.dumps({"title": title, "external": ext, "internal": internal}, indent=2))

    elif cmd == "metadata":
        title = args[0] if args else "Artificial_intelligence"
        meta = wt.get_metadata(title)
        print(json.dumps({"title": title, **meta}, indent=2))

    elif cmd == "batch":
        link_type = get_arg("--type", "both")
        limit = int(get_arg("--limit", "0"))

        titles = wt.get_all_titles()
        if limit:
            titles = titles[:limit]

        print(f"Fetching {link_type} links for {len(titles)} articles...", file=sys.stderr)
        data = wt.batch_links(titles, link_type=link_type)

        for title, links in data.items():
            ext_count = len(links.get("external", []))
            int_count = len(links.get("internal", []))
            if ext_count or int_count:
                print(f"{title}: {ext_count} ext, {int_count} int")

        stats = wt.get_stats()
        print(f"\n--- Stats ---", file=sys.stderr)
        print(f"SPARQL calls: {stats['sparql_calls']}", file=sys.stderr)
        print(f"Cache hits: {stats['cache_hits']}", file=sys.stderr)
        print(f"Total links: {stats['total_links']}", file=sys.stderr)

    elif cmd == "batch-meta":
        limit = int(get_arg("--limit", "0"))
        titles = wt.get_all_titles()
        if limit:
            titles = titles[:limit]

        print(f"Fetching metadata for {len(titles)} articles...", file=sys.stderr)
        meta = wt.batch_metadata(titles)

        for title, m in meta.items():
            cats = ", ".join(m.get("categories", []))
            desc = m.get("description", "")[:80]
            if cats or desc:
                print(f"{title}: [{cats}] {desc}")

        stats = wt.get_stats()
        print(f"\n--- Stats ---", file=sys.stderr)
        print(f"SPARQL calls: {stats['sparql_calls']}", file=sys.stderr)

    elif cmd == "summary":
        title = args[0] if args else "Artificial_intelligence"
        ext = wt.get_external_links(title)
        internal = wt.get_internal_links(title)
        meta = wt.get_metadata(title)
        print(f"{title}")
        if meta.get("description"):
            print(f"  Description: {meta['description']}")
        if meta.get("categories"):
            print(f"  Categories: {', '.join(meta['categories'])}")
        print(f"  External links: {len(ext)}")
        print(f"  Internal links: {len(internal)}")
        if meta.get("seeAlso"):
            print(f"  See also: {', '.join(meta['seeAlso'])}")
        if ext:
            domains: dict[str, int] = {}
            for url in ext:
                try:
                    domain = url.split("//")[1].split("/")[0].replace("www.", "")
                except (IndexError, AttributeError):
                    domain = "unknown"
                domains[domain] = domains.get(domain, 0) + 1
            print(f"  Top domains:")
            for d, c in sorted(domains.items(), key=lambda x: -x[1])[:10]:
                print(f"    {d}: {c}")

    elif cmd == "export":
        fmt = get_arg("--format", "compact")
        titles = wt.get_all_titles()
        data = wt.batch_links(titles, link_type="both")
        if fmt == "compact":
            print(wt.export_compact(data))
        elif fmt == "llm":
            meta = wt.batch_metadata(titles)
            print(wt.export_llm(data, meta=meta))
        else:
            print(json.dumps(data, indent=2))

    elif cmd == "stats":
        stats = wt.get_stats()
        print(json.dumps(stats, indent=2))

    elif cmd == "clear-cache":
        wt.clear_cache()
        print("Cache cleared.")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    cli()
