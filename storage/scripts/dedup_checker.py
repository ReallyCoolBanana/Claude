#!/usr/bin/env python3
"""
dedup_checker.py — Scans for duplicate entries across all subsystems.

Detection methods:
  1. Title similarity (normalized Levenshtein-like comparison)
  2. Content fingerprinting (shingled hash overlap for KB entries)
  3. Duplicate ID detection across all index.json files
  4. Cross-subsystem duplicate detection (e.g., SRC-0018 vs SRC-0039 = same API)

Outputs JSON report with confidence scores and keep recommendations.

Usage:
    python dedup_checker.py                    # scan everything
    python dedup_checker.py --type KB          # scan only KB entries
    python dedup_checker.py --json             # JSON output
    python dedup_checker.py --threshold 0.7    # custom similarity threshold

ID: SCR-DEDUP-CHECKER
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# --- Text similarity ---

def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def trigram_set(text: str) -> set:
    """Generate character trigrams from text."""
    text = normalize_text(text)
    if len(text) < 3:
        return {text}
    return {text[i:i+3] for i in range(len(text) - 2)}


def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def title_similarity(title_a: str, title_b: str) -> float:
    """Similarity score between two titles (0.0-1.0)."""
    if not title_a or not title_b:
        return 0.0
    norm_a = normalize_text(title_a)
    norm_b = normalize_text(title_b)
    if norm_a == norm_b:
        return 1.0
    return jaccard_similarity(trigram_set(title_a), trigram_set(title_b))


def content_shingles(text: str, k: int = 5) -> set:
    """Generate word-level k-shingles from text."""
    words = normalize_text(text).split()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i:i+k]) for i in range(len(words) - k + 1)}


def content_similarity(text_a: str, text_b: str) -> float:
    """Content similarity via shingled overlap."""
    shingles_a = content_shingles(text_a)
    shingles_b = content_shingles(text_b)
    return jaccard_similarity(shingles_a, shingles_b)


# --- Entry loading ---

def load_yaml_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter from Markdown."""
    try:
        import yaml
        text = filepath.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        end = text.find("---", 3)
        if end == -1:
            return None
        meta = yaml.safe_load(text[3:end]) or {}
        # Add body content for content comparison
        meta["_body"] = text[end+3:].strip()
        meta["_filepath"] = str(filepath)
        return meta
    except Exception:
        return None


def load_json_entry(filepath: Path) -> dict | None:
    """Load a JSON file as an entry."""
    try:
        with open(filepath) as f:
            data = json.load(f)
        data["_filepath"] = str(filepath)
        return data
    except Exception:
        return None


def load_index_entries(index_path: Path, list_key: str) -> list[dict]:
    """Load entries from an index.json file."""
    try:
        with open(index_path) as f:
            data = json.load(f)
        entries = data.get(list_key, [])
        for e in entries:
            e["_filepath"] = str(index_path)
        return entries
    except Exception:
        return []


def load_all_entries() -> dict[str, list[dict]]:
    """Load entries from all subsystems."""
    entries = defaultdict(list)

    # KB entries
    kb_dir = REPO_ROOT / "knowledge-base" / "entries"
    if kb_dir.exists():
        for f in sorted(kb_dir.glob("KB-*.md")):
            meta = load_yaml_frontmatter(f)
            if meta:
                entries["KB"].append(meta)

    # SCR entries (from index.json)
    scr_index = REPO_ROOT / "storage" / "scripts" / "index.json"
    if scr_index.exists():
        entries["SCR"] = load_index_entries(scr_index, "scripts")

    # SRC entries (individual files)
    src_dir = REPO_ROOT / "storage" / "sources"
    if src_dir.exists():
        for f in sorted(src_dir.glob("SRC-*.json")):
            data = load_json_entry(f)
            if data:
                entries["SRC"].append(data)

    # SOP entries (individual files)
    sop_dir = REPO_ROOT / "storage" / "coordination" / "sops"
    if sop_dir.exists():
        for f in sorted(sop_dir.glob("sop_*.json")):
            data = load_json_entry(f)
            if data:
                entries["SOP"].append(data)

    # TEAM entries
    for team_dir in [REPO_ROOT / "teams" / "sessions", REPO_ROOT / "teams"]:
        if team_dir.exists():
            for f in sorted(team_dir.glob("TEAM-*.md")):
                meta = load_yaml_frontmatter(f)
                if meta:
                    entries["TEAM"].append(meta)

    # DATA entries (from index.json)
    data_index = REPO_ROOT / "storage" / "data" / "index.json"
    if data_index.exists():
        entries["DATA"] = load_index_entries(data_index, "files")

    return dict(entries)


# --- Duplicate detection ---

