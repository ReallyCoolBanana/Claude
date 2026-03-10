# Master Reference Document

Generated: 2026-03-10 | 13 KB entries | 14 teams | 28 scripts | 35 data sources

---

## Repository Map

```
knowledge-base/           # Shared knowledge entries (Markdown + YAML frontmatter)
  entries/                 # Individual KB entry files (KB-0001 through KB-0014)
  index.json               # Master index with entries, categories, tag_index
  TEMPLATE.md              # Template for new entries
teams/                     # Team session logs and coordination
  sessions/                # Individual team logs (TEAM-0001 through TEAM-0014)
  TEAM_LOG_TEMPLATE.md     # Template for new session logs
storage/
  scripts/                 # 28 reusable scripts (Python + Go)
  api-tools/               # API tool definitions and implementations
  sources/                 # 35 curated data source definitions
market-research/           # Stock picking methods, picks, performance tracking
  methods/                 # Analysis methodology files
  picks/                   # Individual picks with TEMPLATE.md
.claude/                   # Claude Code configuration and hooks
```

---

## Knowledge Base Summary

| ID | Title | Team | Category | Key Finding |
|----|-------|------|----------|-------------|
| KB-0001 | Storage System Stress Test & Improvements | TEAM-0001 | optimization | Validated storage system across writes, reads, integrity, cross-references; established baseline benchmarks |
| KB-0002 | Sonnet Research API -- Multi-Agent System | TEAM-0002 | integration | Built multi-agent research dispatching with queue management and data import |
| KB-0003 | Multi-Team Competitive Research: Quant Stock Analysis | TEAM-0003 | market-research | 5-team competitive research on AI-driven quantitative stock analysis techniques |
| KB-0004 | Data Gathering Methods: Iteration 1 Benchmarks | TEAM-0004 | methodology | First benchmarks of data gathering methods; established improvement loop framework |
| KB-0005 | Data Gathering Iter 2: Parallel Hybrid & Academic APIs | TEAM-0005 | methodology | 5/7 methods functional; added OpenAlex, Wikidata; parallel execution with TF-IDF dedup |
| KB-0006 | Archive Tools for Knowledge Base System | TEAM-0006 | tool-usage | Built 3 archive tools: KB validator, index rebuilder, cross-reference checker |
| KB-0007 | Research Team Configuration Testing | TEAM-0007 | methodology | Optimal team size is 3-agent triad; tested 1/3/5/8 agent configs with benchmarks |
| KB-0008 | Data Storage Methods & AI-Optimized Storage | TEAM-0008 | methodology | Compared storage formats, vector DBs, RAG patterns, key-value stores for AI workloads |
| KB-0009 | Multi-Team Op 3: Archive Optimization & Go Tools | TEAM-0009 | optimization | Go tools achieve 28-143x speedup over Python; coordinator pattern for multi-team ops |
| KB-0010 | Free Public APIs for AI/ML Self-Improvement | TEAM-0012 | integration | Catalogued free APIs for NLP, CV, embeddings, knowledge graphs |
| KB-0012 | Free Public APIs for Dev Tools & General Data | TEAM-0010 | integration | Catalogued GitHub, npm, PyPI, Stack Exchange, HN, geolocation APIs |
| KB-0013 | Free Public APIs for Government & Open Data | TEAM-0013 | integration | Catalogued weather, geospatial, census, economic, and open government APIs |
| KB-0014 | Operation 4: Public Data API Tool Development | TEAM-0014 | integration | Built 6 new data-gathering tools for OpenAlex, Semantic Scholar, HN, Guardian, Wikidata, GitHub |

---

## Team History

