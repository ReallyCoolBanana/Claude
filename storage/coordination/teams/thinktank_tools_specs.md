# Think Tank Tool Specifications

**Author:** THINK-TANK-TOOLS
**Date:** 2026-03-11
**Status:** Ready for implementation

---

## Gap Analysis Summary

After reviewing the full codebase (40 scripts, 1 API tool, 35 data sources, 28 KB entries, coordination system with bus_core, sdk_cli, dashboard, think_tank), the following gaps were identified:

1. **No web scraping pipeline** -- Data gathering methods (SCR-0006 through SCR-0028) query APIs but none scrape and structure HTML documentation from official sources.
2. **Finding deduplication is ad-hoc** -- `think_tank.py` has a `_fingerprint()` function using title+category hash, and `merge_research.py` (SCR-0005) uses string similarity, but there is no standalone, reusable dedup tool that works across all artifact types (KB entries, team findings, bus messages).
3. **No automated quality scoring** -- `grade_research.py` (SCR-0004) scores research JSON on 5 dimensions but does not score individual findings by source authority, freshness, or completeness.
4. **No archive formatter** -- Teams manually create KB entries. `index_rebuilder.py` (SCR-0013) rebuilds indexes but nothing generates entry files from raw findings.
5. **Cross-team search is fragmented** -- `kb_search.py` (SCR-0018) searches KB+logs+scripts via TF-IDF, and `fast-search` (SCR-0015, Go) builds an inverted index of files. Neither searches bus messages, SQLite team_findings, or team output JSON files.
6. **Dashboard lacks bus-level detail** -- `dashboard.py` (SCR-0032) shows agent status and summary bus counts but does not display per-channel message rates, message type breakdowns, or latency metrics.

---

## Tool 1: Web Scraper Pipeline

**Priority:** P1
**ID:** TOOL-SPEC-001

### Purpose
Reusable pipeline for data gathering teams to scrape, parse, and structure HTML documentation from official sources (e.g., API docs, framework references, government portals). Fills the gap between our API-based data gathering methods and sources that only provide HTML.

### Input/Output Format

**Input (CLI or function call):**
```python
{
    "url": "https://docs.example.com/api/v2",
    "mode": "single" | "crawl",        # single page or follow links
    "max_depth": 2,                      # crawl depth limit
    "max_pages": 50,                     # page count limit
    "selectors": {                       # optional CSS selectors
        "content": "main, article, .content",
        "title": "h1, title",
        "nav": "nav, .sidebar"           # for link discovery
    },
    "output_format": "json" | "markdown",
    "cache_ttl": 3600                    # use request_cache (SCR-0022)
}
```

**Output:**
```python
{
    "source_url": "https://docs.example.com/api/v2",
    "scraped_at": "2026-03-11T14:30:00Z",
    "pages": [
        {
            "url": "https://...",
            "title": "...",
            "content_text": "...",         # cleaned plain text
            "content_markdown": "...",     # structured markdown
            "headings": ["h1...", "h2..."],
            "code_blocks": ["..."],
            "links": [{"text": "...", "href": "..."}],
            "word_count": 1234,
            "scraped_at": "..."
        }
    ],
    "metadata": {
        "total_pages": 12,
        "total_words": 15000,
        "crawl_duration_ms": 4500
    }
}
```

### Key Functions/API

```python
# Module: storage/scripts/data-gathering/methods/method_webscraper.py

class WebScraperPipeline:
    def __init__(self, config: dict):
        """Initialize with config dict (see input format above)."""

    def scrape_page(self, url: str) -> dict:
        """Scrape a single page. Returns page dict."""

    def crawl(self, start_url: str) -> dict:
        """Crawl from start_url following links up to max_depth/max_pages."""

    def extract_content(self, html: str, selectors: dict) -> dict:
        """Parse HTML, extract text/markdown/code blocks/headings."""

    def to_markdown(self, html_element) -> str:
        """Convert HTML element to clean markdown."""

# CLI interface:
# python method_webscraper.py --url URL [--crawl] [--depth 2] [--max-pages 50] [--format json]
```

