#!/usr/bin/env python3
"""
Stress Team S2 — Script node fan-out analysis.
Computes direct, 2-hop, and 3-hop reach for all SCR nodes.
Simulates journeys from top hub scripts and logs via route_tracker.
Writes final report to storage/pointer-routes/stress_s2_report.json.
"""

import json
import subprocess
import sys
from pathlib import Path
from collections import deque

BASE = Path(__file__).resolve().parent.parent
POINTER_NETWORK = BASE / "pointer-network.json"
ROUTE_TRACKER = BASE / "coordination" / "route_tracker.py"
REPORT_PATH = BASE / "pointer-routes" / "stress_s2_report.json"

def load_network():
    with open(POINTER_NETWORK) as f:
        return json.load(f)

def build_adjacency(pn):
    """Build adjacency list: node -> set of direct targets."""
    adj = {}
    for node_id, node_data in pn["nodes"].items():
        targets = set()
        for ptr in node_data.get("pointers", []):
            targets.add(ptr["to"])
        adj[node_id] = targets
    return adj

def compute_reach(adj, start, max_hops):
    """BFS from start up to max_hops. Returns set of reachable nodes (excluding start)."""
    visited = {start}
    frontier = {start}
    for _ in range(max_hops):
        next_frontier = set()
        for node in frontier:
            for neighbor in adj.get(node, set()):
                if neighbor not in visited:
                    visited.add(neighbor)
                    next_frontier.add(neighbor)
        frontier = next_frontier
        if not frontier:
            break
    visited.discard(start)
    return visited

def get_path_bfs(adj, start, depth=3):
    """Get a concrete path from start going depth hops, preferring high-degree nodes."""
    path = [start]
    current = start
    visited = {start}
    for _ in range(depth):
        neighbors = adj.get(current, set()) - visited
        if not neighbors:
            break
        # Pick neighbor with highest out-degree for interesting paths
        best = max(neighbors, key=lambda n: len(adj.get(n, set())))
        path.append(best)
        visited.add(best)
        current = best
    return path

