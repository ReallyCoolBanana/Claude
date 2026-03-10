#!/usr/bin/env python3
"""
Stress Test Setup: Populate all three index.json files with realistic test data.
Creates 50 scripts, 30 API tools, and 40 sources for read/retrieval benchmarking.
"""

import json
import random
import os
from datetime import datetime, timedelta

STORAGE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_ROOT = os.path.dirname(STORAGE_DIR)

random.seed(42)  # Reproducible data

def random_date(start_year=2024, end_year=2026):
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 3, 10)
    delta = end - start
    rand_days = random.randint(0, delta.days)
    return (start + timedelta(days=rand_days)).strftime("%Y-%m-%d")

def generate_scripts(n=50):
    categories = ["data-collection", "analysis", "automation", "utility"]
    languages = ["python", "bash", "javascript", "r", "go"]
    teams = ["team-alpha", "team-beta", "team-gamma", "team-delta", "team-epsilon"]

    script_templates = {
        "data-collection": [
            ("scrape_{source}_data", "Scrapes data from {source} and stores in local DB"),
            ("fetch_{source}_api", "Fetches data from {source} API endpoint"),
            ("ingest_{source}_feed", "Ingests real-time feed from {source}"),
            ("crawl_{source}_pages", "Crawls and indexes pages from {source}"),
            ("poll_{source}_updates", "Polls {source} for incremental updates"),
        ],
        "analysis": [
            ("analyze_{metric}_trends", "Analyzes {metric} trends over configurable time windows"),
            ("compute_{metric}_scores", "Computes composite {metric} scores from raw data"),
            ("correlate_{metric}_signals", "Runs correlation analysis on {metric} signals"),
            ("backtest_{metric}_model", "Backtests predictive model for {metric}"),
            ("cluster_{metric}_patterns", "Clusters {metric} patterns using k-means"),
        ],
        "automation": [
            ("auto_{action}_pipeline", "Automated pipeline for {action} workflows"),
            ("schedule_{action}_jobs", "Schedules recurring {action} jobs via cron"),
            ("monitor_{action}_status", "Monitors and alerts on {action} status changes"),
            ("deploy_{action}_config", "Deploys configuration for {action} services"),
            ("sync_{action}_state", "Synchronizes {action} state across environments"),
        ],
        "utility": [
            ("convert_{format}_files", "Converts files between {format} formats"),
            ("validate_{format}_schema", "Validates data against {format} schema"),
            ("clean_{format}_data", "Cleans and normalizes {format} data"),
            ("export_{format}_report", "Exports data as {format} report"),
            ("merge_{format}_datasets", "Merges multiple {format} datasets"),
        ],
    }

    sources = ["yahoo", "reddit", "sec", "bloomberg", "twitter", "github", "arxiv", "census", "weather", "crypto"]
    metrics = ["sentiment", "volatility", "momentum", "liquidity", "correlation", "volume", "price", "risk", "alpha", "beta"]
    actions = ["etl", "backup", "deploy", "notification", "reporting", "indexing", "cleanup", "migration", "scaling", "logging"]
    formats = ["csv", "json", "parquet", "xml", "avro", "protobuf", "yaml", "toml", "hdf5", "feather"]

    fillers = {
        "data-collection": sources,
        "analysis": metrics,
        "automation": actions,
        "utility": formats,
    }
    filler_keys = {
        "data-collection": "source",
        "analysis": "metric",
        "automation": "action",
        "utility": "format",
    }

    scripts = []
    cat_index = {c: [] for c in categories}
    lang_index = {l: [] for l in languages}

    for i in range(n):
        cat = categories[i % len(categories)]
        lang = languages[i % len(languages)]
        template = script_templates[cat][i % len(script_templates[cat])]
        filler_val = fillers[cat][i % len(fillers[cat])]
        filler_key = filler_keys[cat]

        name = template[0].replace(f"{{{filler_key}}}", filler_val)
        desc = template[1].replace(f"{{{filler_key}}}", filler_val)
        ext = {"python": ".py", "bash": ".sh", "javascript": ".js", "r": ".R", "go": ".go"}[lang]

        script_id = f"script-{i+1:03d}"
        entry = {
            "id": script_id,
            "name": name,
            "filename": f"{name}{ext}",
            "description": desc,
            "language": lang,
            "category": cat,
            "created": random_date(),
            "created_by": random.choice(teams),
            "tags": random.sample(["fast", "stable", "experimental", "production", "legacy", "v2", "optimized", "parallel", "gpu", "lightweight"], k=random.randint(2, 5)),
            "dependencies": random.sample(["pandas", "numpy", "requests", "beautifulsoup4", "sklearn", "tensorflow", "flask", "redis", "celery", "boto3", "jq", "curl", "node-fetch", "d3", "ggplot2", "dplyr", "goroutine"], k=random.randint(1, 4)),
            "version": f"{random.randint(1,3)}.{random.randint(0,9)}.{random.randint(0,9)}",
        }
        scripts.append(entry)
        cat_index[cat].append(script_id)
        lang_index[lang].append(script_id)

    return {
        "version": "1.0",
        "last_updated": "2026-03-10",
        "scripts": scripts,
        "categories": cat_index,
        "language_index": lang_index,
    }


