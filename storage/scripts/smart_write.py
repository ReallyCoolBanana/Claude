#!/usr/bin/env python3
"""smart_write.py - Token-efficient entry creator.

Creates new KB/team/SOP/source/script/tool entries with minimal token
overhead. Auto-generates IDs, fills boilerplate from templates, places
files in the correct directory, and updates indexes.

Usage:
    echo "Content here" | python smart_write.py --type kb --title "My Entry" --tags tag1,tag2
    python smart_write.py --type kb --title "My Entry" --content "Inline content" --category optimization
    python smart_write.py --type team --title "Session Log" --file notes.txt
    python smart_write.py --type sop --title "New Procedure" --content "Steps..." --dry-run
    python smart_write.py --type src --title "New API" --content '{"url":"..."}' --tags api,data
"""

import argparse
import datetime
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TODAY = datetime.date.today().isoformat()

# Resource type configs: (prefix, directory, extension, template_relative_path)
TYPE_CONFIG = {
    "kb": {
        "prefix": "KB",
        "dir": "knowledge-base/entries",
        "ext": ".md",
        "index_dir": "knowledge-base",
        "pattern": re.compile(r"^KB-(\d{4})\.md$"),
    },
    "team": {
        "prefix": "TEAM",
        "dir": "teams/sessions",
        "ext": ".md",
        "index_dir": "teams/sessions",
        "pattern": re.compile(r"^TEAM-(\d{4})\.md$"),
    },
    "sop": {
        "prefix": "SOP",
        "dir": "storage/coordination/sops",
        "ext": ".json",
        "index_dir": "storage/coordination/sops",
        "pattern": re.compile(r"^sop_(\d{3})_.*\.json$|^SOP-(\d{4})\.json$"),
    },
    "src": {
        "prefix": "SRC",
        "dir": "storage/sources",
        "ext": ".json",
        "index_dir": "storage/sources",
        "pattern": re.compile(r"^SRC-(\d{4}).*\.json$"),
    },
    "script": {
        "prefix": "SCR",
        "dir": "storage/scripts",
        "ext": ".py",
        "index_dir": "storage/scripts",
        "pattern": re.compile(r".*\.py$"),
    },
    "tool": {
        "prefix": "TOOL",
        "dir": "storage/api-tools",
        "ext": ".py",
        "index_dir": "storage/api-tools",
        "pattern": re.compile(r".*"),
    },
}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def find_next_id(resource_type: str) -> str:
    """Scan existing files to determine the next sequential ID."""
    config = TYPE_CONFIG[resource_type]
    abs_dir = os.path.join(REPO_ROOT, config["dir"])

    if not os.path.isdir(abs_dir):
        return f"{config['prefix']}-0001"

    max_num = 0
    for fname in os.listdir(abs_dir):
        match = config["pattern"].match(fname)
        if match:
            # Extract numeric part from matched groups
            for group in match.groups():
                if group is not None:
                    try:
                        num = int(group)
                        max_num = max(max_num, num)
                    except ValueError:
                        pass

    # For SOP, also check the index for any IDs
    if resource_type == "sop":
        index_path = os.path.join(REPO_ROOT, config["index_dir"], "index.json")
        if os.path.isfile(index_path):
            try:
                with open(index_path, "r") as f:
                    idx = json.load(f)
                for key in ("entries", "sops"):
                    if key in idx and isinstance(idx[key], list):
                        for entry in idx[key]:
                            eid = entry.get("id", "")
                            m = re.match(r"SOP-0*(\d+)", eid)
                            if m:
                                max_num = max(max_num, int(m.group(1)))
            except (json.JSONDecodeError, OSError):
                pass

    next_num = max_num + 1
    return f"{config['prefix']}-{next_num:04d}"


def generate_kb_entry(entry_id: str, title: str, content: str, tags: list,
                      category: str, team: str) -> str:
    """Generate a KB Markdown entry with YAML frontmatter."""
    tag_str = "[" + ", ".join(tags) + "]" if tags else "[]"
    summary = content[:200].strip() if content else ""

    return f"""---
id: {entry_id}
date: {TODAY}
team: {team}
role: agent
category: {category}
tags: {tag_str}
status: draft
confidence: medium
builds_on: []
---
# {title}

## Summary
{summary}

## Context
{content}

## Lessons Learned
(To be filled in after validation.)

## Recommendations
(To be filled in after validation.)
"""


