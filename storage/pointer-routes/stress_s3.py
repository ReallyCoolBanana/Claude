#!/usr/bin/env python3
"""Stress Team S3: Adversarial path testing — find WORST routes in the network."""

import json
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"
REPORT_OUT = Path(__file__).parent / "stress_s3_report.json"


def load_network():
    with open(POINTER_NETWORK) as f:
        return json.load(f)


def get_adjacency(network):
    """Build adjacency list: node -> [(target, weight, strength), ...]"""
    adj = {}
    for node_id, node_data in network.get("nodes", {}).items():
        adj[node_id] = []
        for ptr in node_data.get("pointers", []):
            adj[node_id].append((ptr["to"], ptr.get("weight", 0), ptr.get("strength", "unknown")))
    return adj


def get_domain(node_id, network):
    node = network.get("nodes", {}).get(node_id, {})
    return node.get("domain", "unknown")


def get_sop_nodes(network):
    return [nid for nid in network.get("nodes", {}) if nid.startswith("SOP-")]


# ---------- Task 1: Longest paths (always follow LOWEST weight) ----------

def longest_path_worst(adj, start, max_hops=8):
    """Follow lowest-weight pointer each hop. Stop on cycle or max_hops."""
    path = [start]
    visited = {start}
    current = start
    for _ in range(max_hops):
        neighbors = adj.get(current, [])
        if not neighbors:
            break
        # Sort by weight ascending, pick lowest
        neighbors_sorted = sorted(neighbors, key=lambda x: x[1])
        # Pick the lowest weight neighbor
        target, weight, strength = neighbors_sorted[0]
        if target in visited:
            path.append(target)  # record the cycle-closing hop
            return path, "cycle"
        path.append(target)
        visited.add(target)
        current = target
    return path, "max_hops" if len(path) > max_hops else "dead_end"


def run_longest_paths(network, adj):
    sops = get_sop_nodes(network)
    results = {}
    for sop in sorted(sops):
        path, reason = longest_path_worst(adj, sop)
        end_domain = get_domain(path[-1], network)
        results[sop] = {
            "path": path,
            "length": len(path) - 1,  # hops
            "end_node": path[-1],
            "end_domain": end_domain,
            "termination": reason
        }
    return results


# ---------- Task 2: Wrong domain (cross-domain via worst routes) ----------

def cross_domain_worst(adj, start, start_domain, network, max_hops=8):
    """Follow lowest-weight pointers until we reach a different domain."""
    path = [start]
    visited = {start}
    current = start
    for hop in range(max_hops):
        neighbors = adj.get(current, [])
        if not neighbors:
            return path, None, "dead_end"
        neighbors_sorted = sorted(neighbors, key=lambda x: x[1])
        target, weight, strength = neighbors_sorted[0]
        if target in visited:
            # Try next-lowest that isn't visited
            found = False
            for t, w, s in neighbors_sorted[1:]:
                if t not in visited:
                    target = t
                    found = True
                    break
            if not found:
                path.append(target)
                return path, None, "cycle_trap"
        path.append(target)
        visited.add(target)
        current = target
        cur_domain = get_domain(current, network)
        if cur_domain != start_domain and cur_domain != "unknown":
            return path, cur_domain, "crossed"
    return path, None, "max_hops"


def run_cross_domain(network, adj):
    sops = get_sop_nodes(network)
    results = {}
    for sop in sorted(sops):
        start_domain = get_domain(sop, network)
        path, reached_domain, reason = cross_domain_worst(adj, sop, start_domain, network)
        results[sop] = {
            "path": path,
            "hops": len(path) - 1,
            "start_domain": start_domain,
            "reached_domain": reached_domain,
            "outcome": reason
        }
    return results


# ---------- Task 3: Circular routes (find all cycles) ----------

