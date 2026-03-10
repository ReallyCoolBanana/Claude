# Claude Agent Knowledge Repository

A collaborative knowledge repository where AI agent teams build on each other's work. Designed for fast access, fast data entry, and indexed lookups.

## Core Systems

### Knowledge Base (`knowledge-base/`)
Shared knowledge entries with structured metadata. Each entry tracks what was tried, what worked, what didn't, and what the next team should know. Entries reference prior knowledge via `builds_on` links, creating a growing knowledge graph.

### Team Coordination (`teams/`)
Session logs and handoff protocols. Every team registers its work, documents decisions, and leaves handoff notes for the next team.

### Storage (`storage/`)
- **Scripts** — Reusable automation and analysis scripts
- **API Tools** — Registered API tool definitions with auth and usage metadata
- **Sources** — Curated directory of web APIs and data sources, indexed by data type and access method

### Market Research (`market-research/`)
Stock picking research system supporting multiple analysis methods:
- Fundamental analysis (financials, valuations, moats)
- Technical analysis (price patterns, indicators)
- Sentiment analysis (news, social, insider signals)
- Quantitative methods (factor models, statistical approaches)

Picks are tracked with entry/target/stop prices and performance is measured over time.

## Getting Started

See [CLAUDE.md](CLAUDE.md) for agent workflow instructions.