def find_title_duplicates(entries: list[dict], threshold: float = 0.75) -> list[dict]:
    """Find entries with similar titles."""
    duplicates = []
    for i in range(len(entries)):
        title_a = entries[i].get("title") or entries[i].get("name", "")
        id_a = entries[i].get("id", "?")
        if not title_a:
            continue
        for j in range(i + 1, len(entries)):
            title_b = entries[j].get("title") or entries[j].get("name", "")
            id_b = entries[j].get("id", "?")
            if not title_b:
                continue
            sim = title_similarity(title_a, title_b)
            if sim >= threshold:
                duplicates.append({
                    "type": "title_similarity",
                    "entry_a": id_a,
                    "entry_b": id_b,
                    "title_a": title_a,
                    "title_b": title_b,
                    "similarity": round(sim, 3),
                    "confidence": "high" if sim > 0.9 else "medium" if sim > 0.8 else "low",
                    "recommendation": recommend_keep(entries[i], entries[j]),
                })
    return duplicates


def find_content_duplicates(entries: list[dict], threshold: float = 0.4) -> list[dict]:
    """Find KB entries with similar body content."""
    duplicates = []
    for i in range(len(entries)):
        body_a = entries[i].get("_body", "")
        id_a = entries[i].get("id", "?")
        if len(body_a) < 50:
            continue
        for j in range(i + 1, len(entries)):
            body_b = entries[j].get("_body", "")
            id_b = entries[j].get("id", "?")
            if len(body_b) < 50:
                continue
            sim = content_similarity(body_a, body_b)
            if sim >= threshold:
                duplicates.append({
                    "type": "content_similarity",
                    "entry_a": id_a,
                    "entry_b": id_b,
                    "similarity": round(sim, 3),
                    "confidence": "high" if sim > 0.7 else "medium" if sim > 0.5 else "low",
                    "recommendation": recommend_keep(entries[i], entries[j]),
                })
    return duplicates


def find_id_duplicates(all_entries: dict[str, list[dict]]) -> list[dict]:
    """Find duplicate IDs within and across subsystems."""
    id_map = defaultdict(list)
    for rtype, entries in all_entries.items():
        for entry in entries:
            eid = entry.get("id")
            if eid:
                id_map[eid].append({
                    "resource_type": rtype,
                    "filepath": entry.get("_filepath", "?"),
                    "title": entry.get("title") or entry.get("name", ""),
                })

    duplicates = []
    for eid, locations in id_map.items():
        if len(locations) > 1:
            duplicates.append({
                "type": "duplicate_id",
                "id": eid,
                "count": len(locations),
                "locations": locations,
                "confidence": "high",
                "recommendation": "Assign unique IDs. Keep the most recent or complete version.",
            })
    return duplicates


def recommend_keep(entry_a: dict, entry_b: dict) -> str:
    """Recommend which entry to keep based on recency and completeness."""
    date_a = entry_a.get("date") or entry_a.get("created", "")
    date_b = entry_b.get("date") or entry_b.get("created", "")
    id_a = entry_a.get("id", "?")
    id_b = entry_b.get("id", "?")

    body_a = len(entry_a.get("_body", ""))
    body_b = len(entry_b.get("_body", ""))

    reasons = []

    # Prefer newer
    if date_a > date_b:
        reasons.append(f"{id_a} is newer ({date_a} vs {date_b})")
    elif date_b > date_a:
        reasons.append(f"{id_b} is newer ({date_b} vs {date_a})")

    # Prefer more complete (more fields or longer body)
    fields_a = len([k for k in entry_a if not k.startswith("_")])
    fields_b = len([k for k in entry_b if not k.startswith("_")])
    if fields_a > fields_b + 2:
        reasons.append(f"{id_a} has more fields ({fields_a} vs {fields_b})")
    elif fields_b > fields_a + 2:
        reasons.append(f"{id_b} has more fields ({fields_b} vs {fields_a})")

    if body_a > body_b * 1.5:
        reasons.append(f"{id_a} has more content ({body_a} vs {body_b} chars)")
    elif body_b > body_a * 1.5:
        reasons.append(f"{id_b} has more content ({body_b} vs {body_a} chars)")

    if not reasons:
        return f"Manual review needed: {id_a} and {id_b} are similar in age and completeness."

    keep = id_a if any(id_a in r for r in reasons) else id_b
    return f"Keep {keep}: {'; '.join(reasons)}"