def find_all_cycles(adj, sops, max_depth=8):
    """For each SOP, BFS/DFS to find shortest cycle back to itself."""
    cycles = {}
    all_cycles_list = []

    for sop in sorted(sops):
        # BFS to find shortest path back to sop
        from collections import deque
        queue = deque()
        # (current_node, path)
        neighbors = adj.get(sop, [])
        for target, weight, strength in neighbors:
            queue.append((target, [sop, target]))

        found = False
        visited_states = {sop}  # We want to find paths BACK to sop

        while queue:
            current, path = queue.popleft()
            if len(path) - 1 > max_depth:
                continue
            if current == sop and len(path) > 2:
                cycles[sop] = {
                    "cycle": path,
                    "length": len(path) - 1
                }
                all_cycles_list.append({
                    "start": sop,
                    "cycle": path,
                    "length": len(path) - 1
                })
                found = True
                break
            if current in visited_states and current != sop:
                continue
            visited_states.add(current)
            for target, weight, strength in adj.get(current, []):
                if target == sop or target not in visited_states:
                    queue.append((target, path + [target]))

        if not found:
            cycles[sop] = {
                "cycle": None,
                "length": None
            }

    return cycles, all_cycles_list


def find_network_traps(adj, network):
    """Find nodes that tend to trap traversals in loops."""
    # Count how often each node appears as a cycle member
    sops = get_sop_nodes(network)
    all_nodes = list(adj.keys())

    trap_scores = defaultdict(int)

    # For each node, follow lowest-weight path and see if we loop
    for node in all_nodes:
        path, reason = longest_path_worst(adj, node)
        if reason == "cycle":
            # The last node closes the cycle — it's a trap entry point
            cycle_closer = path[-1]
            # Find where cycle starts
            cycle_start_idx = path.index(cycle_closer)
            cycle_nodes = path[cycle_start_idx:-1]
            for cn in cycle_nodes:
                trap_scores[cn] += 1

    # Sort by score descending
    traps = sorted(trap_scores.items(), key=lambda x: -x[1])
    return [
        {"node": node, "trap_score": score, "domain": get_domain(node, network)}
        for node, score in traps[:20]
    ]


# ---------- Task 4: Log adversarial routes ----------

def log_route(agent, route_path, notes):
    route_str = " -> ".join(route_path)
    cmd = [
        sys.executable, str(ROUTE_TRACKER),
        "log",
        "--agent", agent,
        "--task", "adversarial-routing",
        "--route", route_str,
        "--outcome", "failure",
        "--notes", notes
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"  [{agent}] {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr.strip()}")