### Implementation Notes
- **stdlib only** -- Use `urllib.request` for HTTP, `html.parser.HTMLParser` for parsing. No BeautifulSoup or lxml.
- **Integrate with request_cache (SCR-0022)** for caching HTTP responses.
- **Respect robots.txt** -- Parse `/robots.txt` before crawling; honor `Crawl-delay`.
- **Rate limiting** -- Default 1 request/second, configurable. Integrate with `ddg_utils.py` rate limiter pattern.
- **HTML-to-markdown conversion** -- Handle `<h1>`-`<h6>`, `<p>`, `<code>`/`<pre>`, `<ul>`/`<ol>`, `<a>`, `<table>`, `<blockquote>`. Strip scripts, styles, nav chrome.
- **Error resilience** -- Retry on 429/503 with exponential backoff. Log and skip unreachable pages during crawl.
- **Register in scripts/index.json** as SCR-0041, category `data-collection`, depends on SCR-0022.

---

## Tool 2: Finding Deduplicator

**Priority:** P0
**ID:** TOOL-SPEC-002

### Purpose
Standalone tool to detect and merge duplicate findings across all artifact types: KB entries, team_findings (SQLite), bus messages, team output JSON files, and research JSON. Currently `think_tank.py` uses title+category fingerprinting and `merge_research.py` uses string similarity, but both are embedded and not reusable.

### Input/Output Format

**Input:**
```python
{
    "sources": [
        {"type": "kb", "path": "knowledge-base/"},
        {"type": "sqlite", "db": "storage/coordination/db/state.db", "table": "team_findings"},
        {"type": "jsonl", "path": "bus/*.jsonl"},
        {"type": "json", "paths": ["teams/alpha-1/*.json", "teams/beta-1/*.json"]}
    ],
    "similarity_threshold": 0.75,     # 0.0-1.0, Jaccard/cosine cutoff
    "merge_strategy": "keep_best" | "merge_all" | "report_only"
}
```

**Output:**
```python
{
    "total_findings": 342,
    "unique_findings": 285,
    "duplicate_clusters": [
        {
            "cluster_id": "DUP-001",
            "canonical": { ... },          # best version (highest quality score)
            "duplicates": [
                {"source": "kb/KB-0003", "similarity": 0.92, "finding": {...}},
                {"source": "sqlite/team_findings/42", "similarity": 0.88, "finding": {...}}
            ],
            "merged": { ... }              # if merge_strategy != report_only
        }
    ],
    "stats": {
        "dedup_rate": 0.167,
        "by_source": {"kb": 28, "sqlite": 150, "jsonl": 100, "json": 64},
        "processing_time_ms": 850
    }
}
```

### Key Functions/API

```python
# Module: storage/scripts/dedup_findings.py

class FindingDeduplicator:
    def __init__(self, threshold: float = 0.75):
        """Initialize with similarity threshold."""

    def load_sources(self, sources: list[dict]) -> list[dict]:
        """Load findings from all configured sources into normalized format."""

    def normalize(self, finding: dict, source_type: str) -> dict:
        """Normalize a finding to common schema: {id, title, body, tags, source, team, timestamp}."""

    def fingerprint(self, finding: dict) -> str:
        """Generate content fingerprint (SHA-256 of normalized title+body tokens)."""

    def similarity(self, a: dict, b: dict) -> float:
        """Compute similarity score between two normalized findings.
        Uses token-level Jaccard similarity on title + TF-IDF cosine on body."""

    def cluster(self, findings: list[dict]) -> list[dict]:
        """Group findings into duplicate clusters using union-find."""

    def merge_cluster(self, cluster: list[dict]) -> dict:
        """Merge a cluster into a single canonical finding. Picks highest-quality
        version as base, appends unique details from others."""

    def run(self, sources: list[dict], strategy: str = "report_only") -> dict:
        """Full pipeline: load -> normalize -> cluster -> merge -> report."""

# CLI:
# python dedup_findings.py --sources config.json [--threshold 0.75] [--strategy report_only]
# python dedup_findings.py --kb --sqlite [--threshold 0.8]   # shorthand for common sources
```

