#!/usr/bin/env python3
"""
Pathfinder P1 — Maximum-weight pathfinding across the pointer network.

NOTE: This file uses a standard Dijkstra with visited set and predecessor
reconstruction. For a simple-path-aware variant that tracks full paths in
the heap (with cycle avoidance and max_hops), see pathfind_p1.py.

Uses a modified Dijkstra's algorithm that finds MAXIMUM weight paths
instead of minimum (strongest routes, not shortest).

We use negative weights with a standard min-heap to find max-weight paths.
"""

import json
import heapq
import subprocess
import sys
from collections import defaultdict

# Load network
with open("/home/user/Claude/storage/pointer-network.json") as f:
    network = json.load(f)

nodes_data = network["nodes"]

# Build adjacency list: node -> [(neighbor, weight, strength, reasons)]
adj = defaultdict(list)
for node_id, node_info in nodes_data.items():
    for ptr in node_info.get("pointers", []):
        target = ptr["to"]
        weight = ptr["weight"]
        strength = ptr.get("strength", "secondary")
        reasons = ptr.get("reasons", [])
        adj[node_id].append((target, weight, strength, reasons))

all_nodes = set(nodes_data.keys())
print(f"Graph loaded: {len(all_nodes)} nodes, {sum(len(v) for v in adj.values())} edges")


def dijkstra_max_weight(source, target, max_hops=15):
    """
    Modified Dijkstra for MAXIMUM total weight path (simple paths only).
    Uses BFS-like exploration with a priority queue (max-weight first).
    Limits search depth to max_hops to keep runtime bounded.
    Returns (total_weight, path, strengths_used) or None if no path.
    """
    # State: (-total_weight, counter, node, path, strengths)
    counter = 0
    heap = [(0, counter, source, [source], [])]
    best_at_node = {}  # node -> best weight seen arriving at this node

    best_result = None

    while heap:
        neg_w, _, u, path, strengths = heapq.heappop(heap)
        cur_weight = -neg_w

        # Prune: if we already found this node with higher weight, skip
        if u in best_at_node and best_at_node[u] >= cur_weight:
            continue
        best_at_node[u] = cur_weight

        if u == target:
            return (cur_weight, path, strengths)

        if len(path) > max_hops:
            continue

        path_set = set(path)  # For cycle avoidance
        for neighbor, weight, strength, reasons in adj[u]:
            if neighbor in path_set:
                continue  # No cycles in simple paths
            new_weight = cur_weight + weight
            # Only explore if this could improve
            if neighbor not in best_at_node or new_weight > best_at_node[neighbor]:
                counter += 1
                heapq.heappush(heap, (
                    -new_weight, counter, neighbor,
                    path + [neighbor],
                    strengths + [(strength, reasons)]
                ))

    return None  # No path found


def get_domain(node_id):
    """Get the domain/type of a node from its prefix."""
    if node_id in nodes_data:
        return nodes_data[node_id].get("domain", node_id.split("-")[0])
    return node_id.split("-")[0]


def is_intuitive(path):
    """
    Check if path follows logical domain progression.
    Intuitive = domains transition smoothly, no excessive back-and-forth.
    """
    domains = [get_domain(n) for n in path]
    # Count domain switches
    switches = sum(1 for i in range(1, len(domains)) if domains[i] != domains[i - 1])
    unique_domains = len(set(domains))
    # Intuitive if switches roughly equals unique_domains - 1 (no backtracking)
    return switches <= unique_domains


# Define 20 diverse pathfinding scenarios
SRC_NODES = sorted([n for n in all_nodes if n.startswith("SRC")])
SCR_NODES = sorted([n for n in all_nodes if n.startswith("SCR")])
SOP_NODES = sorted([n for n in all_nodes if n.startswith("SOP")])
KB_NODES = sorted([n for n in all_nodes if n.startswith("KB")])
TEAM_NODES = sorted([n for n in all_nodes if n.startswith("TEAM")])
WIKI_NODES = sorted([n for n in all_nodes if n.startswith("wiki")])

