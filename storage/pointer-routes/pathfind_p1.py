#!/usr/bin/env python3
"""
Maximum-weight pathfinding on the pointer network using modified Dijkstra's.

Standard Dijkstra finds shortest (min-weight) paths. We invert the logic:
- Use a max-heap (negate weights for Python's min-heap)
- Relax edges when cumulative weight is GREATER than current best
- This finds the "strongest" route between any two nodes
"""

import json
import heapq
import subprocess
import sys
import os
from collections import defaultdict

NETWORK_PATH = "/home/user/Claude/storage/pointer-network.json"
REPORT_PATH = "/home/user/Claude/storage/pointer-routes/pathfind_p1_weight.json"
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"

def load_network(path):
    with open(path) as f:
        data = json.load(f)

    # Build adjacency list: node -> [(neighbor, weight, strength, reasons)]
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

def max_weight_dijkstra(adj, source, target):
    """
    Modified Dijkstra for MAXIMUM weight paths.

    Uses negative weights in a min-heap to simulate max-heap behavior.
    Returns (total_weight, path, strengths_used) or (None, None, None) if no path.
    """
    # dist[node] = max total weight to reach node from source
    dist = defaultdict(lambda: -1)
    dist[source] = 0

    # predecessor for path reconstruction
    prev = {}
    edge_info = {}  # node -> (strength, reasons) of the edge used to reach it

    # Max-heap via negation: (-weight, node)
    heap = [(0, source)]  # 0 because we start at source with 0 accumulated weight
    visited = set()

    while heap:
        neg_w, u = heapq.heappop(heap)
        current_w = -neg_w

        if u in visited:
            continue
        visited.add(u)

        if u == target:
            break

        for neighbor, edge_weight, strength, reasons in adj[u]:
            new_weight = current_w + edge_weight
            if new_weight > dist[neighbor]:
                dist[neighbor] = new_weight
                prev[neighbor] = u
                edge_info[neighbor] = (strength, reasons)
                heapq.heappush(heap, (-new_weight, neighbor))

    if target not in prev and source != target:
        return None, None, None

    # Reconstruct path
    path = []
    node = target
    while node != source:
        path.append(node)
        node = prev[node]
    path.append(source)
    path.reverse()

    # Collect strengths used along the path
    strengths = []
    for p in path[1:]:
        s, r = edge_info.get(p, ("unknown", []))
        strengths.append(s)

    return dist[target], path, strengths

def is_intuitive(path, node_domains):
    """
    A path is 'intuitive' if it follows a logical domain progression,
    meaning it doesn't bounce back and forth between the same domains.
    Allow: sops -> scripts -> knowledge-base (progressive)
    Penalize: sops -> scripts -> sops -> scripts (oscillating)
    """
    domains = [node_domains.get(n, "unknown") for n in path]
    # Count domain transitions that revisit an earlier domain
    seen_domains = []
    revisits = 0
    for d in domains:
        if d in seen_domains and d != seen_domains[-1]:
            revisits += 1
        if not seen_domains or seen_domains[-1] != d:
            seen_domains.append(d)

    return revisits == 0, revisits