### Implementation Notes
- **Token-level Jaccard** for title similarity (fast, good for short strings).
- **TF-IDF cosine** for body similarity -- reuse the TF-IDF implementation pattern from `kb_search.py` (SCR-0018).
- **Union-find** for transitive clustering -- if A~B and B~C, they form one cluster even if A and C are below threshold.
- **Canonical selection** -- Pick the finding with the highest quality score (see Tool 3), or most recent if scores equal.
- **Performance target** -- Process 1000 findings in under 5 seconds on current hardware.
- **stdlib only** -- No numpy or sklearn.
- **Register as SCR-0042, category `analysis`.**

---

## Tool 3: Quality Scorer

**Priority:** P0
**ID:** TOOL-SPEC-003

### Purpose
Automated scoring of individual findings/data points based on source authority, freshness, completeness, and specificity. Unlike `grade_research.py` (SCR-0004) which scores entire research documents, this scores individual findings and is designed to be called by other tools (deduplicator, archive formatter, think tank).

### Input/Output Format

**Input (single finding):**
```python
{
    "title": "...",
    "body": "...",
    "source_url": "https://...",
    "source_type": "academic" | "official_docs" | "news" | "community" | "unknown",
    "timestamp": "2026-03-11T...",
    "tags": ["..."],
    "citations": ["..."],
    "team": "TEAM-0010",
    "has_code": true,
    "has_metrics": true
}
```

**Output:**
```python
{
    "overall_score": 0.82,             # 0.0-1.0 weighted composite
    "dimensions": {
        "authority": {
            "score": 0.9,
            "reason": "academic source (Semantic Scholar), 45 citations"
        },
        "freshness": {
            "score": 0.7,
            "reason": "published 2025-06-15, 9 months old"
        },
        "completeness": {
            "score": 0.85,
            "reason": "has title, body (450 words), source_url, tags, citations"
        },
        "specificity": {
            "score": 0.8,
            "reason": "includes code examples and quantitative metrics"
        },
        "corroboration": {
            "score": 0.75,
            "reason": "similar finding from 2 other teams"
        }
    },
    "grade": "A",                      # A/B/C/D/F letter grade
    "recommendations": [
        "Add more recent citations to improve freshness",
        "Cross-reference with official documentation"
    ]
}
```

### Key Functions/API

```python
# Module: storage/scripts/quality_scorer.py

class QualityScorer:
    def __init__(self, weights: dict | None = None):
        """Initialize with optional custom dimension weights.
        Defaults: authority=0.25, freshness=0.15, completeness=0.25,
                  specificity=0.20, corroboration=0.15"""

    def score(self, finding: dict) -> dict:
        """Score a single finding across all dimensions."""

    def score_batch(self, findings: list[dict]) -> list[dict]:
        """Score multiple findings. Corroboration is calculated across the batch."""

    def score_authority(self, finding: dict) -> tuple[float, str]:
        """Score source authority. Domain reputation lookup + citation count."""

    def score_freshness(self, finding: dict) -> tuple[float, str]:
        """Score temporal freshness. Exponential decay from current date."""

    def score_completeness(self, finding: dict) -> tuple[float, str]:
        """Score field completeness. Required vs optional field coverage + body length."""

    def score_specificity(self, finding: dict) -> tuple[float, str]:
        """Score specificity. Checks for code, metrics, named tools, concrete steps."""

    def score_corroboration(self, finding: dict, others: list[dict]) -> tuple[float, str]:
        """Score cross-team corroboration. How many other findings support this one."""

    def to_grade(self, score: float) -> str:
        """Convert 0-1 score to letter grade. A>=0.85, B>=0.70, C>=0.55, D>=0.40, F<0.40."""

# Authority domain tiers (built-in):
AUTHORITY_TIERS = {
    "tier_1": ["arxiv.org", "doi.org", "nature.com", "science.org", "acm.org", "ieee.org"],
    "tier_2": ["github.com", "docs.python.org", "developer.mozilla.org", "wikipedia.org"],
    "tier_3": ["stackoverflow.com", "medium.com", "dev.to", "hackernews"],
    "tier_4": ["reddit.com", "twitter.com", "unknown"]
}

# CLI:
# python quality_scorer.py --file finding.json
# python quality_scorer.py --batch findings.json --output scored.json
# cat finding.json | python quality_scorer.py --stdin
```

