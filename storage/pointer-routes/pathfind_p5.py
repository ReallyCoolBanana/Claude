#!/usr/bin/env python3
"""
Pathfinder P5 — A*-style goal-directed pathfinding on the pointer network.

Implements:
  1. A* search with domain-based heuristics
  2. BFS baseline for comparison
  3. Four goal types: find-data-source, find-monitoring-tool, find-knowledge, find-related-sop
  4. Logging via route_tracker.py
"""

import json
import heapq
import subprocess
import sys
from collections import deque, Counter
from pathlib import Path

NETWORK_PATH = Path(__file__).parent.parent / "pointer-network.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"
REPORT_PATH = Path(__file__).parent / "pathfind_p5_goal.json"


def load_network():
    with open(NETWORK_PATH) as f:
        return json.load(f)


def get_domain(node_id):
    """Return domain prefix from node ID."""
    if node_id.startswith("SOP-"):
        return "SOP"
    elif node_id.startswith("SCR-"):
        return "SCR"
    elif node_id.startswith("SRC-"):
        return "SRC"
    elif node_id.startswith("KB-"):
        return "KB"
    elif node_id.startswith("TEAM-"):
        return "TEAM"
    elif node_id.startswith("TOOL-"):
        return "TOOL"
    elif node_id.startswith("wiki"):
        return "WIKI"
    return "UNKNOWN"


def extract_node_tags(node_id, node_data):
    """Extract tags from node data and pointer reasons."""
    tags = set(node_data.get("tags", []))
    for p in node_data.get("pointers", []):
        for r in p.get("reasons", []):
            if r.startswith("shared_tags:"):
                for t in r.split(":", 1)[1].split(","):
                    tags.add(t.strip())
    return tags


# Domain-to-domain heuristic distances (pre-computed estimates)
DOMAIN_HEURISTICS = {
    # From SOP
    ("SOP", "SCR"): 1,
    ("SOP", "SRC"): 2,
    ("SOP", "KB"): 1,
    ("SOP", "TEAM"): 1,
    ("SOP", "WIKI"): 3,
    ("SOP", "SOP"): 2,
    ("SOP", "TOOL"): 2,
    # From SCR
    ("SCR", "SRC"): 1,
    ("SCR", "SOP"): 1,
    ("SCR", "KB"): 1,
    ("SCR", "TEAM"): 1,
    ("SCR", "WIKI"): 2,
    ("SCR", "SCR"): 2,
    ("SCR", "TOOL"): 2,
    # From KB
    ("KB", "SCR"): 1,
    ("KB", "SRC"): 2,
    ("KB", "SOP"): 2,
    ("KB", "TEAM"): 1,
    ("KB", "WIKI"): 2,
    ("KB", "KB"): 1,
    ("KB", "TOOL"): 2,
    # From TEAM
    ("TEAM", "SCR"): 1,
    ("TEAM", "SRC"): 2,
    ("TEAM", "SOP"): 2,
    ("TEAM", "KB"): 1,
    ("TEAM", "WIKI"): 3,
    ("TEAM", "TEAM"): 2,
    ("TEAM", "TOOL"): 2,
    # From SRC
    ("SRC", "SCR"): 1,
    ("SRC", "SOP"): 2,
    ("SRC", "KB"): 1,
    ("SRC", "TEAM"): 2,
    ("SRC", "WIKI"): 3,
    ("SRC", "SRC"): 2,
    ("SRC", "TOOL"): 2,
    # From WIKI
    ("WIKI", "SCR"): 2,
    ("WIKI", "SOP"): 3,
    ("WIKI", "KB"): 2,
    ("WIKI", "TEAM"): 3,
    ("WIKI", "SRC"): 3,
    ("WIKI", "WIKI"): 1,
    ("WIKI", "TOOL"): 3,
    # From TOOL
    ("TOOL", "SCR"): 1,
    ("TOOL", "SOP"): 2,
    ("TOOL", "KB"): 1,
    ("TOOL", "TEAM"): 2,
    ("TOOL", "SRC"): 2,
    ("TOOL", "WIKI"): 3,
    ("TOOL", "TOOL"): 2,
}


def heuristic(current_id, goal_domain):
    """Estimate remaining distance from current node to target domain."""
    cur_domain = get_domain(current_id)
    if cur_domain == goal_domain:
        return 0
    return DOMAIN_HEURISTICS.get((cur_domain, goal_domain), 3)


