# API Tools Storage

This directory stores registered API tools used for data retrieval, analysis, communication, and integration tasks.

## How to Register a Tool

1. Add the tool configuration file to this directory.
2. Update `index.json` to register the tool with its metadata.
3. Record the authentication type so other teams know how to use it.

## Metadata Format

Each tool entry in `index.json` should include:

| Field           | Type     | Description                                         |
|-----------------|----------|-----------------------------------------------------|
| `id`            | string   | Unique identifier, e.g. `TOOL-0001`                 |
| `name`          | string   | Human-readable tool name                             |
| `description`   | string   | What the tool does                                   |
| `category`      | string   | One of: `data-retrieval`, `analysis`, `communication`, `integration` |
| `endpoint`      | string   | Base URL or endpoint pattern                         |
| `auth_type`     | string   | Authentication method (`none`, `api-key`, `oauth`, `bearer-token`) |
| `rate_limit`    | string   | Rate limit details (e.g. `100 req/min`)              |
| `response_format` | string | Expected response format (`json`, `xml`, etc.)      |
| `last_used`     | string   | ISO date of last usage                               |
| `usage_count`   | number   | Total number of times the tool has been invoked      |
| `added`         | string   | ISO date when the tool was registered                |
| `added_by_team` | string   | Team identifier, e.g. `TEAM-0003`                   |

## Usage Tracking

Every time a tool is invoked, update its entry in `index.json`:
- Increment `usage_count` by 1.
- Set `last_used` to the current date.

This allows teams to identify which tools are actively used and which may need maintenance or removal.

## Categories

- **data-retrieval** — Tools that fetch data from external APIs or databases.
- **analysis** — Tools that perform computation, scoring, or data transformation.
- **communication** — Tools that send notifications, emails, or messages.
- **integration** — Tools that connect to third-party platforms or services.