### Implementation Notes
- **Authority scoring** -- Parse `source_url` domain, look up in tier table. Tier 1 = 0.9-1.0, Tier 2 = 0.7-0.85, Tier 3 = 0.5-0.65, Tier 4 = 0.2-0.4. Add bonus for citation count (log scale).
- **Freshness scoring** -- Exponential decay: `score = exp(-age_days / 365)`. Anything under 30 days = 1.0. Over 2 years = 0.2.
- **Completeness** -- Check presence of: title (required), body (required), source_url, tags, citations, team, timestamp. Body length bonus: 100+ words = 0.1 bonus, 500+ words = 0.2 bonus.
- **Specificity** -- Regex detection of: code blocks, numeric metrics (percentages, dollar amounts, counts), named tools/libraries, step-by-step instructions.
- **Corroboration** -- Reuse deduplicator's similarity function. Score = min(1.0, matching_teams / 3).
- **stdlib only.**
- **Register as SCR-0043, category `analysis`.**

---

## Tool 4: Archive Formatter

**Priority:** P1
**ID:** TOOL-SPEC-004

### Purpose
Standardized tool to convert raw findings (from team output JSON, SQLite team_findings, bus messages) into properly formatted knowledge-base entries following `knowledge-base/TEMPLATE.md`. Eliminates manual KB entry creation.

### Input/Output Format

**Input:**
```python
{
    "findings": [
        {
            "title": "...",
            "body": "...",
            "tags": ["..."],
            "source_url": "...",
            "team": "TEAM-0010",
            "category": "integration",
            "quality_score": 0.82          # from Quality Scorer
        }
    ],
    "mode": "single" | "batch",
    "auto_id": true,                       # auto-assign next KB-XXXX ID
    "builds_on": ["KB-0014"],              # optional explicit builds_on
    "auto_builds_on": true                 # auto-detect via tag overlap
}
```

**Output (per entry):**
- File: `knowledge-base/entries/KB-XXXX.md` with YAML frontmatter
- Updated: `knowledge-base/index.json` (new entry + tag_index + categories)

**Generated file format:**
```markdown
---
id: KB-0029
title: "..."
date: 2026-03-11
team: TEAM-0010
category: integration
tags: [tag1, tag2, tag3]
status: draft
confidence: medium
builds_on: [KB-0014]
---

# [Title]

## Summary
[Auto-generated 2-3 sentence summary from body]

## Key Findings
[Bullet points extracted from body]

## Details
[Full body text, cleaned and formatted]

## Sources
- [source_url]

## Quality
- Score: 0.82 (A)
- Authority: 0.9 | Freshness: 0.7 | Completeness: 0.85
```

### Key Functions/API

```python
# Module: storage/scripts/archive_formatter.py

class ArchiveFormatter:
    def __init__(self, kb_dir: str = "knowledge-base/"):
        """Initialize with KB directory path."""

    def next_id(self) -> str:
        """Determine next available KB-XXXX ID by scanning index.json."""

    def detect_builds_on(self, tags: list[str], category: str) -> list[str]:
        """Auto-detect builds_on by finding existing KB entries with overlapping tags."""

    def generate_summary(self, body: str) -> str:
        """Extract first 2-3 meaningful sentences as summary."""

    def extract_key_findings(self, body: str) -> list[str]:
        """Extract bullet-point findings from body text."""

    def format_entry(self, finding: dict) -> str:
        """Generate full markdown entry with YAML frontmatter."""

    def write_entry(self, finding: dict, dry_run: bool = False) -> str:
        """Write entry file and update index.json. Returns file path."""

    def batch_format(self, findings: list[dict], dry_run: bool = False) -> list[str]:
        """Format and write multiple findings. Returns list of file paths."""

# CLI:
# python archive_formatter.py --file finding.json [--dry-run]
# python archive_formatter.py --batch findings.json [--auto-builds-on]
# python archive_formatter.py --from-sqlite --table team_findings [--min-score 0.7]
```

