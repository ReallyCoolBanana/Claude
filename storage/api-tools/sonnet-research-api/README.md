# Sonnet Research API

A research API system that runs **inside Claude Code**. Agentic teams dispatch
research tasks to Claude Sonnet via sub-agents, collect results, sort them, and
import them into the repository's knowledge base.

## Architecture

```
                          ┌──────────────────┐
                          │  Claude Code      │
                          │  (agentic team)   │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │ ResearchOrchestrator │  ← main entry point
                          └────────┬─────────┘
                     ┌─────────────┼─────────────┐
                     ▼             ▼              ▼
              SonnetResearcher SonnetResearcher  ...
                     │             │
                     ▼             ▼
              ┌────────────────────────┐
              │     RequestQueue       │  ← rate-limited, concurrent
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │  SonnetResearchClient  │  ← Anthropic SDK wrapper
              └───────────┬────────────┘
                          ▼
                   Claude Sonnet API
                          │
                          ▼
              ┌────────────────────────┐
              │    Raw results (JSON)  │
              └───────────┬────────────┘
                    ┌─────┴─────┐
                    ▼           ▼
             ResultSorter   DataImporter
             (sort/filter)  (repo integration)
```

### File overview

| File              | Class / Purpose                              |
|-------------------|----------------------------------------------|
| `config.py`       | Environment variables, model defaults, paths |
| `client.py`       | `SonnetResearchClient` -- Anthropic SDK wrapper with retries |
| `queue_manager.py`| `RequestQueue` -- thread-safe priority queue with rate limiting |
| `researcher.py`   | `SonnetResearcher` -- multi-step research sessions |
| `orchestrator.py` | `ResearchOrchestrator` -- **main entry point** |
| `sorter.py`       | `ResultSorter` -- sort, group, categorize, export results |
| `data_importer.py`| `DataImporter` -- import results into the repo |

## Quick Start

```python
import sys
sys.path.insert(0, "/home/user/Claude/storage/api-tools/sonnet-research-api")

from orchestrator import ResearchOrchestrator

# Requires ANTHROPIC_API_KEY in the environment (or pass it directly).
orch = ResearchOrchestrator()

# Simple: research a list of topics
results = orch.spawn_researchers(
    topics=["quantum computing applications", "transformer architectures"],
    depth="standard",       # "quick", "standard", or "deep"
    max_concurrent=3,
)

for r in results:
    print(r["topic"], "-", r["summary"][:120])
```

## Full Research Mission

```python
from orchestrator import ResearchOrchestrator

orch = ResearchOrchestrator()

# Register a progress callback (optional).
orch.on_progress(lambda done, total, latest: print(f"[{done}/{total}] {latest['topic']}"))

mission = {
    "objective": "Evaluate emerging database technologies for time-series data",
    "sub_tasks": [
        {"topic": "TimescaleDB vs InfluxDB performance",  "depth": "deep",     "priority": 2},
        {"topic": "QuestDB architecture overview",         "depth": "standard", "priority": 1},
        {"topic": "Apache IoTDB for edge computing",      "depth": "standard", "priority": 0},
    ],
    "max_concurrent": 3,
    "output_format": "combined",   # "combined" or "individual"
}

result = orch.run_research_mission(mission)

print(result.mission_id)
print(result.combined_summary)
print(f"Tokens used: {result.total_tokens}")
print(f"Duration: {result.duration_seconds}s")
```

## Sorting and Filtering Results

```python
from sorter import ResultSorter

sorter = ResultSorter(result.results)

# Sort by relevance to a query.
ranked = sorter.sort_by_relevance("time-series performance benchmarks")

# Group by depth level.
by_depth = sorter.sort_by_depth()

# Group by topic similarity.
by_topic = sorter.sort_by_topic()

# Assign to custom categories.
categorized = sorter.categorize(["databases", "architecture", "benchmarks"])

# Filter results.
filtered = sorter.filter_results(min_queries=2, has_summary=True)

# Export to JSON.
sorter.export_sorted("/tmp/sorted_results.json")

# Generate a storage-compatible index.
index = sorter.generate_index(result.results)
```

## Importing Results into the Repo

```python
from data_importer import DataImporter

importer = DataImporter(repo_root="/home/user/Claude")

# Create knowledge-base entries from results.
kb_paths = importer.import_to_knowledge_base(
    result.results,
    category="methodology",
    team="my-team",
)

# Register discovered data sources.
src_paths = importer.import_to_sources(result.results)

# Register discovered API tools (pass explicit definitions).
tool_paths = importer.import_to_api_tools([
    {"name": "TimescaleDB API", "description": "...", "category": "data-retrieval"},
])

# Update all index.json files.
updated = importer.update_indexes()
print("Updated indexes:", updated)
```

## Configuration

| Environment Variable | Purpose                      | Default                |
|----------------------|------------------------------|------------------------|
| `ANTHROPIC_API_KEY`  | Anthropic API key (required) | *(none)*               |

Additional settings in `config.py`:

| Setting                          | Default | Notes                        |
|----------------------------------|---------|------------------------------|
| `MODEL`                          | `claude-sonnet-4-6` | Sonnet model ID    |
| `RATE_LIMIT_REQUESTS_PER_MINUTE` | `50`    | Queue rate limiter           |
| `MAX_CONCURRENT_REQUESTS`        | `3`     | Default parallel workers     |
| `MAX_RETRIES`                    | `3`     | Per-request retry attempts   |
| `MAX_QUEUE_SIZE`                 | `500`   | Maximum queued requests      |
| `DEFAULT_MAX_TOKENS`             | `4096`  | Max output tokens per query  |
| `RESULTS_DIR`                    | `./results/` | Default results directory |

## Data Flow

```
1. Agentic team calls ResearchOrchestrator
2. Orchestrator creates SonnetResearcher instances (one per topic)
3. Each researcher runs multi-step queries at the configured depth
4. Queries flow through SonnetResearchClient (with retry + rate limiting)
5. Raw results come back as structured dicts
6. ResultSorter sorts, groups, categorizes, and exports results
7. DataImporter writes results into the repo:
   - knowledge-base/entries/  (Markdown files)
   - storage/sources/         (JSON source definitions)
   - storage/api-tools/       (JSON tool definitions)
8. Index files are updated so other teams can discover the data
```