| Team | Date | Objective | Agents | Key Output |
|------|------|-----------|--------|------------|
| TEAM-0001 | 2026-03-10 | Stress test storage system across all dimensions | coordinator + 4 leads + 12 sub-agents (16) | KB-0001: storage benchmarks |
| TEAM-0002 | 2026-03-10 | Build Sonnet Research API for multi-agent dispatching | coordinator + 2 team leads + 4 subs (7) | KB-0002: research API system |
| TEAM-0003 | 2026-03-10 | 5-team competitive research on quant stock analysis | coordinator + 5 leads + 22 agents (28) | KB-0003 + SCR-0001..0011 |
| TEAM-0004 | 2026-03-10 | 3-team continuous improvement loop for data gathering | coordinator + 3 leads + 12 agents (16) | KB-0004: iteration 1 benchmarks |
| TEAM-0005 | 2026-03-10 | Iteration 2 data gathering improvements | 1 software-engineer | KB-0005 + SCR-0004..0005 |
| TEAM-0006 | 2026-03-10 | Build 3 archive tools for KB management | 3 agents | KB-0006 + SCR-0012..0014 |
| TEAM-0007 | 2026-03-10 | Test research team configs, fix inefficiencies | coordinator + 7 specialists (8) | KB-0007 + SCR-0021..0022 |
| TEAM-0008 | 2026-03-10 | Research data storage methods & AI-optimized storage | 3 leads + 6 sub-agents (9) | KB-0008 + SCR-0015..0020 |
| TEAM-0009 | 2026-03-10 | Coordinated op: Go tools, archive automation, benchmarks | coordinator + 7 teams (8) | KB-0009: Go tools 28-143x speedup |
| TEAM-0010 | 2026-03-10 | Find best free APIs for dev tools & general data | 1 research-team | KB-0012: dev/general API catalog |
| TEAM-0011 | 2026-03-10 | Find best free APIs for government & open data | 1 research-team | (session log, no separate KB) |
| TEAM-0012 | 2026-03-10 | Find best free APIs for AI/ML self-improvement | 1 research-agent | KB-0010: AI/ML API catalog |
| TEAM-0013 | 2026-03-10 | Find best free APIs for government & open data | 1 research-team | KB-0013: gov/open data API catalog |
| TEAM-0014 | 2026-03-10 | Op 4: Build data-gathering tools for public APIs | coordinator + 8 teams (9) | KB-0014 + SCR-0023..0028 |

---

## Scripts Inventory

### Utility (12 scripts)

| ID | Name | Lang | Description |
|----|------|------|-------------|
| SCR-0001 | validate-storage | Python | Storage system schema validation, integrity checks, orphan detection |
| SCR-0002 | test-edge-cases | Python | 35-test edge case suite for storage system |
| SCR-0003 | repair-storage | Python | Auto-repair for storage issues (versions, dates, dupes); supports --dry-run |
| SCR-0012 | kb-validator | Python | KB entry + index.json consistency validation; JSON report output |
| SCR-0013 | index-rebuilder | Python | Auto-generates index.json from entries; --dry-run and --fix modes |
| SCR-0014 | cross-ref-checker | Python | Validates all cross-refs, detects cycles via DFS, finds orphans |
| SCR-0015 | fast-search | Go | Full-text inverted index search; <5ms query on 126 files |
| SCR-0016 | fast-validate | Go | Repo structure validator; all checks in <2ms |
| SCR-0017 | fast-cache | Go | File-based K/V cache for API responses; SHA-256 keys, TTL, GC |
| SCR-0018 | kb-search | Python | TF-IDF ranked full-text search across KB, teams, scripts |
| SCR-0019 | handoff-tracker | Python | Extracts action items from team logs; tracks resolution rate |
| SCR-0020 | staleness-detector | Python | 6-check repo health scorer (stale, regressions, orphans, broken refs) |

### Data Collection (13 scripts)

| ID | Name | Lang | Description |
|----|------|------|-------------|
| SCR-0006 | data-gathering-websearch | Python | Multi-query DuckDuckGo search with dedup |
| SCR-0007 | data-gathering-wikipedia | Python | Wikipedia API deep extraction with DDG fallback |
| SCR-0008 | data-gathering-arxiv | Python | arXiv paper search with DDG fallback |
| SCR-0009 | data-gathering-hybrid | Python | Multi-source aggregation (web+wiki+arxiv); cross-reference scoring |
| SCR-0010 | data-gathering-iterative | Python | 3-round iterative deepening DuckDuckGo search |
| SCR-0021 | ddg-utils | Python | Shared DDG search utilities; rate limiting; extracted from 4 dupes |
| SCR-0022 | request-cache | Python | File-based HTTP cache; SHA-256 keys, base64, configurable TTL |
| SCR-0023 | data-gathering-openalex | Python | OpenAlex academic search (250M+ works); polite pool rate limits |
| SCR-0024 | data-gathering-hackernews | Python | HN search via Algolia API + Firebase top stories |
| SCR-0025 | data-gathering-guardian | Python | Guardian news via Open Platform API; DDG fallback |
| SCR-0026 | data-gathering-semantic-scholar | Python | Semantic Scholar with TLDRs, citations, open access PDFs |
| SCR-0027 | data-gathering-wikidata | Python | Wikidata entity search + full property extraction |
| SCR-0028 | data-gathering-github | Python | GitHub repo/code search; stars, languages, topics, licenses |