### Implementation Notes
- **Auto-ID** -- Parse `index.json`, find max numeric suffix, increment. Handle gaps.
- **Auto builds_on** -- Compute tag overlap (Jaccard) with existing entries. Link to entries with >= 0.3 overlap.
- **Summary extraction** -- First 2-3 sentences that are not headers, code blocks, or bullet points. Fall back to first N words if no clean sentences found.
- **Key findings extraction** -- Look for bullet points, numbered lists, sentences starting with "We found", "Results show", etc.
- **Index update** -- Use `json.dump` with `indent=2` to maintain readable formatting. Add to `entries`, `categories`, and `tag_index`.
- **Integrate with `kb_validator.py` (SCR-0012)** -- Run validation after write to ensure consistency.
- **Integrate with Quality Scorer (TOOL-SPEC-003)** -- Score finding before archiving; embed score in metadata.
- **stdlib only.**
- **Register as SCR-0044, category `utility`.**

---

## Tool 5: Cross-Team Search

**Priority:** P0
**ID:** TOOL-SPEC-005

### Purpose
Unified search across ALL team artifacts: KB entries, team session logs, bus messages, SQLite team_findings, team output JSON files, script metadata, and source metadata. Fills the gap where `kb_search.py` covers KB+logs+scripts but misses bus messages, SQLite data, and team outputs.

### Input/Output Format

**Input (CLI):**
```
python cross_team_search.py "quantum computing" --type all
python cross_team_search.py "deduplication" --type kb,bus,findings
python cross_team_search.py "API rate limit" --team TEAM-0010 --since 2026-03-10
python cross_team_search.py --tags "sqlite,coordination" --category integration
```

**Input (programmatic):**
```python
{
    "query": "quantum computing",
    "sources": ["kb", "logs", "bus", "findings", "scripts", "sources"],
    "filters": {
        "team": "TEAM-0010",             # optional
        "since": "2026-03-10",           # optional
        "category": "integration",       # optional
        "tags": ["sqlite"],              # optional
        "min_score": 0.5                 # relevance threshold
    },
    "limit": 20,
    "output_format": "json" | "text"
}
```

**Output:**
```python
{
    "query": "quantum computing",
    "total_results": 15,
    "results": [
        {
            "rank": 1,
            "relevance": 0.95,
            "source_type": "kb",
            "source_id": "KB-0003",
            "title": "Multi-Team Competitive Research: Quantitative Stock Analysis with AI",
            "snippet": "...quantum computing approaches to portfolio optimization...",
            "team": "TEAM-0003",
            "date": "2026-03-10",
            "file_path": "knowledge-base/entries/KB-0003.md"
        },
        {
            "rank": 2,
            "relevance": 0.82,
            "source_type": "bus",
            "source_id": "msg-abc123",
            "title": null,
            "snippet": "...quantum-resistant encryption for agent channels...",
            "team": "TEAM-0017",
            "date": "2026-03-11",
            "channel": "global"
        }
    ],
    "facets": {
        "by_source": {"kb": 5, "bus": 4, "findings": 3, "logs": 2, "scripts": 1},
        "by_team": {"TEAM-0003": 4, "TEAM-0017": 3, "TEAM-0010": 8},
        "by_category": {"integration": 6, "methodology": 5, "optimization": 4}
    }
}
```

### Key Functions/API