def generate_api_tools(n=30):
    categories = ["data-retrieval", "analysis", "communication", "integration"]
    auth_types = ["api-key", "oauth2", "basic", "none", "bearer-token"]
    teams = ["team-alpha", "team-beta", "team-gamma", "team-delta"]

    tool_templates = {
        "data-retrieval": [
            ("get_{entity}_list", "Retrieves paginated list of {entity} from remote API"),
            ("search_{entity}_records", "Searches {entity} records with filter parameters"),
            ("stream_{entity}_updates", "Streams real-time {entity} updates via WebSocket"),
            ("download_{entity}_bulk", "Downloads bulk {entity} export as compressed archive"),
        ],
        "analysis": [
            ("run_{analysis}_computation", "Runs {analysis} computation on provided dataset"),
            ("score_{analysis}_model", "Scores records using {analysis} ML model"),
            ("rank_{analysis}_results", "Ranks {analysis} results by configurable criteria"),
            ("predict_{analysis}_outcome", "Predicts {analysis} outcome using ensemble model"),
        ],
        "communication": [
            ("send_{channel}_notification", "Sends notification via {channel}"),
            ("broadcast_{channel}_alert", "Broadcasts alert to all {channel} subscribers"),
            ("poll_{channel}_messages", "Polls for new messages in {channel}"),
            ("format_{channel}_report", "Formats and delivers report through {channel}"),
        ],
        "integration": [
            ("sync_{platform}_data", "Syncs data bidirectionally with {platform}"),
            ("import_{platform}_records", "Imports records from {platform} into local store"),
            ("export_{platform}_batch", "Exports batch of records to {platform}"),
            ("webhook_{platform}_handler", "Handles incoming webhooks from {platform}"),
        ],
    }

    entities = ["stock", "trade", "portfolio", "order", "user", "account", "position", "dividend"]
    analyses = ["sentiment", "risk", "regression", "classification", "clustering", "anomaly", "forecast", "optimization"]
    channels = ["slack", "email", "sms", "webhook", "teams", "discord", "telegram", "pagerduty"]
    platforms = ["salesforce", "snowflake", "bigquery", "postgres", "mongodb", "elasticsearch", "kafka", "airflow"]

    fillers = {
        "data-retrieval": ("entity", entities),
        "analysis": ("analysis", analyses),
        "communication": ("channel", channels),
        "integration": ("platform", platforms),
    }

    tools = []
    cat_index = {c: [] for c in categories}
    auth_index = {a: [] for a in auth_types}

    for i in range(n):
        cat = categories[i % len(categories)]
        auth = auth_types[i % len(auth_types)]
        template = tool_templates[cat][i % len(tool_templates[cat])]
        filler_key, filler_list = fillers[cat]
        filler_val = filler_list[i % len(filler_list)]

        name = template[0].replace(f"{{{filler_key}}}", filler_val)
        desc = template[1].replace(f"{{{filler_key}}}", filler_val)

        tool_id = f"tool-{i+1:03d}"
        entry = {
            "id": tool_id,
            "name": name,
            "description": desc,
            "category": cat,
            "auth_type": auth,
            "base_url": f"https://api.example.com/v{random.randint(1,3)}/{name.replace('_', '-')}",
            "rate_limit": f"{random.choice([100, 500, 1000, 5000, 10000])}/hour",
            "created": random_date(),
            "created_by": random.choice(teams),
            "status": random.choice(["active", "active", "active", "deprecated", "beta"]),
            "response_format": random.choice(["json", "json", "json", "xml", "csv", "protobuf"]),
            "tags": random.sample(["fast", "reliable", "cached", "paginated", "streaming", "batch", "real-time", "async", "rate-limited", "idempotent"], k=random.randint(2, 4)),
        }
        tools.append(entry)
        cat_index[cat].append(tool_id)
        if auth not in auth_index:
            auth_index[auth] = []
        auth_index[auth].append(tool_id)

    return {
        "version": "1.0",
        "last_updated": "2026-03-10",
        "tools": tools,
        "categories": cat_index,
        "auth_types": auth_index,
    }


