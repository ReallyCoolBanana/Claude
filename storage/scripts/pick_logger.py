#!/usr/bin/env python3
"""
pick_logger.py - Automates stock pick creation for the market research system.

Creates pick .md files and updates the picks index.json automatically.
Auto-generates the next PICK ID from the current index.

Usage:
    python pick_logger.py --ticker AAPL --company "Apple Inc" --direction long \
        --method fundamental --entry-price 180.00 --target-price 220.00 \
        --stop-loss 160.00 --timeframe medium-term --confidence high \
        --team-id TEAM-0035 --thesis "Strong iPhone cycle expected"

    python pick_logger.py --ticker AAPL ... --dry-run   # Preview without writing
"""

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

# Resolve paths relative to this script's location in storage/scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PICKS_DIR = REPO_ROOT / "market-research" / "picks"
INDEX_PATH = PICKS_DIR / "index.json"

VALID_DIRECTIONS = ("long", "short")
VALID_METHODS = ("fundamental", "technical", "sentiment", "quantitative", "macro-sector")
VALID_TIMEFRAMES = ("short-term", "medium-term", "long-term")
VALID_CONFIDENCES = ("high", "medium", "low")
VALID_STATUSES = ("active", "closed", "watchlist")


def load_index() -> dict:
    """Load the picks index.json, or return a fresh skeleton if missing."""
    if INDEX_PATH.exists():
        with open(INDEX_PATH, "r") as f:
            return json.load(f)
    return {
        "version": "1.0",
        "last_updated": str(date.today()),
        "total_picks": 0,
        "picks": [],
        "by_method": {m: [] for m in VALID_METHODS},
        "by_status": {s: [] for s in VALID_STATUSES},
        "performance_summary": {
            "total_picks": 0,
            "win_rate": None,
            "avg_return": None,
        },
    }


def next_pick_id(index: dict) -> str:
    """Determine the next PICK-XXXX id from existing picks."""
    max_num = 0
    for pick in index.get("picks", []):
        pid = pick.get("pick_id", "")
        if pid.startswith("PICK-"):
            try:
                num = int(pid.split("-", 1)[1])
                max_num = max(max_num, num)
            except ValueError:
                continue
    return f"PICK-{max_num + 1:04d}"


def render_pick_md(args, pick_id: str, today: str) -> str:
    """Render the pick markdown file content from the template format."""
    builds_on = args.builds_on if args.builds_on else "[]"

    return f"""---
pick_id: {pick_id}
date: {today}
team: {args.team_id}
ticker: "{args.ticker}"
company: "{args.company}"
direction: {args.direction}
method: {args.method}
entry_price: {args.entry_price}
target_price: {args.target_price}
stop_loss: {args.stop_loss}
timeframe: {args.timeframe}
confidence: {args.confidence}
status: {args.status}
builds_on: {builds_on}
---
# Stock Pick: {args.ticker}

## Thesis
{args.thesis}

## Analysis
### Method Applied
{args.method} analysis

### Key Metrics / Signals
{args.signals if args.signals else "To be filled in."}

### Risk Factors
{args.risks if args.risks else "To be filled in."}

## Exit Criteria
{args.exit_criteria if args.exit_criteria else "Exit at target price or stop loss."}

## Outcome (fill in when closed)
### Actual Return
### What Worked
### What Didn't
### Knowledge Entry
Link to KB entry generated from this pick.
"""


def update_index(index: dict, pick_id: str, args, today: str, filename: str) -> dict:
    """Return a new index dict with the pick added."""
    new_pick = {
        "pick_id": pick_id,
        "ticker": args.ticker,
        "company": args.company,
        "date": today,
        "direction": args.direction,
        "method": args.method,
        "entry_price": args.entry_price,
        "target_price": args.target_price,
        "stop_loss": args.stop_loss,
        "confidence": args.confidence,
        "status": args.status,
        "team": args.team_id,
        "file": filename,
    }

    index["picks"].append(new_pick)
    index["total_picks"] = len(index["picks"])
    index["last_updated"] = today

    # Update by_method
    if "by_method" not in index:
        index["by_method"] = {m: [] for m in VALID_METHODS}
    method_list = index["by_method"].setdefault(args.method, [])
    if pick_id not in method_list:
        method_list.append(pick_id)

    # Update by_status
    if "by_status" not in index:
        index["by_status"] = {s: [] for s in VALID_STATUSES}
    status_list = index["by_status"].setdefault(args.status, [])
    if pick_id not in status_list:
        status_list.append(pick_id)

    # Update performance_summary total
    index.setdefault("performance_summary", {})
    index["performance_summary"]["total_picks"] = index["total_picks"]

    return index