def run_dedup_check(resource_type: str | None = None,
                     title_threshold: float = 0.75,
                     content_threshold: float = 0.4) -> dict:
    """Run full deduplication check."""
    all_entries = load_all_entries()

    if resource_type:
        all_entries = {k: v for k, v in all_entries.items() if k == resource_type}

    all_duplicates = []

    # Title duplicates per subsystem
    for rtype, entries in all_entries.items():
        title_dups = find_title_duplicates(entries, title_threshold)
        for d in title_dups:
            d["subsystem"] = rtype
        all_duplicates.extend(title_dups)

    # Content duplicates (KB only — they have body text)
    if "KB" in all_entries:
        content_dups = find_content_duplicates(all_entries["KB"], content_threshold)
        for d in content_dups:
            d["subsystem"] = "KB"
        all_duplicates.extend(content_dups)

    # ID duplicates across all subsystems
    id_dups = find_id_duplicates(all_entries)
    all_duplicates.extend(id_dups)

    # Cross-subsystem checks (e.g., SRC entries describing the same API)
    if "SRC" in all_entries:
        src_entries = all_entries["SRC"]
        for i in range(len(src_entries)):
            url_a = src_entries[i].get("url", "")
            id_a = src_entries[i].get("id", "?")
            for j in range(i + 1, len(src_entries)):
                url_b = src_entries[j].get("url", "")
                id_b = src_entries[j].get("id", "?")
                # Same base URL = likely duplicate
                if url_a and url_b:
                    from urllib.parse import urlparse
                    host_a = urlparse(url_a).netloc
                    host_b = urlparse(url_b).netloc
                    if host_a and host_a == host_b:
                        all_duplicates.append({
                            "type": "same_api_host",
                            "subsystem": "SRC",
                            "entry_a": id_a,
                            "entry_b": id_b,
                            "url_a": url_a,
                            "url_b": url_b,
                            "host": host_a,
                            "confidence": "high",
                            "recommendation": recommend_keep(src_entries[i], src_entries[j]),
                        })

    # Deduplicate the duplicates list (same pair reported by different methods)
    seen_pairs = set()
    unique_dups = []
    for d in all_duplicates:
        pair_key = tuple(sorted([d.get("entry_a", ""), d.get("entry_b", ""),
                                  d.get("id", "")]))
        dup_type = d.get("type", "")
        full_key = (pair_key, dup_type)
        if full_key not in seen_pairs:
            seen_pairs.add(full_key)
            unique_dups.append(d)

    # Summary
    entry_counts = {k: len(v) for k, v in all_entries.items()}
    high_conf = sum(1 for d in unique_dups if d.get("confidence") == "high")
    med_conf = sum(1 for d in unique_dups if d.get("confidence") == "medium")

    return {
        "checked_at": datetime.now().isoformat(),
        "entry_counts": entry_counts,
        "total_entries_scanned": sum(entry_counts.values()),
        "title_threshold": title_threshold,
        "content_threshold": content_threshold,
        "summary": {
            "total_duplicates": len(unique_dups),
            "high_confidence": high_conf,
            "medium_confidence": med_conf,
            "low_confidence": len(unique_dups) - high_conf - med_conf,
            "by_type": defaultdict(int),
        },
        "duplicates": unique_dups,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Scan for duplicate entries across all subsystems"
    )
    parser.add_argument("--type", choices=["KB", "SCR", "SOP", "DATA", "SRC", "TEAM"],
                        help="Scan only this resource type")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="Title similarity threshold (default: 0.75)")
    parser.add_argument("--content-threshold", type=float, default=0.4,
                        help="Content similarity threshold (default: 0.4)")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON report")
    args = parser.parse_args()

    report = run_dedup_check(args.type, args.threshold, args.content_threshold)

    # Compute by_type counts
    by_type = defaultdict(int)
    for d in report["duplicates"]:
        by_type[d["type"]] += 1
    report["summary"]["by_type"] = dict(by_type)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        s = report["summary"]
        print(f"Deduplication Check")
        print(f"  Entries scanned: {report['total_entries_scanned']}")
        print(f"  Duplicates found: {s['total_duplicates']}")
        print(f"    High confidence: {s['high_confidence']}")
        print(f"    Medium confidence: {s['medium_confidence']}")
        print(f"    Low confidence: {s['low_confidence']}")
        print(f"  By type: {dict(by_type)}")
        print()
        for d in report["duplicates"]:
            conf = d.get("confidence", "?")
            dtype = d.get("type", "?")
            if dtype == "duplicate_id":
                print(f"  [{conf.upper()}] Duplicate ID: {d['id']} "
                      f"({d['count']} occurrences)")
            elif dtype == "same_api_host":
                print(f"  [{conf.upper()}] Same API: {d['entry_a']} & {d['entry_b']} "
                      f"-> {d['host']}")
            else:
                sim = d.get("similarity", 0)
                print(f"  [{conf.upper()}] {dtype}: {d.get('entry_a','?')} & "
                      f"{d.get('entry_b','?')} (sim={sim:.2f})")
            print(f"         {d.get('recommendation', '')}")

    sys.exit(0 if report["summary"]["total_duplicates"] == 0 else 1)


if __name__ == "__main__":
    main()
