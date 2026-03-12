# Index Schema Standard v2.0

All domain indexes in this repository MUST follow this base schema.
New domains can be added by creating a new index file following this format.

## Base Schema (required fields for ALL indexes)

```json
{
  "schema_version": "2.0",
  "domain": "knowledge-base|scripts|sources|api-tools|teams|sops|wikipedia-*",
  "last_updated": "YYYY-MM-DD",
  "entry_count": 0,
  "entries": [
    {
      "id": "PREFIX-NNNN",
      "title": "Human-readable title",
      "date": "YYYY-MM-DD",
      "category": "primary-category",
      "tags": ["tag1", "tag2"],
      "status": "active|archived|deprecated",
      "references": {
        "builds_on": [],
        "uses_scripts": [],
        "uses_sources": [],
        "related_to": []
      }
    }
  ],
  "secondary_indexes": {
    "by_category": {},
    "by_tag": {}
  }
}
```

## Domain-Specific Extensions

Each domain MAY add fields beyond the base schema. Extensions are nested
under a domain-specific key to avoid collisions.

### knowledge-base
- `team`: "TEAM-NNNN"
- `confidence`: "high|very-high|medium|low"

### scripts
- `filename`: "script.py"
- `language`: "python|go|bash"
- `dependencies`: ["SCR-NNNN"]

### sources
- `url`: "https://..."
- `data_types`: ["scientific", "financial"]
- `access_type`: "free|freemium|api-key-required|paid"

### api-tools
- `path`: "relative/path/"
- `entry_point`: "main.py"
- `auth_type`: "api-key|none"

### teams
- `filename`: "TEAM-NNNN.md"
- `objective`: "What the team did"
- `members`: []

### sops
- `filename`: "sop_NNN_name.json"
- `phase_count`: N
- `trigger`: "When to use this SOP"

## Router File

`storage/router.json` replaces `unified_index.json` as the entry point.
It maps domains to their index paths without duplicating entries.

```json
{
  "schema_version": "2.0",
  "description": "Repository index router. Start here to find any index.",
  "domains": {
    "knowledge-base": {
      "index_path": "knowledge-base/index.json",
      "entry_prefix": "KB",
      "entry_count": 46,
      "description": "Shared knowledge entries"
    }
  }
}
```

## Rules

1. All tags MUST exist in `storage/taxonomy.json`
2. All cross-references in `references` MUST use valid IDs
3. `secondary_indexes` MUST be kept in sync with entries
4. `entry_count` MUST match `len(entries)`
5. New domains MUST be registered in `storage/router.json`
