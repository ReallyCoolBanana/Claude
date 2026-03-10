#!/usr/bin/env python3
"""
build-cross-ref-index.py (SCR-0022)

Phase 2: Builds a master cross-reference index at storage/cross-references.json.
Reads all three index.json files, applies cross-reference heuristics,
and produces a unified relationship map.

Added by: TEAM-0004 (Team Lead 4)
"""

import json
import os
import re
from datetime import datetime

STORAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_INDEX = os.path.join(STORAGE_DIR, "scripts", "index.json")
TOOLS_INDEX = os.path.join(STORAGE_DIR, "api-tools", "index.json")
SOURCES_INDEX = os.path.join(STORAGE_DIR, "sources", "index.json")
CROSS_REF_OUTPUT = os.path.join(STORAGE_DIR, "cross-references.json")


# Mapping of known relationships: source name keywords -> source IDs
# and tool name keywords -> tool IDs. Built by scanning indexes.
def load_indexes():
    with open(SCRIPTS_INDEX) as f:
        scripts = json.load(f)
    with open(TOOLS_INDEX) as f:
        tools = json.load(f)
    with open(SOURCES_INDEX) as f:
        sources = json.load(f)
    return scripts, tools, sources


def build_keyword_map(entries, id_field="id", name_field="name", desc_field="description"):
    """Build a map of significant keywords to entry IDs."""
    keyword_map = {}
    for entry in entries:
        # Extract meaningful keywords from name and description
        text = f"{entry[name_field]} {entry.get(desc_field, '')}"
        # Normalize
        text = text.lower()
        text = re.sub(r'[^a-z0-9\s]', ' ', text)
        words = text.split()
        # Keep words that are likely identifiers (4+ chars, not common words)
        stopwords = {"from", "with", "that", "this", "and", "the", "for", "are",
                      "data", "using", "used", "into", "based", "real", "time",
                      "free", "tier", "more", "over", "http", "https", "json",
                      "api", "apis", "all", "has", "have", "been", "will", "can"}
        significant = [w for w in words if len(w) >= 4 and w not in stopwords]
        for word in significant:
            if word not in keyword_map:
                keyword_map[word] = []
            if entry[id_field] not in keyword_map[word]:
                keyword_map[word].append(entry[id_field])
    return keyword_map


def find_relationships(scripts, tools, sources):
    """Find relationships between entries using multiple heuristic strategies."""

    # Strategy 1: Name/URL matching (tools <-> sources share the same service)
    tool_source_links = []
    for tool in tools["tools"]:
        tool_text = f"{tool['name']} {tool['description']} {tool['endpoint']}".lower()
        for source in sources["sources"]:
            source_text = f"{source['name']} {source['url']}".lower()
            # Extract domain from both
            tool_domain = re.search(r'https?://(?:www\.)?([^/]+)', tool["endpoint"])
            source_domain = re.search(r'https?://(?:www\.)?([^/]+)', source["url"])

            if tool_domain and source_domain:
                # Compare base domains
                td = tool_domain.group(1).split('.')[0] if tool_domain else ""
                sd = source_domain.group(1).split('.')[0] if source_domain else ""
                if td and sd and (td in sd or sd in td) and len(td) > 3:
                    tool_source_links.append({
                        "tool_id": tool["id"],
                        "source_id": source["id"],
                        "confidence": "high",
                        "method": "domain_match",
                        "detail": f"Domains match: {tool_domain.group(1)} ~ {source_domain.group(1)}"
                    })
                    continue

            # Name overlap check
            source_name_parts = re.sub(r'[^a-z0-9\s]', ' ', source["name"].lower()).split()
            for part in source_name_parts:
                if len(part) > 4 and part in tool_text:
                    tool_source_links.append({
                        "tool_id": tool["id"],
                        "source_id": source["id"],
                        "confidence": "medium",
                        "method": "name_overlap",
                        "detail": f"Name keyword '{part}' found in tool"
                    })
                    break

    # Strategy 2: Script -> Tool/Source relationships
    script_tool_links = []
    script_source_links = []

    for script in scripts["scripts"]:
        script_text = f"{script['name']} {script['description']}".lower()

        # Match scripts to tools
        for tool in tools["tools"]:
            tool_name_parts = re.sub(r'[^a-z0-9\s]', ' ', tool["name"].lower()).split()
            significant_parts = [p for p in tool_name_parts if len(p) > 4]
            for part in significant_parts:
                if part in script_text:
                    script_tool_links.append({
                        "script_id": script["id"],
                        "tool_id": tool["id"],
                        "confidence": "medium",
                        "method": "name_overlap",
                        "detail": f"Keyword '{part}' in script description"
                    })
                    break

        # Match scripts to sources
        for source in sources["sources"]:
            source_name_parts = re.sub(r'[^a-z0-9\s]', ' ', source["name"].lower()).split()
            significant_parts = [p for p in source_name_parts if len(p) > 4]
            for part in significant_parts:
                if part in script_text:
                    script_source_links.append({
                        "script_id": script["id"],
                        "source_id": source["id"],
                        "confidence": "medium",
                        "method": "name_overlap",
                        "detail": f"Keyword '{part}' in script description"
                    })
                    break

    return tool_source_links, script_tool_links, script_source_links


