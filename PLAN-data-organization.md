# Mass Data Organization & Indexing Project — Implementation Plan

## Current State Assessment

### What Exists
- **5 independent index systems**: KB (38 entries), Scripts (40 entries), Sources (35+ entries), API Tools (1 entry), Teams (22 sessions)
- **135 unique KB tags** — 80+ are orphans (1 entry only)
- **29 tools** already built (search, validation, indexing, repair)
- **No unified cross-domain search** — each domain has its own index.json with different schemas
- **Fragmented taxonomy** — inconsistent tag naming, no tag hierarchy, categories overlap

### Key Problems
1. **No unified search**: Can't query "find everything about SQLite" across KB, scripts, sources, and team logs
2. **Tag chaos**: 80+ orphan tags, inconsistent naming (`multi-agent` vs `multi-team`, `hackernews` vs `hacker-news`, `SPIR-V` vs `spir-v`)
3. **Over-broad categories**: `utility` has 20/40 scripts (50%), `general` has 10/35 sources
4. **Missing metadata**: Scripts have no tags, sources have no tags, team sessions have no tags/categories
5. **Stale/phantom entries**: Indexed items with no files, files with no index entries
6. **API Tools nearly empty**: 1 entry in a system designed for many

---

## Phase 1: Audit & Clean (Team: 2 agents)

**Goal**: Fix all data quality issues so subsequent phases work on clean data.

### Team Composition
- **Audit Agent**: Run all validators, collect full issue list
- **Repair Agent**: Fix issues in parallel

### Tasks
1. Run `fast_validate`, `kb_validator.py`, `cross_ref_checker.py`, `validate-storage.py`, `staleness_detector.py`
2. Fix all phantom entries (indexed but no file) — remove from indexes
3. Fix all orphan files (file but not indexed) — add to indexes or delete
4. Normalize YAML frontmatter across all KB entries (consistent field names: `date` not `created`, `team` not `author`)
5. Fix duplicate/mismatched content (KB-0012 content matches KB-0013)
6. Move misplaced team logs (TEAM-0015/0016/0017 from `teams/` root to `teams/sessions/`)
7. Run `index_rebuilder.py --fix` on all indexes
8. Verify: `fast_validate` shows 0 errors, 0 warnings

### Deliverable
- Clean baseline: all indexes match all files, all frontmatter valid

---

## Phase 2: Taxonomy Standardization (Team: 3 agents)

**Goal**: Create a unified, consistent tagging vocabulary that works across all domains.

### Team Composition
- **Think Tank Agent**: Design the taxonomy (tag hierarchy, naming rules, category definitions)
- **KB Tagger Agent**: Apply taxonomy to KB entries + team sessions
- **Storage Tagger Agent**: Apply taxonomy to scripts + sources + API tools

### Tasks

#### 2a. Design Unified Taxonomy (Think Tank)
1. Define tag naming rules:
   - Lowercase, hyphenated (`multi-agent` not `Multi-Agent`)
   - Singular nouns (`api` not `apis`)
   - No acronyms in tags unless universally known (`sqlite`, `vulkan`, `glm`)
2. Define tag hierarchy with namespaces:
   ```
   domain/      → vulkan, sqlite, python, go
   pattern/     → work-stealing, hub-spoke, fan-out
   task/        → search, validation, caching, data-gathering
   infra/       → coordination, communication, storage
   quality/     → stress-test, benchmark, audit
   ```
3. Consolidate overlapping tags:
   - `multi-agent` + `multi-team` → `multi-agent` (teams are an implementation detail)
   - `free-apis` + `public-apis` → `public-api`
   - `hackernews` + `hacker-news` → `hacker-news`
   - `SPIR-V` + `spir-v` → `spir-v`
4. Break up over-broad categories:
   - Scripts `utility` → split into `validation`, `search`, `repair`, `indexing`, `caching`
   - Sources `general` → reclassify each into specific types
5. Define minimum tag count: every entry must have 2-5 tags
6. Output: `storage/taxonomy.json` — the canonical tag vocabulary with definitions

#### 2b. Apply to KB + Teams (KB Tagger)
1. Retag all 38 KB entries using the new taxonomy
2. Add tags to all 22 team session entries (currently have zero tags)
3. Merge orphan tags into standardized equivalents
4. Update `knowledge-base/index.json` tag_index
5. Update `teams/sessions/index.json` with new tags field

#### 2c. Apply to Scripts + Sources + API Tools (Storage Tagger)
1. Add `tags` field to every script in `storage/scripts/index.json` (currently absent)
2. Add `tags` field to every source in `storage/sources/index.json`
3. Reclassify scripts: break `utility` into specific subcategories
4. Reclassify sources: break `general` into specific types
5. Populate `storage/api-tools/index.json` with entries for tools that should be there but aren't

### Deliverable
- `storage/taxonomy.json` — canonical tag vocabulary
- All 5 index.json files updated with consistent tags
- Zero orphan tags, zero inconsistent naming

---

## Phase 3: Unified Search Index (Team: 2 agents)

**Goal**: Build a single search system that queries across all domains.

### Team Composition
- **Index Builder Agent**: Build the unified index + query engine
- **CLI/Integration Agent**: Build CLI tool + integration with existing tools

### Tasks

#### 3a. Build Unified Index
1. Create `storage/unified_index.json` that merges:
   - KB entries (id, title, tags, category, builds_on, date, team)
   - Scripts (id, title, tags, category, language, path)
   - Sources (id, title, tags, data_type, access_type, url)
   - API Tools (id, title, tags, category, auth_type)
   - Team Sessions (id, objective, tags, members, builds_on_knowledge, date)
   - Market Research techniques (category, name, references)
