#!/usr/bin/env python3
"""Upgrade knowledge-base/index.json and teams/sessions/index.json to schema v2.0."""

import json
from collections import defaultdict
from pathlib import Path

BASE = Path("/home/user/Claude")

def load_json(path):
    with open(path) as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

# ── Load all source data ──────────────────────────────────────────────
kb_index = load_json(BASE / "knowledge-base/index.json")
scripts_index = load_json(BASE / "storage/scripts/index.json")
teams_index = load_json(BASE / "teams/sessions/index.json")

# ══════════════════════════════════════════════════════════════════════
# TASK 1: Upgrade knowledge-base/index.json
# ══════════════════════════════════════════════════════════════════════

# Pre-compute: which scripts mention KB entries (search descriptions for KB-NNNN)
import re
kb_pattern = re.compile(r'KB-\d{4}')

script_mentions_kb = defaultdict(list)  # kb_id -> [scr_id, ...]
for scr in scripts_index["scripts"]:
    desc = scr.get("description", "")
    for match in kb_pattern.findall(desc):
        script_mentions_kb[match].append(scr["id"])

# Pre-compute: which teams added which scripts
team_to_scripts = defaultdict(list)
for scr in scripts_index["scripts"]:
    team = scr.get("added_by_team", "")
    if team:
        team_to_scripts[team].append(scr["id"])

# Pre-compute: tag sets per KB entry for related_to (2+ shared tags)
kb_tags = {}
for entry in kb_index["entries"]:
    kb_tags[entry["id"]] = set(entry.get("tags", []))

kb_ids = [e["id"] for e in kb_index["entries"]]

def compute_kb_related_to(entry_id, entry_tags_set):
    related = []
    for other_id in kb_ids:
        if other_id == entry_id:
            continue
        shared = entry_tags_set & kb_tags.get(other_id, set())
        if len(shared) >= 2:
            related.append(other_id)
    return sorted(related)

# Pre-compute: which teams reference each KB entry
kb_referenced_by_teams = defaultdict(list)
for session in teams_index["sessions"]:
    for kb_id in session.get("builds_on_knowledge", []):
        kb_referenced_by_teams[kb_id].append(session["team_id"])

# Build upgraded KB entries
upgraded_kb_entries = []
for entry in kb_index["entries"]:
    eid = entry["id"]
    tags_set = kb_tags[eid]

    # Compute references
    builds_on = entry.pop("builds_on", [])
    uses_scripts = sorted(set(script_mentions_kb.get(eid, [])))
    related_to = compute_kb_related_to(eid, tags_set)

    # Handle status: keep original as validation_status if it was "validated"/"reconstructed"
    original_status = entry.get("status", "active")

    new_entry = {
        "id": eid,
        "title": entry["title"],
        "date": entry["date"],
        "category": entry["category"],
        "tags": entry.get("tags", []),
        "status": "active",
        "references": {
            "builds_on": builds_on if builds_on else [],
            "uses_scripts": uses_scripts,
            "uses_sources": [],
            "related_to": related_to
        },
        # Domain extensions
        "team": entry.get("team", ""),
        "confidence": entry.get("confidence", ""),
    }
    if original_status != "active":
        new_entry["validation_status"] = original_status

    upgraded_kb_entries.append(new_entry)

# Build secondary indexes
kb_by_category = defaultdict(list)
kb_by_tag = defaultdict(list)
for entry in upgraded_kb_entries:
    kb_by_category[entry["category"]].append(entry["id"])
    for tag in entry["tags"]:
        kb_by_tag[tag].append(entry["id"])

# Sort for determinism
kb_by_category = {k: sorted(v) for k, v in sorted(kb_by_category.items())}
kb_by_tag = {k: sorted(v) for k, v in sorted(kb_by_tag.items())}

upgraded_kb = {
    "schema_version": "2.0",
    "domain": "knowledge-base",
    "last_updated": "2026-03-12",
    "entry_count": len(upgraded_kb_entries),
    "entries": upgraded_kb_entries,
    "secondary_indexes": {
        "by_category": dict(kb_by_category),
        "by_tag": dict(kb_by_tag)
    }
}

save_json(BASE / "knowledge-base/index.json", upgraded_kb)
print(f"[OK] knowledge-base/index.json upgraded: {len(upgraded_kb_entries)} entries, schema v2.0")

