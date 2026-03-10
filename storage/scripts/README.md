# Scripts Storage

This directory stores reusable scripts for data collection, analysis, automation, and utility tasks.

## How to Add a Script

1. Place your script file in this directory (or a subdirectory matching its category).
2. Update `index.json` to register the script with its metadata.
3. Use the naming convention described below.

## Naming Conventions

- Use lowercase with hyphens: `fetch-stock-data.py`, `clean-csv-output.sh`
- Prefix with the category when helpful: `dc-scrape-headlines.py` (data-collection), `util-format-json.py` (utility)
- Include the language extension: `.py`, `.sh`, `.js`, `.ts`, `.r`, etc.

## Metadata Format

Each script entry in `index.json` should include:

| Field         | Type     | Description                                  |
|---------------|----------|----------------------------------------------|
| `id`          | string   | Unique identifier, e.g. `SCR-0001`           |
| `name`        | string   | Human-readable name                          |
| `filename`    | string   | Filename relative to this directory          |
| `language`    | string   | Programming language (`python`, `bash`, etc.)|
| `category`    | string   | One of: `data-collection`, `analysis`, `automation`, `utility` |
| `description` | string   | Brief description of what the script does    |
| `dependencies`| string[] | Required packages or tools                   |
| `added`       | string   | ISO date when the script was added           |
| `added_by_team` | string | Team identifier, e.g. `TEAM-0003`          |

## Categories

- **data-collection** — Scripts that fetch, scrape, or download data from external sources.
- **analysis** — Scripts that process, transform, or analyze datasets.
- **automation** — Scripts that automate repetitive workflows or scheduled tasks.
- **utility** — General-purpose helper scripts (formatting, validation, conversion, etc.).
