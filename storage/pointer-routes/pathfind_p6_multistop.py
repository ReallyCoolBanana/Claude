#!/usr/bin/env python3
"""
Multi-stop route planner (TSP-like) on the pointer network.

Given a set of nodes to visit, finds the optimal visitation order
by computing shortest paths between all pairs and solving TSP
via brute-force (for small sets) or nearest-neighbor heuristic.

Uses minimum-weight Dijkstra for shortest paths (fewest hops, lowest weight
as tiebreaker) since we want EFFICIENT routes between stops, not maximum weight.
"""

import json
import heapq
import subprocess
import sys
import os
from collections import defaultdict
from itertools import permutations
from pathlib import Path
from datetime import datetime

_SCRIPT_DIR = Path(__file__).resolve().parent
NETWORK_PATH = _SCRIPT_DIR.parent / "pointer-network.json"
REPORT_PATH = _SCRIPT_DIR / "pathfind_p6_multistop.json"
ROUTE_TRACKER = _SCRIPT_DIR.parent / "coordination" / "route_tracker.py"


def load_network(path):
    """Load pointer network into adjacency list."""
    with open(path) as f:
        data = json.load(f)

    adj = defaultdict(list)
    node_domains = {}

    for node_id, node_data in data["nodes"].items():
        node_domains[node_id] = node_data.get("domain", "unknown")
        for ptr in node_data.get("pointers", []):
            adj[node_id].append((
                ptr["to"],
                ptr["weight"],
                ptr.get("strength", "unspecified"),
                ptr.get("reasons", [])
            ))

    return adj, node_domains


def shortest_path_dijkstra(adj, source, target):
    """
    Standard Dijkstra for minimum-hop paths.
    Primary metric: number of hops. Secondary: total weight (prefer higher).
    Returns (hops, total_weight, path) or (None, None, None).
    """
    # dist[node] = (hops, -total_weight) -- minimize hops, then maximize weight
    dist = {}
    dist[source] = (0, 0)
    prev = {}
    edge_weights = {}

    # Heap: (hops, -total_weight, node)
    heap = [(0, 0, source)]
    visited = set()

    while heap:
        hops, neg_weight, u = heapq.heappop(heap)
        total_w = -neg_weight

        if u in visited:
            continue
        visited.add(u)

        if u == target:
            break

        for neighbor, edge_weight, strength, reasons in adj[u]:
            new_hops = hops + 1
            new_weight = total_w + edge_weight
            key = (new_hops, -new_weight)
            if neighbor not in dist or key < dist[neighbor]:
                dist[neighbor] = key
                prev[neighbor] = u
                edge_weights[neighbor] = edge_weight
                heapq.heappush(heap, (new_hops, -new_weight, neighbor))

    if target not in prev and source != target:
        return None, None, None

    if source == target:
        return 0, 0, [source]

    # Reconstruct path
    path = []
    node = target
    while node != source:
        path.append(node)
        node = prev[node]
    path.append(source)
    path.reverse()

    total_weight = sum(edge_weights.get(n, 0) for n in path[1:])
    return len(path) - 1, total_weight, path


def compute_pairwise_distances(adj, stops):
    """Compute shortest paths between all pairs of stops."""
    pair_cache = {}
    for i, src in enumerate(stops):
        for j, dst in enumerate(stops):
            if i != j:
                hops, weight, path = shortest_path_dijkstra(adj, src, dst)
                pair_cache[(src, dst)] = {
                    "hops": hops,
                    "weight": weight,
                    "path": path
                }
    return pair_cache


def solve_tsp_bruteforce(stops, pair_cache):
    """
    Solve TSP exactly via brute-force permutation (open path, no return).
    For N stops, tries all (N-1)! orderings fixing the first element,
    or all N! if we want true optimal.
    Returns (best_order, total_hops, total_weight).
    """
    best_order = None
    best_hops = float('inf')
    best_weight = 0

    for perm in permutations(stops):
        total_hops = 0
        total_weight = 0
        valid = True
        for i in range(len(perm) - 1):
            info = pair_cache.get((perm[i], perm[i + 1]))
            if info is None or info["hops"] is None:
                valid = False
                break
            total_hops += info["hops"]
            total_weight += info["weight"]
        if valid and (total_hops < best_hops or
                      (total_hops == best_hops and total_weight > best_weight)):
            best_hops = total_hops
            best_weight = total_weight
            best_order = list(perm)

    return best_order, best_hops, best_weight


def solve_tsp_nearest_neighbor(stops, pair_cache, start=None):
    """Nearest-neighbor heuristic for TSP."""
    if start is None:
        # Try all starting points and pick best
        best = None
        for s in stops:
            order, hops, weight = solve_tsp_nearest_neighbor(stops, pair_cache, start=s)
            if order and (best is None or hops < best[1] or
                          (hops == best[1] and weight > best[2])):
                best = (order, hops, weight)
        return best if best else (None, None, None)

    remaining = set(stops) - {start}
    order = [start]
    total_hops = 0
    total_weight = 0

    current = start
    while remaining:
        best_next = None
        best_h = float('inf')
        best_w = 0
        for nxt in remaining:
            info = pair_cache.get((current, nxt))
            if info and info["hops"] is not None:
                if info["hops"] < best_h or (info["hops"] == best_h and info["weight"] > best_w):
                    best_h = info["hops"]
                    best_w = info["weight"]
                    best_next = nxt
        if best_next is None:
            return None, None, None
        order.append(best_next)
        total_hops += best_h
        total_weight += best_w
        remaining.remove(best_next)
        current = best_next

    return order, total_hops, total_weight