def generate_team_entry(entry_id: str, title: str, content: str, tags: list,
                        category: str, team: str) -> str:
    """Generate a team session log."""
    return f"""---
team_id: {entry_id}
date: {TODAY}
members: [agent]
objective: "{title}"
parent_team: null
builds_on_knowledge: []
---
# {title}

## Objective
{title}

## Approach
{content}

## Decisions Made
(To be documented.)

## Knowledge Generated
(To be linked.)

## Handoff Notes
(To be filled by team.)
"""


def generate_sop_entry(entry_id: str, title: str, content: str, tags: list,
                       category: str, team: str) -> dict:
    """Generate an SOP JSON document."""
    num = re.search(r"\d+", entry_id).group()
    return {
        "id": entry_id,
        "version": "1.0",
        "created": TODAY,
        "team": team,
        "title": title,
        "purpose": content[:200] if content else title,
        "summary": content[:200] if content else "",
        "content": content,
        "tags": tags,
        "category": category,
        "status": "draft",
    }


def generate_src_entry(entry_id: str, title: str, content: str, tags: list,
                       category: str, team: str) -> dict:
    """Generate a data source JSON document."""
    # Try to parse content as JSON for structured source data
    extra = {}
    if content:
        try:
            extra = json.loads(content)
        except json.JSONDecodeError:
            extra = {"description": content}

    base = {
        "id": entry_id,
        "name": title,
        "url": extra.get("url", ""),
        "api_docs_url": extra.get("api_docs_url", ""),
        "data_types": extra.get("data_types", tags),
        "access_type": extra.get("access_type", "api"),
        "auth_method": extra.get("auth_method", "none"),
        "rate_limits": extra.get("rate_limits", "unknown"),
        "response_format": extra.get("response_format", "json"),
        "reliability": extra.get("reliability", "unknown"),
        "added_by_team": team,
        "date_added": TODAY,
    }
    # Merge any extra fields
    for k, v in extra.items():
        if k not in base:
            base[k] = v
    return base


def generate_script_stub(entry_id: str, title: str, content: str) -> str:
    """Generate a Python script stub."""
    snake_title = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f'''#!/usr/bin/env python3
"""{snake_title}.py - {title}

Auto-generated by smart_write.py on {TODAY}.
ID: {entry_id}
"""

{content if content else "# TODO: implement"}
'''


def update_index(resource_type: str, entry_id: str, title: str, filename: str,
                 tags: list, category: str, team: str):
    """Add an entry to the relevant index.json."""
    config = TYPE_CONFIG[resource_type]
    index_path = os.path.join(REPO_ROOT, config["index_dir"], "index.json")

    if not os.path.isfile(index_path):
        # Create minimal index
        index_data = {
            "version": "1.0",
            "last_updated": TODAY,
            "count": 0,
            "entries": [],
        }
    else:
        with open(index_path, "r") as f:
            index_data = json.load(f)

    # Find the entries array (could be under different keys in old indexes)
    entries_key = "entries"
    for key in ("entries", "sops", "scripts", "sources", "tools", "sessions", "picks"):
        if key in index_data and isinstance(index_data[key], list):
            entries_key = key
            break

    if entries_key not in index_data:
        index_data[entries_key] = []

    new_entry = {
        "id": entry_id,
        "title": title,
        "file": filename,
        "date": TODAY,
        "team": team,
    }
    if tags:
        new_entry["tags"] = tags
    if category:
        new_entry["category"] = category

    index_data[entries_key].append(new_entry)

    # Update count
    count_key = "count"
    for ck in ("count", "entry_count", "source_count", "sop_count"):
        if ck in index_data:
            count_key = ck
            break
    index_data[count_key] = len(index_data[entries_key])
    index_data["last_updated"] = TODAY

    with open(index_path, "w") as f:
        json.dump(index_data, f, indent=2)

    return index_path


