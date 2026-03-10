#!/usr/bin/env python3
"""
Stress Test: Bulk Source Registration
Generates 40 source entries (SRC-0001 through SRC-0040),
creates individual SRC-XXXX.json files for each,
and updates storage/sources/index.json.
"""

import json
import time
import os
import random
from datetime import datetime, timedelta

SOURCES_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(SOURCES_DIR, "index.json")
NUM_ENTRIES = 40

DATA_TYPES = ["financial", "news", "social-media", "government", "scientific", "geospatial", "general"]
ACCESS_TYPES = ["free", "freemium", "paid", "api-key-required"]

SOURCE_TEMPLATES = {
    "financial": [
        ("Yahoo Finance", "https://finance.yahoo.com", "Stock quotes, historical data, and financial statements"),
        ("Alpha Vantage", "https://www.alphavantage.co", "Real-time and historical stock market data"),
        ("IEX Cloud", "https://iexcloud.io", "Financial data platform with extensive market data"),
        ("Quandl", "https://data.nasdaq.com", "Alternative financial and economic datasets"),
        ("FRED", "https://fred.stlouisfed.org", "Federal Reserve economic data and indicators"),
        ("Bloomberg API", "https://www.bloomberg.com", "Professional financial data terminal API"),
    ],
    "news": [
        ("NewsAPI", "https://newsapi.org", "Aggregate news articles from 80,000+ sources"),
        ("GDELT Project", "https://www.gdeltproject.org", "Global database of events, language, and tone"),
        ("Reuters Connect", "https://www.reutersconnect.com", "Professional news wire and multimedia content"),
        ("AP News API", "https://developer.ap.org", "Associated Press news content and metadata"),
        ("Mediastack", "https://mediastack.com", "Live news and blog articles from worldwide sources"),
    ],
    "social-media": [
        ("Reddit API", "https://www.reddit.com/dev/api", "Access Reddit posts, comments, and subreddit data"),
        ("Twitter/X API", "https://developer.x.com", "Tweets, user data, and trend information"),
        ("Mastodon API", "https://docs.joinmastodon.org", "Federated social network posts and interactions"),
        ("YouTube Data API", "https://developers.google.com/youtube", "Video metadata, comments, and channel stats"),
        ("StockTwits", "https://api.stocktwits.com", "Financial social media sentiment and messages"),
    ],
    "government": [
        ("SEC EDGAR", "https://www.sec.gov/edgar", "SEC filings, 10-K, 10-Q, and insider transactions"),
        ("US Census", "https://api.census.gov", "Demographic, economic, and housing data"),
        ("Data.gov", "https://data.gov", "Open government datasets across federal agencies"),
        ("BLS Stats", "https://www.bls.gov/developers", "Bureau of Labor Statistics employment and price data"),
        ("USDA NASS", "https://quickstats.nass.usda.gov/api", "Agricultural statistics and crop data"),
        ("EPA Data", "https://www.epa.gov/data", "Environmental monitoring and compliance data"),
    ],
    "scientific": [
        ("PubMed", "https://pubmed.ncbi.nlm.nih.gov", "Biomedical literature and life sciences journals"),
        ("arXiv", "https://arxiv.org", "Pre-print papers in physics, CS, math, and more"),
        ("NASA Open Data", "https://data.nasa.gov", "Space exploration, earth science, and aeronautics data"),
        ("NOAA Climate", "https://www.ncdc.noaa.gov", "Climate, weather, and ocean observation data"),
        ("WHO Data", "https://www.who.int/data", "Global health statistics and disease surveillance"),
    ],
    "geospatial": [
        ("OpenStreetMap", "https://www.openstreetmap.org", "Collaborative mapping and geographic data"),
        ("Google Maps API", "https://developers.google.com/maps", "Geocoding, directions, and place data"),
        ("Mapbox", "https://www.mapbox.com", "Custom maps, geocoding, and navigation services"),
        ("US Geological Survey", "https://www.usgs.gov", "Earth science data including earthquakes and terrain"),
        ("Copernicus", "https://www.copernicus.eu", "European earth observation satellite data"),
    ],
    "general": [
        ("Wikipedia API", "https://en.wikipedia.org/w/api.php", "Encyclopedia content and page metadata"),
        ("DBpedia", "https://www.dbpedia.org", "Structured content extracted from Wikipedia"),
        ("Wikidata", "https://www.wikidata.org", "Free collaborative knowledge base"),
        ("Internet Archive", "https://archive.org", "Digital library of websites, books, and media"),
    ],
}

FORMATS = ["json", "csv", "xml", "parquet", "geojson"]
UPDATE_FREQUENCIES = ["real-time", "daily", "weekly", "monthly", "quarterly", "yearly", "on-demand"]


