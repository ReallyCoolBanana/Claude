# Cross-Referencing System for Storage

**Author:** TEAM-0004 (Team Lead 4)
**Date:** 2026-03-10
**Status:** Proposed

## Problem Statement

The storage system has three subsystems (scripts, api-tools, sources) that are semantically related but structurally isolated. There are no formal links between entries across subsystems, making it impossible to answer questions like:

- "Which scripts use the Alpha Vantage data source?"
- "Which API tools are invoked by the earnings calendar script?"
- "If the Reddit API changes, which scripts and tools are affected?"

Currently, the only cross-system field is `added_by_team`, which enables team-based queries but not relationship-based queries.

## Proposed Solution

### 1. New Optional Fields per Schema

**scripts/index.json** — add to each script entry:

```json
{
  "uses_sources": ["SRC-0001", "SRC-0004"],
  "uses_tools": ["TOOL-0001", "TOOL-0004"]
}
```

- `uses_sources` (string[]): List of SRC-IDs this script fetches data from
- `uses_tools` (string[]): List of TOOL-IDs this script invokes

**api-tools/index.json** — add to each tool entry:

```json
{
  "used_by_scripts": ["SCR-0001", "SCR-0005"],
  "related_sources": ["SRC-0001"]
}
```

- `used_by_scripts` (string[]): List of SCR-IDs that invoke this tool
- `related_sources` (string[]): List of SRC-IDs this tool wraps or accesses

**sources/index.json** — add to each source entry:

```json
{
  "consumed_by_scripts": ["SCR-0001", "SCR-0004"],
  "wrapped_by_tools": ["TOOL-0001"]
}
```

- `consumed_by_scripts` (string[]): List of SCR-IDs that consume this source
- `wrapped_by_tools` (string[]): List of TOOL-IDs that wrap this source as an API tool

### 2. Master Cross-Reference Index

A generated file at `storage/cross-references.json` provides a unified view of all relationships. It is built by running `storage/scripts/build-cross-ref-index.py`.

Structure:

```json
{
  "version": "1.0",
  "generated": "2026-03-10T12:00:00",
  "generated_by": "build-cross-ref-index.py",
  "summary": {
    "total_scripts": 20,
    "total_tools": 20,
    "total_sources": 20,
    "total_script_source_links": 8,
    "total_script_tool_links": 6,
    "total_tool_source_links": 12
  },
  "relationships": {
    "script_to_source": [...],
    "script_to_tool": [...],
    "tool_to_source": [...]
  },
  "entries": {
    "SCR-0001": { "uses_sources": [...], "uses_tools": [...] },
    "TOOL-0001": { "used_by_scripts": [...], "related_sources": [...] },
    "SRC-0001": { "consumed_by_scripts": [...], "wrapped_by_tools": [...] }
  },
  "orphans": {
    "scripts_no_source": [...],
    "tools_no_script": [...],
    "sources_no_tool": [...]
  }
}
```

### 3. Rules for Maintaining Cross-References

1. **When adding a script**: populate `uses_sources` and `uses_tools` with the IDs of any sources or tools the script interacts with.
2. **When adding a tool**: populate `related_sources` with the SRC-IDs of data sources the tool accesses.
3. **When removing an entry**: remove its ID from all cross-reference fields in other entries.
4. **Periodically**: run `build-cross-ref-index.py` to regenerate the master index and detect orphans.

### 4. Backward Compatibility

All new fields are **optional**. Existing entries without cross-reference fields will still work. The `build-cross-ref-index.py` script uses heuristic matching to infer relationships for entries that lack explicit cross-references.

## Heuristic Matching Strategy

For entries without explicit cross-references, the build script uses:

1. **Domain matching** — compare endpoint URLs of tools with source URLs (high confidence)
2. **Name keyword overlap** — check if source/tool names appear in script descriptions (medium confidence)
3. **Dependency analysis** — check if script dependencies (e.g., `praw` for Reddit) correlate with known tools/sources (future enhancement)

## Impact Assessment

| Query Type | Before | After |
|---|---|---|
| "Which scripts use source X?" | Fuzzy text search, unreliable | Direct lookup via `consumed_by_scripts` |
| "Which tools does script Y use?" | Impossible without reading code | Direct lookup via `uses_tools` |
| "What breaks if source Z goes down?" | Unknown | Check `consumed_by_scripts` + `wrapped_by_tools` |
| "Find orphan sources" | Manual review | Automated via `orphans` in cross-ref index |
