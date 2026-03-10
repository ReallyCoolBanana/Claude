#!/usr/bin/env python3
"""
DG-0003: Academic Paper Search via arXiv API
=============================================
Uses the arXiv API to search for papers matching a query, extracts titles,
authors, abstracts, categories, and publication dates. Falls back to
DuckDuckGo site-scoped search if the arXiv API is unavailable.

Usage:
    python3 method_arxiv.py "your search query"

Returns JSON to stdout with standardized format.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.etree import ElementTree

from ddg_utils import search_duckduckgo as _ddg_search


METHOD_ID = "DG-0003"
METHOD_NAME = "arXiv Academic Paper Search"
TIMEOUT = 10

ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"


# ---------------------------------------------------------------------------
# arXiv API methods
# ---------------------------------------------------------------------------

def search_arxiv(query, max_results=20, sort_by="relevance"):
    """Search arXiv API and return parsed paper entries."""
    encoded = urllib.parse.quote(query)
    url = (
        f"https://export.arxiv.org/api/query"
        f"?search_query=all:{encoded}"
        f"&start=0&max_results={max_results}"
        f"&sortBy={sort_by}&sortOrder=descending"
    )
    headers = {"User-Agent": "DataGatheringBot/1.0 (research tool)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            xml_data = resp.read().decode("utf-8")
        return parse_arxiv_response(xml_data)
    except urllib.error.HTTPError as e:
        return [{"error": f"HTTP {e.code}: {e.reason}", "source": "arxiv", "query": query}]
    except urllib.error.URLError as e:
        return [{"error": f"URL error: {e.reason}", "source": "arxiv", "query": query}]
    except OSError as e:
        return [{"error": f"Network error: {e}", "source": "arxiv", "query": query}]


def parse_arxiv_response(xml_data):
    """Parse arXiv Atom XML response into structured results."""
    papers = []
    try:
        root = ElementTree.fromstring(xml_data)
    except ElementTree.ParseError as e:
        return [{"error": f"XML parse error: {e}"}]

    for entry in root.findall(f"{{{ATOM_NS}}}entry"):
        paper = {}
        title_el = entry.find(f"{{{ATOM_NS}}}title")
        paper["title"] = _clean_text(title_el.text) if title_el is not None and title_el.text else ""

        summary_el = entry.find(f"{{{ATOM_NS}}}summary")
        paper["abstract"] = _clean_text(summary_el.text) if summary_el is not None and summary_el.text else ""

        authors = []
        for author_el in entry.findall(f"{{{ATOM_NS}}}author"):
            name_el = author_el.find(f"{{{ATOM_NS}}}name")
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())
        paper["authors"] = authors

        published_el = entry.find(f"{{{ATOM_NS}}}published")
        paper["published"] = published_el.text.strip() if published_el is not None and published_el.text else ""

        updated_el = entry.find(f"{{{ATOM_NS}}}updated")
        paper["updated"] = updated_el.text.strip() if updated_el is not None and updated_el.text else ""

        id_el = entry.find(f"{{{ATOM_NS}}}id")
        paper["arxiv_url"] = id_el.text.strip() if id_el is not None and id_el.text else ""
        if paper["arxiv_url"]:
            match = re.search(r'abs/(.+)$', paper["arxiv_url"])
            paper["arxiv_id"] = match.group(1) if match else paper["arxiv_url"]
        else:
            paper["arxiv_id"] = ""

        for link_el in entry.findall(f"{{{ATOM_NS}}}link"):
            if link_el.get("title") == "pdf":
                paper["pdf_url"] = link_el.get("href", "")
                break
        else:
            paper["pdf_url"] = ""

        categories = []
        primary = entry.find(f"{{{ARXIV_NS}}}primary_category")
        paper["primary_category"] = primary.get("term", "") if primary is not None else ""

        for cat_el in entry.findall(f"{{{ATOM_NS}}}category"):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)
        paper["categories"] = categories

        comment_el = entry.find(f"{{{ARXIV_NS}}}comment")
        paper["comment"] = comment_el.text.strip() if comment_el is not None and comment_el.text else ""

        papers.append(paper)

    return papers


def _clean_text(text):
    """Clean up whitespace in extracted text."""
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


# ---------------------------------------------------------------------------
# Fallback: DuckDuckGo site-scoped arXiv search
# ---------------------------------------------------------------------------

def _extract_arxiv_id(url):
    """Extract arXiv ID from a URL."""
    match = re.search(r'arxiv\.org/(?:abs|pdf)/(\d+\.\d+(?:v\d+)?)', url)
    if match:
        return match.group(1)
    return None


def fallback_arxiv_search(query):
    """Use DuckDuckGo to find arXiv papers when the API is blocked."""
    papers = []
    seen_ids = set()

    search_queries = [
        f"site:arxiv.org {query}",
        f"site:arxiv.org {query} paper",
        f"arxiv {query} research",
    ]
    for sq in search_queries:
        hits = _ddg_search(sq)
        for h in hits:
            url = h.get("url", "")
            arxiv_id = _extract_arxiv_id(url)
            if not arxiv_id:
                # Still include arxiv.org URLs even without clean ID
                if "arxiv.org" not in url:
                    continue
                arxiv_id = url  # use full URL as dedup key
            if arxiv_id in seen_ids:
                continue
            seen_ids.add(arxiv_id)

            title = h.get("title", "")
            # Clean up title — DDG often appends " - arXiv"
            title = re.sub(r'\s*[-|]\s*arXiv.*$', '', title).strip()

            papers.append({
                "title": title,
                "abstract": h.get("snippet", ""),
                "authors": [],
                "published": "",
                "updated": "",
                "arxiv_url": url if "arxiv.org" in url else "",
                "arxiv_id": arxiv_id if arxiv_id != url else "",
                "pdf_url": "",
                "primary_category": "",
                "categories": [],
                "comment": "",
                "source": "duckduckgo_fallback",
            })

    return papers


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(query):
    """Execute the arXiv academic paper search method."""
    start = time.time()
    used_fallback = False

    papers = search_arxiv(query, max_results=20)

    # Check if we got real results or only errors
    errors = [p for p in papers if "error" in p]
    valid_papers = [p for p in papers if "error" not in p]

    if not valid_papers and errors:
        # API failed — use fallback
        used_fallback = True
        valid_papers = fallback_arxiv_search(query)
        errors = []

    duration = round(time.time() - start, 2)

    unique_authors = set()
    unique_categories = set()
    for p in valid_papers:
        unique_authors.update(p.get("authors", []))
        unique_categories.update(p.get("categories", []))

    return {
        "method_id": METHOD_ID,
        "method_name": METHOD_NAME,
        "query": query,
        "results": valid_papers,
        "metadata": {
            "duration_seconds": duration,
            "sources_count": len(valid_papers),
            "data_points": len(valid_papers),
            "unique_authors": len(unique_authors),
            "unique_categories": list(unique_categories),
            "errors": errors if errors else None,
            "used_fallback": used_fallback,
        },
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} \"query string\"", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2, ensure_ascii=False))