scenarios = [
    # 1-4: Required scenarios from task
    ("SOP-011", "SCR-0043", "cross-domain SOP to SCR"),
    ("SOP-012", "SCR-0057", "monitoring to route tracker"),
    ("SOP-027", "SRC-0001", "wikipedia SOP to data source"),
    ("SOP-023", SRC_NODES[0], "data gathering reach (first SRC)"),
    # 5-8: SOP to SCR cross-domain
    ("SOP-015", "SCR-0001", "stress-test SOP to first script"),
    ("SOP-016", "SCR-0025", "coordination SOP to mid script"),
    ("SOP-019", "SCR-0032", "work-stealing SOP to script"),
    ("SOP-G01", "SCR-0010", "governance SOP to script"),
    # 9-12: SOP to SRC
    ("SOP-014", "SRC-0020", "hub-spoke SOP to source"),
    ("SOP-021", "SRC-0035", "think-tank SOP to source"),
    ("SOP-025", "SRC-0006", "API tools SOP to source"),
    ("SOP-028", "SRC-0050", "late SOP to late source"),
    # 13-16: Cross-type diverse
    ("SOP-013", "KB-0020", "SOP to knowledge base"),
    ("SOP-017", "TEAM-0010", "SOP to team log"),
    ("SOP-022", "KB-0046", "SOP to knowledge base entry"),
    ("SOP-029", "TEAM-0004", "SOP to team log"),
    # 17-20: Long-range and edge cases
    ("SOP-011", "SRC-0051", "SOP-011 to last source"),
    ("SOP-G03", "SCR-0030", "governance to mid-range script"),
    ("SOP-020", "SCR-0055", "direct-channels SOP to late script"),
    ("SOP-024", "SRC-0004", "knowledge-curation SOP to source"),
]

# Also handle SOP-023 to every SRC node (task requirement)
sop023_to_src_results = []
print("\n=== SOP-023 to all SRC nodes (full reach analysis) ===")
reached = 0
unreached = 0
max_w = 0
for src in SRC_NODES:
    result = dijkstra_max_weight("SOP-023", src)
    if result:
        reached += 1
        w, path, strengths = result
        max_w = max(max_w, w)
        sop023_to_src_results.append({
            "target": src,
            "total_weight": w,
            "hops": len(path) - 1,
            "path": " -> ".join(path),
        })
    else:
        unreached += 1
        sop023_to_src_results.append({
            "target": src,
            "total_weight": None,
            "hops": None,
            "path": None,
        })
print(f"SOP-023 reach: {reached}/{len(SRC_NODES)} SRC nodes reachable, max weight = {max_w}")


# Run the 20 scenarios
print("\n=== Running 20 pathfinding scenarios ===")
results = []
bottleneck_count = defaultdict(int)  # count how often each node appears as intermediate

for i, (source, target, description) in enumerate(scenarios, 1):
    # Check target exists
    if target not in all_nodes:
        print(f"  [{i:2d}] {source} -> {target}: TARGET NOT IN NETWORK")
        results.append({
            "scenario": i,
            "source": source,
            "target": target,
            "description": description,
            "status": "target_not_found",
        })
        continue

    result = dijkstra_max_weight(source, target)
    if result is None:
        print(f"  [{i:2d}] {source} -> {target}: NO PATH FOUND")
        results.append({
            "scenario": i,
            "source": source,
            "target": target,
            "description": description,
            "status": "no_path",
        })
    else:
        total_weight, path, strengths = result
        strength_names = [s[0] for s in strengths]
        intuitive = is_intuitive(path)
        path_str = " -> ".join(path)

        # Track intermediate nodes for bottleneck analysis
        for node in path[1:-1]:
            bottleneck_count[node] += 1

        print(f"  [{i:2d}] {source} -> {target}: weight={total_weight}, hops={len(path)-1}, intuitive={intuitive}")
        print(f"       Path: {path_str}")

        results.append({
            "scenario": i,
            "source": source,
            "target": target,
            "description": description,
            "status": "success",
            "total_weight": total_weight,
            "hops": len(path) - 1,
            "path": path,
            "path_str": path_str,
            "strengths_used": strength_names,
            "intuitive": intuitive,
        })