def generate_source_entry(idx):
    source_id = f"SRC-{idx:04d}"
    data_type = DATA_TYPES[(idx - 1) % len(DATA_TYPES)]
    access_type = ACCESS_TYPES[(idx - 1) % len(ACCESS_TYPES)]

    templates = SOURCE_TEMPLATES[data_type]
    template = templates[(idx - 1) % len(templates)]

    cycle = (idx - 1) // len(DATA_TYPES)
    name = template[0] if cycle == 0 else f"{template[0]} v{cycle + 1}"

    created = datetime(2025, 1, 15) + timedelta(days=random.randint(0, 400))
    last_verified = created + timedelta(days=random.randint(0, 60))

    return {
        "id": source_id,
        "name": name,
        "url": template[1],
        "description": template[2],
        "data_type": data_type,
        "access_type": access_type,
        "formats": random.sample(FORMATS, k=random.randint(1, 3)),
        "update_frequency": random.choice(UPDATE_FREQUENCIES),
        "reliability_score": round(random.uniform(0.6, 1.0), 2),
        "created": created.strftime("%Y-%m-%d"),
        "last_verified": last_verified.strftime("%Y-%m-%d"),
        "documentation_url": f"{template[1]}/docs",
        "rate_limit": random.choice(["100/min", "1000/hour", "10000/day", "unlimited", None]),
        "notes": f"Auto-generated stress test entry for {data_type} data.",
        "author": "stress-test-gamma",
    }


def main():
    print(f"=== Sub-Agent Gamma: Bulk Source Registration ===")
    print(f"Target: {NUM_ENTRIES} entries in {SOURCES_DIR}")
    print()

    with open(INDEX_PATH, "r") as f:
        index = json.load(f)

    t_gen_start = time.perf_counter()
    entries = [generate_source_entry(i) for i in range(1, NUM_ENTRIES + 1)]
    t_gen_end = time.perf_counter()
    print(f"Generated {len(entries)} entries in {t_gen_end - t_gen_start:.4f}s")

    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "DUPLICATE IDs DETECTED!"
    print(f"ID uniqueness check: PASSED ({len(set(ids))} unique IDs)")

    t_files_start = time.perf_counter()
    files_written = 0
    for entry in entries:
        filepath = os.path.join(SOURCES_DIR, f"{entry['id']}.json")
        with open(filepath, "w") as f:
            json.dump(entry, f, indent=2)
        files_written += 1
    t_files_end = time.perf_counter()
    print(f"Wrote {files_written} individual JSON files in {t_files_end - t_files_start:.4f}s")

    t_index_start = time.perf_counter()

    index["sources"] = [{"id": e["id"], "name": e["name"], "data_type": e["data_type"], "access_type": e["access_type"]} for e in entries]
    index["last_updated"] = "2026-03-10"

    for dt in DATA_TYPES:
        index["by_data_type"][dt] = [e["id"] for e in entries if e["data_type"] == dt]

    for at in ACCESS_TYPES:
        index["by_access_type"][at] = [e["id"] for e in entries if e["access_type"] == at]

    with open(INDEX_PATH, "w") as f:
        json.dump(index, f, indent=2)

    t_index_end = time.perf_counter()

    index_size = os.path.getsize(INDEX_PATH)
    total_file_size = index_size
    for entry in entries:
        fpath = os.path.join(SOURCES_DIR, f"{entry['id']}.json")
        total_file_size += os.path.getsize(fpath)

    print(f"Wrote index.json in {t_index_end - t_index_start:.4f}s")
    print(f"Index file size: {index_size:,} bytes ({index_size / 1024:.1f} KB)")
    print(f"Total file size (index + {files_written} source files): {total_file_size:,} bytes ({total_file_size / 1024:.1f} KB)")
    print()

    t_verify_start = time.perf_counter()
    with open(INDEX_PATH, "r") as f:
        verified = json.load(f)

    verified_files = 0
    for entry in entries:
        fpath = os.path.join(SOURCES_DIR, f"{entry['id']}.json")
        with open(fpath, "r") as f:
            data = json.load(f)
            assert data["id"] == entry["id"], f"ID mismatch in {fpath}"
        verified_files += 1

    t_verify_end = time.perf_counter()

    assert len(verified["sources"]) == NUM_ENTRIES
    total_by_data = sum(len(v) for v in verified["by_data_type"].values())
    assert total_by_data == NUM_ENTRIES
    total_by_access = sum(len(v) for v in verified["by_access_type"].values())
    assert total_by_access == NUM_ENTRIES

    print(f"Verification (index + {verified_files} files) in {t_verify_end - t_verify_start:.4f}s")
    print(f"Sources count: {len(verified['sources'])} (expected {NUM_ENTRIES})")
    print(f"Data type distribution: { {k: len(v) for k, v in verified['by_data_type'].items()} }")
    print(f"Access type distribution: { {k: len(v) for k, v in verified['by_access_type'].items()} }")
    print(f"Individual files verified: {verified_files}")
    print(f"Total time: {t_verify_end - t_gen_start:.4f}s")
    print(f"STATUS: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