# ══════════════════════════════════════════════════════════════════════
# TASK 2: Upgrade teams/sessions/index.json
# ══════════════════════════════════════════════════════════════════════

# Pre-compute tag sets per team session for related_to
team_tags = {}
for session in teams_index["sessions"]:
    team_tags[session["team_id"]] = set(session.get("tags", []))

team_ids = [s["team_id"] for s in teams_index["sessions"]]

def compute_team_related_to(team_id, tags_set):
    related = []
    for other_id in team_ids:
        if other_id == team_id:
            continue
        shared = tags_set & team_tags.get(other_id, set())
        if len(shared) >= 2:
            related.append(other_id)
    return sorted(related)

def infer_category_from_tags(tags):
    """Infer a primary category from session tags."""
    tag_set = set(tags)
    # Priority-ordered category inference
    if "coordination" in tag_set or "hub-spoke" in tag_set or "work-stealing" in tag_set:
        return "coordination"
    if "prototype" in tag_set or "communication" in tag_set:
        return "integration"
    if "benchmark" in tag_set or "stress-test" in tag_set:
        return "testing"
    if "data-gathering" in tag_set or "public-api" in tag_set:
        return "data-collection"
    if "multi-agent" in tag_set:
        return "multi-agent"
    if "ai-ml" in tag_set:
        return "research"
    if "validation" in tag_set or "indexing" in tag_set:
        return "maintenance"
    if "automation" in tag_set:
        return "tooling"
    if "storage" in tag_set:
        return "infrastructure"
    if "stock-analysis" in tag_set:
        return "market-research"
    if "bug-fix" in tag_set:
        return "debugging"
    return "general"

upgraded_team_entries = []
for session in teams_index["sessions"]:
    tid = session["team_id"]
    tags = session.get("tags", [])
    tags_set = team_tags[tid]

    builds_on = session.pop("builds_on_knowledge", [])
    uses_scripts = sorted(team_to_scripts.get(tid, []))
    related_to = compute_team_related_to(tid, tags_set)
    category = infer_category_from_tags(tags)

    new_entry = {
        "id": tid,
        "title": session["objective"],
        "date": session["date"],
        "category": category,
        "tags": tags,
        "status": "active",
        "references": {
            "builds_on": builds_on if builds_on else [],
            "uses_scripts": uses_scripts,
            "uses_sources": [],
            "related_to": related_to
        },
        # Domain extensions
        "filename": session["filename"],
        "objective": session["objective"],
        "members": session.get("members", [])
    }
    upgraded_team_entries.append(new_entry)

# Build secondary indexes
team_by_category = defaultdict(list)
team_by_tag = defaultdict(list)
for entry in upgraded_team_entries:
    team_by_category[entry["category"]].append(entry["id"])
    for tag in entry["tags"]:
        team_by_tag[tag].append(entry["id"])

team_by_category = {k: sorted(v) for k, v in sorted(team_by_category.items())}
team_by_tag = {k: sorted(v) for k, v in sorted(team_by_tag.items())}

upgraded_teams = {
    "schema_version": "2.0",
    "domain": "teams",
    "last_updated": "2026-03-12",
    "entry_count": len(upgraded_team_entries),
    "entries": upgraded_team_entries,
    "secondary_indexes": {
        "by_category": dict(team_by_category),
        "by_tag": dict(team_by_tag)
    }
}

save_json(BASE / "teams/sessions/index.json", upgraded_teams)
print(f"[OK] teams/sessions/index.json upgraded: {len(upgraded_team_entries)} entries, schema v2.0")

# ── Summary stats ─────────────────────────────────────────────────────
kb_refs = sum(len(e["references"]["related_to"]) for e in upgraded_kb_entries)
kb_scripts = sum(len(e["references"]["uses_scripts"]) for e in upgraded_kb_entries)
team_refs = sum(len(e["references"]["related_to"]) for e in upgraded_team_entries)
team_scripts = sum(len(e["references"]["uses_scripts"]) for e in upgraded_team_entries)
print(f"\nKB cross-refs: {kb_refs} related_to links, {kb_scripts} uses_scripts links")
print(f"Team cross-refs: {team_refs} related_to links, {team_scripts} uses_scripts links")
print(f"KB categories: {len(kb_by_category)}, KB tags: {len(kb_by_tag)}")
print(f"Team categories: {len(team_by_category)}, Team tags: {len(team_by_tag)}")