def generate_sources(n=40):
    data_types = ["financial", "news", "social-media", "government", "scientific", "geospatial", "general"]
    access_types = ["free", "freemium", "paid", "api-key-required"]
    teams = ["team-alpha", "team-beta", "team-gamma", "team-delta", "team-epsilon"]

    source_templates = {
        "financial": [
            ("yahoo_finance_{variant}", "Yahoo Finance {variant} data feed"),
            ("sec_edgar_{variant}", "SEC EDGAR {variant} filings database"),
            ("fed_reserve_{variant}", "Federal Reserve {variant} data series"),
            ("world_bank_{variant}", "World Bank {variant} indicators"),
            ("crypto_{variant}_exchange", "Cryptocurrency {variant} exchange data"),
            ("nasdaq_{variant}_feed", "NASDAQ {variant} market data feed"),
        ],
        "news": [
            ("reuters_{variant}_wire", "Reuters {variant} news wire service"),
            ("ap_{variant}_feed", "Associated Press {variant} news feed"),
            ("bbc_{variant}_api", "BBC {variant} news API"),
            ("google_{variant}_news", "Google {variant} news aggregation"),
            ("hackernews_{variant}_feed", "Hacker News {variant} feed"),
            ("nyt_{variant}_archive", "New York Times {variant} archive"),
        ],
        "social-media": [
            ("twitter_{variant}_stream", "Twitter/X {variant} streaming API"),
            ("reddit_{variant}_api", "Reddit {variant} API endpoint"),
            ("stocktwits_{variant}", "StockTwits {variant} sentiment data"),
            ("discord_{variant}_bot", "Discord {variant} bot data collector"),
            ("linkedin_{variant}_api", "LinkedIn {variant} professional data"),
            ("mastodon_{variant}_feed", "Mastodon {variant} federated feed"),
        ],
        "government": [
            ("census_{variant}_data", "US Census Bureau {variant} data"),
            ("bls_{variant}_stats", "Bureau of Labor Statistics {variant} data"),
            ("usda_{variant}_reports", "USDA {variant} agricultural reports"),
            ("epa_{variant}_data", "EPA {variant} environmental data"),
            ("noaa_{variant}_weather", "NOAA {variant} weather data"),
            ("fda_{variant}_data", "FDA {variant} regulatory data"),
        ],
        "scientific": [
            ("arxiv_{variant}_papers", "arXiv {variant} research papers"),
            ("pubmed_{variant}_db", "PubMed {variant} medical database"),
            ("nasa_{variant}_data", "NASA {variant} space data"),
            ("cern_{variant}_dataset", "CERN {variant} particle physics data"),
            ("genome_{variant}_db", "Genome {variant} database"),
            ("pangaea_{variant}_data", "PANGAEA {variant} earth science data"),
        ],
        "geospatial": [
            ("osm_{variant}_tiles", "OpenStreetMap {variant} map tiles"),
            ("sentinel_{variant}_imagery", "Sentinel {variant} satellite imagery"),
            ("usgs_{variant}_maps", "USGS {variant} geological maps"),
            ("mapbox_{variant}_api", "Mapbox {variant} geospatial API"),
            ("geonames_{variant}_db", "GeoNames {variant} place database"),
            ("naturalearth_{variant}", "Natural Earth {variant} vector data"),
        ],
        "general": [
            ("wikipedia_{variant}_dump", "Wikipedia {variant} data dump"),
            ("commoncrawl_{variant}", "Common Crawl {variant} web archive"),
            ("dbpedia_{variant}_kg", "DBpedia {variant} knowledge graph"),
            ("wikidata_{variant}_api", "Wikidata {variant} structured data API"),
            ("archive_org_{variant}", "Internet Archive {variant} collection"),
            ("kaggle_{variant}_dataset", "Kaggle {variant} dataset collection"),
        ],
    }

    variants = ["historical", "realtime", "daily", "weekly", "quarterly", "annual", "streaming", "batch", "curated", "raw"]

    sources = []
    type_index = {t: [] for t in data_types}
    access_index = {a: [] for a in access_types}

    for i in range(n):
        dtype = data_types[i % len(data_types)]
        access = access_types[i % len(access_types)]
        template_list = source_templates[dtype]
        template = template_list[i % len(template_list)]
        variant = variants[i % len(variants)]

        name = template[0].replace("{variant}", variant)
        desc = template[1].replace("{variant}", variant)

        source_id = f"source-{i+1:03d}"
        entry = {
            "id": source_id,
            "name": name,
            "description": desc,
            "data_type": dtype,
            "access_type": access,
            "url": f"https://{name.replace('_', '-')}.example.com/api",
            "format": random.choice(["json", "csv", "xml", "parquet", "geojson", "protobuf"]),
            "update_frequency": random.choice(["real-time", "hourly", "daily", "weekly", "monthly", "quarterly"]),
            "created": random_date(),
            "created_by": random.choice(teams),
            "reliability": random.choice(["high", "high", "medium", "medium", "low"]),
            "size_estimate": random.choice(["<1GB", "1-10GB", "10-100GB", "100GB-1TB", ">1TB"]),
            "tags": random.sample(["curated", "raw", "normalized", "streaming", "historical", "comprehensive", "sampled", "verified", "experimental", "production"], k=random.randint(2, 4)),
        }
        sources.append(entry)
        type_index[dtype].append(source_id)
        access_index[access].append(source_id)

    return {
        "version": "1.0",
        "last_updated": "2026-03-10",
        "sources": sources,
        "by_data_type": type_index,
        "by_access_type": access_index,
    }


