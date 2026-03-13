# Proposal: Repository Data Reform — Machine-Friendly Format Transformation

**Date:** 2026-03-11
**Status:** AWAITING APPROVAL
**Requested by:** User

---

## Problem Statement

The current repository stores ~30 KB entries, 25 team logs, 50 scripts, 35 data sources, 13 SOPs, and a full coordination system — all in a mix of YAML-frontmatter Markdown, flat JSON indexes, and Python modules. While human-readable, this structure has friction points for AI agents:

1. **No programmatic discovery** — Agents must read index files, parse YAML frontmatter, and manually resolve cross-references (KB→TEAM→SCR chains)
2. **No unified query interface** — Each subsystem has its own index format; no way to ask "show me everything tagged `coordination`"
3. **No MCP integration** — Context is not served via Model Context Protocol; agents can't pull resources through tool calls
4. **Verbose consumption** — Reading a KB entry requires parsing markdown + YAML; no lightweight "what exists?" summary
5. **No semantic tagging across systems** — Scripts, KB entries, teams, and sources have separate tag namespaces

---

## Proposed 8-Team Structure

### Phase 1: Analysis (Teams 1-3, parallel)

| Team | Name | Mission | Deliverable |
|------|------|---------|-------------|
| **T1** | **Format Audit** | Catalog every data format in the repo. Map all cross-references. Identify redundancies, inconsistencies, and dead links. | Audit report with format inventory and dependency graph |
| **T2** | **MCP Research** | Deep-dive on MCP protocol: resource types, tool exposure, server SDKs (Python `mcp`, Node `@modelcontextprotocol/sdk`), transport options (stdio, SSE, streamable HTTP). Study how Claude Code's `.claude/settings.json` configures MCP servers. | MCP capability matrix and integration feasibility report |
| **T3** | **Agent UX Research** | Study how AI agents actually consume this repo today (from team logs). What queries do they run? What takes multiple steps that should take one? What information do they need at session start? | Agent interaction patterns report with pain points ranked by frequency |

### Phase 2: Solution Design (Teams 4-6, parallel)

| Team | Name | Mission | Deliverable |
|------|------|---------|-------------|
| **T4** | **Pointer Index Design** | Design a unified pointer-file system. Lightweight manifests with tags, types, and paths. Consider: JSON-LD for linked data, NDJSON for streaming, or a single `manifest.json` with typed entries. | 2-3 candidate index schemas with pros/cons |
| **T5** | **MCP Server Architecture** | Design MCP server(s) that expose repo data as resources and tools. Consider: single monolithic server vs. per-subsystem servers (kb-server, scripts-server, coordination-server). Define tool signatures. | MCP server design doc with tool/resource definitions |
| **T6** | **Migration Strategy** | Design the migration path from current formats. Must be backwards-compatible (existing CLAUDE.md workflows still work). Consider: dual-format period, automated converters, validation. | Migration plan with phases and rollback strategy |

### Phase 3: Debate & Evaluation (Team 7)

| Team | Name | Mission | Deliverable |
|------|------|---------|-------------|
| **T7** | **Solution Debate** | Take all proposals from T4-T6, stress-test them. Devil's advocate each approach. Score on: complexity, agent-friendliness, maintainability, MCP compatibility, migration risk. | Comparative matrix with strengths/weaknesses and recommendation |

### Phase 4: Research & Prototype (Team 8)

| Team | Name | Mission | Deliverable |
|------|------|---------|-------------|
| **T8** | **Tool & Ecosystem Research** | Research existing tools: MCP server frameworks, JSON-LD tooling, schema validators, index generators. Find what we can reuse vs. build. Test feasibility of top-rated solution with a small prototype. | Tool inventory + mini proof-of-concept |

---

## Candidate Solutions (To Be Evaluated)

### Solution A: Unified Manifest + MCP Server
- Single `manifest.json` at repo root with typed entries for ALL resources
- Each entry: `{id, type, path, tags[], summary, relations[], metadata}`
- Python MCP server reads manifest, exposes `search`, `get_entry`, `list_by_tag`, `get_related` tools
- Resources exposed as MCP resources with URI templates