def edge_cost(pointer):
    """Convert pointer weight to edge cost (higher weight = lower cost)."""
    w = pointer.get("weight", 1)
    # Invert: weight 10 -> cost 1, weight 1 -> cost 10
    return max(1, 11 - w)


def astar_search(network, start, goal_fn, goal_domain, max_nodes=200):
    """
    A* search from start node to any node satisfying goal_fn.

    Returns: (path, nodes_explored, goal_node) or (None, nodes_explored, None)
    """
    nodes = network["nodes"]
    if start not in nodes:
        return None, 0, None

    # Priority queue: (f_score, tie_breaker, node_id, path)
    counter = 0
    open_set = [(heuristic(start, goal_domain), counter, start, [start])]
    g_scores = {start: 0}
    explored = 0

    while open_set and explored < max_nodes:
        f, _, current, path = heapq.heappop(open_set)
        explored += 1

        # Check if current node satisfies the goal
        if current != start and goal_fn(current, nodes.get(current, {})):
            return path, explored, current

        node_data = nodes.get(current, {})
        for ptr in node_data.get("pointers", []):
            neighbor = ptr["to"]
            if neighbor not in nodes:
                continue
            if neighbor in path:  # avoid cycles
                continue

            cost = edge_cost(ptr)
            tentative_g = g_scores[current] + cost

            if neighbor not in g_scores or tentative_g < g_scores[neighbor]:
                g_scores[neighbor] = tentative_g
                h = heuristic(neighbor, goal_domain)
                f_score = tentative_g + h
                counter += 1
                heapq.heappush(open_set, (f_score, counter, neighbor, path + [neighbor]))

    return None, explored, None


def bfs_search(network, start, goal_fn, max_nodes=200):
    """
    BFS from start node to any node satisfying goal_fn.

    Returns: (path, nodes_explored, goal_node) or (None, nodes_explored, None)
    """
    nodes = network["nodes"]
    if start not in nodes:
        return None, 0, None

    queue = deque([(start, [start])])
    visited = {start}
    explored = 0

    while queue and explored < max_nodes:
        current, path = queue.popleft()
        explored += 1

        if current != start and goal_fn(current, nodes.get(current, {})):
            return path, explored, current

        node_data = nodes.get(current, {})
        # Sort by weight descending so BFS explores stronger pointers first
        pointers = sorted(node_data.get("pointers", []),
                          key=lambda p: p.get("weight", 0), reverse=True)
        for ptr in pointers:
            neighbor = ptr["to"]
            if neighbor not in nodes or neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append((neighbor, path + [neighbor]))

    return None, explored, None


# ============================================================
# Goal functions
# ============================================================

def make_goal_find_data_source():
    """Goal: reach any SRC node."""
    def goal_fn(node_id, node_data):
        return get_domain(node_id) == "SRC"
    return goal_fn, "SRC", "find-data-source"


def make_goal_find_monitoring_tool(network):
    """Goal: reach a SCR node with tags containing 'monitoring'."""
    # Pre-compute which SCR nodes have monitoring tags
    monitoring_scrs = set()
    for nid, nd in network["nodes"].items():
        if get_domain(nid) == "SCR":
            tags = extract_node_tags(nid, nd)
            if "monitoring" in tags:
                monitoring_scrs.add(nid)

    def goal_fn(node_id, node_data):
        return node_id in monitoring_scrs
    return goal_fn, "SCR", "find-monitoring-tool"


def make_goal_find_knowledge(query_tags):
    """Goal: reach a KB node related to query tags."""
    query_set = set(query_tags)

    def goal_fn(node_id, node_data):
        if get_domain(node_id) != "KB":
            return False
        tags = extract_node_tags(node_id, node_data)
        return len(tags & query_set) >= 1
    return goal_fn, "KB", f"find-knowledge({','.join(query_tags)})"


def make_goal_find_related_sop(start_sop, network):
    """Goal: reach a different SOP with shared tags."""
    start_data = network["nodes"].get(start_sop, {})
    start_tags = set(start_data.get("tags", []))
    # Remove 'sop' tag as it's not discriminating
    start_tags.discard("sop")

    def goal_fn(node_id, node_data):
        if get_domain(node_id) != "SOP" or node_id == start_sop:
            return False
        other_tags = set(node_data.get("tags", []))
        other_tags.discard("sop")
        return len(start_tags & other_tags) >= 1
    return goal_fn, "SOP", f"find-related-sop(from={start_sop})"


