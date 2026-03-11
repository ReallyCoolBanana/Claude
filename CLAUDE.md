# Claude Agent Repository

## Prime Directive
**Every AI team builds on the knowledge of the last team.** Before starting work, read the latest team session logs and relevant knowledge base entries.

## Repository Structure

```
knowledge-base/    # Shared knowledge entries (Markdown + YAML frontmatter)
teams/             # Team session logs and coordination
storage/
  scripts/         # Reusable scripts and utilities
  api-tools/       # API tool definitions and implementations
  sources/         # Curated index of web APIs and data sources
market-research/   # Stock picking methods, picks, and performance tracking
.claude/           # Claude Code configuration and hooks
```

## Coordination System

The repository includes a production multi-agent coordination system:

- `storage/coordination/coordinator_hub.py` - Hub-and-spoke agent monitoring (SQLite WAL)
- `storage/coordination/help_protocol.py` - Help requests, idle detection, work assignment
- `storage/coordination/direct_channels.py` - Named peer-to-peer team channels
- `storage/coordination/work_stealing.py` - Shared work queues and pipeline management
- `storage/coordination/multi_team_runner.py` - Multi-team launcher with heartbeat monitoring
- `storage/coordination/bus_cli.py` - CLI for JSONL message bus
- `storage/coordination/dashboard.py` - Real-time agent monitoring dashboard
- `storage/coordination/think_tank.py` - Cross-team findings convergence

SOPs are in `storage/coordination/sops/` (SOP-001 through SOP-016).
Tests are in `storage/coordination/tests/` and `storage/coordination/stress-tests/`.

## Prototype Communication Systems

- `prototype/agent_comm/` - Proto A: JSONL + SQLite WAL (production)
- `prototype/agent_comm_b/` - Proto B: FIFO + mmap (archived)

## Workflow for New Teams

1. Read `teams/` session logs from previous teams
2. Check `knowledge-base/index.json` for relevant prior knowledge
3. Do your work
4. Create knowledge base entries for anything you learned (use `knowledge-base/TEMPLATE.md`)
5. Write a team session log (use `teams/TEAM_LOG_TEMPLATE.md`)
6. Update relevant `index.json` files

## Adding Knowledge
- Use `knowledge-base/TEMPLATE.md` for the entry format
- Place entries in `knowledge-base/entries/`
- Update `knowledge-base/index.json`
- Set `builds_on` to reference prior entries you're extending

## Market Research
- Analysis methods are in `market-research/methods/`
- Log picks using `market-research/picks/TEMPLATE.md`
- Track performance in `market-research/picks/index.json`

## Storage
- Scripts go in `storage/scripts/` with metadata in `index.json`
- API tools go in `storage/api-tools/` with metadata in `index.json`
- Data sources go in `storage/sources/` using `TEMPLATE.json` format