### Analysis (3 scripts)

| ID | Name | Lang | Description |
|----|------|------|-------------|
| SCR-0004 | grade-research | Python | 5-dimension research quality grader with recommendations |
| SCR-0005 | merge-research | Python | Multi-team research merger with string-similarity dedup |
| SCR-0011 | data-gathering-benchmark | Python | Benchmark harness for all gathering methods; timing + quality scores |

---

## Data Sources Catalog

### Financial (7)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0001 | Alpha Vantage | alphavantage.co/query | freemium |
| SRC-0002 | Yahoo Finance | query1.finance.yahoo.com | free |
| SRC-0003 | FRED | api.stlouisfed.org/fred | api-key |
| SRC-0004 | CoinGecko | api.coingecko.com/api/v3 | freemium |
| SRC-0005 | Twelve Data | api.twelvedata.com | freemium |
| SRC-0006 | Exchange Rates | api.exchangerate.host | free |
| SRC-0007 | Finnhub | finnhub.io/api/v1 | freemium |

### News (4)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0008 | NewsAPI | newsapi.org/v2 | freemium |
| SRC-0009 | GNews | gnews.io/api/v4 | freemium |
| SRC-0010 | The Guardian | content.guardianapis.com | api-key |
| SRC-0011 | Hacker News | hacker-news.firebaseio.com/v0 | free |

### Scientific (10)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0012 | OpenAlex | api.openalex.org | free |
| SRC-0013 | Semantic Scholar | api.semanticscholar.org/graph/v1 | free |
| SRC-0014 | Wikidata | wikidata.org/w/api.php | free |
| SRC-0015 | CORE | api.core.ac.uk/v3 | api-key |
| SRC-0016 | CrossRef | api.crossref.org | free |
| SRC-0017 | DBpedia | dbpedia.org/sparql | free |
| SRC-0018 | Papers With Code | paperswithcode.com/api/v1 | free |
| SRC-0031 | Hugging Face Hub | huggingface.co/api | free |
| SRC-0034 | ConceptNet | api.conceptnet.io | free |
| SRC-0035 | Wikipedia REST | en.wikipedia.org/api/rest_v1 | free |

### Government (5)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0003 | FRED | api.stlouisfed.org/fred | api-key |
| SRC-0019 | US Census Bureau | api.census.gov/data | api-key |
| SRC-0020 | Data.gov CKAN | catalog.data.gov/api/3 | free |
| SRC-0023 | BLS | api.bls.gov/publicAPI/v2 | free |
| SRC-0024 | World Bank | api.worldbank.org/v2 | free |

### Geospatial (3)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0021 | Open-Meteo | api.open-meteo.com/v1 | free |
| SRC-0022 | OpenWeatherMap | api.openweathermap.org | freemium |
| SRC-0030 | Nominatim | nominatim.openstreetmap.org | free |

### General (11)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0011 | Hacker News | hacker-news.firebaseio.com/v0 | free |
| SRC-0014 | Wikidata | wikidata.org/w/api.php | free |
| SRC-0017 | DBpedia | dbpedia.org/sparql | free |
| SRC-0025 | GitHub REST API | api.github.com | freemium |
| SRC-0026 | PyPI | pypi.org/pypi | free |
| SRC-0027 | npm Registry | registry.npmjs.org | free |
| SRC-0029 | Stack Exchange | api.stackexchange.com/2.3 | free |
| SRC-0031 | Hugging Face Hub | huggingface.co/api | free |
| SRC-0032 | Free Dictionary | api.dictionaryapi.dev | free |
| SRC-0033 | Datamuse | api.datamuse.com | free |
| SRC-0035 | Wikipedia REST | en.wikipedia.org/api/rest_v1 | free |

### Social Media (1)

