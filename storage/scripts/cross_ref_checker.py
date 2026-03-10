#!/usr/bin/env python3
"""
cross_ref_checker.py — Cross-Reference Checker (SCR-0014)

Validates all cross-references across the repository:
- KB builds_on chains (with cycle detection)
- Team session builds_on_knowledge references
- Script index dependency references
- Orphan entries (files not in any index)
- Dangling references (index entries with no file)
- Dependency DAG with text-based tree output

Usage:
    python3 cross_ref_checker.py

Output: Structured JSON report + text dependency tree to stdout.
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path


def find_repo_root():
    """Find the repository root by looking for CLAUDE.md."""
    d = Path(__file__).resolve().parent
    for _ in range(10):
        if (d / "CLAUDE.md").exists():
            return d
        d = d.parent
    return Path(__file__).resolve().parent.parent.parent


def parse_yaml_frontmatter(filepath):
    """Parse YAML frontmatter from a markdown file."""
    metadata = {}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        return metadata

    if not content.startswith("---"):
        return metadata

    parts = content.split("---", 2)
    if len(parts) < 3:
        return metadata

    yaml_text = parts[1].strip()
    for line in yaml_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()

        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            if inner:
                metadata[key] = [item.strip().strip('"').strip("'") for item in inner.split(",")]
            else:
                metadata[key] = []
        elif (value.startswith('"') and value.endswith('"')) or \
             (value.startswith("'") and value.endswith("'")):
            metadata[key] = value[1:-1]
        elif value.lower() in ("null", "~", ""):
            metadata[key] = None
        else:
            metadata[key] = value

    return metadata


def load_json(filepath):
    """Load a JSON file, return empty dict on failure."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def detect_cycles_dfs(graph):
    """Detect cycles in a directed graph using DFS.

    graph: dict mapping node -> list of successor nodes
    Returns: list of cycles found (each cycle is a list of nodes)
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = defaultdict(int)  # WHITE by default
    cycles = []
    path = []

    def dfs(node):
        color[node] = GRAY
        path.append(node)

        for neighbor in graph.get(node, []):
            if color[neighbor] == GRAY:
                # Found a cycle
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)
            elif color[neighbor] == WHITE:
                dfs(neighbor)

        path.pop()
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            dfs(node)

    return cycles


def build_tree_text(graph, roots, all_nodes, indent=""):
    """Build a text-based dependency tree."""
    lines = []

    if not roots:
        # If no roots, all nodes are in cycles or isolated
        for node in sorted(all_nodes):
            lines.append(f"{indent}{node}")
        return lines

    def render(node, prefix, is_last, visited):
        connector = "\\-- " if is_last else "|-- "
        lines.append(f"{prefix}{connector}{node}")

        children = sorted(graph.get(node, []))
        # Reverse graph: find who depends on this node
        dependents = sorted(reverse_graph.get(node, []))

        if node in visited:
            if dependents:
                lines.append(f"{prefix}{'    ' if is_last else '|   '}(cycle detected)")
            return
        visited.add(node)

        for i, child in enumerate(dependents):
            child_is_last = (i == len(dependents) - 1)
            child_prefix = prefix + ("    " if is_last else "|   ")
            render(child, child_prefix, child_is_last, visited.copy())

    # Build reverse graph (who depends on X)
    reverse_graph = defaultdict(list)
    for node, deps in graph.items():
        for dep in deps:
            reverse_graph[dep].append(node)

    for i, root in enumerate(sorted(roots)):
        is_last = (i == len(roots) - 1)
        render(root, "", is_last, set())

    return lines


def main():
    root = find_repo_root()
    issues = []

    # ================================================================
    # 1. Collect all KB entries from files
    # ================================================================
    kb_entries_dir = root / "knowledge-base" / "entries"
    kb_file_data = {}  # id -> metadata
    kb_files_on_disk = set()  # filenames found on disk

    if kb_entries_dir.is_dir():
        for mdfile in sorted(kb_entries_dir.glob("*.md")):
            kb_files_on_disk.add(mdfile.name)
            meta = parse_yaml_frontmatter(str(mdfile))
            if meta.get("id"):
                kb_file_data[meta["id"]] = meta
                kb_file_data[meta["id"]]["_filename"] = mdfile.name

    kb_ids_from_files = set(kb_file_data.keys())

    # ================================================================
    # 2. Collect KB entries from index.json
    # ================================================================
    kb_index = load_json(root / "knowledge-base" / "index.json")
    kb_ids_from_index = set()
    for entry in kb_index.get("entries", []):
        if "id" in entry:
            kb_ids_from_index.add(entry["id"])

    # ================================================================
    # 3. Collect team sessions from files
    # ================================================================
    sessions_dir = root / "teams" / "sessions"
    team_file_data = {}  # team_id -> metadata
    team_files_on_disk = set()

    if sessions_dir.is_dir():
        for mdfile in sorted(sessions_dir.glob("*.md")):
            if "TEMPLATE" in mdfile.name.upper():
                continue
            team_files_on_disk.add(mdfile.name)
            meta = parse_yaml_frontmatter(str(mdfile))
            if meta.get("team_id"):
                team_file_data[meta["team_id"]] = meta
                team_file_data[meta["team_id"]]["_filename"] = mdfile.name

    # ================================================================
    # 4. Collect script entries from index.json
    # ================================================================
    scripts_index = load_json(root / "storage" / "scripts" / "index.json")
    script_ids_from_index = set()
    script_deps = {}  # id -> [dep_ids]
    for entry in scripts_index.get("scripts", []):
        sid = entry.get("id", "")
        if sid:
            script_ids_from_index.add(sid)
            script_deps[sid] = entry.get("dependencies", [])

    # ================================================================
    # 5. Check KB builds_on references
    # ================================================================
    kb_graph = {}  # id -> [builds_on ids]
    for kb_id, meta in kb_file_data.items():
        builds_on = meta.get("builds_on", [])
        if builds_on is None:
            builds_on = []
        if isinstance(builds_on, str):
            builds_on = [builds_on]
        kb_graph[kb_id] = builds_on

        for ref in builds_on:
            if ref and ref not in kb_ids_from_files:
                issues.append({
                    "level": "error",
                    "type": "dangling_kb_reference",
                    "source": kb_id,
                    "target": ref,
                    "message": f"KB entry {kb_id} builds_on {ref} which does not exist"
                })

    # ================================================================
    # 6. Check team session builds_on_knowledge references
    # ================================================================
    for team_id, meta in team_file_data.items():
        kb_refs = meta.get("builds_on_knowledge", [])
        if kb_refs is None:
            kb_refs = []
        if isinstance(kb_refs, str):
            kb_refs = [kb_refs]
        for ref in kb_refs:
            if ref and ref not in kb_ids_from_files:
                issues.append({
                    "level": "error",
                    "type": "dangling_team_kb_reference",
                    "source": team_id,
                    "target": ref,
                    "message": f"Team {team_id} references KB {ref} which does not exist"
                })

    # ================================================================
    # 7. Check script dependency references
    # ================================================================
    for scr_id, deps in script_deps.items():
        if deps is None:
            continue
        for dep in deps:
            if dep and dep not in script_ids_from_index:
                issues.append({
                    "level": "error",
                    "type": "dangling_script_dependency",
                    "source": scr_id,
                    "target": dep,
                    "message": f"Script {scr_id} depends on {dep} which is not in scripts index"
                })

    # ================================================================
    # 8. Detect cycles in KB dependency graph
    # ================================================================
    kb_cycles = detect_cycles_dfs(kb_graph)
    for cycle in kb_cycles:
        issues.append({
            "level": "error",
            "type": "kb_cycle",
            "cycle": cycle,
            "message": f"Cycle detected in KB builds_on: {' -> '.join(cycle)}"
        })

    # ================================================================
    # 9. Detect cycles in script dependency graph
    # ================================================================
    script_graph = {}
    for scr_id, deps in script_deps.items():
        script_graph[scr_id] = deps if deps else []
    script_cycles = detect_cycles_dfs(script_graph)
    for cycle in script_cycles:
        issues.append({
            "level": "error",
            "type": "script_cycle",
            "cycle": cycle,
            "message": f"Cycle detected in script dependencies: {' -> '.join(cycle)}"
        })

    # ================================================================
    # 10. Find orphan files (files not in any index)
    # ================================================================
    orphans = []

    # KB orphans
    kb_indexed_filenames = set()
    for entry in kb_index.get("entries", []):
        eid = entry.get("id", "")
        kb_indexed_filenames.add(f"{eid}.md")
    for fname in kb_files_on_disk:
        if fname not in kb_indexed_filenames:
            orphans.append({
                "type": "kb_entry",
                "file": fname,
                "message": f"KB file {fname} exists but is not in knowledge-base/index.json"
            })

    # Team session orphans (only if sessions index exists)
    sessions_index = load_json(root / "teams" / "sessions" / "index.json")
    if sessions_index:
        indexed_team_files = set()
        for s in sessions_index.get("sessions", []):
            indexed_team_files.add(s.get("filename", ""))
        for fname in team_files_on_disk:
            if fname not in indexed_team_files:
                orphans.append({
                    "type": "team_session",
                    "file": fname,
                    "message": f"Team session file {fname} not in teams/sessions/index.json"
                })

    # Script file orphans
    scripts_dir = root / "storage" / "scripts"
    indexed_script_files = set()
    for entry in scripts_index.get("scripts", []):
        indexed_script_files.add(entry.get("filename", ""))
    if scripts_dir.is_dir():
        for pyfile in scripts_dir.glob("*.py"):
            if pyfile.name not in indexed_script_files and pyfile.name != "__init__.py":
                orphans.append({
                    "type": "script",
                    "file": pyfile.name,
                    "message": f"Script {pyfile.name} not registered in storage/scripts/index.json"
                })

    for orphan in orphans:
        issues.append({
            "level": "warning",
            "type": "orphan",
            **orphan
        })

    # ================================================================
    # 11. Find dangling index entries (index points to missing file)
    # ================================================================
    # KB index -> missing files
    for entry in kb_index.get("entries", []):
        eid = entry.get("id", "")
        expected_file = f"{eid}.md"
        if expected_file not in kb_files_on_disk:
            issues.append({
                "level": "error",
                "type": "dangling_index_entry",
                "index": "knowledge-base/index.json",
                "id": eid,
                "message": f"Index entry {eid} has no corresponding file {expected_file}"
            })

    # Script index -> missing files
    for entry in scripts_index.get("scripts", []):
        fname = entry.get("filename", "")
        fpath = scripts_dir / fname
        if not fpath.exists():
            issues.append({
                "level": "warning",
                "type": "dangling_index_entry",
                "index": "storage/scripts/index.json",
                "id": entry.get("id", ""),
                "filename": fname,
                "message": f"Script index entry {entry.get('id', '')} references {fname} which does not exist"
            })

    # ================================================================
    # 12. Build and print dependency tree
    # ================================================================
    print("=" * 60)
    print("DEPENDENCY TREE: Knowledge Base (builds_on)")
    print("=" * 60)

    # Find root nodes (entries with no builds_on)
    kb_roots = set()
    for kb_id, deps in kb_graph.items():
        if not deps:
            kb_roots.add(kb_id)

    # Also add nodes that are depended upon but have no deps themselves
    all_kb_nodes = set(kb_graph.keys())
    for deps in kb_graph.values():
        for d in deps:
            all_kb_nodes.add(d)

    tree_lines = build_tree_text(kb_graph, kb_roots, all_kb_nodes)
    if tree_lines:
        for line in tree_lines:
            print(line)
    else:
        print("  (no entries)")
    print()

    if script_graph:
        print("=" * 60)
        print("DEPENDENCY TREE: Scripts (dependencies)")
        print("=" * 60)

        # Script roots: scripts with no dependencies
        script_roots = set()
        for sid, deps in script_graph.items():
            if not deps:
                script_roots.add(sid)

        all_script_nodes = set(script_graph.keys())
        script_tree = build_tree_text(script_graph, script_roots, all_script_nodes)
        for line in script_tree:
            print(line)
        print()

    # ================================================================
    # Summary
    # ================================================================
    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]

    report = {
        "checker": "cross_ref_checker",
        "version": "1.0",
        "repository_root": str(root),
        "stats": {
            "kb_entries": len(kb_ids_from_files),
            "kb_index_entries": len(kb_ids_from_index),
            "team_sessions": len(team_file_data),
            "scripts": len(script_ids_from_index),
            "orphans_found": len(orphans),
            "cycles_found": len(kb_cycles) + len(script_cycles),
        },
        "summary": {
            "total_issues": len(issues),
            "errors": len(errors),
            "warnings": len(warnings),
            "status": "PASS" if len(errors) == 0 else "FAIL"
        },
        "issues": issues
    }

    print("=" * 60)
    print("CROSS-REFERENCE REPORT")
    print("=" * 60)
    print(json.dumps(report, indent=2))

    return 0 if len(errors) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