2. Normalize all entries to common schema:
   ```json
   {
     "id": "KB-0001",
     "domain": "knowledge-base",
     "title": "...",
     "tags": ["tag1", "tag2"],
     "category": "...",
     "date": "YYYY-MM-DD",
     "path": "knowledge-base/entries/KB-0001.md",
     "relationships": ["KB-0002", "SCR-0001"]
   }
   ```
3. Build inverted tag index (tag → list of entries across all domains)
4. Build relationship graph (which entries reference which, across domains)

#### 3b. Build Search Tool
1. Create `storage/scripts/unified_search.py`:
   - Full-text TF-IDF search across all domains (extend `kb_search.py`)
   - Tag-based filtering: `--tag coordination --domain scripts`
   - Category-based filtering: `--category optimization`
   - Domain-scoped search: `--domain kb,scripts`
   - Relationship traversal: `--related KB-0001` (show everything linked to KB-0001)
   - Date range filtering: `--after 2026-03-11`
   - Output: JSON or human-readable table
2. Create `storage/scripts/fast_unified_search/main.go`:
   - Go compiled version for <5ms queries
   - Reads unified_index.json at startup
   - Same query interface as Python version

#### 3c. Auto-Rebuild
1. Create `storage/scripts/rebuild_unified_index.py`:
   - Reads all 5 index.json files + taxonomy.json
   - Generates unified_index.json
   - Run after any index change
2. Add to fast_validate: check unified_index.json freshness

### Deliverable
- `storage/unified_index.json` — single index across all domains
- `unified_search.py` — Python search tool
- `fast_unified_search/main.go` — Go search tool
- `rebuild_unified_index.py` — index generator

---

## Phase 4: Cross-Reference & Relationship Graph (Team: 2 agents)

**Goal**: Map all connections between entries across domains.

### Team Composition
- **Graph Builder Agent**: Discover and index all cross-references
- **Visualization Agent**: Build queryable relationship tools

### Tasks
1. Extend `build-cross-ref-index.py` to cover all 5 domains
2. Discover implicit relationships:
   - KB entry mentions a script by name → link them
   - Team session created a KB entry → link them
   - Script uses an API source → link them
   - KB entry `builds_on` another → already linked, include in graph
3. Build `storage/relationship_graph.json`:
   ```json
   {
     "nodes": [...],
     "edges": [
       {"from": "KB-0015", "to": "SCR-0029", "type": "documents"},
       {"from": "TEAM-0022", "to": "KB-0022", "type": "produced"},
       {"from": "SCR-0024", "to": "SRC-0011", "type": "uses"}
     ]
   }
   ```
4. Add relationship query to unified_search:
   - `--graph KB-0001 --depth 2` — show all entries within 2 hops
   - `--orphans` — find entries with zero relationships (likely missing links)
5. Create `storage/scripts/graph_query.py` for standalone graph queries

### Deliverable
- `storage/relationship_graph.json` — full cross-domain relationship map
- `graph_query.py` — CLI for graph traversal
- Orphan report: entries that should be connected but aren't

---

## Phase 5: Validation & Documentation (Team: 1 agent)

**Goal**: Ensure everything works and document how to use it.

### Tasks
1. Run full validation suite: `fast_validate` + `kb_validator` + `cross_ref_checker` + `validate-storage`
2. Run unified search with test queries to verify results
3. Run graph queries to verify relationships
4. Update `CLAUDE.md` with new tools and workflow
5. Create `storage/SEARCH_GUIDE.md` with usage examples
6. Create KB entry documenting the project (KB-0039)
7. Write team session log

### Deliverable
- All validators pass
- Documentation updated
- Knowledge preserved for future teams

---

## Execution Order & Dependencies

```
Phase 1 (Audit & Clean)
    ↓ (clean data required)
Phase 2 (Taxonomy)
    ↓ (tags required)
Phase 3 (Unified Index) ←── can start 3a while 2 finishes
    ↓ (index required)
Phase 4 (Relationship Graph) ←── can start 4.1-4.3 while 3b finishes
    ↓
Phase 5 (Validation & Docs)
```

**Parallelism opportunities:**
- Phase 2b and 2c are independent (different files)
- Phase 3a and 3b can overlap (index builder feeds search builder)
- Phase 4 can start graph discovery while Phase 3 search tools are being built
- Phase 5 is sequential (depends on everything else)

## Total Team Requirement

| Phase | Agents | Duration Est. | Notes |
|-------|--------|---------------|-------|
| Phase 1 | 2 | Short | Existing tools do most work |
| Phase 2 | 3 | Medium | Most labor-intensive phase |
| Phase 3 | 2 | Medium | Extends existing tools |
| Phase 4 | 2 | Short-Medium | Extends cross_ref_checker |
| Phase 5 | 1 | Short | Validation + docs |

**Total: 10 agents across 5 phases, some parallelizable to 3-4 concurrent batches.**

## Success Criteria
- [ ] `fast_validate` reports 0 errors, 0 warnings
- [ ] Every entry across all 5 domains has 2-5 standardized tags
- [ ] `unified_search.py "SQLite"` returns results from KB, scripts, sources, and team logs
- [ ] `graph_query.py --orphans` returns 0 unconnected entries
- [ ] Taxonomy documented in `storage/taxonomy.json`
- [ ] All tools registered in `storage/scripts/index.json`