def build_cross_ref_index(scripts, tools, sources,
                           tool_source_links, script_tool_links, script_source_links):
    """Build the master cross-reference index."""

    # Build per-entry relationship maps
    script_refs = {}
    for s in scripts["scripts"]:
        script_refs[s["id"]] = {
            "name": s["name"],
            "type": "script",
            "uses_sources": [],
            "uses_tools": [],
        }

    tool_refs = {}
    for t in tools["tools"]:
        tool_refs[t["id"]] = {
            "name": t["name"],
            "type": "tool",
            "used_by_scripts": [],
            "related_sources": [],
        }

    source_refs = {}
    for s in sources["sources"]:
        source_refs[s["id"]] = {
            "name": s["name"],
            "type": "source",
            "consumed_by_scripts": [],
            "wrapped_by_tools": [],
        }

    # Populate from links
    for link in script_source_links:
        sid, srcid = link["script_id"], link["source_id"]
        if srcid not in script_refs[sid]["uses_sources"]:
            script_refs[sid]["uses_sources"].append(srcid)
        if sid not in source_refs[srcid]["consumed_by_scripts"]:
            source_refs[srcid]["consumed_by_scripts"].append(sid)

    for link in script_tool_links:
        sid, tid = link["script_id"], link["tool_id"]
        if tid not in script_refs[sid]["uses_tools"]:
            script_refs[sid]["uses_tools"].append(tid)
        if sid not in tool_refs[tid]["used_by_scripts"]:
            tool_refs[tid]["used_by_scripts"].append(sid)

    for link in tool_source_links:
        tid, srcid = link["tool_id"], link["source_id"]
        if srcid not in tool_refs[tid]["related_sources"]:
            tool_refs[tid]["related_sources"].append(srcid)
        if tid not in source_refs[srcid]["wrapped_by_tools"]:
            source_refs[srcid]["wrapped_by_tools"].append(tid)

    # Build the master index
    cross_ref = {
        "version": "1.0",
        "generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_by": "build-cross-ref-index.py",
        "summary": {
            "total_scripts": len(scripts["scripts"]),
            "total_tools": len(tools["tools"]),
            "total_sources": len(sources["sources"]),
            "total_script_source_links": len(script_source_links),
            "total_script_tool_links": len(script_tool_links),
            "total_tool_source_links": len(tool_source_links),
        },
        "relationships": {
            "script_to_source": script_source_links,
            "script_to_tool": script_tool_links,
            "tool_to_source": tool_source_links,
        },
        "entries": {
            **script_refs,
            **tool_refs,
            **source_refs,
        },
        "orphans": {
            "scripts_no_source": [sid for sid, r in script_refs.items() if not r["uses_sources"]],
            "scripts_no_tool": [sid for sid, r in script_refs.items() if not r["uses_tools"]],
            "tools_no_script": [tid for tid, r in tool_refs.items() if not r["used_by_scripts"]],
            "sources_no_script": [srcid for srcid, r in source_refs.items() if not r["consumed_by_scripts"]],
            "sources_no_tool": [srcid for srcid, r in source_refs.items() if not r["wrapped_by_tools"]],
        }
    }

    return cross_ref


def main():
    print("=" * 70)
    print("PHASE 2: Building Cross-Reference Index")
    print("Team Lead 4 — Sub-Agent Beta")
    print("=" * 70)

    print("\nLoading indexes...")
    scripts, tools, sources = load_indexes()
    print(f"  Scripts: {len(scripts['scripts'])}")
    print(f"  Tools:   {len(tools['tools'])}")
    print(f"  Sources: {len(sources['sources'])}")

    print("\nFinding relationships (heuristic matching)...")
    tool_source_links, script_tool_links, script_source_links = find_relationships(scripts, tools, sources)
    print(f"  Tool <-> Source links: {len(tool_source_links)}")
    print(f"  Script -> Tool links:  {len(script_tool_links)}")
    print(f"  Script -> Source links: {len(script_source_links)}")

    print("\nBuilding cross-reference index...")
    cross_ref = build_cross_ref_index(scripts, tools, sources,
                                       tool_source_links, script_tool_links, script_source_links)

    with open(CROSS_REF_OUTPUT, "w") as f:
        json.dump(cross_ref, f, indent=2)
    print(f"  Written to: {CROSS_REF_OUTPUT}")

    # Print orphan report
    orphans = cross_ref["orphans"]
    print(f"\n  Orphan Report:")
    print(f"    Scripts with no linked source: {len(orphans['scripts_no_source'])}")
    print(f"    Scripts with no linked tool:   {len(orphans['scripts_no_tool'])}")
    print(f"    Tools used by no script:       {len(orphans['tools_no_script'])}")
    print(f"    Sources used by no script:     {len(orphans['sources_no_script'])}")
    print(f"    Sources with no wrapping tool:  {len(orphans['sources_no_tool'])}")

    print("\nPhase 2 complete.\n")


if __name__ == "__main__":
    main()
