# Web Sources / API Directory

This directory maintains a curated index of web APIs and data sources, organized by data type and access method.

## Purpose

Provide a centralized registry so any team can discover available data sources, understand their access requirements, and avoid duplicating research on APIs.

## How to Add a Source

1. Copy `TEMPLATE.json` and fill in all fields for the new source.
2. Save the completed file as `SRC-XXXX.json` (using the next available ID).
3. Update `index.json`:
   - Add the source summary to the `sources` array.
   - Add the source ID to the appropriate `by_data_type` list(s).
   - Add the source ID to the appropriate `by_access_type` list.

## Data Type Categories

| Category       | Description                                      |
|----------------|--------------------------------------------------|
| `financial`    | Stock prices, exchange rates, economic indicators |
| `news`         | News articles, headlines, press releases          |
| `social-media` | Posts, trends, user activity from social platforms |
| `government`   | Census data, regulations, public records          |
| `scientific`   | Research papers, datasets, experiment results     |
| `geospatial`   | Maps, coordinates, geographic datasets            |
| `general`      | Sources that span multiple types or are uncategorized |

## Access Types

| Access Type        | Description                                 |
|--------------------|---------------------------------------------|
| `free`             | No cost, no authentication required         |
| `freemium`         | Free tier available; paid tiers for more     |
| `paid`             | Requires a paid subscription or license      |
| `api-key-required` | Free but requires registering for an API key |

## Sorting and Lookup

Sources can be looked up by:
- **Data type** — via `by_data_type` in `index.json`
- **Access type** — via `by_access_type` in `index.json`
- **ID** — each source has a unique `SRC-XXXX` identifier
