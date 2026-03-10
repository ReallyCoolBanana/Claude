#!/usr/bin/env python3
"""
handoff_tracker.py — Tracks unresolved handoff items across team sessions.

Parses all teams/sessions/TEAM-*.md files, extracts "Handoff Notes" bullet
points, and checks if subsequent teams addressed each item.

Pure stdlib — no external dependencies.

Usage:
    python3 handoff_tracker.py
    python3 handoff_tracker.py --unresolved-only
    python3 handoff_tracker.py --json
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Base paths
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEAM_SESSIONS_DIR = os.path.join(BASE_DIR, "teams", "sessions")
KB_ENTRIES_DIR = os.path.join(BASE_DIR, "knowledge-base", "entries")


# ---------------------------------------------------------------------------
# YAML frontmatter parser
# ---------------------------------------------------------------------------
def parse_frontmatter(text):
    """Parse YAML frontmatter from markdown text. Returns (metadata_dict, body_text)."""
    metadata = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            yaml_block = parts[1].strip()
            body = parts[2].strip()
            for line in yaml_block.split("\n"):
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        items = [x.strip().strip('"').strip("'")
                                 for x in val[1:-1].split(",")]
                        metadata[key] = [x for x in items if x]
                    elif val.startswith('"') and val.endswith('"'):
                        metadata[key] = val[1:-1]
                    else:
                        metadata[key] = val
    return metadata, body


# ---------------------------------------------------------------------------
# Team log loading
# ---------------------------------------------------------------------------
def load_team_logs():
    """Load all team session logs sorted by team ID."""
    logs = []
    if not os.path.isdir(TEAM_SESSIONS_DIR):
        return logs
    for fname in sorted(os.listdir(TEAM_SESSIONS_DIR)):
        if not fname.endswith(".md") or not fname.startswith("TEAM-"):
            continue
        fpath = os.path.join(TEAM_SESSIONS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()
        except (IOError, OSError):
            continue
        meta, body = parse_frontmatter(text)
        team_id = meta.get("team_id", fname.replace(".md", ""))
        logs.append({
            "team_id": team_id,
            "filepath": fpath,
            "body": body,
            "meta": meta,
        })
    return logs


def load_kb_entries():
    """Load all KB entry bodies for cross-referencing."""
    entries = {}
    if not os.path.isdir(KB_ENTRIES_DIR):
        return entries
    for fname in sorted(os.listdir(KB_ENTRIES_DIR)):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(KB_ENTRIES_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                text = f.read()
        except (IOError, OSError):
            continue
        meta, body = parse_frontmatter(text)
        kb_id = meta.get("id", fname.replace(".md", ""))
        entries[kb_id] = {
            "body": body,
            "meta": meta,
            "filepath": fpath,
        }
    return entries


# ---------------------------------------------------------------------------
# Handoff extraction
# ---------------------------------------------------------------------------
def extract_handoff_items(body):
    """
    Extract bullet points from the 'Handoff Notes' section.
    Returns list of dicts with item text and any referenced entities.
    """
    items = []

    # Find the Handoff Notes section
    handoff_match = re.search(
        r'##\s*Handoff\s+Notes.*?\n(.*?)(?=\n##\s|\Z)',
        body, re.DOTALL | re.IGNORECASE
    )
    if not handoff_match:
        return items

    section_text = handoff_match.group(1)

    # Extract numbered or bulleted items
    # Match lines starting with digit+period or dash/asterisk
    bullet_pattern = re.compile(r'^\s*(?:\d+\.\s*|\-\s*|\*\s*)(.+)', re.MULTILINE)
    for m in bullet_pattern.finditer(section_text):
        item_text = m.group(1).strip()
        # Remove markdown bold markers for cleaner text
        clean_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', item_text)

        # Extract referenced entities
        kb_refs = re.findall(r'KB-\d+', item_text)
        scr_refs = re.findall(r'SCR-\d+', item_text)
        team_refs = re.findall(r'TEAM-\d+', item_text)
        file_refs = re.findall(r'`([^`]+\.\w+)`', item_text)
        tool_refs = re.findall(r'`([^`]+\.py)`', item_text)

        items.append({
            "text": item_text,
            "clean_text": clean_text,
            "kb_refs": kb_refs,
            "scr_refs": scr_refs,
            "team_refs": team_refs,
            "file_refs": file_refs,
            "tool_refs": tool_refs,
        })
    return items


# ---------------------------------------------------------------------------
# Resolution checking
# ---------------------------------------------------------------------------
def extract_keywords(text, min_len=4):
    """Extract significant keywords from handoff item text."""
    # Remove markdown formatting
    clean = re.sub(r'[`*\[\]()]', ' ', text)
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9_\-\.]+\b', clean)
    # Filter out very common words and short words
    stop = {"should", "could", "would", "first", "before", "after", "next",
            "team", "that", "this", "with", "from", "have", "been", "they",
            "will", "when", "what", "file", "files", "need", "already",
            "available", "existing", "currently", "running", "safely", "whether"}
    return [w.lower() for w in words if len(w) >= min_len and w.lower() not in stop]


def check_resolution(item, subsequent_logs, kb_entries):
    """
    Check if a handoff item was addressed by subsequent teams or became KB knowledge.
    Returns (status, resolving_team, evidence).
    """
    keywords = extract_keywords(item["clean_text"])
    file_refs = item.get("file_refs", [])
    tool_refs = item.get("tool_refs", [])

    best_match_score = 0
    best_team = None
    best_evidence = ""

    for log in subsequent_logs:
        body_lower = log["body"].lower()
        score = 0
        evidence_parts = []

        # Check keyword overlap
        for kw in keywords:
            if kw.lower() in body_lower:
                score += 1

        # Check file references
        for fref in file_refs:
            if fref.lower() in body_lower:
                score += 3  # Strong signal
                evidence_parts.append(f"references {fref}")

        # Check tool references
        for tref in tool_refs:
            if tref.lower() in body_lower:
                score += 3
                evidence_parts.append(f"references {tref}")

        # Check if the log explicitly mentions the concepts
        for kb_ref in item.get("kb_refs", []):
            if kb_ref.lower() in body_lower:
                score += 2
                evidence_parts.append(f"references {kb_ref}")

        # Normalize by keyword count to get a ratio
        if keywords:
            match_ratio = score / (len(keywords) + len(file_refs) * 3 + len(tool_refs) * 3)
        else:
            match_ratio = 0

        if score > best_match_score:
            best_match_score = score
            best_team = log["team_id"]
            best_evidence = "; ".join(evidence_parts) if evidence_parts else \
                f"{score}/{len(keywords)} keywords matched"

    # Check KB entries too
    kb_match = False
    kb_match_id = None
    for kb_id, entry in kb_entries.items():
        body_lower = entry["body"].lower()
        kw_hits = sum(1 for kw in keywords if kw.lower() in body_lower)
        if keywords and kw_hits / len(keywords) > 0.4:
            kb_match = True
            kb_match_id = kb_id

    # Determine resolution status
    # Threshold: need at least 3 keyword matches or strong file reference
    if best_match_score >= 3:
        status = "resolved"
    elif best_match_score >= 2:
        status = "likely_resolved"
    else:
        status = "unresolved"
        best_team = None
        best_evidence = ""

    return {
        "status": status,
        "resolving_team": best_team,
        "evidence": best_evidence,
        "keyword_matches": best_match_score,
        "kb_reference": kb_match_id if kb_match else None,
    }


# ---------------------------------------------------------------------------
# Main tracking logic
# ---------------------------------------------------------------------------
def track_handoffs(unresolved_only=False):
    """Build complete handoff tracking report."""
    logs = load_team_logs()
    kb_entries = load_kb_entries()

    if not logs:
        return {"error": "No team session logs found", "items": []}

    all_items = []

    for i, log in enumerate(logs):
        items = extract_handoff_items(log["body"])
        subsequent_logs = logs[i + 1:]

        for item_idx, item in enumerate(items):
            resolution = check_resolution(item, subsequent_logs, kb_entries)

            record = {
                "item_number": item_idx + 1,
                "source_team": log["team_id"],
                "text": item["text"],
                "status": resolution["status"],
                "resolving_team": resolution["resolving_team"],
                "evidence": resolution["evidence"],
                "keyword_matches": resolution["keyword_matches"],
                "kb_reference": resolution["kb_reference"],
                "referenced_files": item["file_refs"],
                "referenced_tools": item["tool_refs"],
            }

            if not unresolved_only or resolution["status"] == "unresolved":
                all_items.append(record)

    # Summary statistics
    total = len(all_items) if not unresolved_only else \
        sum(1 for log in logs for _ in extract_handoff_items(log["body"]))
    resolved_count = sum(1 for item in all_items if item["status"] == "resolved")
    likely_count = sum(1 for item in all_items if item["status"] == "likely_resolved")
    unresolved_count = sum(1 for item in all_items if item["status"] == "unresolved")

    # If unresolved_only, recount from all items
    if unresolved_only:
        all_logs_items = []
        for i, log in enumerate(logs):
            items = extract_handoff_items(log["body"])
            subsequent_logs = logs[i + 1:]
            for item in items:
                resolution = check_resolution(item, subsequent_logs, kb_entries)
                all_logs_items.append(resolution["status"])
        resolved_count = all_logs_items.count("resolved")
        likely_count = all_logs_items.count("likely_resolved")
        unresolved_count = all_logs_items.count("unresolved")
        total = len(all_logs_items)

    return {
        "summary": {
            "total_handoff_items": total,
            "resolved": resolved_count,
            "likely_resolved": likely_count,
            "unresolved": unresolved_count,
            "resolution_rate": round(
                (resolved_count + likely_count) / total * 100, 1
            ) if total > 0 else 0,
            "teams_analyzed": len(logs),
        },
        "items": all_items,
    }


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def format_text_report(report):
    """Format report for human-readable terminal output."""
    lines = []
    summary = report["summary"]

    lines.append("=" * 70)
    lines.append("HANDOFF TRACKER REPORT")
    lines.append("=" * 70)
    lines.append(f"Teams analyzed:      {summary['teams_analyzed']}")
    lines.append(f"Total handoff items: {summary['total_handoff_items']}")
    lines.append(f"Resolved:            {summary['resolved']}")
    lines.append(f"Likely resolved:     {summary['likely_resolved']}")
    lines.append(f"Unresolved:          {summary['unresolved']}")
    lines.append(f"Resolution rate:     {summary['resolution_rate']}%")
    lines.append("")

    # Group by source team
    by_team = {}
    for item in report["items"]:
        team = item["source_team"]
        by_team.setdefault(team, []).append(item)

    for team in sorted(by_team):
        lines.append(f"--- {team} ---")
        for item in by_team[team]:
            status_icon = {
                "resolved": "[RESOLVED]",
                "likely_resolved": "[LIKELY]  ",
                "unresolved": "[OPEN]    ",
            }.get(item["status"], "[?]")

            # Truncate long text
            text = item["text"]
            if len(text) > 100:
                text = text[:97] + "..."

            lines.append(f"  {status_icon} {item['item_number']}. {text}")
            if item["resolving_team"]:
                lines.append(f"             -> Addressed by {item['resolving_team']}: "
                              f"{item['evidence']}")
            if item["kb_reference"]:
                lines.append(f"             -> Related KB entry: {item['kb_reference']}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Track unresolved handoff items across team sessions.")
    parser.add_argument("--unresolved-only", action="store_true",
                        help="Show only unresolved items")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON instead of formatted text")
    args = parser.parse_args()

    report = track_handoffs(unresolved_only=args.unresolved_only)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_text_report(report))


if __name__ == "__main__":
    main()
