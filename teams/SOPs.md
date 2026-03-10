# Standard Operating Procedures for Agent Teams

> **Definitive guide for running agent teams in this repository.**
> Derived from patterns observed across 13 team session logs (TEAM-0001 through TEAM-0014).
> Last updated: 2026-03-10.

---

## Table of Contents

1. [Team Types & Configurations](#1-team-types--configurations)
2. [Pre-Mission Checklist](#2-pre-mission-checklist)
3. [During Mission](#3-during-mission)
4. [Post-Mission Checklist](#4-post-mission-checklist)
5. [Research Team SOP](#5-research-team-sop)
6. [Programming Team SOP](#6-programming-team-sop)
7. [Archive/Validation Team SOP](#7-archivevalidation-team-sop)
8. [Naming Conventions](#8-naming-conventions)

---

## 1. Team Types & Configurations

### Solo Agent

- **Example:** TEAM-0005 (1 software engineer), TEAM-0010 (1 research agent), TEAM-0012 (1 research agent), TEAM-0013 (1 research agent)
- **When to use:** Focused implementation or research tasks with a well-defined, narrow scope. The objective should be achievable without parallelism.
- **Best for:**
  - Iteration improvements on existing code (TEAM-0005 improved data-gathering methods from iteration 1 to iteration 2)
  - Single-category API research and cataloguing (TEAM-0010, TEAM-0012, TEAM-0013 each catalogued a different API domain)
  - Tasks where coordination overhead would exceed the work itself
- **Typical output:** 5-7 artifacts (scripts, source entries, or KB entries)
- **Duration:** Fastest to deploy; no coordination delays

### Small Triad (3 Agents)

- **Example:** TEAM-0006 (3 agents: agent-a1, agent-a2, agent-a3)
- **When to use:** Building 2-4 independent but related artifacts that share a common interface or format. All agents can work in parallel without coordination.
- **Best for:**
  - Building a suite of related tools (TEAM-0006 built 3 archive tools: kb_validator, index_rebuilder, cross_ref_checker)
  - Tasks where each agent produces a standalone deliverable
  - When the 3 outputs share a format but not dependencies
- **Key finding (KB-0007):** The 3-agent triad is the optimal unit for research tasks. This was validated by TEAM-0007's configuration testing across 1/2/3/4 agent setups.
- **Typical output:** 3 independent scripts or reports, plus 1 KB entry
- **Duration:** ~5-10 minutes; near-linear speedup over solo

### Coordinator + Sub-teams

- **Examples:** TEAM-0001 (coordinator + 4 team leads + 12 sub-agents), TEAM-0003 (coordinator + 5 team leads + 22 sub-agents), TEAM-0009 (coordinator + 7 teams + 8 sub-agents)
- **When to use:** Multi-phase operations that require dependency ordering, cross-team communication, or consolidation of parallel outputs.
- **Best for:**
  - Stress testing and system validation (TEAM-0001: 4 parallel test teams)
  - Competitive research with grading (TEAM-0003: research teams + verification teams + infrastructure team)
  - Build-and-verify workflows (TEAM-0009: programming teams + archive teams + research team)
- **Coordinator responsibilities:**
  - Route findings between teams
  - Enforce phase ordering (e.g., infrastructure before research, research before verification)
  - Consolidate results into main branch
  - Produce cross-team synergy report
- **Key pattern:** Give the coordinator **read-only access** to all team outputs to prevent write conflicts (established by TEAM-0009).
- **Typical output:** Multiple KB entries, many scripts, comprehensive reports
- **Duration:** 15-30 minutes for full operations

### Parallel Research Teams

- **Examples:** TEAM-0010, TEAM-0012, TEAM-0013 (solo agents running concurrently on different API domains)
- **When to use:** Broad coverage research where multiple independent domains need investigation. Each team gets a distinct, non-overlapping topic.
- **Best for:**
  - API cataloguing across domains (developer tools, AI/ML, government data)
  - Survey-style research where breadth matters more than depth
  - Building up a sources catalog quickly
- **Key pattern:** Assign non-overlapping ID ranges to prevent conflicts (e.g., TEAM-0010 got SRC-0025-0031, TEAM-0012 got SRC-0032-0037, TEAM-0013 got SRC-0018-0024).
- **Risk:** ID collisions if ranges are not pre-allocated. TEAM-0010 noted that parallel teams had created duplicate SRC IDs earlier.
- **Typical output:** 5-8 source entries per team, 1 KB entry per team

### Full Operation

- **Examples:** TEAM-0009 (8 teams, 20 agents), TEAM-0014 (9 agents across research + software teams)
- **When to use:** Large-scale operations combining research, programming, and verification. Use when the mission requires both discovering what to build and building it.
- **Best for:**
  - End-to-end build operations: research what's needed, build it, verify it (TEAM-0009)
  - Large-scale tool expansion with parallel research + parallel coding (TEAM-0014: 6 research teams + 2 software teams)
- **Structure:**
  - Phase 1: Research teams identify targets (run in parallel)
  - Phase 2: Programming teams build tools based on research (run in parallel)
  - Phase 3: Archive teams verify all work
- **Typical output:** 6+ new scripts, multiple KB entries, updated indexes
- **Duration:** 20-30 minutes

### Configuration Summary Table

| Type | Agents | Phases | Coordination | Best For |
|------|--------|--------|-------------|----------|
| Solo Agent | 1 | 1 | None | Focused implementation, narrow research |
| Small Triad | 3 | 1 | None (parallel) | Related independent artifacts |
| Coordinator + Sub-teams | 8-27 | 2-4 | Coordinator agent | Multi-phase dependent workflows |
| Parallel Research | 3-6 solo | 1 | ID range pre-allocation | Broad survey research |
| Full Operation | 9-20 | 2-3 | Coordinator + phases | Research-to-implementation pipelines |

### Optimal Sub-team Sizing

Per KB-0007 (validated by TEAM-0007 testing 4 configurations on identical tasks):

- **Research teams:** 3 lead agents + 2 sub-agents each (9 total) is optimal
- **Per-team sweet spot:** 4-5 agents per team for efficiency (TEAM-0003 finding)
- **Solo agents:** Sufficient for scoped tasks with clear deliverables
- **Diminishing returns:** 4 leads + 2 subs each (12 total) did not significantly outperform 3+2 (9 total)

---

## 2. Pre-Mission Checklist

Complete these steps **before** launching any agents:

### 2.1 Review Prior Work

- [ ] Read the most recent team session logs in `teams/sessions/` (at minimum the last 3-5 logs)
- [ ] Check `knowledge-base/index.json` for entries relevant to your objective
- [ ] Read referenced KB entries in full, not just titles
- [ ] Identify `builds_on` dependencies -- your work should chain from prior knowledge

### 2.2 Identify Available Tools

- [ ] Check `storage/scripts/index.json` for existing scripts that may help
- [ ] Check `storage/sources/index.json` for already-catalogued data sources
- [ ] Check `storage/api-tools/index.json` for API tools
- [ ] Verify needed scripts actually exist on disk (orphan entries are a known issue)

### 2.3 Determine Next IDs

- [ ] Check the highest existing ID for each type you will create:
  - KB entries: scan `knowledge-base/index.json` for highest KB-XXXX
  - Scripts: scan `storage/scripts/index.json` for highest SCR-XXXX
  - Sources: scan `storage/sources/index.json` for highest SRC-XXXX
  - Teams: scan `teams/sessions/` directory for highest TEAM-XXXX
- [ ] **If running parallel teams:** pre-allocate non-overlapping ID ranges to prevent collisions

### 2.4 Environment Check

- [ ] Confirm `ANTHROPIC_API_KEY` is set if using Sonnet Research API
- [ ] Verify network access to required APIs (some are blocked: Wikidata returned 403 in TEAM-0005)
- [ ] Confirm Go 1.21+ is available if using fast_* tools
- [ ] Run `python3 storage/scripts/kb_validator.py` to verify repo health before making changes

### 2.5 Plan Team Structure

- [ ] Select team type from Section 1 based on mission scope
- [ ] Define clear, non-overlapping responsibilities for each agent/team
- [ ] Establish phase ordering if tasks have dependencies
- [ ] Create a backup if performing destructive operations (TEAM-0001 created `backups/storage-backup-*.tar.gz`)

---

## 3. During Mission

### 3.1 Coordination

- **Coordinator agents** should have read-only access to all team outputs; never write to another team's workspace.
- Run teams in **dependency-ordered phases**, not all at once:
  - Infrastructure/tooling teams first (they provide tools others need)
  - Research teams next (they inform what to build)
  - Programming teams after research completes
  - Verification/archive teams last
- **Consolidate to main branch** as each team completes, not all at once at the end.

### 3.2 Parallel Execution

- Use **git worktrees** for isolated parallel agent runs (`.claude/worktrees/`). These can be cleaned up post-mission.
- Use `concurrent.futures.ThreadPoolExecutor` for parallel execution within scripts.
- When multiple agents search the web simultaneously, all searches execute in parallel for maximum throughput (as done by TEAM-0008).

### 3.3 Rate Limits

Respect these rate limits for external APIs:

| API | Rate Limit | Implementation |
|-----|-----------|----------------|
| DuckDuckGo | 0.5s between requests | `ddg_utils.py` rate limiter (needs `threading.Lock` -- see TEAM-0009 handoff) |
| Semantic Scholar | 1 req/sec (100/5min without key) | Built into `method_semantic_scholar.py` |
| OpenAlex | Polite pool with email header | Built into `method_openalex.py` |
| GitHub (unauthenticated) | 60 req/hr | Use PAT for 5,000/hr |
| Stack Exchange | 300/day (no key), 10,000/day (free key) | Register for free key |
| BLS v1 | 25/day | Always use v2 (500/day) with free key |
| Anthropic API | 50 req/min default | Adjust in `config.py` per tier |

### 3.4 Standardized Output Formats

- **Data-gathering methods:** Follow the `method_*.py` pattern (imports, `run()` function, CLI entry point, JSON output)
- **Research results:** Structured JSON with categories, sources, and confidence scores
- **Benchmark results:** Table format with Method, Time, Data Points, Quality Score columns
- **Source entries:** Follow `storage/sources/TEMPLATE.json`
- **Scripts:** Register in `storage/scripts/index.json` upon creation

### 3.5 Error Handling

- Use `request_cache.py` for HTTP requests to avoid redundant calls.
- Implement retry with exponential backoff (e.g., 3 attempts at 1s/2s/4s as in TEAM-0002's client.py).
- Use atomic writes (temp file + rename) to prevent corruption of shared files.
- If Sonnet Research API is unavailable (no API key), pivot to WebSearch-based research (TEAM-0003 precedent).

### 3.6 Logging

- Log all major decisions and approach changes in the session log.
- Record when you pivot from the original plan and why.
- Track which agents completed which deliverables.
- Note any environment issues (blocked APIs, missing tools, etc.).

---

## 4. Post-Mission Checklist

Complete **all** of these steps before considering the mission done:

### 4.1 Create Knowledge Base Entry

- [ ] Use `knowledge-base/TEMPLATE.md` for the entry format
- [ ] Place the entry in `knowledge-base/entries/KB-XXXX.md`
- [ ] Include: Context, Method, Result, Lessons Learned, Recommendations
- [ ] Set frontmatter fields: id, date, team, role, category, tags, status, confidence, builds_on
- [ ] Set `builds_on` to reference the KB entries your work extends

### 4.2 Write Team Session Log

- [ ] Use `teams/TEAM_LOG_TEMPLATE.md` for the format
- [ ] Place the log in `teams/sessions/TEAM-XXXX.md`
- [ ] Include all required sections: Objective, Approach, Decisions Made, Knowledge Generated, Handoff Notes
- [ ] Set frontmatter: team_id, date, members, objective, parent_team, builds_on_knowledge

### 4.3 Update All Indexes

- [ ] Update `knowledge-base/index.json` with new KB entries
- [ ] Update `storage/scripts/index.json` with new scripts
- [ ] Update `storage/sources/index.json` with new source entries
- [ ] Update `teams/sessions/index.json` with the new team log
- [ ] Run `python3 storage/scripts/index_rebuilder.py --dry-run` to verify consistency

### 4.4 Verify Work

- [ ] Run `python3 storage/scripts/kb_validator.py` to check for schema drift
- [ ] Run `python3 storage/scripts/cross_ref_checker.py` to validate references
- [ ] Verify all new files are registered in their respective index.json
- [ ] Confirm no orphan files were left behind

### 4.5 Write Handoff Notes

Handoff notes are **critical** -- they are the primary mechanism for knowledge transfer between teams. Include:

- [ ] What was completed and where the artifacts live
- [ ] What was **not** completed and why
- [ ] Known issues, bugs, or limitations discovered
- [ ] Specific next steps for the follow-on team
- [ ] Environment-specific warnings (blocked APIs, missing keys, etc.)
- [ ] Any open action items (consider running `python3 storage/scripts/handoff_tracker.py` for the full list)

---

## 5. Research Team SOP

### 5.1 Query Formulation

1. Break the research objective into 3-5 non-overlapping topic areas.
2. Assign each topic area to a separate agent or sub-team.
3. For each topic, formulate 2-3 specific search queries (broad + narrow + targeted).
4. Use category-specific terminology to improve search precision.

### 5.2 Source Hierarchy

Use this credibility weighting when evaluating and merging results:

| Tier | Weight | Examples |
|------|--------|----------|
| Academic | 1.5x | arXiv, OpenAlex, Semantic Scholar, PubMed |
| Encyclopedia | 1.2x | Wikipedia, Britannica |
| Structured Knowledge | 1.1x | Wikidata, ConceptNet, DBpedia |
| Web | 1.0x | DuckDuckGo, news sites, blogs |

### 5.3 Multi-Source Validation

- Every key claim must be corroborated by at least 2 independent sources.
- Use the corroboration scoring system from `method_hybrid.py` -- claims confirmed by multiple sources of different types score higher.
- Flag single-source claims as low-confidence.

### 5.4 Deduplication

- Use TF-IDF cosine similarity for deduplication (implemented in `dedup_utils.py`, documented in KB-0005).
- **Threshold:** 0.65 cosine similarity -- balances false positive avoidance with genuine duplicate detection.
- Apply deduplication after merging results from multiple agents/sources.

### 5.5 Grading & Merging

- Use `grade_research.py` (SCR-0004) to objectively score research output on completeness, accuracy, source quality, and formatting.
- Use `merge_research.py` (SCR-0005) to combine outputs from parallel research teams into a single consolidated report.
- Grading criteria (from TEAM-0003): Breadth of coverage, depth per topic, source quality, factual accuracy, actionability.
- TEAM-0003 established that **breadth slightly beats depth** for survey-style research topics.

### 5.6 Competitive Research Pattern

When running multiple teams on the same topic (as in TEAM-0003):

1. Deploy research teams in Phase 1 (broad vs. deep configurations).
2. Deploy verification/grading teams in Phase 2 to score Phase 1 output.
3. Run head-to-head comparison.
4. Consolidate the best findings from all teams into the final report.

---

## 6. Programming Team SOP

### 6.1 Code Patterns

- **Data-gathering methods:** Follow the `method_*.py` pattern:
  - Standard imports at top
  - `run(query)` function as the main entry point returning structured results
  - CLI entry point with `argparse` for standalone use
  - JSON output format with consistent schema
- **Archive/validation tools:** Pure Python stdlib only (no external dependencies). This was a hard requirement from TEAM-0006.
- **Go tools:** For performance-critical paths. Single-binary deployment, requires Go 1.21+.

### 6.2 Required Practices

- **Use `request_cache.py`** for all HTTP requests to avoid redundant calls and respect rate limits.
- **Use `ddg_utils.py`** for DuckDuckGo searches (consolidates rate limiting and error handling).
- **Implement retry with backoff:** 3 attempts with 1s/2s/4s exponential backoff for external API calls.
- **Use atomic writes:** temp file + rename pattern when writing to shared files (index.json, etc.).
- **Use absolute imports only:** Scripts must work from any working directory in Claude Code.

### 6.3 Error Handling

- Handle HTTP 403 (blocked API) gracefully with a clear message (Wikidata is known to 403).
- Handle rate limit responses (HTTP 429) with automatic backoff.
- Avoid bare `except:` clauses -- catch specific exceptions.
- Log errors to stderr; output results to stdout in JSON format.

### 6.4 Registration

After writing a script:

1. Assign the next available SCR-XXXX ID.
2. Add an entry to `storage/scripts/index.json` with: id, name, description, path, category, date_created.
3. Verify the entry was added correctly by running `python3 storage/scripts/kb_validator.py`.

### 6.5 Iteration Pattern

The continuous improvement loop (established by TEAM-0004):

1. **Research team** identifies best practices and improvements.
2. **Software team** implements improvements (builds new methods or enhances existing ones).
3. **Verification team** tests and benchmarks the output.
4. **Software team v2** incorporates feedback from verification.
5. Repeat until loop health is GREEN.

---

## 7. Archive/Validation Team SOP

### 7.1 Standard Validation Suite

Run these tools in order:

1. **`python3 storage/scripts/kb_validator.py`**
   - Validates all KB entries against TEMPLATE.md schema
   - Checks index.json consistency (entries match files on disk)
   - Reports tag mismatches between files and index
   - Example real bug found: KB-0003 had tag `multi-team` in file but `multi-agent` in index

2. **`python3 storage/scripts/cross_ref_checker.py`**
   - Validates all `builds_on` references resolve to real KB entries
   - Detects circular dependency cycles via DFS
   - Outputs both text dependency tree and structured JSON

3. **`python3 storage/scripts/index_rebuilder.py --dry-run`**
   - Scans all directories and compares to index.json files
   - Reports orphan files (on disk but not in index) and dangling references (in index but not on disk)
   - Use `--fix` to auto-repair (but always `--dry-run` first)

### 7.2 Verification Checklist

- [ ] All new files are registered in their respective index.json
- [ ] No orphan files left behind (stress testing has historically created orphans -- TEAM-0001 left 16, TEAM-0007 found 8)
- [ ] All `builds_on` references point to existing KB entries
- [ ] Tags in files match tags in index entries
- [ ] YAML frontmatter in all KB entries and team logs parses correctly
- [ ] New scripts execute without import errors
- [ ] Benchmark results are recorded if applicable

### 7.3 Reporting

Archive team output should be structured:

```
## Validation Report
- Files checked: N
- Errors found: N
- Warnings found: N
- Orphan files: [list]
- Dangling references: [list]
- Auto-fixed: [list of what --fix corrected]
- Manual action needed: [list]
```

### 7.4 Performance Benchmarking

When verifying programming team output:

- Run `python3 benchmark_runner.py "test query"` to benchmark all data-gathering methods.
- Compare against prior iteration baselines (stored in `storage/scripts/data-gathering/benchmarks/`).
- Record results in the standard table format:

| Method | Time | Data Points | Quality Score |
|--------|------|-------------|---------------|

### 7.5 Go Tool Verification

For Go-based tools (fast_search, fast_validate, fast_cache):

- Verify Go 1.21+ is available
- Build with `go build` and confirm single-binary output
- Benchmark against Python equivalents (TEAM-0009 benchmarks are the baseline)
- Verify `fast_validate` runs in <2ms for pre-commit hook suitability

---

## 8. Naming Conventions

### 8.1 ID Formats

| Entity | Format | Example | Where Registered |
|--------|--------|---------|-----------------|
| Knowledge Base entry | KB-XXXX | KB-0014 | `knowledge-base/index.json` |
| Team session log | TEAM-XXXX | TEAM-0014 | `teams/sessions/index.json` |
| Script | SCR-XXXX | SCR-0028 | `storage/scripts/index.json` |
| Source | SRC-XXXX | SRC-0037 | `storage/sources/index.json` |
| API Tool | TOOL-XXXX | TOOL-0001 | `storage/api-tools/index.json` |
| Data-gathering method | DG-XXXX | DG-0007 | Referenced in benchmarks |

### 8.2 File Naming

| Type | Pattern | Example |
|------|---------|---------|
| KB entry | `KB-XXXX.md` | `knowledge-base/entries/KB-0014.md` |
| Team log | `TEAM-XXXX.md` | `teams/sessions/TEAM-0014.md` |
| Data-gathering method | `method_*.py` | `storage/scripts/data-gathering/methods/method_openalex.py` |
| Validation tool | descriptive name | `storage/scripts/kb_validator.py` |
| Source entry | `SRC-XXXX-name.json` | `storage/sources/SRC-0018-fred.json` |
| Go tool | `main.go` in named dir | `storage/scripts/fast_search/main.go` |

### 8.3 Sequential Numbering Rules

- IDs are **zero-padded to 4 digits** (e.g., KB-0001, not KB-1).
- Always check the highest existing ID before assigning a new one.
- **Never reuse** an ID, even if the original entry was deleted or deprecated.
- When running parallel teams, **pre-allocate ID ranges** to prevent collisions.
- Gaps in numbering are acceptable (TEAM-0011 does not exist; this is fine).

### 8.4 Frontmatter Standards

**KB entries** (from `knowledge-base/TEMPLATE.md`):
```yaml
id: KB-XXXX
date: YYYY-MM-DD
team: team-name
role: agent-role
category: [methodology|tool-usage|debugging|optimization|integration|market-research]
tags: [tag1, tag2]
status: validated|experimental|deprecated
confidence: high|medium|low
builds_on: [KB-XXXX]
```

**Team logs** (from `teams/TEAM_LOG_TEMPLATE.md`):
```yaml
team_id: TEAM-XXXX
date: YYYY-MM-DD
members: [agent-roles]
objective: "description"
parent_team: TEAM-XXXX or null
builds_on_knowledge: [KB-XXXX]
```

---

## Appendix A: Common Pitfalls

These issues have been encountered across multiple team sessions:

1. **Orphan files from stress testing.** TEAM-0001 left 16 orphan test files; TEAM-0007 found 8 more. Always clean up or register test artifacts.
2. **ID collisions from parallel teams.** TEAM-0010 found duplicate SRC IDs from earlier parallel runs. Pre-allocate ranges.
3. **Blocked APIs.** Wikidata (403), Stack Exchange (egress policy), Reddit (OAuth required). Test API access before building a method around it.
4. **Missing API keys.** TEAM-0003 had to pivot from Sonnet API to WebSearch when no ANTHROPIC_API_KEY was set. Always have a fallback plan.
5. **Bare except clauses.** TEAM-0007 noted `method_wikipedia.py` still has broad except clauses. Use specific exception types.
6. **Index/file tag mismatches.** KB-0003 had `multi-team` in file but `multi-agent` in index. Run kb_validator.py to catch these.
7. **Thread safety in rate limiter.** `ddg_utils.py` needs `threading.Lock` for parallel benchmark usage (TEAM-0009 handoff).
8. **Wikipedia API limitations.** Returns only 1 result; may need User-Agent fix or different API approach (TEAM-0005).

## Appendix B: Quick Reference -- Tools

| Tool | Path | Purpose |
|------|------|---------|
| kb_validator.py | `storage/scripts/kb_validator.py` | Validate KB entries and indexes |
| cross_ref_checker.py | `storage/scripts/cross_ref_checker.py` | Check builds_on references, detect cycles |
| index_rebuilder.py | `storage/scripts/index_rebuilder.py` | Rebuild index.json from disk (--dry-run/--fix) |
| grade_research.py | `storage/scripts/grade_research.py` | Score research output quality |
| merge_research.py | `storage/scripts/merge_research.py` | Merge parallel research outputs |
| benchmark_runner.py | `storage/scripts/data-gathering/methods/benchmark_runner.py` | Benchmark all data-gathering methods |
| handoff_tracker.py | `storage/scripts/handoff_tracker.py` | Track open action items across teams |
| staleness_detector.py | `storage/scripts/staleness_detector.py` | Check repo health (report-only) |
| kb_search.py | `storage/scripts/kb_search.py` | Full-text TF-IDF search across KB |
| search-storage.py | `storage/scripts/search-storage.py` | Cross-subsystem storage search |
| request_cache.py | `storage/scripts/data-gathering/methods/request_cache.py` | HTTP request caching |
| ddg_utils.py | `storage/scripts/data-gathering/methods/ddg_utils.py` | DuckDuckGo rate-limited search |
| dedup_utils.py | `storage/scripts/data-gathering/methods/dedup_utils.py` | TF-IDF deduplication |
| fast_search | `storage/scripts/fast_search/main.go` | Go full-text search (3.5ms) |
| fast_validate | `storage/scripts/fast_validate/main.go` | Go repo validator (1.4ms) |
| fast_cache | `storage/scripts/fast_cache/main.go` | Go file cache (<1ms) |
