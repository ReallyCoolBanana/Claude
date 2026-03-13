#!/usr/bin/env python3
"""
method_fred.py - FRED (Federal Reserve Economic Data) CLI Tool

Retrieves economic data from the FRED API (https://api.stlouisfed.org/fred/).
Supports API key auth or falls back to scraping public FRED pages.

Usage:
    python3 method_fred.py gdp                     # Look up GDP by common name
    python3 method_fred.py --series CPIAUCSL        # Direct series ID
    python3 method_fred.py unemployment --latest    # Most recent value only
    python3 method_fred.py gdp --format json        # JSON output
    python3 method_fred.py --macro-summary          # Full macro backdrop
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Series name mapping
# ---------------------------------------------------------------------------
SERIES_MAP = {
    # GDP & output
    "gdp": "GDP",
    "real_gdp": "GDPC1",
    "gdp_growth": "A191RL1Q225SBEA",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    # Labor market
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "initial_claims": "ICSA",
    "labor_force_participation": "CIVPART",
    # Interest rates
    "fed_funds": "FEDFUNDS",
    "fed_funds_effective": "DFF",
    "10y_yield": "DGS10",
    "2y_yield": "DGS2",
    "30y_yield": "DGS30",
    "3m_yield": "DGS3MO",
    "10y_tips": "DFII10",
    # Spreads
    "yield_curve": "T10Y2Y",
    "ted_spread": "TEDRATE",
    # Markets
    "sp500": "SP500",
    "vix": "VIXCLS",
    "wilshire5000": "WILL5000IND",
    # Money supply
    "m2": "M2SL",
    # Housing
    "housing_starts": "HOUST",
    "case_shiller": "CSUSHPINSA",
    # Consumer
    "consumer_sentiment": "UMCSENT",
    "retail_sales": "RSAFS",
    # Manufacturing
    "industrial_production": "INDPRO",
    "ism_manufacturing": "MANEMP",
    # Dollar
    "dxy": "DTWEXBGS",
    # Commodities (via FRED proxies)
    "oil_wti": "DCOILWTICO",
    "gold": "GOLDAMGBD228NLBM",
}

MACRO_SUMMARY_SERIES = [
    ("GDP (Quarterly)", "gdp"),
    ("CPI (YoY Inflation Proxy)", "cpi"),
    ("Unemployment Rate", "unemployment"),
    ("Fed Funds Rate", "fed_funds"),
    ("10-Year Treasury Yield", "10y_yield"),
    ("VIX (Volatility Index)", "vix"),
    ("S&P 500", "sp500"),
    ("2s10s Yield Curve Spread", "yield_curve"),
]

# ---------------------------------------------------------------------------
# Rate limiter (simple token-bucket, 1 request per 0.5s)
# ---------------------------------------------------------------------------
_last_request_time = 0.0
REQUEST_INTERVAL = 0.5  # seconds


def _rate_limit():
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < REQUEST_INTERVAL:
        time.sleep(REQUEST_INTERVAL - elapsed)
    _last_request_time = time.time()


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def resolve_series_id(name: str) -> str:
    """Resolve a common name to a FRED series ID, or pass through as-is."""
    key = name.lower().replace("-", "_").replace(" ", "_")
    return SERIES_MAP.get(key, name.upper())


def fetch_series_api(series_id: str, api_key: str, limit: int = 10) -> dict:
    """Fetch series observations via the official FRED API."""
    _rate_limit()

    base = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": str(limit),
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "method_fred/1.0")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"FRED API HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"FRED API connection error: {e.reason}")


def fetch_series_info(series_id: str, api_key: str) -> dict:
    """Fetch series metadata via the official FRED API."""
    _rate_limit()

    base = "https://api.stlouisfed.org/fred/series"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    }
    url = f"{base}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "method_fred/1.0")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
        return data
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"FRED API HTTP {e.code}: {e.reason}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"FRED API connection error: {e.reason}")


def fetch_series_web_fallback(series_id: str, limit: int = 10) -> dict:
    """
    Fallback: scrape the public FRED page for recent data.
    FRED provides a JSON data endpoint used by its charts.
    """
    _rate_limit()

    # FRED's public chart data API (no key required for basic access)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y-%m-%d")

    url = (
        f"https://fred.stlouisfed.org/graph/fredgraph.csv"
        f"?id={series_id}"
        f"&cosd={start_date}"
        f"&coed={end_date}"
    )

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; method_fred/1.0)")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            text = resp.read().decode()
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"FRED web fetch HTTP {e.code} for {series_id}: {e.reason}. "
            f"Try providing an API key with --api-key or FRED_API_KEY env var."
        )
    except urllib.error.URLError as e:
        raise RuntimeError(f"FRED web connection error: {e.reason}")

    # Parse CSV: first line is header (DATE,SERIES_ID), rest is data
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"No data returned for series {series_id}")

    observations = []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) >= 2:
            date_str = parts[0].strip()
            value_str = parts[1].strip()
            if value_str and value_str != ".":
                observations.append({
                    "date": date_str,
                    "value": value_str,
                })

    # Sort descending by date, take most recent
    observations.sort(key=lambda x: x["date"], reverse=True)
    observations = observations[:limit]

    return {"observations": observations}


def fetch_series(series_id: str, api_key: str | None, limit: int = 10) -> dict:
    """Fetch series data, using API key if available, else web fallback."""
    if api_key:
        return fetch_series_api(series_id, api_key, limit)
    else:
        return fetch_series_web_fallback(series_id, limit)


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_table(series_id: str, observations: list[dict], title: str = None) -> str:
    """Format observations as a text table."""
    if not observations:
        return f"No data available for {series_id}"

    header = title or series_id
    lines = [
        f"\n  {header}",
        f"  {'=' * len(header)}",
        f"  {'Date':<14} {'Value':>14}",
        f"  {'-' * 14} {'-' * 14}",
    ]
    for obs in observations:
        date = obs.get("date", "N/A")
        value = obs.get("value", "N/A")
        # Format numeric values
        try:
            v = float(value)
            if abs(v) >= 1_000_000:
                value_fmt = f"{v:>14,.0f}"
            elif abs(v) >= 100:
                value_fmt = f"{v:>14,.2f}"
            else:
                value_fmt = f"{v:>14.3f}"
        except (ValueError, TypeError):
            value_fmt = f"{value:>14}"
        lines.append(f"  {date:<14} {value_fmt}")

    return "\n".join(lines)


def format_json(series_id: str, observations: list[dict]) -> str:
    """Format observations as JSON."""
    return json.dumps({
        "series_id": series_id,
        "count": len(observations),
        "observations": observations,
    }, indent=2)


def format_macro_summary(results: list[tuple[str, str, list[dict]]]) -> str:
    """Format a macro backdrop summary from multiple series."""
    lines = [
        "",
        "  ============================================",
        "  MACRO BACKDROP SUMMARY",
        f"  As of {datetime.now().strftime('%Y-%m-%d')}",
        "  ============================================",
        "",
    ]

    for label, series_id, observations in results:
        if observations:
            latest = observations[0]
            date = latest.get("date", "N/A")
            value = latest.get("value", "N/A")
            try:
                v = float(value)
                if abs(v) >= 1_000_000:
                    value_fmt = f"{v:,.0f}"
                elif abs(v) >= 100:
                    value_fmt = f"{v:,.2f}"
                else:
                    value_fmt = f"{v:.3f}"
            except (ValueError, TypeError):
                value_fmt = value
            lines.append(f"  {label:<34} {value_fmt:>12}   ({date})")
        else:
            lines.append(f"  {label:<34} {'N/A':>12}")

    lines.append("")
    lines.append("  Source: FRED (Federal Reserve Economic Data)")
    lines.append("  https://fred.stlouisfed.org/")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_series():
    """Print available series mappings."""
    lines = [
        "",
        "  Available series aliases:",
        f"  {'Alias':<30} {'FRED Series ID':<16}",
        f"  {'-' * 30} {'-' * 16}",
    ]
    for alias, sid in sorted(SERIES_MAP.items()):
        lines.append(f"  {alias:<30} {sid:<16}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="method_fred",
        description="Retrieve economic data from FRED (Federal Reserve Economic Data).",
        epilog=(
            "Examples:\n"
            "  %(prog)s gdp                       Fetch recent GDP data\n"
            "  %(prog)s unemployment --latest      Latest unemployment rate\n"
            "  %(prog)s --series CPIAUCSL          Direct series ID lookup\n"
            "  %(prog)s --macro-summary            Full macro backdrop\n"
            "  %(prog)s --list                     List available aliases\n"
            "  %(prog)s cpi --format json           JSON output\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Common name for an economic series (e.g., gdp, cpi, unemployment)",
    )
    parser.add_argument(
        "--series", "-s",
        help="Direct FRED series ID (e.g., CPIAUCSL, UNRATE)",
    )
    parser.add_argument(
        "--api-key", "-k",
        default=os.environ.get("FRED_API_KEY"),
        help="FRED API key (or set FRED_API_KEY env var). Optional - falls back to public data.",
    )
    parser.add_argument(
        "--latest", "-l",
        action="store_true",
        help="Show only the most recent data point",
    )
    parser.add_argument(
        "--limit", "-n",
        type=int,
        default=10,
        help="Number of observations to fetch (default: 10)",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--macro-summary", "-m",
        action="store_true",
        help="Fetch key macro indicators and display a summary",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all available series aliases",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # List mode
    if args.list:
        print(list_series())
        return

    # Macro summary mode
    if args.macro_summary:
        results = []
        errors = []
        for label, alias in MACRO_SUMMARY_SERIES:
            series_id = resolve_series_id(alias)
            try:
                data = fetch_series(series_id, args.api_key, limit=1)
                obs = data.get("observations", [])
                results.append((label, series_id, obs))
            except RuntimeError as e:
                errors.append(f"  Warning: {label} ({series_id}): {e}")
                results.append((label, series_id, []))

        if args.format == "json":
            json_out = {
                "macro_summary": {
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "indicators": [],
                }
            }
            for label, series_id, obs in results:
                entry = {
                    "label": label,
                    "series_id": series_id,
                    "latest_date": obs[0]["date"] if obs else None,
                    "latest_value": obs[0]["value"] if obs else None,
                }
                json_out["macro_summary"]["indicators"].append(entry)
            print(json.dumps(json_out, indent=2))
        else:
            print(format_macro_summary(results))
            if errors:
                for e in errors:
                    print(e, file=sys.stderr)
        return

    # Single series mode
    if not args.name and not args.series:
        parser.print_help()
        sys.exit(1)

    if args.series:
        series_id = args.series.upper()
        display_name = series_id
    else:
        display_name = args.name
        series_id = resolve_series_id(args.name)

    limit = 1 if args.latest else args.limit

    try:
        data = fetch_series(series_id, args.api_key, limit=limit)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    observations = data.get("observations", [])

    if args.format == "json":
        print(format_json(series_id, observations))
    else:
        title = f"{display_name} ({series_id})" if display_name != series_id else series_id
        print(format_table(series_id, observations, title=title))
        print()


if __name__ == "__main__":
    main()
