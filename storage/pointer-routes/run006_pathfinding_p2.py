#!/usr/bin/env python3
"""
P2 Strength-Priority Dijkstra Pathfinding

Two algorithms compared on 20 scenarios (10 SOPs x 2 targets each):
1. Strength-priority Dijkstra: edge cost by strength (primary=0, supporting=1, related=2,
   tangential=3, unclassified=4), tiebreaker: prefer higher weight
2. Max-weight baseline: pure Dijkstra maximizing total edge weight (negate weights as cost)

Each scenario: SOP -> 1 SCR target + 1 SRC target
"""

import json
import heapq
import subprocess
import sys
import random
import gc
from pathlib import Path
from datetime import datetime, timezone

OUTPUT = Path(__file__).parent / "run006_pathfinding_p2.json"
POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"

STRENGTH_COST = {
    "primary": 0,
    "supporting": 1,
    "related": 2,
    "tangential": 3,
    "unclassified": 4,
}


def load_adjacency_and_scenarios():
    """Load network, build adjacency, pick scenarios, then free the raw JSON."""
    with open(POINTER_NETWORK) as f:
        pn = json.load(f)

    # Build adjacency: {node: [(neighbor, strength_cost, weight), ...]}
    adj = {}
    for node_id, node_data in pn["nodes"].items():
        edges = []
        for ptr in node_data.get("pointers", []):
            s = STRENGTH_COST.get(ptr.get("strength", "unclassified"), 4)
            edges.append((ptr["to"], s, ptr.get("weight", 0)))
        adj[node_id] = edges

    # Build edge lookup for cross-metric computation
    edge_lookup = {}
    for node_id, edges in adj.items():
        for neighbor, s_cost, weight in edges:
            edge_lookup[(node_id, neighbor)] = (s_cost, weight)

    # Pick scenarios
    sops = sorted([n for n in pn["nodes"] if n.startswith("SOP-")])[:10]
    scrs = sorted([n for n in pn["nodes"] if n.startswith("SCR-")])
    srcs = sorted([n for n in pn["nodes"] if n.startswith("SRC-")])

    random.seed(42)
    scenarios = []
    for sop in sops:
        scenarios.append((sop, random.choice(scrs)))
        scenarios.append((sop, random.choice(srcs)))

    # Free the large raw JSON
    del pn
    gc.collect()

    return adj, edge_lookup, scenarios


def dijkstra_strength(adj, start, target):
    """Strength-priority Dijkstra. Cost: (strength_cost, -weight). Returns (path, cost)."""
    dist = {start: (0, 0)}
    prev = {start: None}
    heap = [(0, 0, 0, start)]
    visited = set()

    while heap:
        cost_s, cost_nw, hops, node = heapq.heappop(heap)
        if node == target:
            path = []
            cur = target
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return list(reversed(path)), cost_s
        if node in visited:
            continue
        visited.add(node)
        for neighbor, s_cost, weight in adj.get(node, []):
            new_cs = cost_s + s_cost
            new_cnw = cost_nw - weight
            new_key = (new_cs, new_cnw)
            old = dist.get(neighbor)
            if old is None or new_key < old:
                dist[neighbor] = new_key
                prev[neighbor] = node
                heapq.heappush(heap, (new_cs, new_cnw, hops + 1, neighbor))
    return None, None


def dijkstra_max_weight(adj, start, target):
    """Max-weight Dijkstra. Cost: -weight. Returns (path, total_weight)."""
    dist = {start: 0}
    prev = {start: None}
    heap = [(0, 0, start)]
    visited = set()

    while heap:
        neg_w, hops, node = heapq.heappop(heap)
        if node == target:
            path = []
            cur = target
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return list(reversed(path)), -neg_w
        if node in visited:
            continue
        visited.add(node)
        for neighbor, s_cost, weight in adj.get(node, []):
            new_neg_w = neg_w - weight
            old = dist.get(neighbor)
            if old is None or new_neg_w < old:
                dist[neighbor] = new_neg_w
                prev[neighbor] = node
                heapq.heappush(heap, (new_neg_w, hops + 1, neighbor))
    return None, None


def path_strength_cost(edge_lookup, path):
    total = 0
    for i in range(len(path) - 1):
        sc, _ = edge_lookup.get((path[i], path[i+1]), (4, 0))
        total += sc
    return total


def path_weight(edge_lookup, path):
    total = 0
    for i in range(len(path) - 1):
        _, w = edge_lookup.get((path[i], path[i+1]), (4, 0))
        total += w
    return total


