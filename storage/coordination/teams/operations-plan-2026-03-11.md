# Operations Plan: A1 + B2 — Dual Operation with Think Tanks & Ops Logger

**Date:** 2026-03-11
**Commander:** Central Coordinator
**Total Agent Count:** ~42 agents across all teams
**Concurrent Operations:** 2 main + 3 think tanks + 1 ops logger = 6 parallel workstreams

---

## ORGANIZATIONAL SCHEME

```
Central Commander (this agent)
├── Operation A1: File Creation SOPs & Naming Standards (4 teams × 3 agents = 12)
│   ├── Team A1-Alpha (3): Research current file patterns + audit naming
│   ├── Team A1-Beta (3): Design naming conventions + controlled vocabulary
│   ├── Team A1-Gamma (3): Build SOPs for file creation/indexing
│   └── Team A1-Delta (3): Build validation tools + enforcement scripts
│
├── Operation B2: Token-Efficient Database I/O (flexible, ~15 agents)
│   ├── Team B2-Alpha (3): Analyze current token costs per operation
│   ├── Team B2-Beta (3): Design query protocol + API layer
│   ├── Team B2-Gamma (3): Prototype SQLite FTS5 + tiered index
│   ├── Team B2-Delta (3): Build token-efficient read/write tools
│   └── Team B2-Epsilon (3): Integration testing + benchmarks
│
├── Think Tank Teams (3 teams × 3 perspectives = 9 analytical streams) [RUNNING]
│   ├── TT-Alpha: Structural Efficiency / Format Optimization / Indexing & Discovery
│   ├── TT-Beta: Token Cost Analysis / AI-Native Language / Software Tools
│   └── TT-Gamma: Database Architecture / Compression & Dedup / Agent I/O Protocols
│
└── Ops Logger (1 team, 3 analytical tasks) [RUNNING]
    └── OL-1: Communication audit / efficiency patterns / metrics framework
```

---

## COMMUNICATION PLAN

### Channels
| Channel | Purpose | Who Uses It |
|---------|---------|-------------|
| `global` | Phase transitions, blockers, completion signals | All teams |
| `op-a1` | Operation A1 internal coordination | A1 teams only |
| `op-b2` | Operation B2 internal coordination | B2 teams only |
| `think-tank` | Think tank findings broadcast | TT teams → Commander |
| `ops-log` | Operational metrics and observations | OL-1 → Commander |

### Communication Protocol
1. **Status updates:** Every team posts to their operation channel after each significant milestone
2. **Cross-operation findings:** If A1 discovers something relevant to B2 (or vice versa), post to `global` with tag `[CROSS-OP]`
3. **Blockers:** Post to `global` immediately with `[BLOCKER]` tag. Commander reassigns resources within 1 cycle
4. **Findings format:** Standard finding schema (F-xxx, category, title, content, priority, source_team, evidence)
5. **Think tank integration:** TT findings are fed into Phase 2 of each operation before design work begins

### Coordination Tools
- **Scratchpad:** For sharing intermediate data between teams (key-based lookup)
- **Work Stealing Queue:** For distributable work items (A1-Delta and B2-Epsilon use this heavily)
- **Direct Channels:** For tight collaboration pairs (e.g., A1-Beta ↔ A1-Gamma for naming conventions → SOP handoff)
- **Pipeline Manager:** For dependent phases within each operation

---

## OPERATION A1: FILE CREATION SOPS & NAMING STANDARDS

### Objective
Research how files are created, stored, and indexed in this repository. Create comprehensive SOPs ensuring files are named correctly, efficiently, in AI-optimized language. Identify and codify all naming patterns.

### Builds On
- KB-0031 (Data Reform Analysis — pointer files, registry generator)
- KB-0030 (Cross-team analysis — 28 sessions, SOP improvements)
- KB-0029 (Best practices sorting schema)
- SOP-013 (Prompt engineering — naming conventions section)
- SOP-016 v2 (Cross-team data flow — output coordination)
- TEAM-0029 (Phase 0 fixes already completed some cleanup)