| ID | Name | URL | Access |
|----|------|-----|--------|
| SRC-0028 | Reddit | reddit.com | free |

**Access summary:** 23 free, 8 freemium, 4 api-key-required, 0 paid

---

## Key Findings & Benchmarks

| Finding | Source | Detail |
|---------|--------|--------|
| Optimal team size | KB-0007 | 3-agent triad outperforms 1, 5, and 8 agent configs on research tasks |
| Data gathering coverage | KB-0005 | 5 of 7 methods functional; OpenAlex + Wikidata added; TF-IDF dedup works |
| Go vs Python speedup | KB-0009 | Go tools achieve 28-143x speedup; fast-validate <2ms, fast-search <5ms |
| Storage format insights | KB-0008 | Compared JSON/SQLite/Parquet/vector DBs; RAG patterns for AI workloads |
| Multi-team coordination | KB-0009 | Coordinator pattern with dedicated prog/archive/research teams scales best |
| Archive automation | KB-0006 | 3-tool suite (validator, rebuilder, cross-ref checker) catches all integrity issues |
| Competitive research | KB-0003 | 5-team competitive format produces higher quality than single-team |
| API tool ecosystem | KB-0014 | 6 new data-gathering scripts covering academic, news, code, and knowledge graph APIs |
| Improvement loop | KB-0004 | Continuous improvement loop framework: benchmark -> identify gaps -> implement -> re-benchmark |
| DDG dedup extraction | KB-0007 | Extracted shared DDG utils from 4 duplicate implementations |

---

## Dependency Graph

### Knowledge Base Chain

```
KB-0001 (Storage Stress Test)
  +-- KB-0002 (Sonnet Research API)
  |     +-- KB-0003 (Quant Stock Analysis)
  |           +-- KB-0004 (Data Gathering Iter 1)
  |                 +-- KB-0005 (Data Gathering Iter 2)
  |                 |     +-- KB-0007 (Team Config Testing) [also <-- KB-0006]
  |                 |     +-- KB-0012 (Dev/General APIs)
  |                 |     +-- KB-0013 (Gov/Open Data APIs)
  |                 |     +-- KB-0014 (API Tool Dev) [also <-- KB-0009, KB-0010]
  |                 +-- KB-0010 (AI/ML APIs) [also <-- KB-0008]
  |                 +-- KB-0012 (Dev/General APIs)
  |                 +-- KB-0013 (Gov/Open Data APIs)
  +-- KB-0006 (Archive Tools)
  |     +-- KB-0007 (Team Config Testing) [also <-- KB-0004, KB-0005]
  |     +-- KB-0009 (Go Tools & Optimization) [also <-- KB-0007, KB-0008]
  +-- KB-0008 (Storage Methods) [also <-- KB-0007]
```

### Script Dependencies

```
SCR-0009 (hybrid)       <-- SCR-0006 (websearch) + SCR-0007 (wikipedia) + SCR-0008 (arxiv)
SCR-0011 (benchmark)    <-- SCR-0006..0010 (all gathering methods)
SCR-0020 (staleness)    <-- SCR-0012 (kb-validator) + SCR-0014 (cross-ref) + SCR-0019 (handoff)
SCR-0023 (openalex)     <-- SCR-0022 (request-cache)
SCR-0024 (hackernews)   <-- SCR-0022 (request-cache)
SCR-0025 (guardian)     <-- SCR-0021 (ddg-utils) + SCR-0022 (request-cache)
SCR-0026 (sem-scholar)  <-- SCR-0022 (request-cache)
SCR-0027 (wikidata)     <-- SCR-0022 (request-cache)
SCR-0028 (github)       <-- SCR-0022 (request-cache)
```

All other scripts (SCR-0001..0003, 0004, 0005, 0012..0019, 0021, 0022) have zero dependencies.

---

## Quick Stats

- **Languages:** 25 Python scripts, 3 Go binaries
- **KB categories:** optimization (2), integration (5), market-research (1), methodology (4), tool-usage (1)
- **Total agents deployed:** ~130+ across 14 team sessions
- **Largest operation:** TEAM-0003 with 28 agents (5-team competitive research)
- **Smallest operation:** TEAM-0005 with 1 agent (iteration 2 improvements)
- **Data sources:** 35 APIs across 7 categories; 23 free, 8 freemium, 4 api-key-required