```python
# Module: storage/scripts/cross_team_search.py

class CrossTeamSearch:
    def __init__(self, repo_root: str = "."):
        """Initialize with repo root. Auto-discovers all source locations."""

    def build_index(self) -> dict:
        """Build/refresh a unified in-memory index across all sources.
        Returns stats dict {source_type: count}."""

    def search(self, query: str, **filters) -> dict:
        """Execute search with optional filters. Returns results dict."""

    # --- Source loaders ---
    def load_kb(self) -> list[dict]:
        """Load and index KB entries from knowledge-base/entries/."""

    def load_logs(self) -> list[dict]:
        """Load and index team session logs from teams/sessions/."""

    def load_bus(self) -> list[dict]:
        """Load and index JSONL bus messages from storage/coordination/bus/."""

    def load_findings(self) -> list[dict]:
        """Load and index SQLite team_findings from state.db."""

    def load_scripts(self) -> list[dict]:
        """Load and index script metadata from storage/scripts/index.json."""

    def load_sources(self) -> list[dict]:
        """Load and index data source metadata from storage/sources/index.json."""

    def load_team_outputs(self) -> list[dict]:
        """Load and index team output JSON files from storage/coordination/teams/."""

    # --- Ranking ---
    def rank_results(self, query: str, docs: list[dict]) -> list[dict]:
        """TF-IDF ranking with source-type boost (KB > findings > bus)."""

    # --- Faceting ---
    def compute_facets(self, results: list[dict]) -> dict:
        """Group results by source, team, category for faceted view."""

# CLI:
# python cross_team_search.py QUERY [--type kb,bus,findings] [--team TEAM] [--since DATE]
#     [--tags TAG1,TAG2] [--category CAT] [--limit N] [--json]
```

### Implementation Notes
- **Unified document schema** -- Every document from every source is normalized to `{id, title, body, source_type, team, date, tags, category, file_path}` before indexing.
- **TF-IDF ranking** -- Reuse pattern from `kb_search.py` (SCR-0018). Add source-type boosting: KB entries get 1.5x, team_findings 1.2x, bus messages 1.0x.
- **Bus message loading** -- Use `bus_read()` from `bus_core.py`. Include expired messages for historical search (override TTL filter).
- **SQLite loading** -- Use `init_db()` from `bus_core.py` to open state.db read-only.
- **Faceted results** -- Group by source_type, team, category for drill-down.
- **Performance** -- Cache the index in memory. Rebuilds only when source files change (check mtime). Target: <100ms search on current corpus.
- **stdlib only.**
- **Register as SCR-0045, category `utility`.**

---

## Tool 6: Bus Monitor Dashboard

**Priority:** P2
**ID:** TOOL-SPEC-006

### Purpose
Real-time bus-level monitoring dashboard that extends the existing `dashboard.py` (SCR-0032) with per-channel message rates, message type breakdowns, latency metrics, and channel health indicators. The existing dashboard shows agent status and summary bus counts but lacks detailed bus analytics.

### Input/Output Format

**CLI usage:**
```
python bus_monitor.py                         # Full bus dashboard
python bus_monitor.py --watch --interval 3    # Auto-refresh
python bus_monitor.py --channel global        # Single channel detail
python bus_monitor.py --json                  # JSON output for tooling
python bus_monitor.py --alerts                # Only show unhealthy channels
```

**JSON output:**
```python
{
    "timestamp": "2026-03-11T14:30:00Z",
    "channels": [
        {
            "name": "global",
            "file": "bus/global.jsonl",
            "file_size_bytes": 45678,
            "total_messages": 342,
            "active_messages": 89,           # non-expired
            "expired_messages": 253,
            "messages_last_1m": 12,
            "messages_last_5m": 45,
            "messages_last_15m": 89,
            "by_type": {
                "info": 45,
                "heartbeat": 120,
                "phase-signal": 30,
                "request": 20,
                "response": 18,
                "blocker": 5
            },
            "by_team": {
                "TEAM-0010": 50,
                "TEAM-0012": 40
            },
            "avg_message_size_bytes": 133,
            "last_message_age_seconds": 5.2,
            "health": "healthy" | "stale" | "dead"
        }
    ],
    "summary": {
        "total_channels": 8,
        "healthy_channels": 6,
        "stale_channels": 1,
        "dead_channels": 1,
        "total_messages": 1200,
        "messages_per_minute": 8.5,
        "total_bus_size_bytes": 234567
    }
}
```