def evaluate_naive_order(stops, pair_cache):
    """Evaluate visiting stops in the given (naive) order."""
    total_hops = 0
    total_weight = 0
    for i in range(len(stops) - 1):
        info = pair_cache.get((stops[i], stops[i + 1]))
        if info is None or info["hops"] is None:
            return None, None
        total_hops += info["hops"]
        total_weight += info["weight"]
    return total_hops, total_weight


def build_full_route(order, pair_cache):
    """Build the full node-by-node route from an optimized stop order."""
    full_path = []
    for i in range(len(order) - 1):
        info = pair_cache[(order[i], order[i + 1])]
        segment = info["path"]
        if i == 0:
            full_path.extend(segment)
        else:
            full_path.extend(segment[1:])  # skip duplicate junction node
    return full_path


def log_route(agent, task, path, outcome, notes):
    """Log route via route_tracker.py."""
    route_str = " -> ".join(path)
    cmd = [
        "python3", ROUTE_TRACKER, "log",
        "--agent", agent,
        "--task", task,
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        print(f"  Logged: {result.stdout.strip()}")
        return result.returncode == 0
    except Exception as e:
        print(f"  Log failed: {e}")
        return False


def plan_multistop(name, stops, adj, pair_cache, node_domains):
    """Plan an optimized multi-stop route and compare with naive ordering."""
    print(f"\n{'='*60}")
    print(f"Route: {name}")
    print(f"Stops: {stops}")

    # Naive order
    naive_hops, naive_weight = evaluate_naive_order(stops, pair_cache)

    # Optimal order (brute force for <= 7 stops, else NN heuristic)
    if len(stops) <= 7:
        opt_order, opt_hops, opt_weight = solve_tsp_bruteforce(stops, pair_cache)
        method = "brute-force"
    else:
        opt_order, opt_hops, opt_weight = solve_tsp_nearest_neighbor(stops, pair_cache)
        method = "nearest-neighbor"

    if opt_order is None:
        print("  Could not find valid route!")
        return None

    full_route = build_full_route(opt_order, pair_cache)

    # Savings
    if naive_hops and naive_hops > 0:
        hop_savings = naive_hops - opt_hops
        hop_savings_pct = round(hop_savings / naive_hops * 100, 1)
    else:
        hop_savings = 0
        hop_savings_pct = 0.0

    # Domain sequence
    domain_seq = [node_domains.get(n, "?") for n in opt_order]

    print(f"  Naive order:     {stops}")
    print(f"  Naive hops:      {naive_hops}, weight: {naive_weight}")
    print(f"  Optimal order:   {opt_order}")
    print(f"  Optimal hops:    {opt_hops}, weight: {opt_weight}")
    print(f"  Full route:      {' -> '.join(full_route)}")
    print(f"  Hop savings:     {hop_savings} hops ({hop_savings_pct}%)")
    print(f"  Method:          {method}")
    print(f"  Domains:         {' -> '.join(domain_seq)}")

    return {
        "name": name,
        "stops": stops,
        "naive_order": stops,
        "naive_hops": naive_hops,
        "naive_weight": naive_weight,
        "optimal_order": opt_order,
        "optimal_hops": opt_hops,
        "optimal_weight": opt_weight,
        "full_route": full_route,
        "full_route_length": len(full_route),
        "hop_savings": hop_savings,
        "hop_savings_pct": hop_savings_pct,
        "method": method,
        "domain_sequence": domain_seq
    }


def main():
    print("Loading pointer network...")
    adj, node_domains = load_network(NETWORK_PATH)
    print(f"Loaded {len(node_domains)} nodes, {sum(len(v) for v in adj.values())} edges")

    # Define multi-stop tasks
    tasks = [
        ("Research workflow", ["SOP-027", "SCR-0043", "SCR-0044", "SRC-0001"]),
        ("Monitoring setup", ["SOP-012", "SCR-0032", "SCR-0019", "SCR-0057"]),
        ("Full audit", ["SOP-011", "SOP-012", "SOP-015", "SOP-028", "SOP-029"]),
        ("Data pipeline", ["SOP-017", "SCR-0023", "SRC-0001", "KB-0001"]),
        ("Cross-domain (one per type)", ["SOP-011", "SCR-0001", "SRC-0001", "KB-0001", "TEAM-0001", "TOOL-0001"]),
        # Additional routes for 15 total
        ("Wiki integration", ["SOP-027", "wiki-ai:AIOps", "SCR-0043", "KB-0042"]),
        ("Script chain", ["SCR-0001", "SCR-0019", "SCR-0032", "SCR-0057"]),
        ("Source survey", ["SRC-0001", "SRC-0005", "SRC-0020", "SRC-0030"]),
        ("Knowledge sweep", ["KB-0001", "KB-0010", "KB-0020", "KB-0030"]),
        ("SOP coverage", ["SOP-013", "SOP-016", "SOP-020", "SOP-025"]),
        ("Cross-team analysis", ["TEAM-0001", "TEAM-0010", "TEAM-0020", "KB-0001"]),
        ("Audit + monitor", ["SOP-011", "SOP-012", "SCR-0032", "SCR-0057", "SOP-015"]),
        ("Full stack trace", ["SOP-017", "SCR-0023", "SRC-0001", "KB-0001", "TEAM-0008"]),
        ("Stress test prep", ["SOP-015", "SCR-0036", "SCR-0037", "KB-0042"]),
        ("Data + wiki", ["SOP-023", "SRC-0001", "wiki-ai:AIOps", "KB-0001"]),
    ]

    # Verify all nodes exist
    all_stops = set()
    for name, stops in tasks:
        all_stops.update(stops)
    missing = [n for n in all_stops if n not in node_domains]
    if missing:
        print(f"WARNING: Missing nodes: {missing}")
        # Remove tasks with missing nodes
        tasks = [(name, stops) for name, stops in tasks
                 if all(s in node_domains for s in stops)]

    # Collect all unique stops and compute pairwise distances
    all_nodes = set()
    for _, stops in tasks:
        all_nodes.update(stops)
    all_nodes = sorted(all_nodes)
    print(f"\nComputing pairwise shortest paths for {len(all_nodes)} unique stops...")
    pair_cache = compute_pairwise_distances(adj, all_nodes)

    # Check reachability
    unreachable = [(s, d) for (s, d), info in pair_cache.items() if info["hops"] is None]
    if unreachable:
        print(f"WARNING: {len(unreachable)} unreachable pairs: {unreachable[:5]}...")

    # Plan all routes
    results = []
    for name, stops in tasks:
        result = plan_multistop(name, stops, adj, pair_cache, node_domains)
        if result:
            results.append(result)

    # Log 15 routes via route_tracker
    print(f"\n{'='*60}")
    print("Logging routes via route_tracker.py...")
    agents = ["p6-1", "p6-2", "p6-3"]
    logged = 0
    for i, r in enumerate(results[:15]):
        agent = agents[i % 3]
        notes = f"stops={len(r['stops'])}, total_weight={r['optimal_weight']}, savings={r['hop_savings_pct']}%"
        ok = log_route(
            agent=agent,
            task="multistop-pathfinding",
            path=r["full_route"],
            outcome="success",
            notes=notes
        )
        if ok:
            logged += 1

    print(f"\nLogged {logged}/15 routes.")

    # Summary statistics
    total_naive_hops = sum(r["naive_hops"] for r in results if r["naive_hops"])
    total_opt_hops = sum(r["optimal_hops"] for r in results)
    total_savings = total_naive_hops - total_opt_hops if total_naive_hops else 0
    avg_savings_pct = round(
        sum(r["hop_savings_pct"] for r in results) / len(results), 1
    ) if results else 0

    summary = {
        "total_routes_planned": len(results),
        "total_naive_hops": total_naive_hops,
        "total_optimized_hops": total_opt_hops,
        "total_hops_saved": total_savings,
        "avg_savings_pct": avg_savings_pct,
        "routes_with_savings": sum(1 for r in results if r["hop_savings"] > 0),
        "routes_no_change": sum(1 for r in results if r["hop_savings"] == 0),
        "max_savings": max((r["hop_savings_pct"] for r in results), default=0),
        "avg_stops_per_route": round(sum(len(r["stops"]) for r in results) / len(results), 1),
        "avg_full_route_length": round(sum(r["full_route_length"] for r in results) / len(results), 1),
    }

    # Build report
    report = {
        "report": "pathfind_p6_multistop",
        "timestamp": datetime.now().isoformat(),
        "algorithm": "Multi-stop TSP planner (brute-force exact for <=7 stops, nearest-neighbor heuristic otherwise)",
        "description": "Plans optimal visitation order for multiple stops on the pointer network, comparing with naive ordering",
        "summary": summary,
        "routes": results,
        "observations": [
            f"Planned {len(results)} multi-stop routes across the pointer network",
            f"Average hop savings from optimization: {avg_savings_pct}%",
            f"Total hops saved: {total_savings} (from {total_naive_hops} naive to {total_opt_hops} optimized)",
            f"{summary['routes_with_savings']}/{len(results)} routes benefited from reordering",
            f"Average route visits {summary['avg_stops_per_route']} stops with {summary['avg_full_route_length']} total nodes traversed",
        ]
    }

    # Write report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {REPORT_PATH}")

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\nObservations:")
    for obs in report["observations"]:
        print(f"  - {obs}")


if __name__ == "__main__":
    main()
