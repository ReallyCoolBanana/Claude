#!/usr/bin/env python3
"""Rebuild the unified search index from all 5 domain index.json files.

Reads knowledge-base, scripts, sources, api-tools, and team sessions,
normalizes each entry to a common schema, and writes a single
storage/unified_index.json with an inverted tag index.
"""

import json
import os
import sys
from datetime import datetime

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

INDEX_SOURCES = {
    "knowledge-base": os.path.join(REPO_ROOT, "knowledge-base", "index.json"),
    "scripts":        os.path.join(REPO_ROOT, "storage", "scripts", "index.json"),
    "sources":        os.path.join(REPO_ROOT, "storage", "sources", "index.json"),
    "api-tools":      os.path.join(REPO_ROOT, "storage", "api-tools", "index.json"),
    "sessions":       os.path.join(REPO_ROOT, "teams", "sessions", "index.json"),
}

OUTPUT_PATH = os.path.join(REPO_ROOT, "storage", "unified_index.json")


def load_json(path):
    """Load a JSON file, returning None on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"WARNING: Could not load {path}: {e}", file=sys.stderr)
        return None


def normalize_kb(data):
    """Normalize knowledge-base entries."""
    entries = []
    for e in data.get("entries", []):
        entries.append({
            "id": e["id"],
            "domain": "knowledge-base",
            "title": e.get("title", ""),
            "tags": e.get("tags", []),
            "category": e.get("category", ""),
            "date": e.get("date", ""),
            "path": f"knowledge-base/entries/{e['id']}.md",
            "description": e.get("title", ""),
        })
    return entries


def normalize_scripts(data):
    """Normalize script entries."""
    entries = []
    for s in data.get("scripts", []):
        tags = list(s.get("tags", []))
        if s.get("language") and s["language"] not in tags:
            tags.append(s["language"])
        entries.append({
            "id": s["id"],
            "domain": "scripts",
            "title": s.get("name", ""),
            "tags": tags,
            "category": s.get("category", ""),
            "date": s.get("added", ""),
            "path": f"storage/scripts/{s.get('filename', '')}",
            "description": s.get("description", ""),
        })
    return entries


def normalize_sources(data):
    """Normalize data source entries."""
    entries = []
    for s in data.get("sources", []):
        tags = list(s.get("data_types", []))
        if s.get("access_type") and s["access_type"] not in tags:
            tags.append(s["access_type"])
        entries.append({
            "id": s["id"],
            "domain": "sources",
            "title": s.get("name", ""),
            "tags": tags,
            "category": s.get("access_type", ""),
            "date": "",
            "path": f"storage/sources/{s['id']}.json",
            "description": f"{s.get('name', '')} - {s.get('url', '')}",
        })
    return entries


def normalize_tools(data):
    """Normalize api-tools entries."""
    entries = []
    for t in data.get("tools", []):
        entries.append({
            "id": t["id"],
            "domain": "api-tools",
            "title": t.get("name", ""),
            "tags": t.get("tags", []),
            "category": t.get("category", ""),
            "date": t.get("added", t.get("date_added", "")),
            "path": f"storage/api-tools/{t.get('path', '')}",
            "description": t.get("description", ""),
        })
    return entries


def normalize_sessions(data):
    """Normalize team session entries."""
    entries = []
    for s in data.get("sessions", []):
        tags = []
        for kb_ref in s.get("builds_on_knowledge", []):
            tags.append(f"builds-on-{kb_ref}")
        entries.append({
            "id": s["team_id"],
            "domain": "sessions",
            "title": s.get("objective", ""),
            "tags": tags,
            "category": "team-session",
            "date": s.get("date", ""),
            "path": f"teams/sessions/{s.get('filename', '')}",
            "description": s.get("objective", ""),
        })
    return entries


NORMALIZERS = {
    "knowledge-base": normalize_kb,
    "scripts":        normalize_scripts,
    "sources":        normalize_sources,
    "api-tools":      normalize_tools,
    "sessions":       normalize_sessions,
}


def build_tag_index(entries):
    """Build an inverted tag index: tag -> [id, ...]."""
    tag_index = {}
    for entry in entries:
        for tag in entry.get("tags", []):
            tag_lower = tag.lower()
            tag_index.setdefault(tag_lower, [])
            if entry["id"] not in tag_index[tag_lower]:
                tag_index[tag_lower].append(entry["id"])
    return tag_index


def build_domain_counts(entries):
    """Count entries per domain."""
    counts = {}
    for entry in entries:
        d = entry["domain"]
        counts[d] = counts.get(d, 0) + 1
    return counts


def main():
    all_entries = []
    errors = []

    for domain, path in INDEX_SOURCES.items():
        data = load_json(path)
        if data is None:
            errors.append(f"Failed to load {domain} from {path}")
            continue

        normalizer = NORMALIZERS[domain]
        entries = normalizer(data)
        all_entries.extend(entries)
        print(f"  {domain}: {len(entries)} entries")

    tag_index = build_tag_index(all_entries)
    domain_counts = build_domain_counts(all_entries)

    unified = {
        "version": "1.0",
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "total_entries": len(all_entries),
        "domain_counts": domain_counts,
        "entries": all_entries,
        "tag_index": tag_index,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(unified, f, indent=2, ensure_ascii=False)

    print(f"\nUnified index written to {OUTPUT_PATH}")
    print(f"  Total entries: {len(all_entries)}")
    print(f"  Unique tags:   {len(tag_index)}")
    print(f"  Domains:       {domain_counts}")

    if errors:
        print(f"\n  Errors: {len(errors)}")
        for e in errors:
            print(f"    - {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