def main():
    # Generate data
    scripts_data = generate_scripts(50)
    tools_data = generate_api_tools(30)
    sources_data = generate_sources(40)

    # Write index files
    scripts_path = os.path.join(STORAGE_ROOT, "scripts", "index.json")
    tools_path = os.path.join(STORAGE_ROOT, "api-tools", "index.json")
    sources_path = os.path.join(STORAGE_ROOT, "sources", "index.json")

    with open(scripts_path, "w") as f:
        json.dump(scripts_data, f, indent=2)
    print(f"Wrote {len(scripts_data['scripts'])} scripts to {scripts_path}")
    print(f"  Categories: {', '.join(f'{k}({len(v)})' for k, v in scripts_data['categories'].items())}")
    print(f"  Languages: {', '.join(f'{k}({len(v)})' for k, v in scripts_data['language_index'].items())}")

    with open(tools_path, "w") as f:
        json.dump(tools_data, f, indent=2)
    print(f"\nWrote {len(tools_data['tools'])} tools to {tools_path}")
    print(f"  Categories: {', '.join(f'{k}({len(v)})' for k, v in tools_data['categories'].items())}")
    print(f"  Auth types: {', '.join(f'{k}({len(v)})' for k, v in tools_data['auth_types'].items())}")

    with open(sources_path, "w") as f:
        json.dump(sources_data, f, indent=2)
    print(f"\nWrote {len(sources_data['sources'])} sources to {sources_path}")
    print(f"  Data types: {', '.join(f'{k}({len(v)})' for k, v in sources_data['by_data_type'].items())}")
    print(f"  Access types: {', '.join(f'{k}({len(v)})' for k, v in sources_data['by_access_type'].items())}")

    print("\nSetup complete. All index.json files populated.")


if __name__ == "__main__":
    main()