def main():
    parser = argparse.ArgumentParser(
        description="Token-efficient entry creator. Auto-generates IDs, "
        "fills boilerplate, places files correctly, and updates indexes.",
        epilog="Examples:\n"
        '  echo "My findings" | smart_write.py --type kb --title "Discovery"\n'
        '  smart_write.py --type sop --title "New Process" --content "Steps" --dry-run\n'
        '  smart_write.py --type src --title "New API" --tags api,rest',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--type", "-t", required=True,
                        choices=list(TYPE_CONFIG.keys()),
                        help="Resource type to create")
    parser.add_argument("--title", required=True,
                        help="Title for the new entry")
    parser.add_argument("--content", "-c",
                        help="Content string (or use stdin or --file)")
    parser.add_argument("--file", "-f",
                        help="Read content from this file")
    parser.add_argument("--tags", help="Comma-separated tags")
    parser.add_argument("--category", default="",
                        help="Category for the entry")
    parser.add_argument("--team", default="B2-Delta",
                        help="Team attribution (default: B2-Delta)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be created without writing")
    parser.add_argument("--format", choices=["json", "text"], default="text",
                        help="Output format for dry-run")

    args = parser.parse_args()

    # Get content from stdin, --file, or --content
    content = ""
    if args.content:
        content = args.content
    elif args.file:
        if not os.path.isfile(args.file):
            print(f"Error: File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        with open(args.file, "r") as f:
            content = f.read()
    elif not sys.stdin.isatty():
        content = sys.stdin.read()

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    category = args.category or "reference"

    # Generate ID
    entry_id = find_next_id(args.type)
    config = TYPE_CONFIG[args.type]

    # Generate file content
    if args.type == "kb":
        file_content = generate_kb_entry(entry_id, args.title, content, tags, category, args.team)
        filename = f"{entry_id}.md"
    elif args.type == "team":
        file_content = generate_team_entry(entry_id, args.title, content, tags, category, args.team)
        filename = f"{entry_id}.md"
    elif args.type == "sop":
        sop_data = generate_sop_entry(entry_id, args.title, content, tags, category, args.team)
        file_content = json.dumps(sop_data, indent=2)
        # Use current naming convention: sop_NNN_descriptor.json
        num = int(re.search(r"\d+", entry_id).group())
        descriptor = re.sub(r"[^a-z0-9]+", "_", args.title.lower()).strip("_")[:30]
        filename = f"sop_{num:03d}_{descriptor}.json"
    elif args.type == "src":
        src_data = generate_src_entry(entry_id, args.title, content, tags, category, args.team)
        file_content = json.dumps(src_data, indent=2)
        descriptor = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")[:20]
        filename = f"{entry_id}-{descriptor}.json"
    elif args.type == "script":
        file_content = generate_script_stub(entry_id, args.title, content)
        snake_title = re.sub(r"[^a-z0-9]+", "_", args.title.lower()).strip("_")
        filename = f"{snake_title}.py"
    elif args.type == "tool":
        file_content = generate_script_stub(entry_id, args.title, content)
        snake_title = re.sub(r"[^a-z0-9]+", "_", args.title.lower()).strip("_")
        filename = f"{snake_title}.py"
    else:
        print(f"Error: Unsupported type: {args.type}", file=sys.stderr)
        sys.exit(1)

    filepath = os.path.join(REPO_ROOT, config["dir"], filename)
    content_tokens = estimate_tokens(file_content)

    if args.dry_run:
        info = {
            "action": "dry-run",
            "entry_id": entry_id,
            "filename": filename,
            "filepath": filepath,
            "content_tokens": content_tokens,
            "would_update_index": os.path.join(REPO_ROOT, config["index_dir"], "index.json"),
            "preview": file_content[:500] + ("..." if len(file_content) > 500 else ""),
        }
        if args.format == "json":
            print(json.dumps(info, indent=2))
        else:
            for k, v in info.items():
                print(f"{k}: {v}")
        print(f"\n--- Token cost: content={content_tokens} ---", file=sys.stderr)
        return

    # Write the file
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(file_content)

    # Update index
    try:
        index_path = update_index(
            args.type, entry_id, args.title, filename, tags, category, args.team
        )
        index_updated = True
    except Exception as e:
        index_path = "N/A"
        index_updated = False
        print(f"Warning: Index update failed: {e}", file=sys.stderr)

    # Try to call auto_index.py if available
    auto_index = os.path.join(REPO_ROOT, "storage/scripts/auto_index.py")
    if os.path.isfile(auto_index):
        try:
            import subprocess
            subprocess.run(
                [sys.executable, auto_index, os.path.join(REPO_ROOT, config["index_dir"])],
                capture_output=True, timeout=10
            )
        except Exception:
            pass  # auto_index is optional

    result = {
        "created": filepath,
        "entry_id": entry_id,
        "content_tokens": content_tokens,
        "index_updated": index_updated,
        "index_path": index_path,
    }

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"Created: {filepath}")
        print(f"ID: {entry_id}")
        print(f"Index: {'updated' if index_updated else 'FAILED'} ({index_path})")

    print(f"\n--- Token cost: content={content_tokens} ---", file=sys.stderr)


if __name__ == "__main__":
    main()