def main():
    print("Loading pointer network and building adjacency...")
    adj, edge_lookup, scenarios = load_adjacency_and_scenarios()
    print(f"Network: {len(adj)} nodes, {len(edge_lookup)} edges")
    print(f"Running {len(scenarios)} scenarios...\n")

    results = []
    routes_to_log = []
    strength_wins = 0
    weight_wins = 0
    ties = 0
    total_strength_hops = 0
    total_weight_hops = 0
    total_strength_score = 0
    reachable_count = 0

    for i, (start, target) in enumerate(scenarios):
        print(f"Scenario {i+1}: {start} -> {target}")

        s_path, s_cost = dijkstra_strength(adj, start, target)
        w_path, w_total = dijkstra_max_weight(adj, start, target)

        if s_path is None and w_path is None:
            print(f"  UNREACHABLE")
            results.append({
                "scenario": i + 1, "start": start, "target": target,
                "reachable": False, "strength_path": None, "weight_path": None,
                "strength_cost": None, "strength_hops": None, "weight_hops": None,
                "strength_total_weight": None, "weight_total_weight": None,
                "comparison": "unreachable"
            })
            continue

        reachable_count += 1
        s_hops = len(s_path) - 1 if s_path else None
        w_hops = len(w_path) - 1 if w_path else None
        s_weight = path_weight(edge_lookup, s_path) if s_path else 0
        w_str_cost = path_strength_cost(edge_lookup, w_path) if w_path else None

        if s_path and w_path:
            if s_cost < w_str_cost:
                comparison = "strength_wins"
                strength_wins += 1
            elif s_cost > w_str_cost:
                comparison = "weight_wins"
                weight_wins += 1
            else:
                comparison = "tie"
                ties += 1
        elif s_path:
            comparison = "strength_only"
            strength_wins += 1
        else:
            comparison = "weight_only"
            weight_wins += 1

        if s_hops is not None:
            total_strength_hops += s_hops
            total_strength_score += s_cost
        if w_hops is not None:
            total_weight_hops += w_hops

        s_route_str = " -> ".join(s_path) if s_path else "NONE"
        w_route_str = " -> ".join(w_path) if w_path else "NONE"

        print(f"  Strength: {s_route_str} (cost={s_cost}, hops={s_hops}, weight={s_weight})")
        print(f"  Weight:   {w_route_str} (weight={w_total}, hops={w_hops}, str_cost={w_str_cost})")
        print(f"  Winner:   {comparison}")

        if s_path:
            routes_to_log.append((s_route_str, s_hops))
        if w_path:
            routes_to_log.append((w_route_str, w_hops))

        results.append({
            "scenario": i + 1, "start": start, "target": target,
            "reachable": True,
            "strength_path": s_path, "weight_path": w_path,
            "strength_cost": s_cost, "strength_hops": s_hops,
            "strength_total_weight": s_weight,
            "weight_total_weight": w_total, "weight_hops": w_hops,
            "weight_strength_cost": w_str_cost,
            "comparison": comparison
        })

    # Summary
    total = len(scenarios)
    summary = {
        "total_scenarios": total,
        "reachable": reachable_count,
        "unreachable": total - reachable_count,
        "strength_wins": strength_wins,
        "weight_wins": weight_wins,
        "ties": ties,
        "strength_win_rate": round(strength_wins / max(reachable_count, 1) * 100, 1),
        "weight_win_rate": round(weight_wins / max(reachable_count, 1) * 100, 1),
        "tie_rate": round(ties / max(reachable_count, 1) * 100, 1),
        "avg_strength_hops": round(total_strength_hops / max(reachable_count, 1), 2),
        "avg_weight_hops": round(total_weight_hops / max(reachable_count, 1), 2),
        "avg_strength_score": round(total_strength_score / max(reachable_count, 1), 2),
    }

    output = {
        "run": "run006_pathfinding_p2",
        "generated": datetime.now(timezone.utc).isoformat(),
        "agent": "p2-helper-1",
        "description": "Strength-priority Dijkstra vs max-weight baseline on 20 SOP->SCR/SRC scenarios",
        "algorithms": {
            "strength_priority": {
                "description": "Dijkstra with edge cost by strength classification",
                "costs": {"primary": 0, "supporting": 1, "related": 2, "tangential": 3, "unclassified": 4},
                "tiebreaker": "prefer higher edge weight"
            },
            "max_weight": {
                "description": "Pure Dijkstra maximizing total path weight",
                "tiebreaker": "prefer fewer hops"
            }
        },
        "summary": summary,
        "scenarios": results
    }

    with open(OUTPUT, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Scenarios: {total} ({reachable_count} reachable)")
    print(f"Strength wins: {strength_wins} ({summary['strength_win_rate']}%)")
    print(f"Weight wins:   {weight_wins} ({summary['weight_win_rate']}%)")
    print(f"Ties:          {ties} ({summary['tie_rate']}%)")
    print(f"Avg strength hops: {summary['avg_strength_hops']}")
    print(f"Avg weight hops:   {summary['avg_weight_hops']}")
    print(f"Avg strength score: {summary['avg_strength_score']}")
    print(f"\nResults written to {OUTPUT}")

    # Now batch-log routes (each spawns subprocess that loads the JSON)
    print(f"\nLogging {len(routes_to_log)} routes...")
    # Free adj before logging to reduce memory during subprocess calls
    del adj, edge_lookup
    gc.collect()

    for route_str, hops in routes_to_log:
        try:
            result = subprocess.run(
                [sys.executable, str(ROUTE_TRACKER), "log",
                 "--agent", "p2-helper-1", "--task", "strength-pathfinding",
                 "--route", route_str, "--outcome", "success"],
                capture_output=True, text=True, timeout=30
            )
            print(f"  Logged ({hops} hops): {result.stdout.strip()}")
        except Exception as e:
            print(f"  Log error: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
