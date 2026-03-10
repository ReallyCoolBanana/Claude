#!/usr/bin/env python3
"""
Storage System Validation Tool (SCR-0001)
Validates the structural integrity of all storage subsections.

Checks performed:
1. Schema validation - index.json matches documented schema
2. Referential integrity - IDs in categories match main arrays
3. File existence - referenced files exist on disk
4. No orphan files - no unreferenced files
5. ID format - correct pattern (SCR-XXXX, TOOL-XXXX, SRC-XXXX)
6. Date format - valid ISO dates
7. Required fields - no empty/placeholder values
8. Duplicate detection - no duplicate IDs
"""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

STORAGE_ROOT = Path(__file__).resolve().parent.parent
IGNORED_FILES = {"README.md", "TEMPLATE.json", "TEMPLATE.md", ".gitkeep",
                 "index.json", "DIRECTIONS.md"}
# Also ignore scripts we create for testing/validation
IGNORED_PREFIXES = {"validate-storage", "test-edge-cases", "repair-storage",
                    "integrity-test-report"}

PLACEHOLDER_VALUES = {"", "YYYY-MM-DD", "TEAM-XXXX", "SRC-XXXX", "SCR-XXXX",
                       "TOOL-XXXX", "free|freemium|paid|api-key-required",
                       "none|api-key|oauth|bearer-token", "json|xml|csv|html",
                       "high|medium|low|unknown"}

MAX_DESCRIPTION_LENGTH = 5000  # Warn if description exceeds this
MAX_NAME_LENGTH = 200  # Warn if name exceeds this
KNOWN_VERSIONS = {"1.0"}  # Known valid schema versions