def main():
    print("=== Stress Team S3: Adversarial Path Testing ===\n")

    network = load_network()
    adj = get_adjacency(network)
    sops = get_sop_nodes(network)
    print(f"Network: {len(adj)} nodes, {sum(len(v) for v in adj.values())} edges")
    print(f"SOP nodes: {len(sops)}\n")

    # Task 1: Longest paths
    print("--- Task 1: Longest paths (always follow lowest weight) ---")
    longest_paths = run_longest_paths(network, adj)
    worst_by_length = sorted(longest_paths.items(), key=lambda x: -x[1]["length"])
    for sop, info in worst_by_length[:5]:
        print(f"  {sop}: {info['length']} hops -> {info['end_node']} ({info['termination']})")
        print(f"    Path: {' -> '.join(info['path'])}")
    print()

    # Task 2: Cross-domain difficulty
    print("--- Task 2: Cross-domain difficulty via worst routes ---")
    cross_domain = run_cross_domain(network, adj)
    hard_crossings = sorted(cross_domain.items(), key=lambda x: -x[1]["hops"])
    for sop, info in hard_crossings[:5]:
        print(f"  {sop}: {info['hops']} hops to reach {info['reached_domain']} ({info['outcome']})")
        print(f"    Path: {' -> '.join(info['path'])}")
    print()

    # Task 3: Circular routes
    print("--- Task 3: Circular routes ---")
    cycles, all_cycles = find_all_cycles(adj, sops)
    short_cycles = [(sop, info) for sop, info in cycles.items() if info["length"] is not None]
    short_cycles.sort(key=lambda x: x[1]["length"])
    print(f"  SOPs with cycles: {len(short_cycles)} / {len(sops)}")
    for sop, info in short_cycles[:5]:
        print(f"  {sop}: cycle length {info['length']}")
        print(f"    Cycle: {' -> '.join(info['cycle'])}")

    no_cycle = [(sop, info) for sop, info in cycles.items() if info["length"] is None]
    print(f"  SOPs with no cycle found (within 8 hops): {len(no_cycle)}")
    print()

    # Find network traps
    print("--- Network traps ---")
    traps = find_network_traps(adj, network)
    for t in traps[:10]:
        print(f"  {t['node']}: trap_score={t['trap_score']} ({t['domain']})")
    print()

    # Task 4: Log 15 adversarial routes
    print("--- Task 4: Logging 15 adversarial routes ---")
    routes_to_log = []

    # 5 longest paths (s3-1)
    for sop, info in worst_by_length[:5]:
        routes_to_log.append((
            "s3-1",
            info["path"],
            json.dumps({"type": "longest", "finding": f"{info['length']} hops via lowest-weight, terminates: {info['termination']}, ends at {info['end_node']}"})
        ))

    # 5 hardest cross-domain (s3-2)
    for sop, info in hard_crossings[:5]:
        routes_to_log.append((
            "s3-2",
            info["path"],
            json.dumps({"type": "wrong-domain", "finding": f"{info['hops']} hops to cross domain, outcome: {info['outcome']}, reached: {info['reached_domain']}"})
        ))

    # 5 shortest cycles (s3-3 through s3-5)
    agents_cycle = ["s3-3", "s3-4", "s3-5", "s3-3", "s3-4"]
    for i, (sop, info) in enumerate(short_cycles[:5]):
        routes_to_log.append((
            agents_cycle[i],
            info["cycle"],
            json.dumps({"type": "circular", "finding": f"cycle length {info['length']} back to {sop}"})
        ))

    for agent, path, notes in routes_to_log:
        log_route(agent, path, notes)

    print()

    # Task 5: Write report
    print("--- Task 5: Writing report ---")

    # Compute summary statistics
    cycle_lengths = [info["length"] for _, info in short_cycles]
    cross_hops = [info["hops"] for _, info in cross_domain.items()]
    longest_hops = [info["length"] for _, info in longest_paths.items()]

    report = {
        "generated": "2026-03-12",
        "team": "stress-s3",
        "description": "Adversarial path testing: worst-case routes in pointer network",
        "summary": {
            "total_sops_tested": len(sops),
            "sops_with_cycles": len(short_cycles),
            "sops_without_cycles": len(no_cycle),
            "avg_longest_path_hops": round(sum(longest_hops) / max(len(longest_hops), 1), 2),
            "max_longest_path_hops": max(longest_hops) if longest_hops else 0,
            "avg_cross_domain_hops": round(sum(cross_hops) / max(len(cross_hops), 1), 2),
            "max_cross_domain_hops": max(cross_hops) if cross_hops else 0,
            "shortest_cycle_length": min(cycle_lengths) if cycle_lengths else None,
            "avg_cycle_length": round(sum(cycle_lengths) / max(len(cycle_lengths), 1), 2) if cycle_lengths else None,
            "network_trap_count": len(traps)
        },
        "longest_paths": {
            sop: info for sop, info in worst_by_length
        },
        "cross_domain_difficulty": {
            sop: info for sop, info in hard_crossings
        },
        "cycles_found": [
            {"sop": sop, "cycle": info["cycle"], "length": info["length"]}
            for sop, info in short_cycles
        ],
        "no_cycles": [sop for sop, _ in no_cycle],
        "network_traps": traps,
        "adversarial_routes_logged": [
            {"agent": a, "path": p, "notes": n}
            for a, p, n in routes_to_log
        ]
    }

    with open(REPORT_OUT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"Report written to {REPORT_OUT}")
    print(f"\n=== S3 Complete ===")
    print(f"Key findings:")
    print(f"  - Worst longest path: {worst_by_length[0][1]['length']} hops from {worst_by_length[0][0]}")
    print(f"  - Hardest cross-domain: {hard_crossings[0][1]['hops']} hops from {hard_crossings[0][0]}")
    if short_cycles:
        print(f"  - Shortest cycle: {short_cycles[0][1]['length']} hops from {short_cycles[0][0]}")
    print(f"  - Top network trap: {traps[0]['node']} (score={traps[0]['trap_score']})" if traps else "  - No traps found")


if __name__ == "__main__":
    main()
