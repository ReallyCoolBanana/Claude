#!/usr/bin/env python3
"""SEC EDGAR data-gathering tool.

Fetches SEC filing data (10-K, 10-Q, 8-K) for a given ticker symbol
using the SEC EDGAR API.

Usage:
    python3 method_sec_edgar.py --ticker AAPL --filings 10-K,10-Q --limit 5
    python3 method_sec_edgar.py --ticker MSFT --filings 10-K --limit 2 --json
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_FILING_BASE = "https://www.sec.gov/Archives/edgar/data"
USER_AGENT = "MarketResearchBot/1.0 (research@example.com)"

# Rate-limit: SEC allows 10 req/s; we stay conservative.
_last_request_time = 0.0
_MIN_INTERVAL = 0.12  # ~8 req/s max


def _throttled_request(url: str) -> bytes:
    """Make a GET request with rate limiting and proper User-Agent."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            _last_request_time = time.time()
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError(f"Resource not found: {url}") from exc
        raise ConnectionError(
            f"HTTP {exc.code} from SEC EDGAR: {exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ConnectionError(
            f"Network error contacting SEC EDGAR: {exc.reason}"
        ) from exc


def lookup_cik(ticker: str) -> tuple[str, str]:
    """Return (zero-padded CIK, company name) for a ticker symbol.

    Uses the SEC company_tickers.json bulk file which maps every
    ticker to its CIK and company title.
    """
    data = json.loads(_throttled_request(EDGAR_COMPANY_TICKERS_URL))
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            cik = str(entry["cik_str"]).zfill(10)
            return cik, entry.get("title", "")
    raise LookupError(
        f"Ticker '{ticker}' not found in SEC EDGAR company list."
    )


def fetch_filings(
    cik: str,
    filing_types: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict]:
    """Fetch recent filings for a CIK.

    Returns a list of dicts with keys:
        type, date, accession, primary_doc, url, description
    """
    url = EDGAR_SUBMISSIONS_URL.format(cik=cik)
    data = json.loads(_throttled_request(url))

    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return []

    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    descriptions = recent.get("primaryDocDescription", [])

    wanted = set()
    if filing_types:
        wanted = {ft.upper() for ft in filing_types}

    results: list[dict] = []
    for i in range(len(forms)):
        form = forms[i]
        if wanted and form.upper() not in wanted:
            continue

        acc_nodash = accessions[i].replace("-", "")
        doc_url = (
            f"{EDGAR_FILING_BASE}/{cik.lstrip('0')}"
            f"/{acc_nodash}/{primary_docs[i]}"
        )

        results.append(
            {
                "type": form,
                "date": dates[i],
                "accession": accessions[i],
                "primary_doc": primary_docs[i],
                "url": doc_url,
                "description": descriptions[i] if i < len(descriptions) else "",
            }
        )
        if len(results) >= limit:
            break

    return results


def print_table(filings: list[dict], company: str, ticker: str) -> None:
    """Print filings as a formatted table."""
    print(f"\nSEC Filings for {company} ({ticker.upper()})")
    print("=" * 90)

    if not filings:
        print("  No filings found matching the criteria.")
        return

    # Column widths
    w_type = max(6, max(len(f["type"]) for f in filings))
    w_date = 12
    w_desc = 30

    header = (
        f"  {'Type':<{w_type}}  {'Date':<{w_date}}  "
        f"{'Description':<{w_desc}}  URL"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    for f in filings:
        desc = f["description"][:w_desc] if f["description"] else ""
        print(
            f"  {f['type']:<{w_type}}  {f['date']:<{w_date}}  "
            f"{desc:<{w_desc}}  {f['url']}"
        )

    print(f"\n  Total: {len(filings)} filing(s)\n")


def print_json(filings: list[dict], company: str, ticker: str) -> None:
    """Print filings as JSON."""
    output = {
        "ticker": ticker.upper(),
        "company": company,
        "filings": filings,
        "count": len(filings),
    }
    print(json.dumps(output, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch SEC EDGAR filing data for a given ticker."
    )
    parser.add_argument(
        "--ticker", required=True, help="Stock ticker symbol (e.g. AAPL)"
    )
    parser.add_argument(
        "--filings",
        default=None,
        help="Comma-separated filing types to filter (e.g. 10-K,10-Q,8-K). "
        "Default: all types.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of filings to return (default: 10)",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    filing_types = None
    if args.filings:
        filing_types = [ft.strip() for ft in args.filings.split(",")]

    try:
        cik, company = lookup_cik(args.ticker)
    except LookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ConnectionError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1

    try:
        filings = fetch_filings(cik, filing_types, args.limit)
    except (LookupError, ConnectionError) as exc:
        print(f"Error fetching filings: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print_json(filings, company, args.ticker)
    else:
        print_table(filings, company, args.ticker)

    return 0


if __name__ == "__main__":
    sys.exit(main())