def main():
    print("Loading pointer network...")
    pn = load_network()
    adj = build_adjacency(pn)

    # Identify all SCR nodes
    scr_nodes = sorted([n for n in pn["nodes"] if n.startswith("SCR-")])
    print(f"Found {len(scr_nodes)} SCR nodes")

    # Step 1: Compute reach for all SCR nodes
    print("Computing reach for all SCR nodes...")
    per_script_reach = {}
    for scr in scr_nodes:
        r1 = compute_reach(adj, scr, 1)
        r2 = compute_reach(adj, scr, 2)
        r3 = compute_reach(adj, scr, 3)
        per_script_reach[scr] = {
            "direct": len(r1),
            "2hop": len(r2),
            "3hop": len(r3)
        }

    # Step 2: Find hubs and dead-ends
    sorted_by_3hop = sorted(per_script_reach.items(), key=lambda x: x[1]["3hop"], reverse=True)
    hub_scripts = [{"id": k, **v} for k, v in sorted_by_3hop[:10]]
    dead_end_scripts = [{"id": k, **v} for k, v in sorted_by_3hop[-10:]]

    print(f"\nTop 10 hub scripts (by 3-hop reach):")
    for h in hub_scripts:
        print(f"  {h['id']}: direct={h['direct']}, 2hop={h['2hop']}, 3hop={h['3hop']}")

    print(f"\nBottom 10 dead-end scripts (by 3-hop reach):")
    for d in dead_end_scripts:
        print(f"  {d['id']}: direct={d['direct']}, 2hop={d['2hop']}, 3hop={d['3hop']}")

    # Step 3: Simulate 15 journeys from top 5 hub scripts (3 each)
    print("\nSimulating 15 journeys...")
    top5 = [item["id"] for item in hub_scripts[:5]]
    journey_results = []

    for i, hub in enumerate(top5):
        agent_id = f"s2-{i+1}"
        for j in range(3):
            # Get a path, vary by using different strategies
            if j == 0:
                path = get_path_bfs(adj, hub, depth=3)
            elif j == 1:
                # Try a different path by removing first-choice neighbors
                temp_adj = {k: set(v) for k, v in adj.items()}
                if hub in temp_adj and len(temp_adj[hub]) > 1:
                    # Remove the highest-degree neighbor to force alternate path
                    best = max(temp_adj[hub], key=lambda n: len(adj.get(n, set())))
                    temp_adj[hub].discard(best)
                path = get_path_bfs(temp_adj, hub, depth=3)
            else:
                # Third journey: pick lowest-degree neighbor first
                path = [hub]
                current = hub
                visited = {hub}
                for _ in range(3):
                    neighbors = adj.get(current, set()) - visited
                    if not neighbors:
                        break
                    worst = min(neighbors, key=lambda n: len(adj.get(n, set())))
                    path.append(worst)
                    visited.add(worst)
                    current = worst

            reach_3hop = per_script_reach[hub]["3hop"]
            route_str = " -> ".join(path)
            outcome = "success" if len(path) >= 3 else "partial"

            print(f"  Journey {i*3+j+1}: agent={agent_id}, route={route_str}, reach={reach_3hop}")

            # Log via route_tracker.py
            cmd = [
                sys.executable, str(ROUTE_TRACKER), "log",
                "--agent", agent_id,
                "--task", "fanout-stress",
                "--route", route_str,
                "--outcome", outcome,
                "--notes", f"reach={reach_3hop}"
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            print(f"    -> {result.stdout.strip()}")
            if result.returncode != 0 and result.stderr:
                print(f"    ERR: {result.stderr.strip()}")

            journey_results.append({
                "agent": agent_id,
                "hub": hub,
                "journey": j+1,
                "route": path,
                "outcome": outcome,
                "reach_3hop": reach_3hop
            })

    # Step 4: Compute averages
    all_direct = [v["direct"] for v in per_script_reach.values()]
    all_2hop = [v["2hop"] for v in per_script_reach.values()]
    all_3hop = [v["3hop"] for v in per_script_reach.values()]
    n = len(scr_nodes)

    avg_reach = {
        "avg_direct": round(sum(all_direct) / n, 2),
        "avg_2hop": round(sum(all_2hop) / n, 2),
        "avg_3hop": round(sum(all_3hop) / n, 2),
        "min_direct": min(all_direct),
        "max_direct": max(all_direct),
        "min_3hop": min(all_3hop),
        "max_3hop": max(all_3hop)
    }

    # Recommendations: scripts that need more connections
    # Threshold: direct reach below average, or 3-hop reach in bottom quartile
    avg_d = avg_reach["avg_direct"]
    q25_3hop = sorted(all_3hop)[len(all_3hop) // 4]
    recommendations = []
    for scr, reach in per_script_reach.items():
        reasons = []
        if reach["direct"] < avg_d * 0.5:
            reasons.append(f"very low direct reach ({reach['direct']} vs avg {avg_d})")
        if reach["3hop"] <= q25_3hop:
            reasons.append(f"3-hop reach in bottom quartile ({reach['3hop']} <= {q25_3hop})")
        if reasons:
            recommendations.append({
                "script": scr,
                "current_reach": reach,
                "reasons": reasons
            })
    recommendations.sort(key=lambda x: x["current_reach"]["3hop"])

    # Write report
    report = {
        "stress_test": "S2 — Script Node Fan-Out",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "total_scr_nodes": len(scr_nodes),
        "per_script_reach": per_script_reach,
        "hub_scripts": hub_scripts,
        "dead_end_scripts": dead_end_scripts,
        "avg_reach": avg_reach,
        "journeys_simulated": journey_results,
        "recommendations": recommendations
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to {REPORT_PATH}")
    print(f"Network averages: direct={avg_reach['avg_direct']}, 2hop={avg_reach['avg_2hop']}, 3hop={avg_reach['avg_3hop']}")
    print(f"Scripts needing more connections: {len(recommendations)}")

if __name__ == "__main__":
    main()
