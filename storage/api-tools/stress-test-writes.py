#!/usr/bin/env python3
"""
Stress Test: Bulk API Tool Registration
Generates 30 realistic API tool entries (TOOL-0001 through TOOL-0030)
and writes them into storage/api-tools/index.json.
"""

import json
import time
import os
import random
from datetime import datetime, timedelta

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(TOOLS_DIR, "index.json")
NUM_ENTRIES = 30

CATEGORIES = ["data-retrieval", "analysis", "communication", "integration"]
AUTH_TYPES = ["api-key", "oauth2", "bearer-token", "basic-auth", "none"]

TOOL_TEMPLATES = {
    "data-retrieval": [
        ("yahoo_finance_fetcher", "Fetch stock quotes and historical data from Yahoo Finance API"),
        ("sec_edgar_scraper", "Retrieve SEC EDGAR filings and parse XBRL data"),
        ("fred_economic_data", "Pull economic indicators from Federal Reserve FRED API"),
        ("alpha_vantage_client", "Access Alpha Vantage market data and technical indicators"),
        ("polygon_io_stream", "Stream real-time market data from Polygon.io"),
        ("coinmarketcap_tracker", "Track cryptocurrency prices and market cap from CoinMarketCap"),
        ("openweather_collector", "Collect weather data and forecasts from OpenWeatherMap API"),
        ("census_data_puller", "Pull demographic data from US Census Bureau API"),
    ],
    "analysis": [
        ("sentiment_analyzer", "Analyze text sentiment using NLP models via API"),
        ("risk_calculator", "Calculate portfolio risk metrics using Monte Carlo simulation"),
        ("correlation_engine", "Compute cross-asset correlation matrices"),
        ("anomaly_detector", "Detect anomalies in time series data using statistical methods"),
        ("trend_predictor", "Predict market trends using ensemble ML models"),
        ("volatility_estimator", "Estimate implied and historical volatility"),
        ("earnings_analyzer", "Parse and analyze quarterly earnings reports"),
        ("technical_indicator_calc", "Calculate technical indicators (RSI, MACD, Bollinger)"),
    ],
    "communication": [
        ("slack_notifier", "Send alerts and reports to Slack channels"),
        ("email_dispatcher", "Dispatch formatted email reports via SMTP/SendGrid"),
        ("telegram_bot", "Send trading signals to Telegram bot channels"),
        ("discord_webhook", "Post updates to Discord via webhooks"),
        ("sms_alerter", "Send SMS alerts via Twilio API for critical events"),
        ("teams_connector", "Post messages to Microsoft Teams channels"),
        ("pushover_notifier", "Push mobile notifications via Pushover API"),
    ],
    "integration": [
        ("postgres_connector", "Connect and execute queries against PostgreSQL databases"),
        ("redis_cache_layer", "Cache API responses in Redis with TTL management"),
        ("s3_storage_manager", "Upload/download data files to AWS S3 buckets"),
        ("kafka_producer", "Publish events to Apache Kafka topics"),
        ("elasticsearch_indexer", "Index and search documents in Elasticsearch"),
        ("graphql_gateway", "Route queries through a GraphQL federation gateway"),
        ("webhook_receiver", "Receive and process incoming webhooks"),
    ],
}

TAGS_POOL = [
    "finance", "market-data", "real-time", "historical", "api",
    "nlp", "ml", "statistics", "risk", "portfolio",
    "alerts", "notifications", "messaging", "reporting",
    "database", "caching", "storage", "streaming", "etl", "search",
]


def generate_tool_entry(idx):
    tool_id = f"TOOL-{idx:04d}"
    category = CATEGORIES[(idx - 1) % len(CATEGORIES)]
    auth_type = AUTH_TYPES[(idx - 1) % len(AUTH_TYPES)]

    templates = TOOL_TEMPLATES[category]
    template = templates[(idx - 1) % len(templates)]

    name = template[0]
    description = template[1]

    created = datetime(2025, 3, 1) + timedelta(days=random.randint(0, 365))
    last_used = created + timedelta(days=random.randint(1, 60))
    usage_count = random.randint(1, 5000)

    tags = random.sample(TAGS_POOL, k=random.randint(2, 5))
    rate_limit = random.choice([60, 100, 500, 1000, 5000, None])

    return {
        "id": tool_id,
        "name": name,
        "description": description,
        "category": category,
        "auth_type": auth_type,
        "base_url": "https://api.example.com/v1/data",
        "rate_limit_per_minute": rate_limit,
        "tags": tags,
        "usage_count": usage_count,
        "last_used": last_used.strftime("%Y-%m-%d"),
        "created": created.strftime("%Y-%m-%d"),
        "status": random.choice(["active", "active", "active", "deprecated", "testing"]),
        "version": f"{random.randint(1, 4)}.{random.randint(0, 9)}",
        "author": "stress-test-beta",
    }


def main():
    print(f"=== Sub-Agent Beta: Bulk API Tool Registration ===")
    print(f"Target: {NUM_ENTRIES} entries in {INDEX_PATH}")
    print()

    with open(INDEX_PATH, "r") as f:
        index = json.load(f)

    t_gen_start = time.perf_counter()
    entries = [generate_tool_entry(i) for i in range(1, NUM_ENTRIES + 1)]
    t_gen_end = time.perf_counter()
    print(f"Generated {len(entries)} entries in {t_gen_end - t_gen_start:.4f}s")

    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "DUPLICATE IDs DETECTED!"
    print(f"ID uniqueness check: PASSED ({len(set(ids))} unique IDs)")

    t_write_start = time.perf_counter()

    index["tools"] = entries
    index["last_updated"] = "2026-03-10"

    for cat in CATEGORIES:
        index["categories"][cat] = [e["id"] for e in entries if e["category"] == cat]

    auth_index = {}
    for e in entries:
        at = e["auth_type"]
        if at not in auth_index:
            auth_index[at] = []
        auth_index[at].append(e["id"])
    index["auth_types"] = auth_index

    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)

    t_write_end = time.perf_counter()

    file_size = os.path.getsize(INDEX_PATH)
    print(f"Wrote index.json in {t_write_end - t_write_start:.4f}s")
    print(f"File size: {file_size:,} bytes ({file_size / 1024:.1f} KB)")
    print()

    t_verify_start = time.perf_counter()
    with open(INDEX_PATH, "r") as f:
        verified = json.load(f)
    t_verify_end = time.perf_counter()

    assert len(verified["tools"]) == NUM_ENTRIES
    total_categorized = sum(len(v) for v in verified["categories"].values())
    assert total_categorized == NUM_ENTRIES
    total_auth = sum(len(v) for v in verified["auth_types"].values())
    assert total_auth == NUM_ENTRIES

    usage_counts = [e["usage_count"] for e in verified["tools"]]
    print(f"Verification read in {t_verify_end - t_verify_start:.4f}s")
    print(f"Tools count: {len(verified['tools'])} (expected {NUM_ENTRIES})")
    print(f"Category distribution: { {k: len(v) for k, v in verified['categories'].items()} }")
    print(f"Auth type distribution: { {k: len(v) for k, v in verified['auth_types'].items()} }")
    print(f"Usage count range: {min(usage_counts)} - {max(usage_counts)} (avg: {sum(usage_counts)//len(usage_counts)})")
    print(f"Total time: {t_verify_end - t_gen_start:.4f}s")
    print(f"STATUS: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