### Phase 1: Research & Audit (Teams A1-Alpha, A1-Beta in parallel)

**Team A1-Alpha (3 agents): Current State Audit**
- Lead: Audit Coordinator
- Sub-1: File Pattern Scanner — scan every directory, catalog all naming patterns, identify inconsistencies
- Sub-2: Index Format Analyst — compare all 6+ index formats, document schemas, find divergences
- Sub-3: Token Cost Measurer — measure token cost of reading each index, template, and common file

Output: `/home/user/Claude/storage/coordination/teams/a1-alpha/output/file_pattern_audit.json`

**Team A1-Beta (3 agents): Convention Research**
- Lead: Research Coordinator
- Sub-1: AI Naming Research — web search for AI-native naming conventions, controlled vocabularies
- Sub-2: Repo Convention Miner — extract implicit naming rules from the 31 KB entries, 29 team logs, 20 SOPs
- Sub-3: Cross-Repo Comparator — study how other AI-agent repos (AutoGPT, CrewAI, LangChain) organize files

Output: `/home/user/Claude/storage/coordination/teams/a1-beta/output/convention_research.json`

### Phase 2: Design (Teams A1-Beta, A1-Gamma — after Phase 1 + Think Tank input)

**Team A1-Beta (reassigned, 3 agents): Naming Convention Design**
- Design controlled vocabulary for this repo (prefixes, suffixes, separators)
- Define canonical naming rules per resource type (KB, SOP, team log, script, source, API tool)
- Create abbreviation dictionary (e.g., `kb` for knowledge-base, `coord` for coordination)
- Design file path shortening strategy (consistent depth limits, meaningful hierarchy)

Output: `/home/user/Claude/storage/coordination/teams/a1-beta/output/naming_conventions.json`

**Team A1-Gamma (3 agents): SOP Drafting**
- Lead: SOP Architect
- Sub-1: SOP Writer — Draft SOP-029: File Creation & Naming Standards
- Sub-2: SOP Writer — Draft SOP-030: Index Management & Maintenance
- Sub-3: Template Designer — Create updated templates with AI-optimized field names

SOPs to create:
1. **SOP-029: File Creation & Naming Standards** — When and how to create files, mandatory naming patterns, directory placement rules, file size guidelines
2. **SOP-030: Index Management & Maintenance** — How indexes are generated (not hand-edited), validation rules, when to regenerate, how to handle conflicts
3. **SOP-031: AI-Efficient Language Guide** — Controlled vocabulary, preferred terms vs discouraged synonyms, abbreviation dictionary, tag taxonomy

Output: `/home/user/Claude/storage/coordination/sops/sop_029_file_naming.json`, `sop_030_index_management.json`, `sop_031_ai_language.json`

### Phase 3: Build & Enforce (Team A1-Delta — after Phase 2)

**Team A1-Delta (3 agents): Validation Tools**
- Lead: Tool Architect
- Sub-1: Linter Builder — Build `validate_naming.py` that checks all file names against conventions
- Sub-2: Auto-Indexer — Build `auto_index.py` that regenerates all index files from source files
- Sub-3: Hook Builder — Build pre-commit hook that runs validation + auto-indexing

Tools to build:
1. `storage/scripts/validate_naming.py` — Checks file names, directory structure, frontmatter compliance
2. `storage/scripts/auto_index.py` — Regenerates all index.json files from source file frontmatter/metadata
3. `.claude/hooks/pre-commit-naming.sh` — Runs validation on staged files, blocks non-compliant commits

Output: The tools themselves + `/home/user/Claude/storage/coordination/teams/a1-delta/output/tool_report.json`

### Phase 4: Integration & Documentation
- Commander merges all outputs
- Updates CLAUDE.md with new naming conventions
- Creates KB entry documenting the complete naming system
- Creates team session log

---

## OPERATION B2: TOKEN-EFFICIENT DATABASE I/O

### Objective
Analyze and build software that makes inputting data and finding data more token-efficient for AI agents. Reduce the number of tokens agents spend on data I/O operations.