def define_scenarios():
    """Define 20 diverse pathfinding scenarios."""
    return [
        # 4 specified scenarios
        ("SOP-011", "SCR-0043", "cross-domain SOP to script"),
        ("SOP-012", "SCR-0057", "monitoring to route tracker"),
        ("SOP-027", "SRC-0001", "wikipedia to data source"),
        ("SOP-023", "SRC-0001", "data gathering to first source"),  # SOP-023 to SRC expanded below

        # 16 more diverse scenarios
        ("SOP-011", "SRC-0051", "cross-domain SOP to distant source"),
        ("SOP-013", "SCR-0001", "SOP to first script"),
        ("SOP-014", "SCR-0030", "agent comms to script"),
        ("SOP-015", "SRC-0020", "stress testing to source"),
        ("SOP-016", "SCR-0050", "SOP to high-numbered script"),
        ("SOP-017", "SRC-0040", "SOP to mid-range source"),
        ("SOP-018", "SCR-0010", "SOP to low-numbered script"),
        ("SOP-019", "SRC-0030", "SOP to source"),
        ("SOP-020", "SCR-0025", "SOP to mid script"),
        ("SOP-021", "SRC-0015", "SOP to source"),
        ("SOP-022", "SCR-0040", "SOP to high script"),
        ("SOP-024", "SRC-0045", "SOP to high source"),
        ("SOP-025", "SCR-0005", "SOP to low script"),
        ("SOP-026", "SRC-0050", "SOP to high source"),
        ("SOP-028", "SCR-0035", "SOP to mid script"),
        ("SOP-029", "SCR-0055", "SOP to near-last script"),
    ]

