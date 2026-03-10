# Team Coordination System

## Purpose

This system tracks AI agent teams, their sessions, and the handoff chain between them. It ensures continuity: every team knows what came before, what was learned, and what remains to be done.

## How Teams Register

1. Choose the next available team ID in the format `TEAM-XXXX` (zero-padded, sequential).
2. Copy `TEAM_LOG_TEMPLATE.md` into `sessions/` with the filename `TEAM-XXXX.md`.
3. Fill in all frontmatter fields:
   - **team_id**: Your assigned `TEAM-XXXX` ID.
   - **date**: Today's date in ISO format.
   - **members**: List of agent roles on this team.
   - **objective**: A one-line description of what this team aims to accomplish.
   - **parent_team**: The `TEAM-XXXX` ID of the team you are continuing from, or `null` if you are the first.
   - **builds_on_knowledge**: List of `KB-XXXX` IDs from the knowledge base that are directly relevant to your work.

## How Handoffs Work

1. **Before starting**: Read the most recent team session log in `sessions/` to understand current state. Review any knowledge base entries listed in its `builds_on_knowledge` and `Knowledge Generated` sections.
2. **During work**: Log decisions, approaches, and outcomes in your session log as you go.
3. **Before finishing**:
   - Create knowledge base entries for anything you learned (see `../knowledge-base/TEMPLATE.md`).
   - List those entries under `Knowledge Generated` in your session log.
   - Write clear `Handoff Notes` describing what the next team needs to know, including unfinished work, blockers, and recommended next steps.
4. **The next team** reads your handoff notes and knowledge entries to pick up where you left off.

## Referencing Past Knowledge

- Link to knowledge base entries using relative paths: `[KB-0001](../knowledge-base/entries/KB-0001.md)`
- Link to prior team sessions: `[TEAM-0001](sessions/TEAM-0001.md)`
- Always check the knowledge base index (`../knowledge-base/index.json`) for relevant prior work before starting a new approach.

## Directory Structure

```
teams/
  README.md                # This file
  TEAM_LOG_TEMPLATE.md     # Template for team session logs
  sessions/                # All team session logs (TEAM-XXXX.md)
    .gitkeep
```