### Key Functions/API

```python
# Module: storage/coordination/bus_monitor.py

class BusMonitor:
    def __init__(self, bus_dir: str = None):
        """Initialize with bus directory. Auto-detects from bus_core defaults."""

    def scan_channels(self) -> list[str]:
        """Discover all .jsonl channel files in bus directory."""

    def analyze_channel(self, channel: str) -> dict:
        """Full analysis of a single channel: message counts, rates, types, health."""

    def compute_rates(self, messages: list[dict], windows: list[int] = [60, 300, 900]) -> dict:
        """Compute message rates over time windows (seconds)."""

    def compute_type_breakdown(self, messages: list[dict]) -> dict:
        """Count messages by type."""

    def compute_team_breakdown(self, messages: list[dict]) -> dict:
        """Count messages by team."""

    def assess_health(self, channel_stats: dict) -> str:
        """Assess channel health: healthy (msg in last 60s), stale (60-300s), dead (>300s)."""

    def full_report(self) -> dict:
        """Generate full bus monitoring report across all channels."""

    def render_text(self, report: dict) -> str:
        """Render report as formatted text dashboard."""

    def render_alerts(self, report: dict) -> str:
        """Render only channels with issues (stale/dead/high blocker rate)."""

# CLI:
# python bus_monitor.py [--watch] [--interval N] [--channel CH] [--json] [--alerts]
```

### Implementation Notes
- **Read all channels** -- Glob `bus/*.jsonl`, parse each with `bus_read()` from `bus_core.py` at offset 0 (full scan for analytics).
- **Time-windowed rates** -- For each channel, bucket messages by timestamp into 1m/5m/15m windows.
- **Health thresholds** -- Healthy: message in last 60s. Stale: 60-300s. Dead: >300s. Flag channels with >10% blocker messages as "warning".
- **Text rendering** -- Use ANSI colors (reuse `_C` color class pattern from `sdk_cli.py`). Show per-channel bars for message rates.
- **Watch mode** -- Clear screen + reprint every N seconds. Use existing dashboard.py pattern.
- **Integration** -- Can be imported by `dashboard.py` to add a bus section, or run standalone.
- **Performance** -- Full scan of all channels should complete in <500ms on current bus volume.
- **stdlib only.**
- **Register as SCR-0046, category `utility`.**

---

## Implementation Priority Order

| Priority | Tool | Justification |
|----------|------|---------------|
| **P0** | Finding Deduplicator | Every team produces overlapping findings. Dedup is a force multiplier for all downstream tools. |
| **P0** | Quality Scorer | Needed by deduplicator (canonical selection) and archive formatter (gating). Low dependency, high reuse. |
| **P0** | Cross-Team Search | Most-requested capability. Teams waste time re-discovering what other teams already found. |
| **P1** | Web Scraper Pipeline | Needed for data gathering expansion. Many official docs are HTML-only. |
| **P1** | Archive Formatter | Automates manual KB entry creation. Depends on Quality Scorer. |
| **P2** | Bus Monitor Dashboard | Nice-to-have for operations. Existing dashboard covers basic needs. |

## Dependency Graph

```
Quality Scorer (P0)
    |
    v
Finding Deduplicator (P0) ---> Archive Formatter (P1)
    |
    v
Cross-Team Search (P0)

Web Scraper Pipeline (P1) [independent]

Bus Monitor Dashboard (P2) [independent]
```

## Shared Conventions

All tools MUST:
1. Use **Python standard library only** (no pip dependencies)
2. Support **CLI** with `argparse` and **programmatic** import
3. Output **JSON** when `--json` flag is passed
4. Include comprehensive **docstrings** with type hints
5. Register in `storage/scripts/index.json` with proper metadata
6. Follow the `bus_core.py` pattern for any bus/SQLite interactions
7. Include a `if __name__ == "__main__"` block for standalone use
8. Handle errors gracefully with structured error output