def log_route(agent, path, total_weight, hops, outcome="success"):
    """Log route via route_tracker.py"""
    route_str = " -> ".join(path)
    notes = f"total_weight={total_weight}, hops={hops}"
    cmd = [
        "python3", ROUTE_TRACKER, "log",
        "--agent", agent,
        "--task", "weight-pathfinding",
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0, result.stdout.strip()
    except Exception as e:
        return False, str(e)

def main():
    print("Loading pointer network...")
    adj, node_domains = load_network(NETWORK_PATH)
    print(f"Loaded {len(node_domains)} nodes, {sum(len(v) for v in adj.values())} edges")

    scenarios = define_scenarios()
    results = []
    bottleneck_counter = defaultdict(int)  # Count how often each intermediate node appears

    print(f"\nRunning {len(scenarios)} pathfinding scenarios...\n")

    for i, (source, target, description) in enumerate(scenarios):
        print(f"[{i+1:2d}/20] {source} -> {target} ({description})")

        total_weight, path, strengths = max_weight_dijkstra(adj, source, target)

        if path is None:
            print(f"        NO PATH FOUND")
            result = {
                "scenario": i + 1,
                "source": source,
                "target": target,
                "description": description,
                "path": None,
                "total_weight": None,
                "hops": None,
                "strengths_used": None,
                "intuitive": None,
                "domain_revisits": None
            }
        else:
            hops = len(path) - 1
            intuitive, revisits = is_intuitive(path, node_domains)
            domain_seq = [node_domains.get(n, "?") for n in path]

            print(f"        Path: {' -> '.join(path)}")
            print(f"        Weight: {total_weight}, Hops: {hops}, Intuitive: {intuitive}")
            print(f"        Domains: {' -> '.join(domain_seq)}")
            print(f"        Strengths: {strengths}")

            # Track intermediate nodes for bottleneck analysis
            for node in path[1:-1]:
                bottleneck_counter[node] += 1

            result = {
                "scenario": i + 1,
                "source": source,
                "target": target,
                "description": description,
                "path": path,
                "total_weight": total_weight,
                "hops": hops,
                "strengths_used": strengths,
                "intuitive": intuitive,
                "domain_revisits": revisits,
                "domain_sequence": domain_seq
            }

        results.append(result)

    # --- SOP-023 to every SRC node (data gathering reach analysis) ---
    print("\n--- SOP-023 reach analysis to all SRC nodes ---")
    src_nodes = sorted([n for n in node_domains if n.startswith("SRC-")])
    sop023_reach = []
    for src in src_nodes:
        w, p, s = max_weight_dijkstra(adj, "SOP-023", src)
        if p:
            hops = len(p) - 1
            for node in p[1:-1]:
                bottleneck_counter[node] += 1
            sop023_reach.append({
                "target": src,
                "total_weight": w,
                "hops": hops,
                "path": p,
                "strengths_used": s
            })
            print(f"  SOP-023 -> {src}: weight={w}, hops={hops}, path={' -> '.join(p)}")
        else:
            sop023_reach.append({"target": src, "total_weight": None, "hops": None, "path": None})
            print(f"  SOP-023 -> {src}: NO PATH")

    # Bottleneck analysis
    top_bottlenecks = sorted(bottleneck_counter.items(), key=lambda x: -x[1])[:20]
    print("\n--- Top routing bottlenecks (intermediate nodes) ---")
    for node, count in top_bottlenecks:
        print(f"  {node} ({node_domains.get(node, '?')}): appeared in {count} paths")

    # Summary statistics
    found = [r for r in results if r["path"] is not None]
    not_found = [r for r in results if r["path"] is None]
    intuitive_count = sum(1 for r in found if r.get("intuitive"))
    avg_weight = sum(r["total_weight"] for r in found) / len(found) if found else 0
    avg_hops = sum(r["hops"] for r in found) / len(found) if found else 0

    summary = {
        "total_scenarios": 20,
        "paths_found": len(found),
        "paths_not_found": len(not_found),
        "intuitive_paths": intuitive_count,
        "non_intuitive_paths": len(found) - intuitive_count,
        "avg_total_weight": round(avg_weight, 2),
        "avg_hops": round(avg_hops, 2),
        "max_weight_path": max((r for r in found), key=lambda r: r["total_weight"], default=None),
        "min_weight_path": min((r for r in found), key=lambda r: r["total_weight"], default=None),
    }

    # Compile report
    report = {
        "report": "pathfind_p1_weight",
        "timestamp": __import__("datetime").datetime.now().isoformat(),
        "algorithm": "Modified Dijkstra (maximum weight)",
        "description": "Finds strongest (highest cumulative weight) paths between SOP sources and SCR/SRC targets",
        "summary": summary,
        "paths": results,
        "sop023_reach_to_all_src": sop023_reach,
        "bottlenecks": [
            {"node": node, "domain": node_domains.get(node, "?"), "path_appearances": count}
            for node, count in top_bottlenecks
        ],
        "observations": []
    }

    # Generate observations
    obs = []
    if top_bottlenecks:
        top_node, top_count = top_bottlenecks[0]
        obs.append(f"Primary routing bottleneck: {top_node} ({node_domains.get(top_node, '?')}) appears in {top_count} paths")

    obs.append(f"{intuitive_count}/{len(found)} paths follow intuitive domain progression (no domain revisits)")
    obs.append(f"Average path weight: {avg_weight:.1f}, Average hops: {avg_hops:.1f}")

    sop023_reachable = sum(1 for r in sop023_reach if r["path"] is not None)
    obs.append(f"SOP-023 can reach {sop023_reachable}/{len(src_nodes)} SRC nodes")

    if sop023_reach:
        reachable = [r for r in sop023_reach if r["total_weight"] is not None]
        if reachable:
            best = max(reachable, key=lambda r: r["total_weight"])
            obs.append(f"Strongest SOP-023->SRC route: {best['target']} with weight {best['total_weight']}")

    # Check strength distribution
    all_strengths = []
    for r in found:
        all_strengths.extend(r.get("strengths_used", []))
    strength_counts = defaultdict(int)
    for s in all_strengths:
        strength_counts[s] += 1
    obs.append(f"Strength distribution in optimal paths: {dict(strength_counts)}")

    report["observations"] = obs

    # Write report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {REPORT_PATH}")

    # Log routes via route_tracker
    print("\n--- Logging routes via route_tracker.py ---")
    agent_names = ["p1-1", "p1-2", "p1-3", "p1-4"]
    logged_count = 0
    for i, result in enumerate(results):
        if result["path"] is not None:
            agent = agent_names[i % 4]
            ok, msg = log_route(agent, result["path"], result["total_weight"], result["hops"])
            status = "OK" if ok else "FAIL"
            print(f"  [{status}] {agent}: scenario {i+1} ({result['source']} -> {result['target']})")
            logged_count += 1

    print(f"\nLogged {logged_count} routes. Done.")

if __name__ == "__main__":
    main()
