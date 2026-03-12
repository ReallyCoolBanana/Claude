#!/usr/bin/env python3
"""
Run-006 Division 4B: Pathfinding
Agents P7-1, P7-2 (failure-aware routing) and P8-1, P8-2 (exploration pathfinding)
"""

import json
import math
import heapq
import subprocess
import sys
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime, timezone

NETWORK_PATH = Path("/home/user/Claude/storage/pointer-network.json")
ROUTE_LOG_PATH = Path("/home/user/Claude/storage/pointer-routes/route_log.jsonl")
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"
OUTPUT_PATH = Path("/home/user/Claude/storage/pointer-routes/run006_pathfinding_p7_p8.json")

DEFAULT_RELIABILITY = 0.8

# ============================================================
# Data loading
# ============================================================

def load_network():
    with open(NETWORK_PATH) as f:
        net = json.load(f)
    # Build adjacency: node -> [(neighbor, weight, strength, reasons)]
    adj = defaultdict(list)
    all_nodes = set(net["nodes"].keys())
    for node_id, node_data in net["nodes"].items():
        for ptr in node_data.get("pointers", []):
            to = ptr["to"]
            w = ptr.get("weight", 1)
            s = ptr.get("strength", "unclassified")
            r = ptr.get("reasons", [])
            adj[node_id].append((to, w, s, r))
    return adj, all_nodes, net


