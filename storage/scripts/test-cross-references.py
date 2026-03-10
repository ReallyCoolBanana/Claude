#!/usr/bin/env python3
"""
test-cross-references.py (SCR-0021)

Phase 1: Cross-Reference Analysis
Populates all three index.json files with 20 entries each, then tests
cross-referencing capabilities and reports on gaps.

Added by: TEAM-0004 (Team Lead 4)
"""

import json
import os
import time
from datetime import datetime

STORAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_INDEX = os.path.join(STORAGE_DIR, "scripts", "index.json")
TOOLS_INDEX = os.path.join(STORAGE_DIR, "api-tools", "index.json")
SOURCES_INDEX = os.path.join(STORAGE_DIR, "sources", "index.json")

# ──────────────────────────────────────────────
# TEST DATA: 20 scripts
# ──────────────────────────────────────────────
SCRIPTS_DATA = [
    {"id": "SCR-0001", "name": "Fetch Stock Prices", "filename": "fetch-stock-prices.py", "language": "python", "category": "data-collection", "description": "Fetches daily stock prices from Alpha Vantage API and stores as CSV", "dependencies": ["requests", "pandas"], "added": "2026-01-15", "added_by_team": "TEAM-0001"},
    {"id": "SCR-0002", "name": "Scrape SEC Filings", "filename": "scrape-sec-filings.py", "language": "python", "category": "data-collection", "description": "Scrapes SEC EDGAR for 10-K and 10-Q filings using the EDGAR API", "dependencies": ["requests", "beautifulsoup4"], "added": "2026-01-15", "added_by_team": "TEAM-0001"},
    {"id": "SCR-0003", "name": "Sentiment Analyzer", "filename": "sentiment-analyzer.py", "language": "python", "category": "analysis", "description": "Analyzes sentiment of news headlines from NewsAPI using VADER", "dependencies": ["nltk", "requests"], "added": "2026-01-20", "added_by_team": "TEAM-0001"},
    {"id": "SCR-0004", "name": "Portfolio Optimizer", "filename": "portfolio-optimizer.py", "language": "python", "category": "analysis", "description": "Runs mean-variance optimization on portfolio data from Yahoo Finance", "dependencies": ["numpy", "scipy", "pandas"], "added": "2026-01-22", "added_by_team": "TEAM-0001"},
    {"id": "SCR-0005", "name": "Earnings Calendar Fetcher", "filename": "fetch-earnings-calendar.py", "language": "python", "category": "data-collection", "description": "Fetches upcoming earnings dates from Finnhub API", "dependencies": ["requests"], "added": "2026-02-01", "added_by_team": "TEAM-0002"},
    {"id": "SCR-0006", "name": "Reddit Sentiment Scraper", "filename": "scrape-reddit-sentiment.py", "language": "python", "category": "data-collection", "description": "Scrapes Reddit posts from r/wallstreetbets using Reddit API for sentiment analysis", "dependencies": ["praw", "pandas"], "added": "2026-02-03", "added_by_team": "TEAM-0002"},
    {"id": "SCR-0007", "name": "CSV to JSON Converter", "filename": "util-csv-to-json.py", "language": "python", "category": "utility", "description": "Converts CSV files to JSON format with configurable options", "dependencies": ["pandas"], "added": "2026-02-05", "added_by_team": "TEAM-0002"},
    {"id": "SCR-0008", "name": "Weather Data Collector", "filename": "fetch-weather-data.py", "language": "python", "category": "data-collection", "description": "Collects weather data from OpenWeatherMap API for geographic correlation analysis", "dependencies": ["requests"], "added": "2026-02-08", "added_by_team": "TEAM-0002"},
    {"id": "SCR-0009", "name": "Automated Report Generator", "filename": "auto-report-gen.py", "language": "python", "category": "automation", "description": "Generates daily market summary reports using data from multiple sources", "dependencies": ["jinja2", "pandas", "requests"], "added": "2026-02-10", "added_by_team": "TEAM-0002"},
    {"id": "SCR-0010", "name": "Forex Rate Tracker", "filename": "fetch-forex-rates.py", "language": "python", "category": "data-collection", "description": "Tracks real-time forex rates from ExchangeRate-API", "dependencies": ["requests", "pandas"], "added": "2026-02-12", "added_by_team": "TEAM-0003"},
    {"id": "SCR-0011", "name": "Twitter Trend Analyzer", "filename": "analyze-twitter-trends.py", "language": "python", "category": "analysis", "description": "Analyzes trending topics on Twitter/X using the X API v2", "dependencies": ["tweepy", "pandas"], "added": "2026-02-14", "added_by_team": "TEAM-0003"},
    {"id": "SCR-0012", "name": "Backtest Engine", "filename": "backtest-engine.py", "language": "python", "category": "analysis", "description": "Backtests trading strategies against historical data from Yahoo Finance", "dependencies": ["pandas", "numpy", "matplotlib"], "added": "2026-02-16", "added_by_team": "TEAM-0003"},
    {"id": "SCR-0013", "name": "Slack Notifier", "filename": "notify-slack.py", "language": "python", "category": "automation", "description": "Sends formatted notifications to Slack channels via Slack API webhook", "dependencies": ["requests"], "added": "2026-02-18", "added_by_team": "TEAM-0003"},
    {"id": "SCR-0014", "name": "Data Validator", "filename": "util-validate-data.py", "language": "python", "category": "utility", "description": "Validates data files against predefined schemas", "dependencies": ["jsonschema", "pandas"], "added": "2026-02-20", "added_by_team": "TEAM-0003"},
    {"id": "SCR-0015", "name": "GDP Data Fetcher", "filename": "fetch-gdp-data.py", "language": "python", "category": "data-collection", "description": "Fetches GDP and macroeconomic data from World Bank Open Data API", "dependencies": ["requests", "pandas"], "added": "2026-02-22", "added_by_team": "TEAM-0003"},
    {"id": "SCR-0016", "name": "Crypto Price Tracker", "filename": "fetch-crypto-prices.py", "language": "python", "category": "data-collection", "description": "Fetches cryptocurrency prices from CoinGecko API", "dependencies": ["requests", "pandas"], "added": "2026-02-25", "added_by_team": "TEAM-0004"},
    {"id": "SCR-0017", "name": "Email Alert Sender", "filename": "send-email-alerts.py", "language": "python", "category": "automation", "description": "Sends email alerts via SendGrid API when price thresholds are breached", "dependencies": ["sendgrid", "pandas"], "added": "2026-02-27", "added_by_team": "TEAM-0004"},
    {"id": "SCR-0018", "name": "Geospatial Data Merger", "filename": "merge-geo-data.py", "language": "python", "category": "analysis", "description": "Merges geospatial datasets from Census Bureau API with economic indicators", "dependencies": ["geopandas", "pandas", "requests"], "added": "2026-03-01", "added_by_team": "TEAM-0004"},
    {"id": "SCR-0019", "name": "Schedule Runner", "filename": "schedule-runner.sh", "language": "bash", "category": "automation", "description": "Cron-compatible runner that executes data collection scripts on a schedule", "dependencies": ["cron", "bash"], "added": "2026-03-05", "added_by_team": "TEAM-0004"},
    {"id": "SCR-0020", "name": "JSON Schema Linter", "filename": "util-lint-json.py", "language": "python", "category": "utility", "description": "Lints and validates JSON files in the storage system", "dependencies": ["jsonschema"], "added": "2026-03-08", "added_by_team": "TEAM-0004"},
]