def log_route(agent_id, task, route, outcome, notes):
    """Log via route_tracker.py."""
    route_str = " -> ".join(route)
    cmd = [
        sys.executable, str(ROUTE_TRACKER), "log",
        "--agent", agent_id,
        "--task", task,
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  LOG ERROR: {result.stderr.strip()}")
    else:
        print(f"  Logged: {result.stdout.strip()}")


def run_all_searches():
    """Run 20 goal-directed searches from various starting SOPs."""
    network = load_network()

    # Get all SOP node IDs
    sop_nodes = sorted([nid for nid in network["nodes"] if nid.startswith("SOP-")])

    # Pre-compute monitoring goal
    monitoring_goal_fn, monitoring_domain, _ = make_goal_find_monitoring_tool(network)

    # Define 20 search tasks
    searches = []

    # 5 "find data source" searches
    for sop in ["SOP-011", "SOP-017", "SOP-023", "SOP-028", "SOP-G01"]:
        gf, gd, gt = make_goal_find_data_source()
        searches.append((sop, gf, gd, gt, "p5-1"))

    # 5 "find monitoring tool" searches
    for sop in ["SOP-012", "SOP-015", "SOP-016", "SOP-021", "SOP-029"]:
        searches.append((sop, monitoring_goal_fn, monitoring_domain, "find-monitoring-tool", "p5-2"))

    # 5 "find knowledge" searches with various query tags
    knowledge_queries = [
        ("SOP-013", ["coordination", "pipeline"]),
        ("SOP-014", ["infrastructure", "automation"]),
        ("SOP-019", ["python", "sqlite"]),
        ("SOP-020", ["validation", "bug-fix"]),
        ("SOP-025", ["wikipedia", "enrichment"]),
    ]
    for sop, qtags in knowledge_queries:
        gf, gd, gt = make_goal_find_knowledge(qtags)
        searches.append((sop, gf, gd, gt, "p5-3"))

    # 5 "find related SOP" searches
    for sop in ["SOP-011", "SOP-017", "SOP-022", "SOP-024", "SOP-G03"]:
        gf, gd, gt = make_goal_find_related_sop(sop, network)
        searches.append((sop, gf, gd, gt, "p5-4"))

    results = []
    total_astar_explored = 0
    total_bfs_explored = 0
    total_astar_found = 0
    total_bfs_found = 0
    astar_path_lengths = []
    bfs_path_lengths = []

    print("=" * 80)
    print("PATHFINDER P5 — Goal-Directed A* vs BFS")
    print("=" * 80)

    for i, (start, goal_fn, goal_domain, goal_type, agent) in enumerate(searches, 1):
        print(f"\n--- Search {i}/20: {goal_type} from {start} (agent={agent}) ---")

        # A* search
        astar_path, astar_explored, astar_goal = astar_search(
            network, start, goal_fn, goal_domain
        )

        # BFS search
        bfs_path, bfs_explored, bfs_goal = bfs_search(
            network, start, goal_fn
        )

        astar_found = astar_path is not None
        bfs_found = bfs_path is not None

        total_astar_explored += astar_explored
        total_bfs_explored += bfs_explored
        if astar_found:
            total_astar_found += 1
            astar_path_lengths.append(len(astar_path) - 1)
        if bfs_found:
            total_bfs_found += 1
            bfs_path_lengths.append(len(bfs_path) - 1)

        savings = bfs_explored - astar_explored if bfs_found and astar_found else 0
        savings_pct = round(savings / max(bfs_explored, 1) * 100, 1)

        result = {
            "search_id": i,
            "start": start,
            "goal_type": goal_type,
            "goal_domain": goal_domain,
            "agent": agent,
            "astar": {
                "found": astar_found,
                "path": astar_path,
                "path_length": len(astar_path) - 1 if astar_path else None,
                "nodes_explored": astar_explored,
                "goal_reached": astar_goal
            },
            "bfs": {
                "found": bfs_found,
                "path": bfs_path,
                "path_length": len(bfs_path) - 1 if bfs_path else None,
                "nodes_explored": bfs_explored,
                "goal_reached": bfs_goal
            },
            "comparison": {
                "nodes_saved": savings,
                "savings_pct": savings_pct,
                "path_quality_equal": (
                    astar_path is not None and bfs_path is not None and
                    len(astar_path) == len(bfs_path)
                ) if astar_found and bfs_found else None
            }
        }
        results.append(result)

        # Print summary
        if astar_found:
            print(f"  A*:  {' -> '.join(astar_path)} ({astar_explored} nodes explored)")
        else:
            print(f"  A*:  NOT FOUND ({astar_explored} nodes explored)")

        if bfs_found:
            print(f"  BFS: {' -> '.join(bfs_path)} ({bfs_explored} nodes explored)")
        else:
            print(f"  BFS: NOT FOUND ({bfs_explored} nodes explored)")

        if astar_found and bfs_found:
            print(f"  Savings: {savings} fewer nodes ({savings_pct}%)")
            if len(astar_path) == len(bfs_path):
                print(f"  Path quality: EQUAL (both {len(astar_path)-1} hops)")
            else:
                print(f"  Path quality: A*={len(astar_path)-1} hops, BFS={len(bfs_path)-1} hops")

        # Log route via route_tracker
        if astar_found:
            outcome = "success"
            route = astar_path
        else:
            outcome = "failure"
            route = [start]

        notes = f"goal={goal_type}, astar_explored={astar_explored}, bfs_explored={bfs_explored}"
        if astar_found and bfs_found:
            notes += f", savings={savings_pct}%"

        log_route(agent, "goal-pathfinding", route, outcome, notes)

    # Summary statistics
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    avg_astar = round(total_astar_explored / 20, 1)
    avg_bfs = round(total_bfs_explored / 20, 1)
    overall_savings = total_bfs_explored - total_astar_explored
    overall_savings_pct = round(overall_savings / max(total_bfs_explored, 1) * 100, 1)

    print(f"A* found goal: {total_astar_found}/20")
    print(f"BFS found goal: {total_bfs_found}/20")
    print(f"Total A* nodes explored: {total_astar_explored} (avg {avg_astar}/search)")
    print(f"Total BFS nodes explored: {total_bfs_explored} (avg {avg_bfs}/search)")
    print(f"Overall node savings: {overall_savings} ({overall_savings_pct}%)")

    if astar_path_lengths:
        print(f"Avg A* path length: {round(sum(astar_path_lengths)/len(astar_path_lengths), 2)} hops")
    if bfs_path_lengths:
        print(f"Avg BFS path length: {round(sum(bfs_path_lengths)/len(bfs_path_lengths), 2)} hops")

    equal_quality = sum(1 for r in results
                        if r["comparison"]["path_quality_equal"] is True)
    both_found = sum(1 for r in results
                     if r["astar"]["found"] and r["bfs"]["found"])
    print(f"Path quality equal: {equal_quality}/{both_found} cases where both found")

    summary = {
        "total_searches": 20,
        "astar_success_rate": total_astar_found,
        "bfs_success_rate": total_bfs_found,
        "total_astar_nodes_explored": total_astar_explored,
        "total_bfs_nodes_explored": total_bfs_explored,
        "avg_astar_explored": avg_astar,
        "avg_bfs_explored": avg_bfs,
        "overall_node_savings": overall_savings,
        "overall_savings_pct": overall_savings_pct,
        "avg_astar_path_length": round(sum(astar_path_lengths) / max(len(astar_path_lengths), 1), 2),
        "avg_bfs_path_length": round(sum(bfs_path_lengths) / max(len(bfs_path_lengths), 1), 2),
        "path_quality_equal_count": equal_quality,
        "both_found_count": both_found,
    }

    # Build report
    report = {
        "experiment": "pathfinder-p5-goal-directed",
        "description": "A*-style goal-directed pathfinding with domain heuristics vs BFS baseline",
        "network_stats": {
            "total_nodes": len(network["nodes"]),
            "total_edges": network.get("stats", {}).get("total_edges", 0)
        },
        "heuristics_used": {
            "type": "domain-distance",
            "values": {f"{k[0]}->{k[1]}": v for k, v in DOMAIN_HEURISTICS.items()}
        },
        "goal_types": [
            {"name": "find-data-source", "target_domain": "SRC", "count": 5},
            {"name": "find-monitoring-tool", "target_domain": "SCR", "filter": "tags contain 'monitoring'", "count": 5},
            {"name": "find-knowledge", "target_domain": "KB", "filter": "tags overlap with query", "count": 5},
            {"name": "find-related-sop", "target_domain": "SOP", "filter": "shared tags with start SOP", "count": 5},
        ],
        "summary": summary,
        "searches": results
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to: {REPORT_PATH}")
    return report


if __name__ == "__main__":
    run_all_searches()
