# Storage System Directions for AI Teams

This document explains how to use the `storage/` system. Read it before adding, modifying, or consuming anything stored here.

## Overview

The storage system has three subsections:

| Directory     | Purpose                                    | ID Format   |
|---------------|--------------------------------------------|-------------|
| `scripts/`    | Reusable code (Python, Bash, JS, etc.)     | `SCR-XXXX`  |
| `api-tools/`  | Registered API tool configurations         | `TOOL-XXXX` |
| `sources/`    | Curated index of web APIs and data sources | `SRC-XXXX`  |

Each subsection has its own `README.md` (detailed rules), `index.json` (registry), and in some cases a template file.

## The Golden Rules

1. **Always update `index.json`** when you add, modify, or remove an entry. The index is the source of truth for discovery.
2. **Use the next available ID** — check the existing entries in `index.json` and increment. Never reuse a retired ID.
3. **Set `added_by_team`** to your team identifier (e.g. `TEAM-0005`) so future teams know who added what.
4. **Don't duplicate** — search the index before adding. If a similar script, tool, or source already exists, extend or update it instead.

## Adding a Script (`scripts/`)

1. Write your script and place it in `scripts/` (or a category subdirectory).
2. Follow the naming convention: lowercase with hyphens, include the extension (`fetch-earnings.py`).
3. Add an entry to `scripts/index.json` with all required fields (see `scripts/README.md` for the schema).
4. Add the script ID to the appropriate `categories` list in the index.

**Required fields:** `id`, `name`, `filename`, `language`, `category`, `description`, `dependencies`, `added`, `added_by_team`

## Adding an API Tool (`api-tools/`)

1. Create a tool configuration file in `api-tools/`.
2. Register it in `api-tools/index.json` with all required fields (see `api-tools/README.md`).
3. Add the tool ID to the appropriate `categories` list.
4. **Track usage**: every time you invoke a tool, increment `usage_count` and update `last_used` in the index.

**Required fields:** `id`, `name`, `description`, `category`, `endpoint`, `auth_type`, `rate_limit`, `response_format`, `last_used`, `usage_count`, `added`, `added_by_team`

## Adding a Data Source (`sources/`)

1. Copy `sources/TEMPLATE.json` to `sources/SRC-XXXX.json` (next available ID).
2. Fill in every field — leave nothing as placeholder text.
3. Update `sources/index.json`:
   - Add a summary object to the `sources` array.
   - Add the ID to every relevant `by_data_type` list.
   - Add the ID to the correct `by_access_type` list.

**Required fields:** `id`, `name`, `url`, `api_docs_url`, `data_types`, `access_type`, `auth_method`, `rate_limits`, `response_format`, `reliability`, `last_verified`, `notes`, `added_by_team`

## Consuming Storage Entries

When you need a script, tool, or source:

1. **Read the relevant `index.json`** first — it's your lookup table.
2. Filter by category, language, data type, or access type as needed.
3. Read the entry's file or config for full details before using it.
4. If a source or tool has `reliability: "low"` or hasn't been verified recently, re-verify before depending on it.

## Modifying Existing Entries

- **Update, don't replace** — edit the existing file and index entry in place.
- **Bump `last_updated`** in the index's top-level field to today's date.
- If you significantly change a script's behavior, update its `description` in the index.
- If a source URL changes or goes offline, update or mark its `reliability` accordingly.

## Removing Entries

- Delete the file and remove the entry from `index.json` (both the main array and any category lists).
- Do NOT leave orphaned index entries pointing to deleted files.
- Note the removal in your team session log so future teams understand why.

## Common Mistakes to Avoid

- Adding a file but forgetting to update `index.json`
- Using a placeholder ID like `SRC-XXXX` instead of the real next ID
- Leaving `TEMPLATE` fields unfilled (e.g. empty strings, `YYYY-MM-DD` dates)
- Creating a duplicate of an existing source under a different ID
- Forgetting to track API tool usage counts
