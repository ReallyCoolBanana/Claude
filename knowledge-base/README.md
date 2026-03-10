# AI Agent Knowledge Base

> Prime Directive: "Every AI team builds on the knowledge of the last team."

## Purpose

This knowledge base is the persistent memory layer for all AI agent teams. It captures methodology, tool usage, debugging insights, optimizations, integration patterns, and market research so that each successive team starts from a higher baseline rather than from scratch.

## How It Works

### Entry Format

Every knowledge entry is a Markdown file with YAML frontmatter stored in `entries/`. Each entry has:

- **id**: Unique identifier in the format `KB-XXXX` (zero-padded, sequential)
- **date**: ISO date when the entry was created
- **team**: The team that generated this knowledge
- **role**: The agent role that authored it
- **category**: One of `methodology`, `tool-usage`, `debugging`, `optimization`, `integration`, `market-research`
- **tags**: Freeform tags for cross-cutting search
- **status**: `validated` (proven reliable), `experimental` (promising but unconfirmed), or `deprecated` (superseded or found incorrect)
- **confidence**: `high`, `medium`, or `low`
- **builds_on**: List of `KB-XXXX` IDs that this entry extends or refines

See [TEMPLATE.md](TEMPLATE.md) for the full entry template.

### Adding an Entry

1. Copy `TEMPLATE.md` into `entries/` with the filename `KB-XXXX.md` (use the next available ID).
2. Fill in all frontmatter fields and content sections.
3. Update `index.json`:
   - Increment `entry_count`.
   - Add the entry metadata to the `entries` array.
   - Add the entry ID to the appropriate `categories` list.
   - Add the entry ID under each of its tags in `tag_index`.
   - Update `last_updated` to today's date.

### Searching the Knowledge Base

- **By category**: Look up `categories.<category>` in `index.json` to get all entry IDs in that category.
- **By tag**: Look up `tag_index.<tag>` in `index.json`.
- **By dependency**: Check the `builds_on` field in any entry to trace the knowledge chain.
- **Full text**: Search across `entries/*.md` using grep or similar tools.

### Updating or Deprecating an Entry

- To refine an entry, create a new entry that `builds_on` the original and set the original's status to `deprecated` if it is fully superseded.
- Never delete entries. Mark them `deprecated` instead so the historical chain remains intact.

## Directory Structure

```
knowledge-base/
  README.md          # This file
  TEMPLATE.md        # Template for new entries
  index.json         # Searchable index of all entries
  entries/           # All knowledge entry files (KB-XXXX.md)
    .gitkeep
```
