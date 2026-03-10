#!/usr/bin/env python3
"""
Stress Test: Bulk Script Registration
Generates 50 realistic script entries (SCR-0001 through SCR-0050)
and writes them into storage/scripts/index.json.
"""

import json
import time
import os
import random
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(SCRIPT_DIR, "index.json")
NUM_ENTRIES = 50

CATEGORIES = ["data-collection", "analysis", "automation", "utility"]
LANGUAGES = ["python", "bash", "javascript", "ruby", "go"]

SCRIPT_TEMPLATES = {
    "data-collection": [
        ("scrape_{target}_data", "Scrape {target} data from public endpoints"),
        ("fetch_{target}_feed", "Fetch and parse {target} RSS/API feed"),
        ("crawl_{target}_pages", "Crawl {target} website pages for structured data"),
        ("ingest_{target}_stream", "Ingest real-time {target} data stream"),
        ("download_{target}_archive", "Download and extract {target} historical archives"),
    ],
    "analysis": [
        ("analyze_{target}_trends", "Analyze {target} trend patterns over time"),
        ("compute_{target}_metrics", "Compute key {target} performance metrics"),
        ("correlate_{target}_signals", "Cross-correlate {target} signals with benchmarks"),
        ("backtest_{target}_strategy", "Backtest trading strategy on {target} data"),
        ("cluster_{target}_segments", "Cluster {target} data into meaningful segments"),
    ],
    "automation": [
        ("auto_deploy_{target}", "Automate deployment of {target} services"),
        ("schedule_{target}_jobs", "Schedule recurring {target} processing jobs"),
        ("sync_{target}_repos", "Synchronize {target} repositories across environments"),
        ("rotate_{target}_keys", "Rotate API keys and credentials for {target}"),
        ("monitor_{target}_health", "Monitor {target} system health and alert on anomalies"),
    ],
    "utility": [
        ("convert_{target}_format", "Convert {target} data between formats"),
        ("validate_{target}_schema", "Validate {target} data against expected schema"),
        ("cleanup_{target}_cache", "Clean up stale {target} cache and temp files"),
        ("export_{target}_report", "Export {target} summary report to CSV/PDF"),
        ("migrate_{target}_db", "Migrate {target} database schema and data"),
    ],
}

TARGETS = [
    "stock", "crypto", "forex", "bond", "commodity",
    "news", "sentiment", "earnings", "sec_filing", "insider",
    "weather", "census", "healthcare", "energy", "transport",
    "social", "reddit", "twitter", "github", "linkedin",
]

TAGS_POOL = [
    "finance", "data", "automation", "scraping", "api",
    "ml", "analytics", "reporting", "etl", "monitoring",
    "scheduling", "deployment", "security", "migration", "caching",
    "streaming", "batch", "realtime", "archival", "validation",
]


def generate_script_entry(idx):
    script_id = f"SCR-{idx:04d}"
    category = CATEGORIES[(idx - 1) % len(CATEGORIES)]
    language = LANGUAGES[(idx - 1) % len(LANGUAGES)]
    target = TARGETS[(idx - 1) % len(TARGETS)]

    templates = SCRIPT_TEMPLATES[category]
    template = templates[(idx - 1) % len(templates)]

    name = template[0].format(target=target)
    description = template[1].format(target=target)
    ext = {"python": ".py", "bash": ".sh", "javascript": ".js", "ruby": ".rb", "go": ".go"}[language]

    created = datetime(2025, 6, 1) + timedelta(days=random.randint(0, 270))
    updated = created + timedelta(days=random.randint(0, 30))

    tags = random.sample(TAGS_POOL, k=random.randint(2, 5))

    return {
        "id": script_id,
        "name": name,
        "filename": f"{name}{ext}",
        "language": language,
        "category": category,
        "description": description,
        "tags": tags,
        "version": f"{random.randint(1, 3)}.{random.randint(0, 9)}.{random.randint(0, 9)}",
        "created": created.strftime("%Y-%m-%d"),
        "last_updated": updated.strftime("%Y-%m-%d"),
        "dependencies": [],
        "author": "stress-test-alpha",
    }


def main():
    print(f"=== Sub-Agent Alpha: Bulk Script Registration ===")
    print(f"Target: {NUM_ENTRIES} entries in {INDEX_PATH}")
    print()

    with open(INDEX_PATH, "r") as f:
        index = json.load(f)

    t_gen_start = time.perf_counter()
    entries = [generate_script_entry(i) for i in range(1, NUM_ENTRIES + 1)]
    t_gen_end = time.perf_counter()
    print(f"Generated {len(entries)} entries in {t_gen_end - t_gen_start:.4f}s")

    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "DUPLICATE IDs DETECTED!"
    print(f"ID uniqueness check: PASSED ({len(set(ids))} unique IDs)")

    t_write_start = time.perf_counter()

    index["scripts"] = entries
    index["last_updated"] = "2026-03-10"

    for cat in CATEGORIES:
        index["categories"][cat] = [e["id"] for e in entries if e["category"] == cat]

    lang_index = {}
    for e in entries:
        lang = e["language"]
        if lang not in lang_index:
            lang_index[lang] = []
        lang_index[lang].append(e["id"])
    index["language_index"] = lang_index

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

    assert len(verified["scripts"]) == NUM_ENTRIES
    total_categorized = sum(len(v) for v in verified["categories"].values())
    assert total_categorized == NUM_ENTRIES
    total_lang = sum(len(v) for v in verified["language_index"].values())
    assert total_lang == NUM_ENTRIES

    print(f"Verification read in {t_verify_end - t_verify_start:.4f}s")
    print(f"Scripts count: {len(verified['scripts'])} (expected {NUM_ENTRIES})")
    print(f"Category distribution: { {k: len(v) for k, v in verified['categories'].items()} }")
    print(f"Language distribution: { {k: len(v) for k, v in verified['language_index'].items()} }")
    print(f"Total time: {t_verify_end - t_gen_start:.4f}s")
    print(f"STATUS: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