### Builds On
- KB-0008 (Data storage methods, AI-optimized storage)
- KB-0031 (Data reform — MCP server prototype, pointer files)
- KB-0009 (Multi-team optimization, pipeline patterns)
- SOP-025 (Proto A dev pipeline)
- Existing MCP server prototype in `storage/mcp-server/server.py`

### Phase 1: Analysis (Teams B2-Alpha, B2-Beta in parallel)

**Team B2-Alpha (3 agents): Token Cost Baseline**
- Lead: Analysis Coordinator
- Sub-1: Read Cost Analyst — Measure tokens needed to read each resource type (KB entry, SOP, index, team log). Count boilerplate vs content tokens
- Sub-2: Write Cost Analyst — Measure tokens needed to create each resource type. Count template overhead, index update cost, validation overhead
- Sub-3: Query Cost Analyst — Measure tokens needed for common queries: "find KB entries about X", "get latest team log", "list all SOPs". Count wasted tokens from reading irrelevant data

Output: `/home/user/Claude/storage/coordination/teams/b2-alpha/output/token_cost_baseline.json`

**Team B2-Beta (3 agents): Solution Research**
- Lead: Research Coordinator
- Sub-1: Database Solutions — Research SQLite FTS5, virtual tables, JSON1 extension for structured queries
- Sub-2: Index Solutions — Research tiered indexing (summary → detail), bloom filters, inverted indexes
- Sub-3: Protocol Solutions — Research query protocols, lazy loading, streaming, field-level access

Output: `/home/user/Claude/storage/coordination/teams/b2-beta/output/solution_research.json`

### Phase 2: Design (Teams B2-Beta, B2-Gamma — after Phase 1 + Think Tank input)

**Team B2-Beta (reassigned, 3 agents): Query Protocol Design**
- Design a query interface that returns only needed fields/entries
- Design tiered index: Level 0 (50-token summary) → Level 1 (tags+titles) → Level 2 (full content)
- Design token budget system: agents specify max tokens, system returns best results within budget

Output: `/home/user/Claude/storage/coordination/teams/b2-beta/output/query_protocol_design.json`

**Team B2-Gamma (3 agents): Database Schema Design**
- Design SQLite schema for unified knowledge store
- Design FTS5 virtual tables for full-text search across all resources
- Design materialized views for common query patterns
- Design migration path from current file-based system

Output: `/home/user/Claude/storage/coordination/teams/b2-gamma/output/db_schema_design.json`

### Phase 3: Prototype (Teams B2-Gamma, B2-Delta in parallel — after Phase 2)

**Team B2-Gamma (reassigned, 3 agents): Database + FTS5 Prototype**
- Build SQLite database with FTS5 indexing all resources
- Build `query_kb.py` — command-line tool for token-efficient queries
- Build import scripts that populate DB from existing file-based data

Output: `storage/scripts/query_kb.py`, `storage/scripts/import_to_db.py`, `storage/data/knowledge.db`

**Team B2-Delta (3 agents): Token-Efficient I/O Tools**
- Build `smart_read.py` — reads only requested fields from KB/SOP entries (not entire file)
- Build `smart_write.py` — creates entries with minimal token overhead (auto-fills boilerplate)
- Build `smart_search.py` — searches across all resource types, returns ranked results within token budget

Output: `storage/scripts/smart_read.py`, `storage/scripts/smart_write.py`, `storage/scripts/smart_search.py`

### Phase 4: Benchmark & Validate (Team B2-Epsilon — after Phase 3)

**Team B2-Epsilon (3 agents): Integration Testing**
- Lead: Test Coordinator
- Sub-1: Before/After Benchmarks — Compare token costs of common operations old way vs new way
- Sub-2: Integration Tests — Verify all tools work correctly with existing data
- Sub-3: MCP Integration — Wire new tools into existing MCP server (extend server.py)

Output: `/home/user/Claude/storage/coordination/teams/b2-epsilon/output/benchmark_results.json`

