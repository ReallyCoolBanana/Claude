# Cross-Reference Improvement Report

**Team:** TEAM-0004 (Team Lead 4)
**Date:** 2026-03-10
**Scripts Created:** SCR-0021, SCR-0022, SCR-0023

---

## 1. Cross-Referencing Gaps Found (Phase 1)

Phase 1 (`test-cross-references.py`) populated all three index.json files with 20 realistic entries each and attempted three cross-system queries. Findings:

### Gap 1: No Formal Cross-References
The storage system has **zero structural links** between subsystems. Scripts do not record which sources or tools they use, tools do not record which scripts invoke them, and sources do not record their consumers.

### Gap 2: Fuzzy Matching is the Only Option
Without explicit links, the only way to infer relationships is text-based keyword matching across names and descriptions. This approach:
- **35 matches** found for scripts-to-sources, but many were false positives (e.g., the word "data" matched nearly every entry)
- **30 matches** found for scripts-to-tools, with similar noise
- Short identifiers like "FRED" or "X" are missed entirely (below the 4-character threshold needed to avoid false positives)
- No way to distinguish "mentions" from "actually uses"

### Gap 3: Single Cross-System Field
The field `added_by_team` is the **only field shared across all three schemas**. Team-based aggregation (Query 3) was trivial and fast (0.03ms), while relationship queries required O(n*m) fuzzy matching.

### Gap 4: No Unified Search
Each subsystem has its own index with different schemas, categories, and lookup structures. There is no way to search across all three simultaneously without loading and normalizing all three files.

---

## 2. Improvements Implemented (Phase 2)

### Cross-Reference Schema (`CROSS_REFERENCES.md`)
Proposed and documented new optional fields for each subsystem:

| Subsystem | New Fields | Purpose |
|-----------|-----------|---------|
| scripts | `uses_sources` (SRC-ID[]), `uses_tools` (TOOL-ID[]) | Track what a script depends on |
| api-tools | `used_by_scripts` (SCR-ID[]), `related_sources` (SRC-ID[]) | Track what uses a tool and what it wraps |
| sources | `consumed_by_scripts` (SCR-ID[]), `wrapped_by_tools` (TOOL-ID[]) | Track downstream consumers |

All fields are **optional** for backward compatibility.

### Cross-Reference Index Builder (`build-cross-ref-index.py`)
Builds `storage/cross-references.json` — a master index containing:
- **Relationship maps** between all entries (script-to-source, script-to-tool, tool-to-source)
- **Per-entry reference lists** showing all connections for each ID
- **Orphan detection** identifying entries with no connections
- **Confidence scores** (high for domain-match, medium for keyword overlap)

Results from initial build:
- 18 tool-to-source links found (via domain matching)
- 17 script-to-tool links found
- 13 script-to-source links found
- 7 scripts with no linked source, 5 with no linked tool
- 8 sources consumed by no script

---

## 3. Search Tool Demo (Phase 3)

The unified search tool (`search-storage.py`) was tested with 7 demo queries:

| Query | Filters | Results |
|-------|---------|---------|
| Keyword "stock" | All subsystems | 9 results across scripts, tools, sources |
| Keyword "fetch" | Scripts only | 4 results, correctly ranked |
| Team TEAM-0002 | All subsystems | 11 results (5 scripts, 3 tools, 3 sources) |
| Data type "financial" | Sources + tools | 11 results |
| After 2026-03-01 | All subsystems | 30 results |
| Category "communication" | Tools only | 3 results (SendGrid, Slack, Twilio) |
| Keyword "price" | All subsystems | 9 results with cross-references shown |

Key features demonstrated:
- **Relevance scoring** — name matches rank higher than description matches
- **Cross-reference integration** — when `cross-references.json` exists, related entries are shown
- **Multi-filter support** — keyword + subsystem + team can be combined
- **Clean table output** with consistent column alignment

---

## 4. Recommendations for Future Improvements

### Short-term (next 1-2 teams)
1. **Populate cross-reference fields** — When adding new entries, fill in `uses_sources`, `uses_tools`, etc. This is the highest-impact, lowest-effort improvement.
2. **Run build-cross-ref-index.py periodically** — Add to team workflows to keep the master index current.
3. **Add `tags` field** to all three schemas — free-form tags would improve search without requiring formal links.

### Medium-term (3-5 teams)
4. **Dependency graph visualization** — Generate a DOT/Mermaid diagram from cross-references.json showing the relationship graph.
5. **Validation script** — Check for dangling references (e.g., `uses_sources: ["SRC-9999"]` where SRC-9999 does not exist).
6. **Change impact analysis** — "If SRC-0001 goes down, what scripts and tools are affected?" (now possible with cross-references).

### Long-term
7. **Automated relationship detection** — Parse actual script source code (not just descriptions) to detect API calls and imports, then auto-populate cross-references.

---

## 5. Should the System Move to SQLite?

### Assessment: Not yet, but plan for it.

**Current scale (60 entries total):**
JSON files work well. Load times are negligible (<1ms), and the human-readable format makes it easy for AI teams to inspect and edit entries directly. The cross-reference index adds relationship queries without needing a database.

**When to migrate (estimated threshold: 200-500 entries):**
- JSON file sizes become unwieldy (>100KB per index)
- Cross-reference queries require joins across 3+ files on every search
- Concurrent modifications by multiple teams risk merge conflicts
- Need for complex queries (e.g., "find all scripts that use sources with reliability='low' and haven't been verified in 30 days")

**Migration path:**
1. Keep JSON as the human-editable source of truth
2. Build a `sync-to-sqlite.py` script that imports all JSON indexes into a SQLite database
3. Point search tools at SQLite for read queries
4. When ready, flip: SQLite becomes the source of truth, with a JSON export script for human inspection

**Bottom line:** The JSON + cross-reference index approach handles the current scale well. SQLite becomes worthwhile when entry counts exceed a few hundred or when query complexity demands relational joins. The cross-reference schema designed here maps cleanly to a relational model, so migration would be straightforward.

---

## Files Created

| File | ID | Purpose |
|------|----|---------|
| `storage/scripts/test-cross-references.py` | SCR-0021 | Phase 1 analysis script |
| `storage/scripts/build-cross-ref-index.py` | SCR-0022 | Phase 2 cross-ref builder |
| `storage/scripts/search-storage.py` | SCR-0023 | Phase 3 unified search tool |
| `storage/CROSS_REFERENCES.md` | — | Cross-reference schema docs |
| `storage/cross-references.json` | — | Generated master cross-ref index |
| `storage/scripts/cross-ref-improvement-report.md` | — | This report |