# ──────────────────────────────────────────────
# TEST DATA: 20 API tools
# ──────────────────────────────────────────────
TOOLS_DATA = [
    {"id": "TOOL-0001", "name": "Alpha Vantage Stock API", "description": "Retrieves stock prices, technical indicators, and fundamental data", "category": "data-retrieval", "endpoint": "https://www.alphavantage.co/query", "auth_type": "api-key", "rate_limit": "5 req/min (free tier)", "response_format": "json", "last_used": "2026-03-09", "usage_count": 142, "added": "2026-01-15", "added_by_team": "TEAM-0001"},
    {"id": "TOOL-0002", "name": "SEC EDGAR API", "description": "Access SEC filings, company data, and XBRL financial statements", "category": "data-retrieval", "endpoint": "https://efts.sec.gov/LATEST", "auth_type": "none", "rate_limit": "10 req/sec", "response_format": "json", "last_used": "2026-03-08", "usage_count": 87, "added": "2026-01-15", "added_by_team": "TEAM-0001"},
    {"id": "TOOL-0003", "name": "NewsAPI", "description": "Fetches news articles and headlines from 80,000+ sources worldwide", "category": "data-retrieval", "endpoint": "https://newsapi.org/v2", "auth_type": "api-key", "rate_limit": "100 req/day (free)", "response_format": "json", "last_used": "2026-03-09", "usage_count": 203, "added": "2026-01-18", "added_by_team": "TEAM-0001"},
    {"id": "TOOL-0004", "name": "Yahoo Finance Unofficial", "description": "Unofficial API for Yahoo Finance stock data and historical prices", "category": "data-retrieval", "endpoint": "https://query1.finance.yahoo.com/v8", "auth_type": "none", "rate_limit": "2000 req/hour", "response_format": "json", "last_used": "2026-03-09", "usage_count": 315, "added": "2026-01-20", "added_by_team": "TEAM-0001"},
    {"id": "TOOL-0005", "name": "Finnhub API", "description": "Real-time stock prices, earnings calendar, and company profiles", "category": "data-retrieval", "endpoint": "https://finnhub.io/api/v1", "auth_type": "api-key", "rate_limit": "60 req/min", "response_format": "json", "last_used": "2026-03-07", "usage_count": 96, "added": "2026-02-01", "added_by_team": "TEAM-0002"},
    {"id": "TOOL-0006", "name": "Reddit API (via PRAW)", "description": "Fetches Reddit posts, comments, and subreddit data", "category": "data-retrieval", "endpoint": "https://oauth.reddit.com/api/v1", "auth_type": "oauth", "rate_limit": "60 req/min", "response_format": "json", "last_used": "2026-03-06", "usage_count": 54, "added": "2026-02-03", "added_by_team": "TEAM-0002"},
    {"id": "TOOL-0007", "name": "OpenWeatherMap API", "description": "Current weather, forecasts, and historical weather data", "category": "data-retrieval", "endpoint": "https://api.openweathermap.org/data/2.5", "auth_type": "api-key", "rate_limit": "60 req/min", "response_format": "json", "last_used": "2026-03-05", "usage_count": 38, "added": "2026-02-08", "added_by_team": "TEAM-0002"},
    {"id": "TOOL-0008", "name": "ExchangeRate-API", "description": "Real-time and historical foreign exchange rates for 161 currencies", "category": "data-retrieval", "endpoint": "https://v6.exchangerate-api.com/v6", "auth_type": "api-key", "rate_limit": "1500 req/month (free)", "response_format": "json", "last_used": "2026-03-09", "usage_count": 71, "added": "2026-02-12", "added_by_team": "TEAM-0003"},
    {"id": "TOOL-0009", "name": "X/Twitter API v2", "description": "Access tweets, user data, and trends from X (formerly Twitter)", "category": "data-retrieval", "endpoint": "https://api.twitter.com/2", "auth_type": "bearer-token", "rate_limit": "300 req/15min", "response_format": "json", "last_used": "2026-03-08", "usage_count": 112, "added": "2026-02-14", "added_by_team": "TEAM-0003"},
    {"id": "TOOL-0010", "name": "Slack Webhook API", "description": "Sends messages and notifications to Slack channels", "category": "communication", "endpoint": "https://hooks.slack.com/services", "auth_type": "bearer-token", "rate_limit": "1 req/sec", "response_format": "json", "last_used": "2026-03-09", "usage_count": 267, "added": "2026-02-18", "added_by_team": "TEAM-0003"},
    {"id": "TOOL-0011", "name": "World Bank Open Data API", "description": "GDP, population, and macroeconomic indicators for all countries", "category": "data-retrieval", "endpoint": "https://api.worldbank.org/v2", "auth_type": "none", "rate_limit": "unlimited", "response_format": "json", "last_used": "2026-03-04", "usage_count": 29, "added": "2026-02-22", "added_by_team": "TEAM-0003"},
    {"id": "TOOL-0012", "name": "CoinGecko API", "description": "Cryptocurrency prices, market data, and exchange information", "category": "data-retrieval", "endpoint": "https://api.coingecko.com/api/v3", "auth_type": "none", "rate_limit": "10-30 req/min", "response_format": "json", "last_used": "2026-03-09", "usage_count": 83, "added": "2026-02-25", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0013", "name": "SendGrid Email API", "description": "Transactional and marketing email delivery service", "category": "communication", "endpoint": "https://api.sendgrid.com/v3/mail/send", "auth_type": "api-key", "rate_limit": "100 emails/day (free)", "response_format": "json", "last_used": "2026-03-07", "usage_count": 41, "added": "2026-02-27", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0014", "name": "US Census Bureau API", "description": "Demographic, economic, and geographic data from US Census", "category": "data-retrieval", "endpoint": "https://api.census.gov/data", "auth_type": "api-key", "rate_limit": "500 req/day", "response_format": "json", "last_used": "2026-03-03", "usage_count": 22, "added": "2026-03-01", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0015", "name": "FRED Economic Data API", "description": "Federal Reserve economic data — interest rates, inflation, employment", "category": "data-retrieval", "endpoint": "https://api.stlouisfed.org/fred", "auth_type": "api-key", "rate_limit": "120 req/min", "response_format": "json", "last_used": "2026-03-08", "usage_count": 56, "added": "2026-03-02", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0016", "name": "Google Trends Unofficial", "description": "Trending search queries and interest over time data", "category": "analysis", "endpoint": "https://trends.google.com/trends/api", "auth_type": "none", "rate_limit": "variable", "response_format": "json", "last_used": "2026-03-06", "usage_count": 33, "added": "2026-03-03", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0017", "name": "Polygon.io API", "description": "Real-time and historical stock, options, and crypto market data", "category": "data-retrieval", "endpoint": "https://api.polygon.io/v2", "auth_type": "api-key", "rate_limit": "5 req/min (free)", "response_format": "json", "last_used": "2026-03-09", "usage_count": 67, "added": "2026-03-04", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0018", "name": "OpenAI GPT API", "description": "Natural language processing, text generation, and embeddings", "category": "analysis", "endpoint": "https://api.openai.com/v1", "auth_type": "bearer-token", "rate_limit": "varies by model", "response_format": "json", "last_used": "2026-03-09", "usage_count": 189, "added": "2026-03-05", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0019", "name": "Twilio SMS API", "description": "Send and receive SMS and MMS messages programmatically", "category": "communication", "endpoint": "https://api.twilio.com/2010-04-01", "auth_type": "api-key", "rate_limit": "varies by plan", "response_format": "json", "last_used": "2026-03-04", "usage_count": 15, "added": "2026-03-06", "added_by_team": "TEAM-0004"},
    {"id": "TOOL-0020", "name": "GitHub REST API", "description": "Manage repos, issues, PRs, and workflows on GitHub", "category": "integration", "endpoint": "https://api.github.com", "auth_type": "bearer-token", "rate_limit": "5000 req/hour", "response_format": "json", "last_used": "2026-03-09", "usage_count": 124, "added": "2026-03-07", "added_by_team": "TEAM-0004"},
]

# ──────────────────────────────────────────────
# TEST DATA: 20 sources
# ──────────────────────────────────────────────
SOURCES_DATA = [
    {"id": "SRC-0001", "name": "Alpha Vantage", "url": "https://www.alphavantage.co", "data_types": ["financial"], "access_type": "freemium", "description": "Stock prices, forex, crypto, and technical indicators", "last_verified": "2026-03-09", "added_by_team": "TEAM-0001"},
    {"id": "SRC-0002", "name": "SEC EDGAR", "url": "https://www.sec.gov/edgar", "data_types": ["financial", "government"], "access_type": "free", "description": "SEC filings, company financial statements, and regulatory documents", "last_verified": "2026-03-08", "added_by_team": "TEAM-0001"},
    {"id": "SRC-0003", "name": "NewsAPI.org", "url": "https://newsapi.org", "data_types": ["news"], "access_type": "freemium", "description": "Global news articles and headlines from 80,000+ sources", "last_verified": "2026-03-09", "added_by_team": "TEAM-0001"},
    {"id": "SRC-0004", "name": "Yahoo Finance", "url": "https://finance.yahoo.com", "data_types": ["financial"], "access_type": "free", "description": "Stock quotes, historical data, financial news, and portfolio tracking", "last_verified": "2026-03-09", "added_by_team": "TEAM-0001"},
    {"id": "SRC-0005", "name": "Finnhub", "url": "https://finnhub.io", "data_types": ["financial"], "access_type": "freemium", "description": "Real-time stock data, earnings calendar, and company profiles", "last_verified": "2026-03-07", "added_by_team": "TEAM-0002"},
    {"id": "SRC-0006", "name": "Reddit", "url": "https://www.reddit.com", "data_types": ["social-media"], "access_type": "api-key-required", "description": "Social media posts, comments, and community discussions", "last_verified": "2026-03-06", "added_by_team": "TEAM-0002"},
    {"id": "SRC-0007", "name": "OpenWeatherMap", "url": "https://openweathermap.org", "data_types": ["geospatial", "scientific"], "access_type": "freemium", "description": "Weather data, forecasts, and historical climate information", "last_verified": "2026-03-05", "added_by_team": "TEAM-0002"},
    {"id": "SRC-0008", "name": "ExchangeRate-API", "url": "https://www.exchangerate-api.com", "data_types": ["financial"], "access_type": "freemium", "description": "Real-time and historical foreign exchange rates", "last_verified": "2026-03-09", "added_by_team": "TEAM-0003"},
    {"id": "SRC-0009", "name": "X/Twitter", "url": "https://developer.twitter.com", "data_types": ["social-media", "news"], "access_type": "paid", "description": "Tweets, user profiles, and trending topics", "last_verified": "2026-03-08", "added_by_team": "TEAM-0003"},
    {"id": "SRC-0010", "name": "World Bank Open Data", "url": "https://data.worldbank.org", "data_types": ["financial", "government"], "access_type": "free", "description": "GDP, population, development indicators for all countries", "last_verified": "2026-03-04", "added_by_team": "TEAM-0003"},
    {"id": "SRC-0011", "name": "CoinGecko", "url": "https://www.coingecko.com", "data_types": ["financial"], "access_type": "free", "description": "Cryptocurrency prices, market caps, and exchange data", "last_verified": "2026-03-09", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0012", "name": "US Census Bureau", "url": "https://www.census.gov", "data_types": ["government", "geospatial"], "access_type": "api-key-required", "description": "Demographic, economic, and geographic data for the United States", "last_verified": "2026-03-03", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0013", "name": "FRED (Federal Reserve)", "url": "https://fred.stlouisfed.org", "data_types": ["financial", "government"], "access_type": "api-key-required", "description": "Federal Reserve economic data — interest rates, CPI, employment figures", "last_verified": "2026-03-08", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0014", "name": "Google Trends", "url": "https://trends.google.com", "data_types": ["social-media", "general"], "access_type": "free", "description": "Search interest data and trending queries over time", "last_verified": "2026-03-06", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0015", "name": "Polygon.io", "url": "https://polygon.io", "data_types": ["financial"], "access_type": "freemium", "description": "Real-time and historical market data for stocks, options, and crypto", "last_verified": "2026-03-09", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0016", "name": "NOAA Climate Data", "url": "https://www.ncdc.noaa.gov", "data_types": ["scientific", "geospatial"], "access_type": "free", "description": "Climate records, weather station data, and environmental datasets", "last_verified": "2026-03-02", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0017", "name": "arXiv", "url": "https://arxiv.org", "data_types": ["scientific"], "access_type": "free", "description": "Preprint research papers in physics, mathematics, CS, and more", "last_verified": "2026-03-09", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0018", "name": "GitHub", "url": "https://github.com", "data_types": ["general"], "access_type": "freemium", "description": "Source code repositories, issues, pull requests, and developer activity", "last_verified": "2026-03-09", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0019", "name": "Quandl/Nasdaq Data Link", "url": "https://data.nasdaq.com", "data_types": ["financial"], "access_type": "freemium", "description": "Financial, economic, and alternative datasets", "last_verified": "2026-03-05", "added_by_team": "TEAM-0004"},
    {"id": "SRC-0020", "name": "OpenStreetMap / Nominatim", "url": "https://nominatim.openstreetmap.org", "data_types": ["geospatial"], "access_type": "free", "description": "Geocoding, reverse geocoding, and map data", "last_verified": "2026-03-07", "added_by_team": "TEAM-0004"},
]


def populate_indexes():
    """Write all 20 entries to each index.json file."""

    # Scripts index
    scripts_index = {
        "version": "1.0",
        "last_updated": "2026-03-10",
        "scripts": SCRIPTS_DATA,
        "categories": {
            "data-collection": [s["id"] for s in SCRIPTS_DATA if s["category"] == "data-collection"],
            "analysis": [s["id"] for s in SCRIPTS_DATA if s["category"] == "analysis"],
            "automation": [s["id"] for s in SCRIPTS_DATA if s["category"] == "automation"],
            "utility": [s["id"] for s in SCRIPTS_DATA if s["category"] == "utility"],
        },
        "language_index": {}
    }
    for s in SCRIPTS_DATA:
        lang = s["language"]
        if lang not in scripts_index["language_index"]:
            scripts_index["language_index"][lang] = []
        scripts_index["language_index"][lang].append(s["id"])

    with open(SCRIPTS_INDEX, "w") as f:
        json.dump(scripts_index, f, indent=2)
    print(f"  Populated scripts/index.json with {len(SCRIPTS_DATA)} entries")

    # Tools index
    tools_index = {
        "version": "1.0",
        "last_updated": "2026-03-10",
        "tools": TOOLS_DATA,
        "categories": {
            "data-retrieval": [t["id"] for t in TOOLS_DATA if t["category"] == "data-retrieval"],
            "analysis": [t["id"] for t in TOOLS_DATA if t["category"] == "analysis"],
            "communication": [t["id"] for t in TOOLS_DATA if t["category"] == "communication"],
            "integration": [t["id"] for t in TOOLS_DATA if t["category"] == "integration"],
        },
        "auth_types": {}
    }
    for t in TOOLS_DATA:
        at = t["auth_type"]
        if at not in tools_index["auth_types"]:
            tools_index["auth_types"][at] = []
        tools_index["auth_types"][at].append(t["id"])

    with open(TOOLS_INDEX, "w") as f:
        json.dump(tools_index, f, indent=2)
    print(f"  Populated api-tools/index.json with {len(TOOLS_DATA)} entries")

    # Sources index
    sources_index = {
        "version": "1.0",
        "last_updated": "2026-03-10",
        "sources": SOURCES_DATA,
        "by_data_type": {
            "financial": [], "news": [], "social-media": [], "government": [],
            "scientific": [], "geospatial": [], "general": []
        },
        "by_access_type": {
            "free": [], "freemium": [], "paid": [], "api-key-required": []
        }
    }
    for s in SOURCES_DATA:
        for dt in s["data_types"]:
            if dt in sources_index["by_data_type"]:
                sources_index["by_data_type"][dt].append(s["id"])
        if s["access_type"] in sources_index["by_access_type"]:
            sources_index["by_access_type"][s["access_type"]].append(s["id"])

    with open(SOURCES_INDEX, "w") as f:
        json.dump(sources_index, f, indent=2)
    print(f"  Populated sources/index.json with {len(SOURCES_DATA)} entries")


def test_scripts_to_sources():
    """Test: Which scripts fetch data from which sources?"""
    print("\n" + "=" * 70)
    print("QUERY 1: Which scripts fetch data from which sources?")
    print("=" * 70)

    start = time.time()

    # We have to do fuzzy name/description matching — no formal links exist
    matches = []
    for script in SCRIPTS_DATA:
        desc = (script["description"] + " " + script["name"]).lower()
        for source in SOURCES_DATA:
            src_name = source["name"].lower()
            src_url = source["url"].lower()
            # Check if source name appears in script description
            name_parts = src_name.replace("/", " ").replace("-", " ").split()
            for part in name_parts:
                if len(part) > 3 and part in desc:
                    matches.append((script["id"], script["name"], source["id"], source["name"], f"keyword '{part}'"))
                    break

    elapsed = time.time() - start

    print(f"\n  Method: Fuzzy keyword matching on descriptions/names")
    print(f"  Time: {elapsed*1000:.2f} ms")
    print(f"  Matches found: {len(matches)}")
    print(f"\n  {'Script':<30} {'Source':<25} {'Matched on'}")
    print(f"  {'-'*30} {'-'*25} {'-'*20}")
    for sid, sname, srcid, srcname, reason in matches:
        print(f"  {sid + ' ' + sname:<30} {srcid + ' ' + srcname:<25} {reason}")

    # Report gaps
    scripts_with_match = set(m[0] for m in matches)
    scripts_without = [s for s in SCRIPTS_DATA if s["id"] not in scripts_with_match and s["category"] == "data-collection"]
    if scripts_without:
        print(f"\n  GAP: {len(scripts_without)} data-collection scripts have NO matched source:")
        for s in scripts_without:
            print(f"    - {s['id']} {s['name']}")

    return matches, elapsed


def test_tools_to_scripts():
    """Test: Which tools are used by which scripts?"""
    print("\n" + "=" * 70)
    print("QUERY 2: Which API tools are used by which scripts?")
    print("=" * 70)

    start = time.time()

    matches = []
    for script in SCRIPTS_DATA:
        desc = (script["description"] + " " + script["name"]).lower()
        for tool in TOOLS_DATA:
            tool_name = tool["name"].lower()
            tool_endpoint = tool["endpoint"].lower()
            # Match by tool name keywords or endpoint domain
            name_parts = tool_name.replace("/", " ").replace("-", " ").replace("(", "").replace(")", "").split()
            for part in name_parts:
                if len(part) > 3 and part in desc:
                    matches.append((script["id"], script["name"], tool["id"], tool["name"], f"keyword '{part}'"))
                    break

    elapsed = time.time() - start

    print(f"\n  Method: Fuzzy keyword matching on descriptions/names")
    print(f"  Time: {elapsed*1000:.2f} ms")
    print(f"  Matches found: {len(matches)}")
    print(f"\n  {'Script':<30} {'Tool':<30} {'Matched on'}")
    print(f"  {'-'*30} {'-'*30} {'-'*20}")
    for sid, sname, tid, tname, reason in matches:
        print(f"  {sid + ' ' + sname:<30} {tid + ' ' + tname:<30} {reason}")

    # Report false positives and negatives
    print(f"\n  WARNING: Fuzzy matching is unreliable:")
    print(f"    - 'stock' in description could match multiple stock APIs")
    print(f"    - Short names like 'FRED' or 'X' are missed or cause false positives")
    print(f"    - No way to distinguish between 'mentions' and 'actually uses'")

    return matches, elapsed


def test_team_contributions():
    """Test: Which team added the most entries?"""
    print("\n" + "=" * 70)
    print("QUERY 3: Which team added the most entries across all subsystems?")
    print("=" * 70)

    start = time.time()

    team_counts = {}
    for dataset_name, dataset in [("scripts", SCRIPTS_DATA), ("tools", TOOLS_DATA), ("sources", SOURCES_DATA)]:
        for entry in dataset:
            team = entry["added_by_team"]
            if team not in team_counts:
                team_counts[team] = {"scripts": 0, "tools": 0, "sources": 0, "total": 0}
            team_counts[team][dataset_name] += 1
            team_counts[team]["total"] += 1

    elapsed = time.time() - start

    print(f"\n  Method: Scan all three index files and aggregate by added_by_team")
    print(f"  Time: {elapsed*1000:.2f} ms")
    print(f"\n  {'Team':<12} {'Scripts':>8} {'Tools':>8} {'Sources':>8} {'Total':>8}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for team in sorted(team_counts.keys()):
        c = team_counts[team]
        print(f"  {team:<12} {c['scripts']:>8} {c['tools']:>8} {c['sources']:>8} {c['total']:>8}")

    winner = max(team_counts.items(), key=lambda x: x[1]["total"])
    print(f"\n  Winner: {winner[0]} with {winner[1]['total']} total entries")
    print(f"\n  NOTE: This query was straightforward because all three schemas")
    print(f"        include 'added_by_team'. It's the ONLY cross-system field.")

    return team_counts, elapsed


def report_findings(q1_time, q2_time, q3_time):
    """Report on cross-referencing gaps."""
    print("\n" + "=" * 70)
    print("CROSS-REFERENCING GAP ANALYSIS")
    print("=" * 70)

    print("""
  FINDINGS:

  1. NO FORMAL CROSS-REFERENCES exist between subsystems.
     - Scripts don't record which sources or tools they use
     - Tools don't record which scripts invoke them
     - Sources don't record which scripts or tools consume them

  2. FUZZY MATCHING is the only option for cross-referencing:
     - Must parse descriptions and names for keyword overlap
     - High false-positive rate (e.g., 'stock' matches many things)
     - High false-negative rate (e.g., 'FRED' is too short to match reliably)
     - No standardized naming convention across subsystems

  3. QUERY PERFORMANCE:
     - Query 1 (scripts->sources): {q1:.2f} ms  (O(n*m) text matching)
     - Query 2 (scripts->tools):   {q2:.2f} ms  (O(n*m) text matching)
     - Query 3 (team counts):      {q3:.2f} ms  (O(n) aggregation)
     Note: With only 20 entries each, performance is fine.
     At 1000+ entries, fuzzy matching would degrade significantly.

  4. STRUCTURAL GAPS:
     - No 'uses_sources' field in scripts schema
     - No 'uses_tools' field in scripts schema
     - No 'used_by_scripts' field in tools schema
     - No 'consumed_by_scripts' field in sources schema
     - No master cross-reference index
     - No unified search across subsystems

  5. SINGLE SHARED FIELD: 'added_by_team'
     - This is the ONLY field that enables cross-system queries
     - Team-based queries are easy; relationship-based queries are not

  RECOMMENDATION: Add explicit cross-reference fields to each schema
  and build a master cross-reference index for fast lookups.
""".format(q1=q1_time*1000, q2=q2_time*1000, q3=q3_time*1000))


if __name__ == "__main__":
    print("=" * 70)
    print("PHASE 1: Cross-Reference Analysis")
    print("Team Lead 4 — Sub-Agent Alpha")
    print("=" * 70)

    print("\nStep 1: Populating all index.json files with 20 entries each...")
    populate_indexes()

    print("\nStep 2: Testing cross-reference queries...\n")

    _, q1_time = test_scripts_to_sources()
    _, q2_time = test_tools_to_scripts()
    _, q3_time = test_team_contributions()

    report_findings(q1_time, q2_time, q3_time)

    print("Phase 1 complete.\n")