ID_PATTERNS = {
    "scripts": re.compile(r"^SCR-\d{4}$"),
    "api-tools": re.compile(r"^TOOL-\d{4}$"),
    "sources": re.compile(r"^SRC-\d{4}$"),
}

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ValidationResult:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, subsection, check, message):
        self.errors.append({"subsection": subsection, "check": check, "message": message})

    def warning(self, subsection, check, message):
        self.warnings.append({"subsection": subsection, "check": check, "message": message})

    @property
    def passed(self):
        return len(self.errors) == 0

    def summary(self):
        lines = []
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  [{e['subsection']}] {e['check']}: {e['message']}")
        if self.warnings:
            lines.append(f"WARNINGS ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  [{w['subsection']}] {w['check']}: {w['message']}")
        if self.passed and not self.warnings:
            lines.append("ALL CHECKS PASSED")
        elif self.passed:
            lines.append(f"PASSED with {len(self.warnings)} warning(s)")
        else:
            lines.append(f"FAILED: {len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        return "\n".join(lines)


def is_ignored_file(filename):
    if filename in IGNORED_FILES:
        return True
    stem = Path(filename).stem
    for prefix in IGNORED_PREFIXES:
        if stem.startswith(prefix):
            return True
    return False


def validate_date(date_str):
    """Check if a string is a valid ISO date."""
    if not DATE_PATTERN.match(date_str):
        return False
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def is_placeholder(value):
    """Check if a value is a template placeholder."""
    if isinstance(value, str):
        return value in PLACEHOLDER_VALUES
    if isinstance(value, list) and len(value) == 0:
        return False  # Empty lists are valid for new indexes
    return False


def load_index(path, result, subsection):
    """Load and parse an index.json file."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data
    except json.JSONDecodeError as e:
        result.error(subsection, "json-parse", f"index.json is malformed: {e}")
        return None
    except FileNotFoundError:
        result.error(subsection, "file-missing", "index.json not found")
        return None


def validate_scripts(result):
    """Validate the scripts/ subsection."""
    subsection = "scripts"
    index_path = STORAGE_ROOT / "scripts" / "index.json"
    data = load_index(index_path, result, subsection)
    if data is None:
        return

    # Schema: version, last_updated, scripts[], categories{}, language_index{}
    required_top = ["version", "last_updated", "scripts", "categories"]
    for field in required_top:
        if field not in data:
            result.error(subsection, "schema", f"Missing top-level field: {field}")

    # Validate version
    if "version" in data:
        if not isinstance(data["version"], str):
            result.error(subsection, "schema", "version must be a string")
        elif not data["version"]:
            result.error(subsection, "schema", "version must not be empty")
        elif data["version"] not in KNOWN_VERSIONS:
            result.warning(subsection, "schema", f"Unknown version '{data['version']}' (known: {KNOWN_VERSIONS})")

    # Validate last_updated
    if "last_updated" in data:
        if not validate_date(data["last_updated"]):
            result.error(subsection, "date-format", f"Invalid last_updated date: {data['last_updated']}")

    # Valid categories
    valid_categories = {"data-collection", "analysis", "automation", "utility"}
    if "categories" in data:
        for cat in data["categories"]:
            if cat not in valid_categories:
                result.warning(subsection, "schema", f"Unknown category: {cat}")

    # Required fields per entry
    entry_fields = ["id", "name", "filename", "language", "category",
                    "description", "dependencies", "added", "added_by_team"]
    scripts = data.get("scripts", [])
    seen_ids = set()
    seen_filenames = set()

    for entry in scripts:
        eid = entry.get("id", "<no id>")

        # Required fields
        for field in entry_fields:
            if field not in entry:
                result.error(subsection, "required-fields", f"{eid}: missing field '{field}'")
            elif is_placeholder(entry[field]):
                result.error(subsection, "placeholder", f"{eid}: field '{field}' has placeholder value")

        # ID format
        if "id" in entry and not ID_PATTERNS["scripts"].match(entry["id"]):
            result.error(subsection, "id-format", f"Invalid script ID format: {entry['id']}")

        # Duplicate IDs
        if eid in seen_ids:
            result.error(subsection, "duplicate-id", f"Duplicate ID: {eid}")
        seen_ids.add(eid)

        # Date validation
        if "added" in entry and isinstance(entry["added"], str) and entry["added"] not in PLACEHOLDER_VALUES:
            if not validate_date(entry["added"]):
                result.error(subsection, "date-format", f"{eid}: invalid date: {entry['added']}")

        # File existence
        if "filename" in entry and entry["filename"]:
            filepath = STORAGE_ROOT / "scripts" / entry["filename"]
            if not filepath.exists():
                result.error(subsection, "file-exists", f"{eid}: file not found: {entry['filename']}")
            seen_filenames.add(entry["filename"])

        # Category validity
        if "category" in entry and entry["category"] not in valid_categories:
            result.error(subsection, "schema", f"{eid}: invalid category '{entry['category']}'")

        # Length checks
        if "description" in entry and isinstance(entry["description"], str):
            if len(entry["description"]) > MAX_DESCRIPTION_LENGTH:
                result.warning(subsection, "length",
                               f"{eid}: description is {len(entry['description'])} chars (max recommended: {MAX_DESCRIPTION_LENGTH})")
        if "name" in entry and isinstance(entry["name"], str):
            if len(entry["name"]) > MAX_NAME_LENGTH:
                result.warning(subsection, "length",
                               f"{eid}: name is {len(entry['name'])} chars (max recommended: {MAX_NAME_LENGTH})")

    # Referential integrity: IDs in category lists must exist in main array
    if "categories" in data:
        for cat, ids in data["categories"].items():
            for ref_id in ids:
                if ref_id not in seen_ids:
                    result.error(subsection, "ref-integrity",
                                 f"Category '{cat}' references non-existent ID: {ref_id}")

        # Every entry's category should have it listed
        for entry in scripts:
            eid = entry.get("id")
            cat = entry.get("category")
            if eid and cat and cat in data["categories"]:
                if eid not in data["categories"][cat]:
                    result.error(subsection, "ref-integrity",
                                 f"{eid} has category '{cat}' but is not in categories['{cat}']")

    # Orphan files
    scripts_dir = STORAGE_ROOT / "scripts"
    for f in scripts_dir.iterdir():
        if f.is_file() and not is_ignored_file(f.name) and f.name not in seen_filenames:
            result.warning(subsection, "orphan-file", f"Unreferenced file: {f.name}")


def validate_api_tools(result):
    """Validate the api-tools/ subsection."""
    subsection = "api-tools"
    index_path = STORAGE_ROOT / "api-tools" / "index.json"
    data = load_index(index_path, result, subsection)
    if data is None:
        return

    required_top = ["version", "last_updated", "tools", "categories"]
    for field in required_top:
        if field not in data:
            result.error(subsection, "schema", f"Missing top-level field: {field}")

    if "last_updated" in data:
        if not validate_date(data["last_updated"]):
            result.error(subsection, "date-format", f"Invalid last_updated date: {data['last_updated']}")

    # Validate version
    if "version" in data:
        if not isinstance(data["version"], str):
            result.error(subsection, "schema", "version must be a string")
        elif not data["version"]:
            result.error(subsection, "schema", "version must not be empty")
        elif data["version"] not in KNOWN_VERSIONS:
            result.warning(subsection, "schema", f"Unknown version '{data['version']}' (known: {KNOWN_VERSIONS})")

    valid_categories = {"data-retrieval", "analysis", "communication", "integration"}
    if "categories" in data:
        for cat in data["categories"]:
            if cat not in valid_categories:
                result.warning(subsection, "schema", f"Unknown category: {cat}")

    entry_fields = ["id", "name", "description", "category", "endpoint", "auth_type",
                    "rate_limit", "response_format", "last_used", "usage_count",
                    "added", "added_by_team"]
    tools = data.get("tools", [])
    seen_ids = set()

    for entry in tools:
        eid = entry.get("id", "<no id>")

        for field in entry_fields:
            if field not in entry:
                result.error(subsection, "required-fields", f"{eid}: missing field '{field}'")
            elif field != "usage_count" and is_placeholder(entry[field]):
                result.error(subsection, "placeholder", f"{eid}: field '{field}' has placeholder value")

        if "id" in entry and not ID_PATTERNS["api-tools"].match(entry["id"]):
            result.error(subsection, "id-format", f"Invalid tool ID format: {entry['id']}")

        if eid in seen_ids:
            result.error(subsection, "duplicate-id", f"Duplicate ID: {eid}")
        seen_ids.add(eid)

        for date_field in ["added", "last_used"]:
            if date_field in entry and isinstance(entry[date_field], str):
                if entry[date_field] not in PLACEHOLDER_VALUES and not validate_date(entry[date_field]):
                    result.error(subsection, "date-format",
                                 f"{eid}: invalid {date_field}: {entry[date_field]}")

        if "usage_count" in entry and not isinstance(entry["usage_count"], (int, float)):
            result.error(subsection, "schema", f"{eid}: usage_count must be a number")

        if "category" in entry and entry["category"] not in valid_categories:
            result.error(subsection, "schema", f"{eid}: invalid category '{entry['category']}'")

        # Length checks
        if "description" in entry and isinstance(entry["description"], str):
            if len(entry["description"]) > MAX_DESCRIPTION_LENGTH:
                result.warning(subsection, "length",
                               f"{eid}: description is {len(entry['description'])} chars (max recommended: {MAX_DESCRIPTION_LENGTH})")
        if "name" in entry and isinstance(entry["name"], str):
            if len(entry["name"]) > MAX_NAME_LENGTH:
                result.warning(subsection, "length",
                               f"{eid}: name is {len(entry['name'])} chars (max recommended: {MAX_NAME_LENGTH})")

    # Referential integrity
    if "categories" in data:
        for cat, ids in data["categories"].items():
            for ref_id in ids:
                if ref_id not in seen_ids:
                    result.error(subsection, "ref-integrity",
                                 f"Category '{cat}' references non-existent ID: {ref_id}")

        for entry in tools:
            eid = entry.get("id")
            cat = entry.get("category")
            if eid and cat and cat in data["categories"]:
                if eid not in data["categories"][cat]:
                    result.error(subsection, "ref-integrity",
                                 f"{eid} has category '{cat}' but is not in categories['{cat}']")

    # Orphan files (tool config files)
    tools_dir = STORAGE_ROOT / "api-tools"
    seen_filenames = set()
    for entry in tools:
        # Tools may or may not have config files; check if they reference one
        if "config_file" in entry:
            seen_filenames.add(entry["config_file"])
    for f in tools_dir.iterdir():
        if f.is_file() and not is_ignored_file(f.name) and f.name not in seen_filenames:
            result.warning(subsection, "orphan-file", f"Unreferenced file: {f.name}")


def validate_sources(result):
    """Validate the sources/ subsection."""
    subsection = "sources"
    index_path = STORAGE_ROOT / "sources" / "index.json"
    data = load_index(index_path, result, subsection)
    if data is None:
        return

    required_top = ["version", "last_updated", "sources", "by_data_type", "by_access_type"]
    for field in required_top:
        if field not in data:
            result.error(subsection, "schema", f"Missing top-level field: {field}")

    if "last_updated" in data:
        if not validate_date(data["last_updated"]):
            result.error(subsection, "date-format", f"Invalid last_updated date: {data['last_updated']}")

    # Validate version
    if "version" in data:
        if not isinstance(data["version"], str):
            result.error(subsection, "schema", "version must be a string")
        elif not data["version"]:
            result.error(subsection, "schema", "version must not be empty")
        elif data["version"] not in KNOWN_VERSIONS:
            result.warning(subsection, "schema", f"Unknown version '{data['version']}' (known: {KNOWN_VERSIONS})")

    valid_data_types = {"financial", "news", "social-media", "government",
                        "scientific", "geospatial", "general"}
    valid_access_types = {"free", "freemium", "paid", "api-key-required"}

    entry_fields = ["id", "name", "url", "api_docs_url", "data_types", "access_type",
                    "auth_method", "rate_limits", "response_format", "reliability",
                    "last_verified", "notes", "added_by_team"]
    sources = data.get("sources", [])
    seen_ids = set()

    for entry in sources:
        eid = entry.get("id", "<no id>")

        for field in entry_fields:
            if field not in entry:
                result.error(subsection, "required-fields", f"{eid}: missing field '{field}'")
            elif is_placeholder(entry[field]):
                result.error(subsection, "placeholder", f"{eid}: field '{field}' has placeholder value")

        if "id" in entry and not ID_PATTERNS["sources"].match(entry["id"]):
            result.error(subsection, "id-format", f"Invalid source ID format: {entry['id']}")

        if eid in seen_ids:
            result.error(subsection, "duplicate-id", f"Duplicate ID: {eid}")
        seen_ids.add(eid)

        if "last_verified" in entry and isinstance(entry["last_verified"], str):
            if entry["last_verified"] not in PLACEHOLDER_VALUES and not validate_date(entry["last_verified"]):
                result.error(subsection, "date-format",
                             f"{eid}: invalid last_verified: {entry['last_verified']}")

        # Check source file existence (SRC-XXXX.json)
        if "id" in entry and ID_PATTERNS["sources"].match(entry["id"]):
            src_file = STORAGE_ROOT / "sources" / f"{entry['id']}.json"
            if not src_file.exists():
                result.error(subsection, "file-exists",
                             f"{eid}: source file not found: {entry['id']}.json")

        if "access_type" in entry and entry["access_type"] not in valid_access_types:
            if entry["access_type"] not in PLACEHOLDER_VALUES:
                result.error(subsection, "schema",
                             f"{eid}: invalid access_type '{entry['access_type']}'")

    # Referential integrity: by_data_type
    if "by_data_type" in data:
        for dtype, ids in data["by_data_type"].items():
            if dtype not in valid_data_types:
                result.warning(subsection, "schema", f"Unknown data_type category: {dtype}")
            for ref_id in ids:
                if ref_id not in seen_ids:
                    result.error(subsection, "ref-integrity",
                                 f"by_data_type['{dtype}'] references non-existent ID: {ref_id}")

    # Referential integrity: by_access_type
    if "by_access_type" in data:
        for atype, ids in data["by_access_type"].items():
            if atype not in valid_access_types:
                result.warning(subsection, "schema", f"Unknown access_type category: {atype}")
            for ref_id in ids:
                if ref_id not in seen_ids:
                    result.error(subsection, "ref-integrity",
                                 f"by_access_type['{atype}'] references non-existent ID: {ref_id}")

    # Cross-check: every source should appear in its declared categories
    for entry in sources:
        eid = entry.get("id")
        if not eid:
            continue
        # Check data_types
        if "data_types" in entry and "by_data_type" in data:
            for dt in entry["data_types"]:
                if dt in data["by_data_type"] and eid not in data["by_data_type"][dt]:
                    result.error(subsection, "ref-integrity",
                                 f"{eid} lists data_type '{dt}' but is not in by_data_type['{dt}']")
        # Check access_type
        if "access_type" in entry and "by_access_type" in data:
            at = entry["access_type"]
            if at in data["by_access_type"] and eid not in data["by_access_type"][at]:
                result.error(subsection, "ref-integrity",
                             f"{eid} has access_type '{at}' but is not in by_access_type['{at}']")

    # Orphan files
    sources_dir = STORAGE_ROOT / "sources"
    for f in sources_dir.iterdir():
        if f.is_file() and not is_ignored_file(f.name):
            # Source files should be SRC-XXXX.json
            stem = f.stem
            if ID_PATTERNS["sources"].match(stem) and stem not in seen_ids:
                result.warning(subsection, "orphan-file", f"Unreferenced source file: {f.name}")
            elif not ID_PATTERNS["sources"].match(stem):
                result.warning(subsection, "orphan-file", f"Non-standard file: {f.name}")


def main():
    print("=" * 60)
    print("Storage System Validation")
    print("=" * 60)
    print(f"Storage root: {STORAGE_ROOT}")
    print()

    result = ValidationResult()

    validate_scripts(result)
    validate_api_tools(result)
    validate_sources(result)

    print(result.summary())
    print()

    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
