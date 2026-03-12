#!/usr/bin/env python3
"""smart_read.py - Token-efficient selective file reader.

Reads specific fields from KB/SOP/source/team entries without loading
the entire file into agent context. Supports YAML frontmatter (Markdown)
and JSON files with field-level and section-level extraction.

Usage:
    python smart_read.py KB-0001 --fields title,tags,status
    python smart_read.py KB-0001 --section "Results"
    python smart_read.py KB-0001 --summary
    python smart_read.py storage/coordination/sops/sop_020_bug_fix_workflow.json --fields title,purpose
    python smart_read.py --file knowledge-base/entries/KB-0008.md --fields id,date --format json
"""

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Maps entry ID prefixes to directories and extensions
ID_TO_DIR = {
    "KB":   ("knowledge-base/entries", ".md"),
    "TEAM": ("teams/sessions", ".md"),
    "SRC":  ("storage/sources", ".json"),
    "SOP":  ("storage/coordination/sops", ".json"),
    "SCR":  ("storage/scripts", ".py"),
    "TOOL": ("storage/api-tools", None),
    "PICK": ("market-research/picks", ".md"),
}


def estimate_tokens(text: str) -> int:
    """Approximate token count: ~4 chars per token."""
    return max(1, len(text) // 4)


def resolve_path(identifier: str) -> str:
    """Resolve an entry_id or file_path to an absolute path."""
    # If it looks like a path (has / or extension), resolve directly
    if "/" in identifier or "." in identifier:
        candidate = identifier
        if not os.path.isabs(candidate):
            candidate = os.path.join(REPO_ROOT, candidate)
        if os.path.isfile(candidate):
            return candidate
        raise FileNotFoundError(f"File not found: {candidate}")

    # Parse as ID: KB-0001, SOP-0020, SRC-0001, etc.
    match = re.match(r"^([A-Z]+)-(\d+)$", identifier)
    if not match:
        raise ValueError(f"Cannot parse identifier: {identifier}. Use PREFIX-NNNN or a file path.")

    prefix = match.group(1)
    num = match.group(2)

    if prefix not in ID_TO_DIR:
        raise ValueError(f"Unknown prefix '{prefix}'. Known: {', '.join(ID_TO_DIR.keys())}")

    base_dir, ext = ID_TO_DIR[prefix]
    abs_dir = os.path.join(REPO_ROOT, base_dir)

    if ext:
        # Direct match: KB-0001.md
        candidate = os.path.join(abs_dir, f"{identifier}{ext}")
        if os.path.isfile(candidate):
            return candidate

    # For SOP files with old naming: sop_NNN_descriptive.json
    if prefix == "SOP":
        num_int = int(num)
        # Try 3-digit (old convention), 4-digit (new convention), and bare number
        for pad in (f"{num_int:03d}", f"{num_int:04d}", str(num_int)):
            for fname in os.listdir(abs_dir):
                if fname.startswith(f"sop_{pad}_") and fname.endswith(".json"):
                    return os.path.join(abs_dir, fname)

    # For SRC files with descriptor suffix: SRC-0001-openalex.json
    if prefix == "SRC":
        for fname in os.listdir(abs_dir):
            if fname.startswith(f"{identifier}-") and fname.endswith(".json"):
                return os.path.join(abs_dir, fname)
            if fname == f"{identifier}.json":
                return os.path.join(abs_dir, fname)

    raise FileNotFoundError(
        f"Cannot find file for {identifier} in {abs_dir}. "
        f"Tried pattern: {identifier}{ext or '.*'}"
    )


def parse_yaml_frontmatter(text: str):
    """Parse YAML frontmatter from Markdown. Returns (metadata_dict, body_text)."""
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    front = text[4:end].strip()
    body = text[end + 4:].strip()

    metadata = {}
    current_key = None
    current_list = None

    for line in front.split("\n"):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        # Key-value pair
        kv_match = re.match(r"^([a-z_]+)\s*:\s*(.*)$", line_stripped)
        if kv_match:
            key = kv_match.group(1)
            value = kv_match.group(2).strip()

            if current_key and current_list is not None:
                metadata[current_key] = current_list
                current_list = None

            current_key = key

            # Inline list: [a, b, c]
            if value.startswith("[") and value.endswith("]"):
                items = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
                metadata[key] = items
                current_key = None
            elif value == "" or value == "[]":
                current_list = []
            elif value.lower() in ("null", "~"):
                metadata[key] = None
                current_key = None
            else:
                metadata[key] = value.strip("'\"")
                current_key = None
        elif line_stripped.startswith("- ") and current_key:
            if current_list is None:
                current_list = []
            current_list.append(line_stripped[2:].strip().strip("'\""))

    if current_key and current_list is not None:
        metadata[current_key] = current_list

    return metadata, body


def extract_section(body: str, section_name: str) -> str:
    """Extract a specific ## section from Markdown body."""
    pattern = re.compile(
        r"^##\s+" + re.escape(section_name) + r"\s*$",
        re.MULTILINE | re.IGNORECASE
    )
    match = pattern.search(body)
    if not match:
        # Try partial match
        pattern = re.compile(
            r"^##\s+.*" + re.escape(section_name) + r".*$",
            re.MULTILINE | re.IGNORECASE
        )
        match = pattern.search(body)
        if not match:
            return f"[Section '{section_name}' not found]"

    start = match.end()
    # Find the next ## heading or end of text
    next_heading = re.search(r"^##\s+", body[start:], re.MULTILINE)
    if next_heading:
        end = start + next_heading.start()
    else:
        end = len(body)

    return body[start:end].strip()


def extract_json_fields(data: dict, fields: list) -> dict:
    """Extract specific fields from a JSON dict. Supports dot notation for nesting."""
    result = {}
    for field in fields:
        parts = field.split(".")
        current = data
        try:
            for part in parts:
                if isinstance(current, dict):
                    current = current[part]
                elif isinstance(current, list) and part.isdigit():
                    current = current[int(part)]
                else:
                    current = f"[field '{field}' not found]"
                    break
            result[field] = current
        except (KeyError, IndexError, TypeError):
            result[field] = f"[field '{field}' not found]"
    return result


def format_output(data, fmt: str) -> str:
    """Format output as json or text."""
    if fmt == "json":
        return json.dumps(data, indent=2, default=str)

    # Text format
    lines = []
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list):
                lines.append(f"{key}: {', '.join(str(v) for v in value)}")
            elif isinstance(value, dict):
                lines.append(f"{key}:")
                for k2, v2 in value.items():
                    lines.append(f"  {k2}: {v2}")
            else:
                lines.append(f"{key}: {value}")
    elif isinstance(data, str):
        lines.append(data)
    else:
        lines.append(str(data))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Token-efficient selective file reader. "
        "Reads specific fields or sections without loading entire files.",
        epilog="Examples:\n"
        "  smart_read.py KB-0001 --fields id,tags,status\n"
        "  smart_read.py KB-0008 --section Context\n"
        "  smart_read.py KB-0001 --summary\n"
        "  smart_read.py SOP-0020 --fields title,purpose --format json\n"
        "  smart_read.py SRC-0001 --fields name,url",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", help="Entry ID (KB-0001, SOP-0020) or file path")
    parser.add_argument("--fields", "-f", help="Comma-separated field names to extract")
    parser.add_argument("--section", "-s", help="Markdown section heading to extract (for .md files)")
    parser.add_argument("--summary", action="store_true", help="Return first 200 chars of content")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format (default: text)")
    parser.add_argument("--file", help="Alternative way to specify file path")

    args = parser.parse_args()

    target = args.file if args.file else args.target

    try:
        filepath = resolve_path(target)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    full_content = open(filepath, "r", encoding="utf-8").read()
    full_tokens = estimate_tokens(full_content)
    is_markdown = filepath.endswith(".md")
    is_json = filepath.endswith(".json")

    result = {}

    if is_markdown:
        metadata, body = parse_yaml_frontmatter(full_content)

        if args.fields:
            fields = [f.strip() for f in args.fields.split(",")]
            for f in fields:
                if f == "title":
                    # Extract title from first # heading in body
                    title_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
                    result["title"] = title_match.group(1) if title_match else metadata.get("title", "[no title]")
                elif f == "summary":
                    result["summary"] = body[:200].strip()
                elif f in metadata:
                    result[f] = metadata[f]
                else:
                    result[f] = f"[field '{f}' not found in frontmatter]"

        elif args.section:
            section_text = extract_section(body, args.section)
            result = {"section": args.section, "content": section_text}

        elif args.summary:
            title_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
            title = title_match.group(1) if title_match else metadata.get("id", "unknown")
            result = {
                "id": metadata.get("id", "unknown"),
                "title": title,
                "summary": body[:200].strip(),
            }

        else:
            # Default: return metadata only (no body)
            result = metadata

    elif is_json:
        try:
            data = json.loads(full_content)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in {filepath}: {e}", file=sys.stderr)
            sys.exit(1)

        if args.fields:
            fields = [f.strip() for f in args.fields.split(",")]
            result = extract_json_fields(data, fields)

        elif args.summary:
            # For JSON, return id/title/purpose or first 200 chars
            summary_fields = {}
            for key in ("id", "title", "name", "purpose", "description"):
                if key in data:
                    val = data[key]
                    if isinstance(val, str) and len(val) > 200:
                        val = val[:200] + "..."
                    summary_fields[key] = val
            result = summary_fields if summary_fields else {"summary": json.dumps(data)[:200]}

        elif args.section:
            # For JSON, --section maps to a top-level key
            if args.section in data:
                result = {args.section: data[args.section]}
            else:
                # Try case-insensitive / partial match
                for key in data:
                    if args.section.lower() in key.lower():
                        result[key] = data[key]
                        break
                if not result:
                    result = {"error": f"Key '{args.section}' not found. Available: {', '.join(data.keys())}"}
        else:
            # Default: return top-level keys with types
            result = {k: type(v).__name__ for k, v in data.items()}

    else:
        # Unknown file type - return summary
        result = {"content": full_content[:200] if args.summary else full_content}

    output = format_output(result, args.format)
    output_tokens = estimate_tokens(output)

    print(output)
    print(f"\n--- Token stats: output={output_tokens}, full_file={full_tokens}, saved={full_tokens - output_tokens} ({100 * (full_tokens - output_tokens) // max(1, full_tokens)}%) ---", file=sys.stderr)


if __name__ == "__main__":
    main()