**Strengths:** Single source of truth, clean MCP integration, simple mental model
**Weaknesses:** Large single file, merge conflicts, single point of failure

### Solution B: Distributed Pointer Files + Aggregator
- Each directory keeps its own `.index.json` (already partially exists)
- Add a lightweight `.pointers.ndjson` per directory with standardized schema
- Aggregator script builds unified view on demand
- MCP server reads aggregated view

**Strengths:** Distributed ownership, git-friendly, incremental updates
**Weaknesses:** Consistency harder to enforce, requires aggregation step

### Solution C: JSON-LD Semantic Layer
- Convert all metadata to JSON-LD with shared `@context`
- Define ontology: `KBEntry`, `TeamSession`, `Script`, `DataSource`, `SOP`
- Relations become proper linked data (`builds_on`, `generated_by`, `uses_script`)
- MCP server with SPARQL-like query support

**Strengths:** Rich queryability, semantic relationships, standards-based
**Weaknesses:** Higher complexity, learning curve, overkill for current scale

### Solution D: Hybrid — Tagged NDJSON + MCP Tools
- Keep Markdown files as-is (human-readable source of truth)
- Generate `.registry.ndjson` files per subsystem (one JSON object per line)
- Unified tag namespace across all subsystems
- MCP server with focused tools: `search(tags, type)`, `get(id)`, `graph(id, depth)`
- Auto-regeneration via git hooks or on-demand

**Strengths:** Best of both worlds, streaming-friendly, backwards compatible
**Weaknesses:** Requires generation step, potential staleness

---

## Tools to Research

| Tool | Purpose | Status |
|------|---------|--------|
| `mcp` (Python SDK) | Build MCP servers in Python | Available on PyPI |
| `@modelcontextprotocol/sdk` | Build MCP servers in Node.js | Available on npm |
| `fastmcp` | Simplified Python MCP server builder | Community tool |
| JSON Schema / `jsonschema` | Validate pointer file schemas | Standard tooling |
| `jq` / `gron` | CLI JSON querying for agents | Available |
| `linkml` | Schema-driven linked data modeling | Research candidate |
| Git hooks | Auto-regenerate indexes on commit | Built-in |
| `watchdog` (Python) | File-change triggered regeneration | Available |

---

## Execution Timeline

```
Phase 1 (Analysis):    T1 + T2 + T3 in parallel     → audit reports
Phase 2 (Design):      T4 + T5 + T6 in parallel     → solution proposals
Phase 3 (Debate):      T7 evaluates all proposals    → ranked comparison
Phase 4 (Research):    T8 prototypes top solution    → feasibility proof
                       ↓
            USER DECISION POINT — choose solution(s) to implement
                       ↓
Phase 5 (Implement):   Implementation teams (TBD)    → production system
```

---

## Team Composition (Per Team)

Each team = 1 Claude agent session with focused mandate:
- **Lead Agent**: Drives research/design, writes deliverable
- Uses existing repo tools (coordination system, scripts) where applicable
- Writes findings as KB entries and team session logs per CLAUDE.md workflow
- Cross-references prior work (especially KB-0021 Proto A analysis, KB-0030 cross-team analysis)

---

## Success Criteria

1. An agent starting a new session can discover ALL relevant resources in ≤2 tool calls
2. Cross-system queries work (e.g., "scripts used by TEAM-0018 related to KB-0021")
3. MCP server exposes repo knowledge as tools/resources usable by Claude Code
4. Existing Markdown files remain human-readable
5. Index/pointer files are auto-validatable
6. Migration is reversible

---

## What I Need From You

1. **Approve/modify the 8-team structure**
2. **Approve/modify the 4 candidate solutions** (or suggest others)
3. **Prioritize**: Which phases matter most? Should we skip straight to prototyping?
4. **Scope**: Full repo reform or start with one subsystem (e.g., knowledge-base only)?
