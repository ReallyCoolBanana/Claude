# Data Gathering Methods -- API Usage Guide

> **13 methods** for structured data collection from web, academic, news, and knowledge graph sources.
> All methods live in `storage/scripts/data-gathering/methods/`.
> No API keys required (all use free/public endpoints).

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Method Reference](#method-reference)
   - [DG-0001: Multi-query Web Search](#dg-0001-multi-query-web-search)
   - [DG-0002: Wikipedia API Deep Extraction](#dg-0002-wikipedia-api-deep-extraction)
   - [DG-0003: arXiv Academic Paper Search](#dg-0003-arxiv-academic-paper-search)
   - [DG-0004: Hybrid Multi-Source Aggregation v2](#dg-0004-hybrid-multi-source-aggregation-v2)
   - [DG-0005: Iterative Deepening Search](#dg-0005-iterative-deepening-search)
   - [DG-0006: Guardian News Search](#dg-0006-guardian-news-search)
   - [DG-0007: Semantic Scholar Paper Search](#dg-0007-semantic-scholar-paper-search)
   - [DG-0008: Wikidata Knowledge Graph Search](#dg-0008-wikidata-knowledge-graph-search)
   - [DG-0009: GitHub Repository Search](#dg-0009-github-repository-search)
   - [DG-0005 (HN): Hacker News Search](#dg-0005-hn-hacker-news-search)
   - [OpenAlex Academic Search](#openalex-academic-search)
3. [Shared Utilities](#shared-utilities)
   - [ddg_utils.py](#ddg_utilspy)
   - [request_cache.py](#request_cachepy)
   - [dedup_utils.py](#dedup_utilspy)
   - [benchmark_runner.py](#benchmark_runnerpy)
4. [Benchmarks Summary](#benchmarks-summary)

---

## Quick Start

All methods follow the same pattern: import the module, call `run(query)`, get a standardized dict back.

```python
import sys, os

# Add the methods directory to the Python path
METHODS_DIR = "/home/user/Claude/storage/scripts/data-gathering/methods"
sys.path.insert(0, METHODS_DIR)

# Use any single method
import method_websearch
result = method_websearch.run("artificial intelligence trading strategies")
print(result["metadata"]["data_points"])  # number of results
for r in result["results"]:
    print(r["title"], r["url"])

# Combine multiple methods manually
import method_arxiv
import method_openalex
import method_wikipedia

web = method_websearch.run("quantum computing")
arxiv = method_arxiv.run("quantum computing")
openalex = method_openalex.run("quantum computing")
wiki = method_wikipedia.run("quantum computing")

# Or use the hybrid method which does this automatically with
# parallel execution, cross-referencing, and deduplication
import method_hybrid
combined = method_hybrid.run("quantum computing")
for r in combined["results"][:5]:
    print(f"[{r['source_type']}] {r['title']} (score: {r['weighted_score']})")
```

### Standardized Return Format

Every `run(query)` returns a dict with at minimum:

```python
{
    "method_id": "DG-XXXX",
    "method_name": "Human-readable Name",
    "query": "the original query",
    "results": [ ... ],         # list of result dicts (structure varies by method)
    "metadata": {
        "duration_seconds": 2.5,
        "sources_count": 10,
        "data_points": 15,
        # ... method-specific extras
    }
}
```

### CLI Usage

Every method also works standalone from the command line:

```bash
cd /home/user/Claude/storage/scripts/data-gathering/methods
python3 method_websearch.py "your query"    # outputs JSON to stdout
```

---

## Method Reference

---

### DG-0001: Multi-query Web Search

**Source:** DuckDuckGo HTML search
**File:** `method_websearch.py`

**What it does:** Takes a query, generates 5 variant queries (original, + "overview research", reordered words, + "techniques methods", + "recent developments"), searches DuckDuckGo HTML for each variant, deduplicates results by URL.

**Import and usage:**
```python
from method_websearch import run
result = run("machine learning optimization")
```

**Parameters:**
- `run(query: str) -> dict` -- single string query

**Return format:**
```python
{
    "method_id": "DG-0001",
    "method_name": "Multi-query Web Search",
    "query": "...",
    "results": [
        {
            "title": "Page Title",
            "url": "https://...",
            "snippet": "Description text...",
            "source_query": "the variant query that found this result"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 4.86,
        "sources_count": 31,
        "data_points": 31,
        "query_variants_used": ["original", "original overview research", ...]
    }
}
```

**Rate limits and caveats:**
- DuckDuckGo rate limit: 0.5s between requests (enforced by `ddg_utils.py`)
- 5 queries per call = ~2.5s minimum from rate limiting alone
- DuckDuckGo may return CAPTCHAs under heavy use
- Results are web-quality (not curated); no abstracts or structured metadata

**Example:**
```python
result = run("renewable energy storage")
print(f"Found {result['metadata']['data_points']} results in {result['metadata']['duration_seconds']}s")
for r in result["results"][:3]:
    print(f"  {r['title']}: {r['url']}")
```

---

### DG-0002: Wikipedia API Deep Extraction

**Source:** Wikipedia MediaWiki API
**File:** `method_wikipedia.py`

**What it does:** Searches Wikipedia for articles, then for each hit extracts: full intro extract, categories, internal links, external references, section headings, and word count. Also follows "See also" links from the top 2 articles for breadth. Falls back to DuckDuckGo site-scoped search if the Wikipedia API is blocked.

**Import and usage:**
```python
from method_wikipedia import run
result = run("quantum computing")
```

**Parameters:**
- `run(query: str) -> dict`

**Return format:**
```python
{
    "method_id": "DG-0002",
    "method_name": "Wikipedia API Deep Extraction",
    "query": "...",
    "results": [
        {
            "title": "Quantum computing",
            "page_id": 25202,
            "extract": "Quantum computing is a type of computation...",
            "categories": ["Quantum computing", "Emerging technologies", ...],
            "internal_links": ["Qubit", "Quantum gate", ...],  # up to 10
            "external_references": ["https://...", ...],        # up to 10
            "url": "https://en.wikipedia.org/wiki/Quantum_computing",
            "search_snippet": "HTML snippet from search...",
            "word_count": 15234,
            "sections": [
                {"index": "1", "heading": "Overview", "level": "2"},
                ...
            ],
            "found_via": "see_also"  # present only for see-also results
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 1.47,
        "sources_count": 6,
        "data_points": 1,
        "articles_searched": 5,
        "see_also_followed": 3,
        "used_fallback": false
    }
}
```

**Rate limits and caveats:**
- Wikipedia API has no hard rate limit but requests a polite User-Agent
- `data_points` counts only articles with non-empty extracts
- Fallback mode (DuckDuckGo) returns less structured data: no categories, no sections, no internal links
- In benchmarks, sometimes returns limited results (1 data point) depending on query

**Example:**
```python
result = run("artificial intelligence")
for article in result["results"]:
    print(f"{article['title']} ({article['word_count']} words)")
    print(f"  Categories: {', '.join(article['categories'][:5])}")
    print(f"  Sections: {len(article.get('sections', []))}")
```

---

### DG-0003: arXiv Academic Paper Search

**Source:** arXiv Atom API
**File:** `method_arxiv.py`

**What it does:** Queries the arXiv API for up to 20 papers sorted by relevance. Parses the Atom XML to extract title, abstract, authors, dates, categories, arXiv ID, and PDF URL. Falls back to DuckDuckGo site-scoped search (`site:arxiv.org`) if the API fails.

**Import and usage:**
```python
from method_arxiv import run
result = run("transformer attention mechanism")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal: `search_arxiv(query, max_results=20, sort_by="relevance")` is also callable directly

**Return format:**
```python
{
    "method_id": "DG-0003",
    "method_name": "arXiv Academic Paper Search",
    "query": "...",
    "results": [
        {
            "title": "Attention Is All You Need",
            "abstract": "The dominant sequence transduction models...",
            "authors": ["Ashish Vaswani", "Noam Shazeer", ...],
            "published": "2017-06-12T17:57:34Z",
            "updated": "2017-12-06T00:00:00Z",
            "arxiv_url": "http://arxiv.org/abs/1706.03762v7",
            "arxiv_id": "1706.03762v7",
            "pdf_url": "http://arxiv.org/pdf/1706.03762v7",
            "primary_category": "cs.CL",
            "categories": ["cs.CL", "cs.LG"],
            "comment": "15 pages, 5 figures"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 2.66,
        "sources_count": 9,
        "data_points": 9,
        "unique_authors": 25,
        "unique_categories": ["cs.CL", "cs.LG", "cs.AI"],
        "errors": null,
        "used_fallback": false
    }
}
```

**Rate limits and caveats:**
- arXiv API: ~3 requests/second; no API key needed
- API uses `export.arxiv.org` (the API endpoint, not the main site)
- Fallback results lack authors, dates, categories, and PDF URLs
- In iteration 1 this method returned 0 results due to HTTP vs HTTPS bug (now fixed)

**Example:**
```python
result = run("large language models")
for paper in result["results"][:3]:
    print(f"{paper['title']}")
    print(f"  Authors: {', '.join(paper['authors'][:3])}")
    print(f"  Category: {paper['primary_category']}")
    print(f"  PDF: {paper['pdf_url']}")
```

---

### DG-0004: Hybrid Multi-Source Aggregation v2

**Source:** Combines DuckDuckGo + Wikipedia + arXiv + OpenAlex + Wikidata
**File:** `method_hybrid.py`

**What it does:** Runs 5 sub-methods in parallel using `ThreadPoolExecutor`, normalizes all results into a common format, cross-references findings across sources (keyword overlap and URL matching), applies credibility weighting by source tier, and deduplicates using TF-IDF cosine similarity. This is the most comprehensive single-call method.

**Import and usage:**
```python
from method_hybrid import run, run_with_results
result = run("climate change mitigation")

# Or, if you already ran standalone methods and want to avoid re-execution:
precomputed = {
    "web_search": websearch_result,
    "wikipedia": wiki_result,
    "arxiv": arxiv_result,
    "openalex": openalex_result,
    "wikidata": wikidata_result,
}
result = run_with_results("climate change mitigation", precomputed)
```

**Parameters:**
- `run(query: str) -> dict` -- runs all 5 sub-methods in parallel
- `run_with_results(query: str, precomputed_results: dict) -> dict` -- uses pre-computed results (avoids double-execution in benchmarks)

**Credibility tiers:**
| Source    | Tier                  | Weight |
|-----------|-----------------------|--------|
| arxiv     | academic              | 1.5    |
| openalex  | academic              | 1.5    |
| wikipedia | encyclopedia          | 1.2    |
| wikidata  | structured_knowledge  | 1.1    |
| web_search| web                   | 1.0    |

**Return format:**
```python
{
    "method_id": "DG-0004",
    "method_name": "Hybrid Multi-Source Aggregation v2",
    "query": "...",
    "results": [
        {
            "title": "...",
            "url": "...",
            "snippet": "first 300 chars of content...",
            "source_type": "arxiv",               # which sub-method found this
            "credibility_tier": "academic",
            "credibility_weight": 1.5,
            "corroboration_score": 2.5,            # higher = confirmed by more sources
            "corroborating_sources": ["arxiv", "openalex_related"],
            "weighted_score": 3.75,                # corroboration * weight * bonuses
            # source-specific fields may also be present:
            "authors": [...],
            "published": "...",
            "categories": [...],
            "cited_by_count": 150,
        },
        ...  # sorted by weighted_score descending
    ],
    "metadata": {
        "duration_seconds": 5.11,
        "sources_count": 40,
        "data_points": 46,
        "sub_method_stats": {
            "web_search": {"results": 32, "duration": 4.8, "error": null},
            "wikipedia": {"results": 1, "duration": 1.5, "error": null},
            "arxiv": {"results": 9, "duration": 2.7, "error": null},
            "openalex": {"results": 15, "duration": 0.5, "error": null},
            "wikidata": {"results": 10, "duration": 1.2, "error": null},
        },
        "corroborated_findings": 12,
        "avg_corroboration_score": 1.8,
        "avg_weighted_score": 2.1,
        "credibility_tier_distribution": {
            "academic": 20, "encyclopedia": 1, "structured_knowledge": 8, "web": 25
        },
        "deduplication_applied": true
    }
}
```

**Rate limits and caveats:**
- Inherits rate limits of all 5 sub-methods
- Parallel execution means total time is roughly max(sub-method times), not the sum
- Cross-referencing requires >=4 keyword overlap to count (reduced from 3 to avoid false positives)
- Content-less results (snippet < 20 chars) get a 40% weight penalty
- Results corroborated by 3+ distinct source types get a 20% weight bonus
- TF-IDF deduplication threshold: 0.65 cosine similarity

**Example:**
```python
result = run("CRISPR gene editing applications")
# Top results are sorted by weighted_score -- most credible and corroborated first
for r in result["results"][:5]:
    print(f"[{r['credibility_tier']}] {r['title']} (score: {r['weighted_score']})")
    print(f"  Corroborated by: {r['corroborating_sources']}")
```

---

### DG-0005: Iterative Deepening Search

**Source:** DuckDuckGo HTML search (3 rounds)
**File:** `method_iterative.py`

**What it does:** Performs 3 rounds of progressively deeper searching. Round 1 uses the original query. Each subsequent round analyzes accumulated results to extract emergent key terms and generates 3 new focused queries. Deduplicates by URL across all rounds. Also produces a synthesis of top themes.

**Import and usage:**
```python
from method_iterative import run
result = run("renewable energy storage")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal constant: `MAX_ROUNDS = 3`

**Return format:**
```python
{
    "method_id": "DG-0005",
    "method_name": "Iterative Deepening Search",
    "query": "...",
    "results": [
        {
            "title": "...",
            "url": "...",
            "snippet": "...",
            "depth_level": 1,         # which round found this (1, 2, or 3)
            "search_query": "the actual query used"
        },
        ...
    ],
    "synthesis": {
        "top_themes": [
            {"term": "battery", "frequency": 15},
            {"term": "lithium", "frequency": 12},
            ...  # up to 15 themes
        ],
        "total_rounds": 3,
        "rounds": [
            {
                "round": 1,
                "queries_used": ["renewable energy storage"],
                "new_results_found": 18,
                "duration_seconds": 2.1
            },
            {
                "round": 2,
                "queries_used": ["renewable energy storage battery lithium", ...],
                "new_results_found": 14,
                "duration_seconds": 3.0
            },
            ...
        ]
    },
    "metadata": {
        "duration_seconds": 7.88,
        "sources_count": 43,
        "data_points": 43,
        "depth_distribution": {"level_1": 18, "level_2": 14, "level_3": 11}
    }
}
```

**Rate limits and caveats:**
- Slowest single method (~8s) because it makes 7+ sequential DuckDuckGo requests across 3 rounds
- Each round has up to 3 queries, each with 0.5s rate limit
- Quality of deeper rounds depends on the quality of round 1 results
- Stop words are filtered when extracting key terms; terms must appear >=2 times to be used

**Example:**
```python
result = run("autonomous vehicles safety")
print(f"Explored {result['synthesis']['total_rounds']} rounds")
for theme in result["synthesis"]["top_themes"][:5]:
    print(f"  Theme: {theme['term']} (mentioned {theme['frequency']} times)")
print(f"Depth distribution: {result['metadata']['depth_distribution']}")
```

---

### DG-0006: Guardian News Search

**Source:** The Guardian Content API
**File:** `method_guardian.py`

**What it does:** Searches The Guardian's Content API for news articles. Extracts headline, standfirst, body text (truncated to 500 chars), section name, publication date, and type. Uses the free `test` API key. Falls back to DuckDuckGo site-scoped search if the API is unavailable.

**Import and usage:**
```python
from method_guardian import run
result = run("artificial intelligence regulation")
```

**Parameters:**
- `run(query: str) -> dict`

**Return format:**
```python
{
    "method_id": "DG-0006",
    "method_name": "Guardian News Search",
    "query": "...",
    "results": [
        {
            "webTitle": "AI regulation: EU passes landmark act",
            "webUrl": "https://www.theguardian.com/...",
            "sectionName": "Technology",
            "webPublicationDate": "2024-03-15T10:30:00Z",
            "headline": "AI regulation: EU passes landmark act",
            "standfirst": "New rules will govern...",
            "bodyText": "The European Union has... (truncated to 500 chars)",
            "type": "article",
            "pillarName": "News"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 1.2,
        "sources_count": 20,
        "data_points": 18,      # count of articles with non-empty bodyText
        "sections": ["Technology", "World news", "Business"],
        "errors": null,
        "used_fallback": false
    }
}
```

**Rate limits and caveats:**
- Uses the `test` API key -- limited to 1 request/second and 500 results/day
- Body text is truncated to 500 characters
- Fallback mode returns only title and snippet, no body text or section info
- Good for current events and news analysis; English-language content only

**Example:**
```python
result = run("climate summit 2025")
for article in result["results"][:3]:
    print(f"[{article['sectionName']}] {article['headline']}")
    print(f"  Published: {article['webPublicationDate']}")
    print(f"  {article['standfirst']}")
```

---

### DG-0007: Semantic Scholar Paper Search

**Source:** Semantic Scholar Academic Graph API
**File:** `method_semantic_scholar.py`

**What it does:** Searches the Semantic Scholar API for papers. Returns rich metadata including AI-generated TLDRs, citation counts, fields of study, external IDs (DOI, ArXiv), and open access PDF links.

**Import and usage:**
```python
from method_semantic_scholar import run
result = run("graph neural networks")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal: `search_papers(query, limit=20)` is also callable directly

**Return format:**
```python
{
    "method_id": "DG-0007",
    "method_name": "Semantic Scholar Paper Search",
    "query": "...",
    "results": [
        {
            "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "title": "Semi-Supervised Classification with Graph...",
            "abstract": "We present a scalable approach...",
            "year": 2017,
            "citationCount": 15000,
            "url": "https://www.semanticscholar.org/paper/...",
            "authors": ["Thomas N. Kipf", "Max Welling"],
            "tldr": "A scalable approach for semi-supervised learning...",
            "fieldsOfStudy": ["Computer Science", "Mathematics"],
            "doi": "10.48550/arXiv.1609.02907",
            "arxivId": "1609.02907",
            "openAccessPdfUrl": "https://arxiv.org/pdf/1609.02907.pdf"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 2.0,
        "sources_count": 20,
        "data_points": 20,
        "unique_authors": 45,
        "unique_fields": ["Computer Science", "Mathematics"],
        "errors": null
    }
}
```

**Rate limits and caveats:**
- Hard-coded 1-second sleep before each request (respects Semantic Scholar's 1 req/sec limit for unauthenticated users)
- No API key required but authenticated users get 100 req/sec
- TLDRs are AI-generated summaries -- not always available for every paper
- `fieldsOfStudy` may be empty for some papers
- New method (iteration 3) -- no benchmark data yet

**Example:**
```python
result = run("reinforcement learning robotics")
for paper in result["results"][:3]:
    print(f"{paper['title']} ({paper['year']})")
    print(f"  Citations: {paper['citationCount']}")
    if paper['tldr']:
        print(f"  TLDR: {paper['tldr']}")
```

---

### DG-0008: Wikidata Knowledge Graph Search

**Source:** Wikidata API (MediaWiki-based)
**File:** `method_wikidata.py`

**What it does:** Two-phase search: (1) finds up to 10 matching entities via `wbsearchentities`, then (2) fetches detailed properties for each entity including labels, descriptions, aliases, instance_of/subclass_of claims (as Q-IDs), and Wikipedia sitelinks.

**Import and usage:**
```python
from method_wikidata import run
result = run("machine learning")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal helpers: `search_entities(query, limit=10)`, `get_entity_details(entity_id)`

**Return format:**
```python
{
    "method_id": "DG-0008",
    "method_name": "Wikidata Knowledge Graph Search",
    "query": "...",
    "results": [
        {
            "id": "Q2539",
            "label": "machine learning",
            "description": "branch of artificial intelligence",
            "aliases": ["ML", "statistical learning"],
            "instance_of": ["Q11862829"],     # Q-IDs, not labels
            "subclass_of": ["Q11660"],
            "wikipedia_title": "Machine learning",
            "wikipedia_url": "https://en.wikipedia.org/wiki/Machine_learning",
            "wikidata_url": "https://www.wikidata.org/wiki/Q2539"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 2.0,
        "sources_count": 10,
        "data_points": 10,
        "entities_searched": 10,
        "errors": null
    }
}
```

**Rate limits and caveats:**
- Makes N+1 API calls (1 search + N entity detail fetches), so 11 calls for 10 entities
- `instance_of` and `subclass_of` return Q-IDs, not human-readable labels (resolving labels would require additional API calls)
- Useful for structured knowledge: "what kind of thing is X?", aliases, cross-language links
- Pairs well with Wikipedia method for deeper context

**Example:**
```python
result = run("Python programming language")
for entity in result["results"][:3]:
    print(f"{entity['label']}: {entity['description']}")
    print(f"  Aliases: {entity['aliases']}")
    print(f"  Wikipedia: {entity['wikipedia_url']}")
```

---

### DG-0009: GitHub Repository Search

**Source:** GitHub REST API v3
**File:** `method_github.py`

**What it does:** Searches GitHub for repositories (sorted by stars, up to 20) and code matches (up to 10). Extracts repo metadata: full name, description, stars, language, topics, license, forks, and open issues.

**Import and usage:**
```python
from method_github import run
result = run("machine learning framework")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal helpers: `search_repositories(query, per_page=20)`, `search_code(query, per_page=10)`

**Return format:**
```python
{
    "method_id": "DG-0009",
    "method_name": "GitHub Repository Search",
    "query": "...",
    "results": [          # repository results
        {
            "full_name": "tensorflow/tensorflow",
            "description": "An Open Source Machine Learning Framework...",
            "html_url": "https://github.com/tensorflow/tensorflow",
            "stargazers_count": 180000,
            "language": "C++",
            "topics": ["machine-learning", "deep-learning", "tensorflow"],
            "updated_at": "2026-03-10T12:00:00Z",
            "forks_count": 75000,
            "open_issues_count": 2500,
            "license": "Apache License 2.0"
        },
        ...
    ],
    "code_results": [     # separate code search results
        {
            "name": "model.py",
            "path": "src/model.py",
            "html_url": "https://github.com/.../src/model.py",
            "repository": "user/repo",
            "repository_url": "https://github.com/user/repo",
            "repository_description": "..."
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 1.5,
        "sources_count": 20,
        "data_points": 30,             # repos + code results
        "repositories_found": 20,
        "code_matches_found": 10,
        "unique_languages": ["Python", "C++", "JavaScript"],
        "unique_topics": ["machine-learning", "deep-learning"],
        "errors": null,
        "rate_limited": false
    }
}
```

**Rate limits and caveats:**
- **Unauthenticated: 10 search requests/minute** -- most restrictive of all methods
- Returns HTTP 403 when rate limited; error is captured in metadata
- Makes 2 API calls per `run()` (repos + code), so 5 calls/minute max
- Code search sometimes requires authentication
- `license` may be empty if the repo has no license file

**Example:**
```python
result = run("natural language processing")
if result["metadata"]["rate_limited"]:
    print("Rate limited! Try again in 60 seconds.")
else:
    for repo in result["results"][:5]:
        print(f"{repo['full_name']} ({repo['stargazers_count']} stars)")
        print(f"  {repo['description']}")
        print(f"  Language: {repo['language']}, License: {repo['license']}")
```

---

### DG-0005 (HN): Hacker News Search

**Source:** Algolia HN Search API + Firebase HN API
**File:** `method_hackernews.py`

**What it does:** Searches Hacker News stories via the Algolia-powered API (up to 20 results) and also fetches the current top 10 stories from the Firebase API. Good for tech community sentiment and trending topics.

> Note: This file uses `METHOD_ID = "DG-0005"` which overlaps with method_iterative.py. The index.json does not include this method.

**Import and usage:**
```python
from method_hackernews import run
result = run("rust programming language")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal helpers: `search_stories(query, hits_per_page=20)`, `fetch_top_stories(limit=10)`

**Return format:**
```python
{
    "method_id": "DG-0005",
    "method_name": "Hacker News Search",
    "query": "...",
    "results": [           # Algolia search results
        {
            "title": "Show HN: A Rust web framework",
            "url": "https://example.com/...",
            "author": "username",
            "points": 350,
            "num_comments": 120,
            "created_at": "2026-03-01T12:00:00.000Z",
            "objectID": "12345678",
            "hn_url": "https://news.ycombinator.com/item?id=12345678"
        },
        ...
    ],
    "top_stories": [       # separate: current top stories (not query-filtered)
        {
            "title": "...",
            "url": "...",
            "author": "...",
            "points": 500,
            "num_comments": 200,
            "created_at": 1709300000,     # Unix timestamp from Firebase API
            "objectID": "12345679",
            "hn_url": "https://news.ycombinator.com/item?id=12345679"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 3.0,
        "sources_count": 20,
        "top_stories_count": 10,
        "data_points": 30,
        "unique_authors": 25,
        "total_points": 2500,
        "errors": null
    }
}
```

**Rate limits and caveats:**
- Algolia HN API: generous rate limits, no key needed
- Firebase API: makes N+1 calls (1 for story IDs + N for individual stories), so 11 calls for 10 top stories
- `top_stories` are NOT filtered by query -- they are the current HN front page
- `created_at` format differs between Algolia (ISO string) and Firebase (Unix int)

**Example:**
```python
result = run("GPT-4 benchmarks")
print(f"Found {result['metadata']['sources_count']} relevant stories")
for story in sorted(result["results"], key=lambda s: s["points"], reverse=True)[:3]:
    print(f"  {story['title']} ({story['points']} pts, {story['num_comments']} comments)")
```

---

### OpenAlex Academic Search

**Source:** OpenAlex API (250M+ academic works)
**File:** `method_openalex.py`

**What it does:** Searches the OpenAlex catalog of academic works. Extracts title, authors, publication year, citation count, DOI, open access status/URL, reconstructed abstract (from inverted index format), and host venue details.

> Note: This file uses `METHOD_ID = "DG-0004"` which overlaps with method_hybrid.py. The index.json lists it as DG-0006.

**Import and usage:**
```python
from method_openalex import run
result = run("deep reinforcement learning")
```

**Parameters:**
- `run(query: str) -> dict`
- Internal: `search_works(query, per_page=20)`

**Return format:**
```python
{
    "method_id": "DG-0004",
    "method_name": "OpenAlex Academic Search",
    "query": "...",
    "results": [
        {
            "title": "Playing Atari with Deep Reinforcement Learning",
            "authors": ["Volodymyr Mnih", "Koray Kavukcuoglu", ...],
            "publication_year": 2013,
            "cited_by_count": 8500,
            "doi": "https://doi.org/10.48550/arXiv.1312.5602",
            "open_access": {
                "is_oa": true,
                "oa_status": "gold",
                "oa_url": "https://arxiv.org/pdf/1312.5602"
            },
            "abstract": "We present the first deep learning model...",
            "host_venue": {
                "name": "ArXiv",
                "type": "repository",
                "issn_l": "",
                "is_oa": true
            },
            "openalex_id": "https://openalex.org/W2100...",
            "type": "article"
        },
        ...
    ],
    "metadata": {
        "duration_seconds": 0.5,
        "sources_count": 15,
        "data_points": 15,
        "unique_authors": 40,
        "total_citations": 25000,
        "errors": null
    }
}
```

**Rate limits and caveats:**
- Fastest academic source (~0.5s typical)
- No API key required; uses `mailto` parameter for polite pool (faster responses)
- Abstracts are reconstructed from an inverted index and may have minor word-order issues
- `total_citations` in metadata is the sum across all returned works -- useful for gauging topic impact
- Very good coverage: 250M+ works from all disciplines

**Example:**
```python
result = run("protein folding prediction")
# Sort by citation count for most influential papers
papers = sorted(result["results"], key=lambda p: p["cited_by_count"], reverse=True)
for p in papers[:3]:
    print(f"{p['title']} ({p['publication_year']})")
    print(f"  Citations: {p['cited_by_count']}, DOI: {p['doi']}")
    if p['open_access']['is_oa']:
        print(f"  Open Access: {p['open_access']['oa_url']}")
```

---

## Shared Utilities

### ddg_utils.py

Central DuckDuckGo search module used by methods DG-0001, DG-0002, DG-0003, and DG-0005.

**Classes:**
- `DuckDuckGoParser(HTMLParser)` -- parses DuckDuckGo HTML results pages, extracting `url`, `title`, and `snippet` from each result.

**Functions:**

```python
from ddg_utils import search_duckduckgo, set_user_agent, set_rate_limit

# Main search function
results = search_duckduckgo(
    query="your search",          # required
    timeout=10,                   # optional, default: 10 seconds
    user_agent="Custom/1.0",     # optional, override per-request
)
# Returns: list[dict] with keys: url, title, snippet
# On error: [{"error": "...", "query": "..."}]

# Configuration
set_user_agent("MyBot/2.0")     # change default UA for all future requests
set_rate_limit(1.0)             # change delay between requests (default: 0.5s)
```

**Key details:**
- Uses `https://html.duckduckgo.com/html/` (the HTML-only endpoint, more scraping-friendly)
- Module-level rate limiting: 0.5s minimum between any two DDG requests (shared across all callers)
- Automatically integrates with `request_cache.py` if available
- `ddg_search` is an alias for `search_duckduckgo`

---

### request_cache.py

File-based HTTP GET cache layer. All methods auto-import this; it is used transparently.

**Cache location:** `storage/scripts/data-gathering/.cache/`
**Cache key:** SHA-256 of URL + sorted headers
**Storage format:** JSON files with base64-encoded response body

**Functions:**

```python
from request_cache import cached_request, clear_cache, cache_stats

# Cached HTTP GET -- drop-in replacement for urllib
body_bytes = cached_request(
    url="https://api.example.com/data",
    headers={"Accept": "application/json"},   # optional, also used in cache key
    timeout=10,                               # optional, default: 10 seconds
    ttl=3600,                                 # optional, default: 3600 seconds (1 hour)
)
# Returns: bytes (raw response body)
# Raises: urllib.error.HTTPError, urllib.error.URLError on network failure

# Cache maintenance
removed_count = clear_cache()           # remove expired entries only
removed_count = clear_cache(all=True)   # remove ALL entries

# Cache inspection
stats = cache_stats()
# Returns: {
#     "total_entries": 42,
#     "expired_entries": 5,
#     "active_entries": 37,
#     "total_bytes": 1048576,
#     "cache_dir": "/home/user/Claude/storage/scripts/data-gathering/.cache"
# }
```

**Key details:**
- Cache hits are served with zero network latency
- Corrupt cache files are automatically deleted and re-fetched
- Default TTL is 1 hour; override per-request with the `ttl` parameter
- All methods set `USE_CACHE = True` and gracefully fall back to direct HTTP if the import fails

---

### dedup_utils.py

TF-IDF-based deduplication and similarity scoring. Used by `method_hybrid.py`.

**Functions:**

```python
from dedup_utils import (
    deduplicate_results,    # main dedup function
    similarity_score,       # compare two text strings
    tokenize,               # text -> word list (stop words removed)
    build_idf_from_results, # pre-compute IDF from result list
    merge_citation_lists,   # merge citation lists, removing duplicates
)

# Deduplicate a list of result dicts
deduped = deduplicate_results(
    results,                # list of dicts with title/snippet/abstract/etc.
    threshold=0.65,         # cosine similarity threshold (default: 0.65)
    idf=None,               # optional pre-computed IDF dict
)
# Returns: list of dicts with duplicates merged; adds:
#   "merged_from_sources", "duplicate_count", boosted "corroboration_score"

# Compare two text strings
score = similarity_score("text about AI", "article on artificial intelligence")
# Returns: float between 0.0 and 1.0

# Merge citation lists from multiple sources
merged = merge_citation_lists(list1, list2, list3)
```

**Key details:**
- Pure Python implementation (no numpy/scipy dependency)
- `STOP_WORDS` set is exported and reused by `method_hybrid.py`
- When duplicates are merged, the first occurrence is kept and subsequent ones contribute source info
- Threshold of 0.65 balances dedup aggressiveness; lower = more aggressive merging

---

### benchmark_runner.py

Runs all methods against a test query and collects performance metrics.

**CLI usage:**
```bash
cd /home/user/Claude/storage/scripts/data-gathering/methods
python3 benchmark_runner.py "test query"                           # parallel, stdout
python3 benchmark_runner.py "test query" output.json               # save to file
python3 benchmark_runner.py "test query" --sequential output.json  # sequential mode
```

**Python usage:**
```python
from benchmark_runner import run_benchmark, compute_quality_score

results = run_benchmark(
    query="artificial intelligence",
    methods_to_run=None,     # None = all methods; or pass a list of method dicts
    parallel=True,           # default: parallel execution
)
# Returns dict with: version, iteration, benchmarks[], summary{}

# Quality scoring for any method result
score = compute_quality_score(method_result)  # returns 0-100
```

**Key details:**
- Auto-detects iteration number from existing benchmark files
- In parallel mode, standalone methods run concurrently, then hybrid reuses their results via `run_with_results()`
- Quality score (0-100) factors: data points (30), unique sources (20), content richness (20), citations/corroboration (15), source diversity (15)
- Output goes to `storage/scripts/data-gathering/benchmarks/`

---

## Benchmarks Summary

Performance data is documented in knowledge base entries **KB-0004** and **KB-0005**.

### Iteration 1 (initial implementation)
- **1 of 4 methods working** (only DG-0001 Web Search returned results)
- Wikipedia: failed silently (HTTP 403, bad User-Agent)
- arXiv: failed (HTTP vs HTTPS URL bug)
- Hybrid: failed because sub-methods failed

### Iteration 2 (bug fixes + new sources)
- **5 of 7 methods working**
- Fixed Wikipedia User-Agent, arXiv HTTPS, added DuckDuckGo fallbacks
- Added OpenAlex and Wikidata as new sources
- Added parallel execution to hybrid and benchmark runner
- Added TF-IDF deduplication

| Method          | Speed   | Data Points | Quality Score |
|-----------------|---------|-------------|---------------|
| WebSearch       | 4.86s   | 32          | 75            |
| Wikipedia       | 1.47s   | 1           | 10            |
| arXiv           | 2.66s   | 9           | 50            |
| Iterative       | 7.88s   | 43          | 75            |
| OpenAlex        | 0.50s   | 15          | 65            |
| Wikidata        | ~2s     | 10          | --            |
| **Hybrid v2**   | 5.11s   | 46          | **92**        |

### Iteration 3 (new methods, not yet benchmarked)
- Added: Semantic Scholar (DG-0007), Guardian News (DG-0006), GitHub (DG-0009), Hacker News
- These are not yet integrated into the hybrid method or benchmark runner

### Recommendations for Future Teams
1. **For general research:** Use `method_hybrid.run()` -- best quality score (92) and covers 5 sources in parallel.
2. **For academic papers:** Use `method_openalex.run()` (fastest, richest metadata) or `method_semantic_scholar.run()` (has TLDRs).
3. **For current events/news:** Use `method_guardian.run()`.
4. **For tech/developer topics:** Combine `method_hackernews.run()` + `method_github.run()`.
5. **For deep exploration of a topic:** Use `method_iterative.run()` for progressive deepening.
6. **For structured knowledge/ontologies:** Use `method_wikidata.run()`.
7. **Watch GitHub rate limits** -- only 10 unauthenticated search requests/minute.
