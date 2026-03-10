#!/usr/bin/env python3
"""
Storage System Repair Tool (SCR-0003)
Detects and fixes common structural issues in the storage system.

Repairs performed:
1. Rebuild category indexes from main arrays
2. Remove orphan entries from category lists
3. Fix missing dates (set to today)
4. Validate and clean JSON (re-serialize with consistent formatting)
5. Fix empty version fields
6. Remove duplicate IDs (keep first occurrence)
7. Truncate excessively long descriptions (with warning)
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from copy import deepcopy

STORAGE_ROOT = Path(__file__).resolve().parent.parent
TODAY = date.today().isoformat()

DATE_PATTERN_STR = r"^\d{4}-\d{2}-\d{2}$"

import re
DATE_PATTERN = re.compile(DATE_PATTERN_STR)

ID_PATTERNS = {
    "scripts": re.compile(r"^SCR-\d{4}$"),
    "api-tools": re.compile(r"^TOOL-\d{4}$"),
    "sources": re.compile(r"^SRC-\d{4}$"),
}

PLACEHOLDER_DATES = {"", "YYYY-MM-DD"}
MAX_DESCRIPTION_LENGTH = 5000


class RepairLog:
    def __init__(self):
        self.actions = []

    def log(self, subsection, action, detail):
        self.actions.append({"subsection": subsection, "action": action, "detail": detail})
        print(f"  [FIX] [{subsection}] {action}: {detail}")

    def summary(self):
        if not self.actions:
            return "No repairs needed."
        lines = [f"Total repairs: {len(self.actions)}"]
        for a in self.actions:
            lines.append(f"  [{a['subsection']}] {a['action']}: {a['detail']}")
        return "\n".join(lines)


def validate_date(date_str):
    if not DATE_PATTERN.match(str(date_str)):
        return False
    try:
        datetime.strptime(str(date_str), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return None


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def repair_scripts(log, dry_run=False):
    """Repair the scripts/ subsection."""
    subsection = "scripts"
    index_path = STORAGE_ROOT / "scripts" / "index.json"
    data = load_json(index_path)

    if data is None:
        log.log(subsection, "skip", "Could not load index.json (malformed or missing)")
        return

    modified = False
    original = deepcopy(data)

    # Fix missing/empty version
    if "version" not in data or not data.get("version"):
        data["version"] = "1.0"
        log.log(subsection, "fix-version", "Set version to '1.0'")
        modified = True

    # Fix missing last_updated
    if "last_updated" not in data or not validate_date(data.get("last_updated", "")):
        data["last_updated"] = TODAY
        log.log(subsection, "fix-date", f"Set last_updated to {TODAY}")
        modified = True

    # Ensure categories structure exists
    valid_categories = {"data-collection", "analysis", "automation", "utility"}
    if "categories" not in data:
        data["categories"] = {cat: [] for cat in valid_categories}
        log.log(subsection, "fix-structure", "Created missing categories object")
        modified = True

    # Ensure scripts array exists
    if "scripts" not in data:
        data["scripts"] = []
        log.log(subsection, "fix-structure", "Created missing scripts array")
        modified = True

    # Remove duplicate IDs (keep first)
    seen_ids = set()
    deduped = []
    for entry in data.get("scripts", []):
        eid = entry.get("id")
        if eid in seen_ids:
            log.log(subsection, "remove-duplicate", f"Removed duplicate entry {eid}")
            modified = True
            continue
        seen_ids.add(eid)
        deduped.append(entry)
    data["scripts"] = deduped

    # Fix individual entries
    for entry in data["scripts"]:
        eid = entry.get("id", "<no-id>")

        # Fix missing/invalid dates
        if "added" not in entry or not validate_date(entry.get("added", "")):
            old = entry.get("added", "<missing>")
            entry["added"] = TODAY
            log.log(subsection, "fix-date", f"{eid}: set added from '{old}' to {TODAY}")
            modified = True

        # Truncate long descriptions
        if "description" in entry and len(entry["description"]) > MAX_DESCRIPTION_LENGTH:
            entry["description"] = entry["description"][:MAX_DESCRIPTION_LENGTH] + "... [truncated]"
            log.log(subsection, "truncate", f"{eid}: description truncated to {MAX_DESCRIPTION_LENGTH} chars")
            modified = True

    # Rebuild category indexes from main array
    new_categories = {cat: [] for cat in valid_categories}
    for entry in data["scripts"]:
        cat = entry.get("category")
        eid = entry.get("id")
        if cat in new_categories and eid:
            new_categories[cat].append(eid)

    # Check if categories changed
    for cat in valid_categories:
        old_ids = sorted(data.get("categories", {}).get(cat, []))
        new_ids = sorted(new_categories.get(cat, []))
        if old_ids != new_ids:
            log.log(subsection, "rebuild-categories",
                    f"Category '{cat}': {old_ids} -> {new_ids}")
            modified = True

    data["categories"] = new_categories

    # Ensure language_index exists
    if "language_index" not in data:
        data["language_index"] = {}
        log.log(subsection, "fix-structure", "Created missing language_index")
        modified = True

    # Rebuild language_index
    new_lang = {}
    for entry in data["scripts"]:
        lang = entry.get("language")
        eid = entry.get("id")
        if lang and eid:
            new_lang.setdefault(lang, []).append(eid)
    if new_lang != data.get("language_index"):
        data["language_index"] = new_lang
        if new_lang:
            log.log(subsection, "rebuild-language-index", f"Rebuilt language_index: {list(new_lang.keys())}")
            modified = True

    if modified:
        data["last_updated"] = TODAY
        if not dry_run:
            save_json(index_path, data)
            log.log(subsection, "save", "Saved repaired index.json")
        else:
            log.log(subsection, "dry-run", "Would save repaired index.json")


def repair_api_tools(log, dry_run=False):
    """Repair the api-tools/ subsection."""
    subsection = "api-tools"
    index_path = STORAGE_ROOT / "api-tools" / "index.json"
    data = load_json(index_path)

    if data is None:
        log.log(subsection, "skip", "Could not load index.json")
        return

    modified = False

    # Fix missing/empty version
    if "version" not in data or not data.get("version"):
        data["version"] = "1.0"
        log.log(subsection, "fix-version", "Set version to '1.0'")
        modified = True

    # Fix missing last_updated
    if "last_updated" not in data or not validate_date(data.get("last_updated", "")):
        data["last_updated"] = TODAY
        log.log(subsection, "fix-date", f"Set last_updated to {TODAY}")
        modified = True

    # Ensure structures exist
    valid_categories = {"data-retrieval", "analysis", "communication", "integration"}
    if "categories" not in data:
        data["categories"] = {cat: [] for cat in valid_categories}
        log.log(subsection, "fix-structure", "Created missing categories object")
        modified = True

    if "tools" not in data:
        data["tools"] = []
        log.log(subsection, "fix-structure", "Created missing tools array")
        modified = True

    if "auth_types" not in data:
        data["auth_types"] = {}
        log.log(subsection, "fix-structure", "Created missing auth_types object")
        modified = True

    # Remove duplicate IDs
    seen_ids = set()
    deduped = []
    for entry in data.get("tools", []):
        eid = entry.get("id")
        if eid in seen_ids:
            log.log(subsection, "remove-duplicate", f"Removed duplicate entry {eid}")
            modified = True
            continue
        seen_ids.add(eid)
        deduped.append(entry)
    data["tools"] = deduped

    # Fix entries
    for entry in data["tools"]:
        eid = entry.get("id", "<no-id>")

        for date_field in ["added", "last_used"]:
            if date_field not in entry or not validate_date(entry.get(date_field, "")):
                old = entry.get(date_field, "<missing>")
                entry[date_field] = TODAY
                log.log(subsection, "fix-date", f"{eid}: set {date_field} from '{old}' to {TODAY}")
                modified = True

        if "usage_count" not in entry or not isinstance(entry["usage_count"], (int, float)):
            entry["usage_count"] = 0
            log.log(subsection, "fix-field", f"{eid}: set usage_count to 0")
            modified = True

        if "description" in entry and len(entry["description"]) > MAX_DESCRIPTION_LENGTH:
            entry["description"] = entry["description"][:MAX_DESCRIPTION_LENGTH] + "... [truncated]"
            log.log(subsection, "truncate", f"{eid}: description truncated")
            modified = True

    # Rebuild category indexes
    new_categories = {cat: [] for cat in valid_categories}
    for entry in data["tools"]:
        cat = entry.get("category")
        eid = entry.get("id")
        if cat in new_categories and eid:
            new_categories[cat].append(eid)

    for cat in valid_categories:
        old_ids = sorted(data.get("categories", {}).get(cat, []))
        new_ids = sorted(new_categories.get(cat, []))
        if old_ids != new_ids:
            log.log(subsection, "rebuild-categories", f"Category '{cat}': {old_ids} -> {new_ids}")
            modified = True
    data["categories"] = new_categories

    # Rebuild auth_types index
    new_auth = {}
    for entry in data["tools"]:
        auth = entry.get("auth_type")
        eid = entry.get("id")
        if auth and eid:
            new_auth.setdefault(auth, []).append(eid)
    if new_auth != data.get("auth_types"):
        data["auth_types"] = new_auth
        if new_auth:
            log.log(subsection, "rebuild-auth-index", f"Rebuilt auth_types: {list(new_auth.keys())}")
            modified = True

    if modified:
        data["last_updated"] = TODAY
        if not dry_run:
            save_json(index_path, data)
            log.log(subsection, "save", "Saved repaired index.json")
        else:
            log.log(subsection, "dry-run", "Would save repaired index.json")


def repair_sources(log, dry_run=False):
    """Repair the sources/ subsection."""
    subsection = "sources"
    index_path = STORAGE_ROOT / "sources" / "index.json"
    data = load_json(index_path)

    if data is None:
        log.log(subsection, "skip", "Could not load index.json")
        return

    modified = False

    # Fix version
    if "version" not in data or not data.get("version"):
        data["version"] = "1.0"
        log.log(subsection, "fix-version", "Set version to '1.0'")
        modified = True

    # Fix last_updated
    if "last_updated" not in data or not validate_date(data.get("last_updated", "")):
        data["last_updated"] = TODAY
        log.log(subsection, "fix-date", f"Set last_updated to {TODAY}")
        modified = True

    # Ensure structures
    valid_data_types = {"financial", "news", "social-media", "government",
                        "scientific", "geospatial", "general"}
    valid_access_types = {"free", "freemium", "paid", "api-key-required"}

    if "sources" not in data:
        data["sources"] = []
        log.log(subsection, "fix-structure", "Created missing sources array")
        modified = True

    if "by_data_type" not in data:
        data["by_data_type"] = {dt: [] for dt in valid_data_types}
        log.log(subsection, "fix-structure", "Created missing by_data_type")
        modified = True

    if "by_access_type" not in data:
        data["by_access_type"] = {at: [] for at in valid_access_types}
        log.log(subsection, "fix-structure", "Created missing by_access_type")
        modified = True

    # Remove duplicates
    seen_ids = set()
    deduped = []
    for entry in data.get("sources", []):
        eid = entry.get("id")
        if eid in seen_ids:
            log.log(subsection, "remove-duplicate", f"Removed duplicate entry {eid}")
            modified = True
            continue
        seen_ids.add(eid)
        deduped.append(entry)
    data["sources"] = deduped

    # Fix entries
    for entry in data["sources"]:
        eid = entry.get("id", "<no-id>")

        if "last_verified" not in entry or not validate_date(entry.get("last_verified", "")):
            old = entry.get("last_verified", "<missing>")
            entry["last_verified"] = TODAY
            log.log(subsection, "fix-date", f"{eid}: set last_verified from '{old}' to {TODAY}")
            modified = True

    # Rebuild by_data_type
    new_by_dt = {dt: [] for dt in valid_data_types}
    for entry in data["sources"]:
        eid = entry.get("id")
        for dt in entry.get("data_types", []):
            if dt in new_by_dt and eid:
                new_by_dt[dt].append(eid)

    for dt in valid_data_types:
        old = sorted(data.get("by_data_type", {}).get(dt, []))
        new = sorted(new_by_dt.get(dt, []))
        if old != new:
            log.log(subsection, "rebuild-by-data-type", f"'{dt}': {old} -> {new}")
            modified = True
    data["by_data_type"] = new_by_dt

    # Rebuild by_access_type
    new_by_at = {at: [] for at in valid_access_types}
    for entry in data["sources"]:
        eid = entry.get("id")
        at = entry.get("access_type")
        if at in new_by_at and eid:
            new_by_at[at].append(eid)

    for at in valid_access_types:
        old = sorted(data.get("by_access_type", {}).get(at, []))
        new = sorted(new_by_at.get(at, []))
        if old != new:
            log.log(subsection, "rebuild-by-access-type", f"'{at}': {old} -> {new}")
            modified = True
    data["by_access_type"] = new_by_at

    if modified:
        data["last_updated"] = TODAY
        if not dry_run:
            save_json(index_path, data)
            log.log(subsection, "save", "Saved repaired index.json")
        else:
            log.log(subsection, "dry-run", "Would save repaired index.json")


def main():
    print("=" * 60)
    print("Storage System Repair Tool")
    print("=" * 60)
    print(f"Storage root: {STORAGE_ROOT}")
    print(f"Date: {TODAY}")

    dry_run = "--dry-run" in sys.argv
    if dry_run:
        print("MODE: DRY RUN (no files will be modified)")
    else:
        print("MODE: LIVE (files will be modified)")
    print()

    log = RepairLog()

    print("Repairing scripts/...")
    repair_scripts(log, dry_run)
    print()

    print("Repairing api-tools/...")
    repair_api_tools(log, dry_run)
    print()

    print("Repairing sources/...")
    repair_sources(log, dry_run)
    print()

    print("=" * 60)
    print(log.summary())
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