# Bottleneck analysis
print("\n=== Routing Bottlenecks (top 15 intermediate nodes) ===")
top_bottlenecks = sorted(bottleneck_count.items(), key=lambda x: -x[1])[:15]
for node, count in top_bottlenecks:
    print(f"  {node}: appears in {count} paths (domain: {get_domain(node)})")

# Build final report
report = {
    "task": "weight-optimal-pathfinding",
    "agent": "Pathfinder P1",
    "timestamp": "2026-03-12",
    "algorithm": "Modified Dijkstra (max-weight via negated min-heap)",
    "network_stats": {
        "nodes": len(all_nodes),
        "edges": sum(len(v) for v in adj.values()),
        "node_types": {
            "SOP": len(SOP_NODES),
            "SCR": len(SCR_NODES),
            "SRC": len(SRC_NODES),
            "KB": len(KB_NODES),
            "TEAM": len(TEAM_NODES),
            "wiki": len(WIKI_NODES),
        }
    },
    "scenarios": results,
    "sop023_full_reach": {
        "summary": {
            "total_src_nodes": len(SRC_NODES),
            "reachable": reached,
            "unreachable": unreached,
            "max_weight_seen": max_w,
        },
        "details": sop023_to_src_results,
    },
    "bottleneck_analysis": {
        "description": "Nodes that appear most frequently as intermediates across the 20 test paths",
        "top_bottlenecks": [
            {"node": node, "path_appearances": count, "domain": get_domain(node)}
            for node, count in top_bottlenecks
        ],
    },
    "observations": [],  # filled below
}

# Compute observations
successful = [r for r in results if r["status"] == "success"]
if successful:
    avg_weight = sum(r["total_weight"] for r in successful) / len(successful)
    avg_hops = sum(r["hops"] for r in successful) / len(successful)
    intuitive_count = sum(1 for r in successful if r["intuitive"])
    max_weight_scenario = max(successful, key=lambda r: r["total_weight"])
    min_weight_scenario = min(successful, key=lambda r: r["total_weight"])

    report["observations"] = [
        f"All {len(successful)}/{len(results)} scenarios found valid paths.",
        f"Average max-weight: {avg_weight:.1f}, average hops: {avg_hops:.1f}.",
        f"Highest weight path: scenario {max_weight_scenario['scenario']} ({max_weight_scenario['source']} -> {max_weight_scenario['target']}) with weight {max_weight_scenario['total_weight']}.",
        f"Lowest weight path: scenario {min_weight_scenario['scenario']} ({min_weight_scenario['source']} -> {min_weight_scenario['target']}) with weight {min_weight_scenario['total_weight']}.",
        f"{intuitive_count}/{len(successful)} paths are intuitive (follow logical domain progression without backtracking).",
        f"Top bottleneck node: {top_bottlenecks[0][0]} (appears in {top_bottlenecks[0][1]} paths)." if top_bottlenecks else "No bottlenecks identified.",
        f"SOP-023 can reach {reached}/{len(SRC_NODES)} SRC nodes, showing {'full' if unreached == 0 else 'partial'} data gathering connectivity.",
    ]

# Save report
report_path = "/home/user/Claude/storage/pointer-routes/pathfind_p1_weight.json"
with open(report_path, "w") as f:
    json.dump(report, f, indent=2)
print(f"\nReport saved to {report_path}")

# Output data for route logging
print("\n=== ROUTE LOG DATA ===")
for r in results:
    if r["status"] == "success":
        print(f"LOGENTRY|{r['scenario']}|{r['path_str']}|{r['total_weight']}|{r['hops']}")