### Phase 5: Integration & Documentation
- Commander merges all outputs
- Updates CLAUDE.md with new I/O tools
- Creates KB entry documenting the efficiency gains
- Creates team session log

---

## EXECUTION TIMELINE

```
Time →

Phase 0 (NOW - already running):
  [TT-Alpha]  [TT-Beta]  [TT-Gamma]  [OL-1]
  ──────────  ─────────  ──────────  ──────
  researching  researching  researching  auditing

Phase 1 (after approval + TT results):
  Op A1: [A1-Alpha: audit] [A1-Beta: research]     ← parallel
  Op B2: [B2-Alpha: costs] [B2-Beta: solutions]     ← parallel
  OL-1:  [monitoring all teams, logging patterns]

Phase 2 (after Phase 1 completes):
  Op A1: [A1-Beta: design conventions] [A1-Gamma: write SOPs]  ← parallel
  Op B2: [B2-Beta: query protocol] [B2-Gamma: DB schema]       ← parallel
  OL-1:  [monitoring, interim report]

Phase 3 (after Phase 2 completes):
  Op A1: [A1-Delta: build tools + hooks]
  Op B2: [B2-Gamma: DB prototype] [B2-Delta: I/O tools]  ← parallel
  OL-1:  [monitoring, logging]

Phase 4 (after Phase 3 completes):
  Op A1: [Commander: integration + docs]
  Op B2: [B2-Epsilon: benchmarks + MCP integration]
  OL-1:  [final report]

Phase 5: Convergence
  Commander merges everything, creates KB entries + team log
```

### Parallelism Summary
- **Max concurrent agents:** ~18 (Phase 1: 12 from A1/B2 + 1 OL = 13, but some TTs may still be running)
- **Operations A1 and B2 run fully in parallel** throughout all phases
- **Within each operation,** teams within the same phase run in parallel
- **Cross-operation:** Think tank findings feed into Phase 2 of both operations

---

## THINK TANK INTEGRATION (already running)

Three think tank teams are generating ideas now. Their findings will be integrated:

| Think Tank | Perspectives | Feeds Into |
|-----------|-------------|------------|
| TT-Alpha | Structural efficiency, format optimization, indexing | A1 Phase 2 design, B2 Phase 2 design |
| TT-Beta | Token cost analysis, AI-native language, software tools | A1 Phase 2 (naming), A1 Phase 3 (tools) |
| TT-Gamma | Database architecture, compression, agent I/O protocols | B2 Phase 2 (schema), B2 Phase 3 (prototype) |

**Integration method:** Commander reads all TT outputs before launching Phase 2 agents. High-priority ideas are added as explicit requirements in Phase 2 prompts.

---

## OPS LOGGER INTEGRATION (already running)

The Ops Logger team audits communication patterns from all historical teams and monitors the current operation.

**Deliverables:**
1. Communication usage audit (which tools were used, by whom, how effectively)
2. Efficiency patterns worth adopting
3. Inefficiency patterns to avoid
4. Metrics framework for measuring future operations
5. Real-time observations from this operation's execution

**Integration method:** OL findings feed into A1's SOPs (communication best practices) and B2's tools (communication efficiency tools).

---

## OUTPUT PATH ALLOCATION TABLE