def load_route_log():
    entries = []
    with open(ROUTE_LOG_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


# ============================================================
# P7: Edge reliability from historical data
# ============================================================

def build_edge_reliability(route_log):
    """Build reliability scores per directed edge from route history."""
    edge_success = Counter()
    edge_total = Counter()

    for entry in route_log:
        outcome = entry.get("outcome", "unknown")
        hops = entry.get("hops", [])
        route = entry.get("route", [])

        if len(route) < 2:
            continue

        # Each hop in a successful route is a success; in a failed route, last hop is failure
        for i, hop in enumerate(hops):
            fr = hop.get("from", "")
            to = hop.get("to", "")
            if not fr or not to:
                continue
            edge = (fr, to)
            edge_total[edge] += 1
            if outcome == "success":
                edge_success[edge] += 1
            elif outcome == "failure":
                # Only the last hop is the failure point
                if i < len(hops) - 1:
                    edge_success[edge] += 1
                # else: failure on this edge, don't count as success
            elif outcome == "partial":
                # Partial: earlier hops succeeded
                if i < len(hops) - 1:
                    edge_success[edge] += 1

    reliability = {}
    for edge in edge_total:
        total = edge_total[edge]
        success = edge_success[edge]
        reliability[edge] = success / total if total > 0 else DEFAULT_RELIABILITY

    return reliability, edge_total


def failure_aware_dijkstra(adj, all_nodes, source, target, reliability):
    """
    Dijkstra where cost = -log(reliability).
    This finds the path maximizing the product of edge reliabilities.
    Unseen edges get DEFAULT_RELIABILITY.
    """
    # cost[node] = min -log(product of reliabilities) = sum of -log(reliability)
    dist = {n: float('inf') for n in all_nodes}
    dist[source] = 0.0
    prev = {n: None for n in all_nodes}
    prev_edge_info = {}
    visited = set()
    heap = [(0.0, source)]

    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        if u == target:
            break

        for (v, weight, strength, reasons) in adj.get(u, []):
            if v in visited:
                continue
            edge = (u, v)
            rel = reliability.get(edge, DEFAULT_RELIABILITY)
            # Clamp reliability to avoid log(0)
            rel = max(rel, 0.01)
            cost = -math.log(rel)
            new_dist = d + cost
            if new_dist < dist.get(v, float('inf')):
                dist[v] = new_dist
                prev[v] = u
                prev_edge_info[(u, v)] = (weight, strength, reasons)
                heapq.heappush(heap, (new_dist, v))

    # Reconstruct path
    if dist.get(target, float('inf')) == float('inf'):
        return None, float('inf'), []

    path = []
    node = target
    while node is not None:
        path.append(node)
        node = prev[node]
    path.reverse()

    # Collect hop info
    hops = []
    for i in range(len(path) - 1):
        u, v = path[i], path[i+1]
        info = prev_edge_info.get((u, v), (0, "unknown", []))
        edge = (u, v)
        rel = reliability.get(edge, DEFAULT_RELIABILITY)
        hops.append({
            "from": u, "to": v,
            "weight": info[0], "strength": info[1],
            "reasons": info[2], "reliability": round(rel, 4)
        })

    total_reliability = math.exp(-dist[target]) if dist[target] < float('inf') else 0
    return path, total_reliability, hops


def naive_max_weight_dijkstra(adj, all_nodes, source, target):
    """
    Naive routing: maximize total weight (use negative weight as cost).
    """
    dist = {n: float('inf') for n in all_nodes}
    dist[source] = 0.0
    prev = {n: None for n in all_nodes}
    prev_edge_info = {}
    visited = set()
    heap = [(0.0, source)]

    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        if u == target:
            break

        for (v, weight, strength, reasons) in adj.get(u, []):
            if v in visited:
                continue
            # Cost = 1/(weight+1) to prefer high-weight edges
            cost = 1.0 / (weight + 1)
            new_dist = d + cost
            if new_dist < dist.get(v, float('inf')):
                dist[v] = new_dist
                prev[v] = u
                prev_edge_info[(u, v)] = (weight, strength, reasons)
                heapq.heappush(heap, (new_dist, v))

    if dist.get(target, float('inf')) == float('inf'):
        return None, 0, []

    path = []
    node = target
    while node is not None:
        path.append(node)
        node = prev[node]
    path.reverse()

    hops = []
    total_weight = 0
    for i in range(len(path) - 1):
        u, v = path[i], path[i+1]
        info = prev_edge_info.get((u, v), (0, "unknown", []))
        hops.append({"from": u, "to": v, "weight": info[0], "strength": info[1], "reasons": info[2]})
        total_weight += info[0]

    return path, total_weight, hops


# ============================================================
# P8: Exploration pathfinding
# ============================================================

def build_visit_frequency(route_log):
    """Build visit frequency map from route log."""
    freq = Counter()
    for entry in route_log:
        for node in entry.get("route", []):
            freq[node] += 1
    return freq


def exploration_routing(adj, all_nodes, source, visit_freq, max_hops=8):
    """
    Greedy exploration: at each step, pick the neighbor with lowest visit count.
    Ties broken by preferring nodes not in current path, then by weight.
    """
    path = [source]
    visited_in_path = {source}
    hops = []

    for _ in range(max_hops):
        current = path[-1]
        neighbors = adj.get(current, [])
        if not neighbors:
            break

        # Score: prefer least visited nodes not already in path
        candidates = []
        for (v, weight, strength, reasons) in neighbors:
            if v in visited_in_path:
                continue
            freq = visit_freq.get(v, 0)
            # Primary sort: visit frequency (ascending), secondary: -weight (descending)
            candidates.append((freq, -weight, v, weight, strength, reasons))

        if not candidates:
            # All neighbors visited in this path, allow revisit of globally least visited
            for (v, weight, strength, reasons) in neighbors:
                if v == path[-1]:
                    continue
                freq = visit_freq.get(v, 0)
                candidates.append((freq, -weight, v, weight, strength, reasons))

        if not candidates:
            break

        candidates.sort()
        best = candidates[0]
        _, _, next_node, weight, strength, reasons = best

        hops.append({
            "from": current, "to": next_node,
            "weight": weight, "strength": strength,
            "reasons": reasons,
            "target_visit_count": visit_freq.get(next_node, 0)
        })

        path.append(next_node)
        visited_in_path.add(next_node)
        # Update frequency for tracking during this run
        visit_freq[next_node] = visit_freq.get(next_node, 0) + 1

    return path, hops


# ============================================================
# Route logging helper
# ============================================================

def log_route(agent, task, route, outcome, hops):
    route_str = " -> ".join(route)
    cmd = [
        "python3", ROUTE_TRACKER, "log",
        "--agent", agent,
        "--task", task,
        "--route", route_str,
        "--outcome", outcome,
        "--hops", str(hops)
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=10)
    except Exception as e:
        print(f"  [warn] route log failed for {agent}: {e}", file=sys.stderr)


# ============================================================
# Main execution
# ============================================================

def main():
    print("Loading network and route log...")
    adj, all_nodes, net = load_network()
    route_log = load_route_log()

    sops = sorted([n for n in all_nodes if n.startswith("SOP-")])
    # Pick 10 SOPs for P7 scenarios
    sop_10 = sops[:10]

    # Non-SOP target nodes (pick diverse ones)
    non_sop = sorted([n for n in all_nodes if not n.startswith("SOP-")])
    # Pick targets spread across domains
    kb_targets = [n for n in non_sop if n.startswith("KB-")]
    team_targets = [n for n in non_sop if n.startswith("TEAM-")]
    scr_targets = [n for n in non_sop if n.startswith("SCR-")]
    src_targets = [n for n in non_sop if n.startswith("SRC-")]

    # 2 targets per SOP = 20 scenarios
    target_pool = []
    for i in range(10):
        # Alternate between KB/TEAM and SCR/SRC targets
        t1 = kb_targets[i % len(kb_targets)] if i % 2 == 0 else team_targets[i % len(team_targets)]
        t2 = scr_targets[i % len(scr_targets)] if i % 2 == 0 else src_targets[i % len(src_targets)]
        target_pool.append((t1, t2))

    # ============================================================
    # P7: Failure-aware routing
    # ============================================================
    print("\n=== P7-1 & P7-2: Failure-Aware Routing ===")
    reliability, edge_counts = build_edge_reliability(route_log)

    print(f"  Edges with history: {len(reliability)}")
    print(f"  Default reliability for unseen: {DEFAULT_RELIABILITY}")

    # Reliability stats
    if reliability:
        vals = list(reliability.values())
        avg_rel = sum(vals) / len(vals)
        min_rel = min(vals)
        max_rel = max(vals)
        low_rel = [(e, r) for e, r in reliability.items() if r < 0.7]
        print(f"  Avg reliability: {avg_rel:.3f}, Min: {min_rel:.3f}, Max: {max_rel:.3f}")
        print(f"  Low reliability edges (<0.7): {len(low_rel)}")

    p7_results = []
    p7_comparison = {"failure_aware_wins": 0, "naive_wins": 0, "ties": 0, "both_unreachable": 0}

    for idx, sop in enumerate(sop_10):
        targets = target_pool[idx]
        for tidx, target in enumerate(targets):
            agent = f"p7-{1 + tidx}"
            scenario_id = f"scenario_{idx*2 + tidx + 1}"

            # Failure-aware route
            fa_path, fa_reliability, fa_hops = failure_aware_dijkstra(adj, all_nodes, sop, target, reliability)

            # Naive max-weight route
            nv_path, nv_weight, nv_hops = naive_max_weight_dijkstra(adj, all_nodes, sop, target)

            # Compute naive route's reliability for comparison
            naive_rel = 1.0
            if nv_path:
                for i in range(len(nv_path) - 1):
                    edge = (nv_path[i], nv_path[i+1])
                    naive_rel *= reliability.get(edge, DEFAULT_RELIABILITY)

            fa_found = fa_path is not None
            nv_found = nv_path is not None

            if fa_found and nv_found:
                if fa_reliability > naive_rel + 0.001:
                    p7_comparison["failure_aware_wins"] += 1
                    winner = "failure_aware"
                elif naive_rel > fa_reliability + 0.001:
                    p7_comparison["naive_wins"] += 1
                    winner = "naive"
                else:
                    p7_comparison["ties"] += 1
                    winner = "tie"
            elif fa_found:
                p7_comparison["failure_aware_wins"] += 1
                winner = "failure_aware"
            elif nv_found:
                p7_comparison["naive_wins"] += 1
                winner = "naive"
            else:
                p7_comparison["both_unreachable"] += 1
                winner = "unreachable"

            result = {
                "scenario_id": scenario_id,
                "agent": agent,
                "source": sop,
                "target": target,
                "failure_aware": {
                    "path": fa_path,
                    "hops": len(fa_path) - 1 if fa_path else 0,
                    "reliability": round(fa_reliability, 6),
                    "hop_details": fa_hops
                },
                "naive_max_weight": {
                    "path": nv_path,
                    "hops": len(nv_path) - 1 if nv_path else 0,
                    "total_weight": nv_weight,
                    "reliability_of_naive_path": round(naive_rel, 6),
                    "hop_details": nv_hops
                },
                "winner": winner,
                "reliability_improvement": round(fa_reliability - naive_rel, 6) if (fa_found and nv_found) else None
            }
            p7_results.append(result)

            # Log the failure-aware route
            if fa_path:
                log_route(agent, "failure-aware-pathfinding",
                         fa_path, "success", len(fa_path) - 1)

            print(f"  {scenario_id}: {sop} -> {target} | FA rel={fa_reliability:.4f} vs Naive rel={naive_rel:.4f} | Winner: {winner}")

    # ============================================================
    # P8: Exploration pathfinding
    # ============================================================
    print("\n=== P8-1 & P8-2: Exploration Pathfinding ===")
    visit_freq = build_visit_frequency(route_log)

    print(f"  Nodes with prior visits: {len(visit_freq)}")
    print(f"  Total network nodes: {len(all_nodes)}")
    unvisited_before = len(all_nodes) - len([n for n in all_nodes if visit_freq.get(n, 0) > 0])
    print(f"  Unvisited nodes before exploration: {unvisited_before}")

    # Use all 22 SOPs, take first 20 for journeys
    exploration_sops = sops[:20]
    # Make a copy of visit_freq for exploration tracking
    exploration_freq = Counter(visit_freq)

    p8_results = []
    total_new_nodes = 0
    coverage_before = len([n for n in all_nodes if visit_freq.get(n, 0) > 0])
    nodes_discovered_set = set()

    for idx, sop in enumerate(exploration_sops):
        agent = f"p8-{1 + (idx % 2)}"

        # Track which nodes are new for this journey
        pre_visited = set(n for n in all_nodes if exploration_freq.get(n, 0) > 0)

        path, hops = exploration_routing(adj, all_nodes, sop, exploration_freq, max_hops=8)

        post_visited = set(n for n in all_nodes if exploration_freq.get(n, 0) > 0)
        new_nodes = post_visited - pre_visited
        nodes_discovered_set.update(new_nodes)
        total_new_nodes += len(new_nodes)

        # Calculate exploration efficiency
        efficiency = len(new_nodes) / len(path) if path else 0

        result = {
            "journey_id": f"explore_{idx + 1}",
            "agent": agent,
            "start": sop,
            "path": path,
            "hops": len(path) - 1,
            "new_nodes_discovered": len(new_nodes),
            "new_nodes_list": sorted(new_nodes),
            "exploration_efficiency": round(efficiency, 4),
            "coverage_after": len(post_visited),
            "hop_details": hops
        }
        p8_results.append(result)

        # Log the exploration route
        if path:
            log_route(agent, "exploration-pathfinding",
                     path, "success", len(path) - 1)

        print(f"  explore_{idx+1}: {sop} | hops={len(path)-1} | new_nodes={len(new_nodes)} | efficiency={efficiency:.2f}")

    coverage_after = len([n for n in all_nodes if exploration_freq.get(n, 0) > 0])
    coverage_increase = coverage_after - coverage_before

    # ============================================================
    # Compile final results
    # ============================================================
    print("\n=== Compiling Results ===")

    # P7 summary stats
    fa_reliabilities = [r["failure_aware"]["reliability"] for r in p7_results if r["failure_aware"]["path"]]
    nv_reliabilities = [r["naive_max_weight"]["reliability_of_naive_path"] for r in p7_results if r["naive_max_weight"]["path"]]
    improvements = [r["reliability_improvement"] for r in p7_results if r["reliability_improvement"] is not None]

    p7_summary = {
        "total_scenarios": 20,
        "comparison": p7_comparison,
        "avg_failure_aware_reliability": round(sum(fa_reliabilities) / len(fa_reliabilities), 6) if fa_reliabilities else 0,
        "avg_naive_reliability": round(sum(nv_reliabilities) / len(nv_reliabilities), 6) if nv_reliabilities else 0,
        "avg_reliability_improvement": round(sum(improvements) / len(improvements), 6) if improvements else 0,
        "max_reliability_improvement": round(max(improvements), 6) if improvements else 0,
        "edge_reliability_stats": {
            "edges_with_history": len(reliability),
            "default_for_unseen": DEFAULT_RELIABILITY,
            "avg_observed_reliability": round(sum(reliability.values()) / len(reliability), 4) if reliability else 0,
            "low_reliability_edges": len([r for r in reliability.values() if r < 0.7])
        }
    }

    # P8 summary stats
    new_counts = [r["new_nodes_discovered"] for r in p8_results]
    efficiencies = [r["exploration_efficiency"] for r in p8_results]

    p8_summary = {
        "total_journeys": 20,
        "total_new_nodes_discovered": len(nodes_discovered_set),
        "cumulative_new_node_hits": total_new_nodes,
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
        "coverage_increase": coverage_increase,
        "coverage_percent_before": round(100 * coverage_before / len(all_nodes), 2),
        "coverage_percent_after": round(100 * coverage_after / len(all_nodes), 2),
        "avg_new_nodes_per_journey": round(sum(new_counts) / len(new_counts), 2) if new_counts else 0,
        "avg_exploration_efficiency": round(sum(efficiencies) / len(efficiencies), 4) if efficiencies else 0,
        "visit_frequency_top_10": dict(Counter(exploration_freq).most_common(10))
    }

    output = {
        "run": "Run-006",
        "division": "4B",
        "task": "Pathfinding",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": {
            "p7-1": {"role": "Failure-aware routing (odd scenarios)"},
            "p7-2": {"role": "Failure-aware routing (even scenarios)"},
            "p8-1": {"role": "Exploration pathfinding (odd journeys)"},
            "p8-2": {"role": "Exploration pathfinding (even journeys)"}
        },
        "p7_failure_aware_routing": {
            "method": "Dijkstra with cost = -log(reliability); unseen edges default to 0.8",
            "summary": p7_summary,
            "scenarios": p7_results
        },
        "p8_exploration_pathfinding": {
            "method": "Greedy exploration preferring least-visited nodes; 8-hop max",
            "summary": p8_summary,
            "journeys": p8_results
        }
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nResults written to {OUTPUT_PATH}")
    print(f"\nP7 Summary: FA wins={p7_comparison['failure_aware_wins']}, Naive wins={p7_comparison['naive_wins']}, Ties={p7_comparison['ties']}")
    print(f"P7 Avg reliability improvement: {p7_summary['avg_reliability_improvement']}")
    print(f"P8 Summary: Coverage {p8_summary['coverage_percent_before']}% -> {p8_summary['coverage_percent_after']}%, New nodes: {p8_summary['total_new_nodes_discovered']}")


if __name__ == "__main__":
    main()