def validate_args(args) -> list[str]:
    """Return a list of validation error messages (empty if valid)."""
    errors = []

    if not args.ticker or not args.ticker.strip():
        errors.append("--ticker is required and cannot be empty")
    if not args.company or not args.company.strip():
        errors.append("--company is required and cannot be empty")
    if args.direction not in VALID_DIRECTIONS:
        errors.append(f"--direction must be one of: {', '.join(VALID_DIRECTIONS)}")
    if args.method not in VALID_METHODS:
        errors.append(f"--method must be one of: {', '.join(VALID_METHODS)}")
    if args.timeframe not in VALID_TIMEFRAMES:
        errors.append(f"--timeframe must be one of: {', '.join(VALID_TIMEFRAMES)}")
    if args.confidence not in VALID_CONFIDENCES:
        errors.append(f"--confidence must be one of: {', '.join(VALID_CONFIDENCES)}")
    if args.status not in VALID_STATUSES:
        errors.append(f"--status must be one of: {', '.join(VALID_STATUSES)}")
    if args.entry_price is not None and args.entry_price <= 0:
        errors.append("--entry-price must be positive")
    if args.target_price is not None and args.target_price <= 0:
        errors.append("--target-price must be positive")
    if args.stop_loss is not None and args.stop_loss <= 0:
        errors.append("--stop-loss must be positive")
    if not args.thesis or not args.thesis.strip():
        errors.append("--thesis is required and cannot be empty")
    if not args.team_id or not args.team_id.strip():
        errors.append("--team-id is required and cannot be empty")

    # Sanity checks for price relationships
    if args.direction == "long":
        if (args.target_price is not None and args.entry_price is not None
                and args.target_price <= args.entry_price):
            errors.append("For long picks, target_price should be above entry_price")
        if (args.stop_loss is not None and args.entry_price is not None
                and args.stop_loss >= args.entry_price):
            errors.append("For long picks, stop_loss should be below entry_price")
    elif args.direction == "short":
        if (args.target_price is not None and args.entry_price is not None
                and args.target_price >= args.entry_price):
            errors.append("For short picks, target_price should be below entry_price")
        if (args.stop_loss is not None and args.entry_price is not None
                and args.stop_loss <= args.entry_price):
            errors.append("For short picks, stop_loss should be above entry_price")

    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a new stock pick and update the picks index.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --ticker AAPL --company "Apple Inc" --direction long \\
      --method fundamental --entry-price 180 --target-price 220 \\
      --stop-loss 160 --timeframe medium-term --confidence high \\
      --team-id TEAM-0035 --thesis "Strong iPhone cycle"

  %(prog)s --ticker TSLA --company "Tesla Inc" --direction short \\
      --method technical --entry-price 300 --target-price 250 \\
      --stop-loss 330 --timeframe short-term --confidence medium \\
      --team-id TEAM-0042 --thesis "Bearish head-and-shoulders" --dry-run
        """,
    )

    # Required arguments
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol (e.g. AAPL)")
    parser.add_argument("--company", required=True, help="Full company name")
    parser.add_argument("--direction", required=True, choices=VALID_DIRECTIONS,
                        help="Trade direction: long or short")
    parser.add_argument("--method", required=True, choices=VALID_METHODS,
                        help="Analysis method used")
    parser.add_argument("--entry-price", required=True, type=float,
                        help="Entry price")
    parser.add_argument("--target-price", required=True, type=float,
                        help="Target price")
    parser.add_argument("--stop-loss", required=True, type=float,
                        help="Stop loss price")
    parser.add_argument("--timeframe", required=True, choices=VALID_TIMEFRAMES,
                        help="Investment timeframe")
    parser.add_argument("--confidence", required=True, choices=VALID_CONFIDENCES,
                        help="Confidence level")
    parser.add_argument("--team-id", required=True, help="Team ID (e.g. TEAM-0035)")
    parser.add_argument("--thesis", required=True, help="Main investment thesis text")

    # Optional arguments
    parser.add_argument("--status", default="active", choices=VALID_STATUSES,
                        help="Pick status (default: active)")
    parser.add_argument("--builds-on", default=None,
                        help='Comma-separated list of KB/PICK IDs this builds on (e.g. "KB-0001,PICK-0003")')
    parser.add_argument("--signals", default=None,
                        help="Key metrics or signals text")
    parser.add_argument("--risks", default=None,
                        help="Risk factors text")
    parser.add_argument("--exit-criteria", default=None,
                        help="Custom exit criteria text")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview the pick without writing any files")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Normalize ticker to uppercase
    args.ticker = args.ticker.upper().strip()

    # Format builds_on as YAML list
    if args.builds_on:
        items = [s.strip() for s in args.builds_on.split(",") if s.strip()]
        args.builds_on = "[" + ", ".join(items) + "]"

    # Validate
    errors = validate_args(args)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1

    # Load index and determine next ID
    index = load_index()
    pick_id = next_pick_id(index)
    today = str(date.today())
    filename = f"{pick_id}-{args.ticker}.md"
    filepath = PICKS_DIR / filename

    # Check for duplicate ticker in active picks
    for existing in index.get("picks", []):
        if (existing.get("ticker") == args.ticker
                and existing.get("status") == "active"
                and args.status == "active"):
            print(f"WARNING: Active pick already exists for {args.ticker} "
                  f"({existing['pick_id']})", file=sys.stderr)

    # Render markdown
    md_content = render_pick_md(args, pick_id, today)

    # Update index
    updated_index = update_index(index, pick_id, args, today, filename)

    if args.dry_run:
        print("=" * 60)
        print(f"DRY RUN - Pick {pick_id} for {args.ticker}")
        print("=" * 60)
        print(f"\nFile would be created: {filepath}")
        print(f"Index would be updated: {INDEX_PATH}")
        print(f"\n--- {filename} ---")
        print(md_content)
        print(f"--- index.json changes ---")
        print(f"  total_picks: {index.get('total_picks', 0)} -> {updated_index['total_picks']}")
        print(f"  new entry in picks[]: {pick_id}")
        print(f"  by_method[{args.method}] += {pick_id}")
        print(f"  by_status[{args.status}] += {pick_id}")
        return 0

    # Write the pick file
    PICKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(md_content)
    print(f"Created: {filepath}")

    # Write updated index
    with open(INDEX_PATH, "w") as f:
        json.dump(updated_index, f, indent=2)
        f.write("\n")
    print(f"Updated: {INDEX_PATH}")

    print(f"\nPick {pick_id} ({args.ticker}) logged successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