| Agent | Output Path |
|-------|------------|
| TT-Alpha | `storage/coordination/teams/think-tank-alpha/output/op_ideas.json` |
| TT-Beta | `storage/coordination/teams/think-tank-beta/output/op_ideas.json` |
| TT-Gamma | `storage/coordination/teams/think-tank-gamma/output/op_ideas.json` |
| OL-1 | `storage/coordination/teams/ops-logger/output/comms_audit.json` |
| A1-Alpha | `storage/coordination/teams/a1-alpha/output/file_pattern_audit.json` |
| A1-Beta (P1) | `storage/coordination/teams/a1-beta/output/convention_research.json` |
| A1-Beta (P2) | `storage/coordination/teams/a1-beta/output/naming_conventions.json` |
| A1-Gamma Sub-1 | `storage/coordination/sops/sop_029_file_naming.json` |
| A1-Gamma Sub-2 | `storage/coordination/sops/sop_030_index_management.json` |
| A1-Gamma Sub-3 | `storage/coordination/sops/sop_031_ai_language.json` |
| A1-Delta | `storage/coordination/teams/a1-delta/output/tool_report.json` |
| B2-Alpha | `storage/coordination/teams/b2-alpha/output/token_cost_baseline.json` |
| B2-Beta (P1) | `storage/coordination/teams/b2-beta/output/solution_research.json` |
| B2-Beta (P2) | `storage/coordination/teams/b2-beta/output/query_protocol_design.json` |
| B2-Gamma (P2) | `storage/coordination/teams/b2-gamma/output/db_schema_design.json` |
| B2-Gamma (P3) | `storage/scripts/query_kb.py` + `import_to_db.py` |
| B2-Delta | `storage/scripts/smart_read.py` + `smart_write.py` + `smart_search.py` |
| B2-Epsilon | `storage/coordination/teams/b2-epsilon/output/benchmark_results.json` |

No conflicts. All paths unique.

---

## DELIVERABLES CHECKLIST

### Operation A1 Deliverables
- [ ] Complete file pattern audit (every directory, every naming pattern)
- [ ] Controlled vocabulary / abbreviation dictionary
- [ ] SOP-029: File Creation & Naming Standards
- [ ] SOP-030: Index Management & Maintenance
- [ ] SOP-031: AI-Efficient Language Guide
- [ ] `validate_naming.py` — naming convention linter
- [ ] `auto_index.py` — automatic index regeneration
- [ ] Pre-commit hook for naming enforcement
- [ ] Updated templates with AI-optimized field names
- [ ] KB entry documenting the complete naming system

### Operation B2 Deliverables
- [ ] Token cost baseline for all I/O operations
- [ ] Query protocol specification
- [ ] SQLite + FTS5 database schema
- [ ] `query_kb.py` — token-efficient query tool
- [ ] `import_to_db.py` — populate DB from files
- [ ] `smart_read.py` — field-level file reading
- [ ] `smart_write.py` — minimal-overhead file creation
- [ ] `smart_search.py` — cross-resource search with token budget
- [ ] Before/after token cost benchmarks
- [ ] MCP server extensions
- [ ] KB entry documenting efficiency gains

### Think Tank Deliverables
- [ ] TT-Alpha: 30+ ideas across 3 perspectives
- [ ] TT-Beta: 30+ ideas with token cost estimates
- [ ] TT-Gamma: 30+ ideas on database and protocols

### Ops Logger Deliverables
- [ ] Communication usage audit
- [ ] Efficiency patterns catalog
- [ ] Inefficiency patterns catalog
- [ ] Metrics framework
- [ ] Operation observation report

---

## RISK MITIGATION

| Risk | Mitigation |
|------|-----------|
| Inward-facing gravity (teams build infrastructure for infrastructure) | SOP-023 anti-recursion check. All tools must serve external use cases. Commander reviews Phase 2 designs for practical utility. |
| Think tank ideas arrive late | Phase 1 research is independent of TT input. Only Phase 2 design waits for TT ideas. Fallback: proceed with Phase 1 findings if TTs are slow. |
| Agent count too high (42 total) | Not all 42 run simultaneously. Max concurrent is ~18. Phased execution means only 6-12 agents active at once. Per SOP-022, research ops support 9-12 agents. |
| Cross-operation conflicts | Dedicated channels (op-a1, op-b2) prevent noise. Path allocation table prevents file conflicts. Commander mediates cross-op findings. |
| Token budget exceeded | B2-Alpha measures costs first. Phase 2 designs include token budgets. No tool should require >5000 tokens for a common operation. |

---

## APPROVAL REQUIRED

This plan launches the following on approval:
1. **Phase 1:** 4 teams (12 agents) for A1 + B2 research/audit
2. Think tank results integrated into Phase 2 prompts
3. Subsequent phases launch sequentially per the timeline above

**Think tanks and ops logger are already running** to parallelize idea generation with plan approval.
