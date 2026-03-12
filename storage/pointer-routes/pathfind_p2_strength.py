#!/usr/bin/env python3
"""
Pathfinder P2 — Strength-priority pathfinding on the pointer network.

Algorithm: Modified Dijkstra with lexicographic cost (strength_tier_sum, -weight_sum).
At each hop, prefer edges with better strength classification first (primary > supporting
> related > tangential > unclassified), then higher weight as tiebreaker.

Also runs pure weight-based max-Dijkstra for comparison on the same 20 scenarios.
"""

import json
import heapq
import subprocess
from collections import defaultdict, Counter
from datetime import datetime

NETWORK_PATH = "/home/user/Claude/storage/pointer-network.json"
REPORT_PATH = "/home/user/Claude/storage/pointer-routes/pathfind_p2_strength.json"
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"

STRENGTH_ORDER = {
    "primary": 0,
    "supporting": 1,
    "related": 2,
    "tangential": 3,
    "unclassified": 4,
}

def load_network():
    with open(NETWORK_PATH) as f:
        data = json.load(f)
    adj = defaultdict(list)
    node_domains = {}
    for node_id, node_data in data["nodes"].items():
        node_domains[node_id] = node_data.get("domain", "unknown")
        for ptr in node_data.get("pointers", []):
            strength = ptr.get("strength", "unclassified")
            adj[node_id].append((ptr["to"], ptr["weight"], strength))
    return adj, node_domains


def strength_dijkstra(adj, source, target):
    """
    Modified Dijkstra minimizing (strength_tier_sum, -weight_sum).
    Lower strength tier sum = more primary/supporting edges used.
    Higher weight sum = stronger connections (tiebreaker within same tier quality).

    Each node visited at most once (standard Dijkstra guarantee).
    """
    if source == target:
        return [source], [], 0, 0

    # cost = (strength_tier_sum, -weight_sum) — minimize both lexicographically
    heap = [(0, 0, source)]  # (str_sum, -weight_sum, node)
    best = {}
    prev = {}  # node -> (predecessor, strength, weight)

    while heap:
        str_sum, neg_w, node = heapq.heappop(heap)

        if node in best:
            continue
        best[node] = (str_sum, neg_w)

        if node == target:
            path, strengths = [], []
            n = target
            while n != source:
                path.append(n)
                pred, s, w = prev[n]
                strengths.append(s)
                n = pred
            path.append(source)
            path.reverse()
            strengths.reverse()
            return path, strengths, -neg_w, len(path) - 1

        for neighbor, weight, strength in adj.get(node, []):
            if neighbor in best:
                continue
            tier = STRENGTH_ORDER.get(strength, 4)
            new_str = str_sum + tier
            new_neg_w = neg_w - weight
            new_cost = (new_str, new_neg_w)

            # Only push if we haven't settled this node
            old = prev.get(neighbor)
            if old is None or new_cost < (best.get(neighbor, (float('inf'), float('inf')))):
                prev[neighbor] = (node, strength, weight)
                heapq.heappush(heap, (new_str, new_neg_w, neighbor))

    return None, None, None, None


def max_weight_dijkstra(adj, source, target):
    """Pure weight-based maximum-weight Dijkstra for comparison."""
    if source == target:
        return [source], [], 0, 0

    dist = defaultdict(lambda: -1)
    dist[source] = 0
    prev = {}
    edge_info = {}
    heap = [(0, source)]
    visited = set()

    while heap:
        neg_w, u = heapq.heappop(heap)
        current_w = -neg_w
        if u in visited:
            continue
        visited.add(u)
        if u == target:
            break
        for neighbor, weight, strength in adj.get(u, []):
            new_weight = current_w + weight
            if new_weight > dist[neighbor]:
                dist[neighbor] = new_weight
                prev[neighbor] = u
                edge_info[neighbor] = strength
                heapq.heappush(heap, (-new_weight, neighbor))

    if target not in prev and source != target:
        return None, None, None, None

    path = []
    node = target
    while node != source:
        path.append(node)
        node = prev[node]
    path.append(source)
    path.reverse()
    strengths = [edge_info.get(p, "unknown") for p in path[1:]]
    return path, strengths, dist[target], len(path) - 1


def is_intuitive(path, node_domains):
    """Check if path follows logical domain progression (no oscillations)."""
    domains = [node_domains.get(n, "unknown") for n in path]
    seen_domains = []
    revisits = 0
    for d in domains:
        if d in seen_domains and d != seen_domains[-1]:
            revisits += 1
        if not seen_domains or seen_domains[-1] != d:
            seen_domains.append(d)
    return revisits == 0, revisits


def strength_score(strengths):
    """Average strength quality (0=all primary, 4=all unclassified)."""
    if not strengths:
        return 0
    return sum(STRENGTH_ORDER.get(s, 4) for s in strengths) / len(strengths)


KNOWN_TRAP_NODES = {
    "KB-0004", "KB-0016", "SCR-0019", "SCR-0007", "SCR-0005",
    "SCR-0003", "SCR-0001", "KB-0002", "KB-0009"
}


def hits_trap(path, trap_nodes):
    return [n for n in path[1:-1] if n in trap_nodes]


def define_scenarios():
    """20 scenarios: 10 SOPs x 2 targets each (1 SCR, 1 SRC)."""
    return [
        ("SOP-011", "SCR-0043", "SOP-011 to cross-domain script"),
        ("SOP-011", "SRC-0051", "SOP-011 to distant source"),
        ("SOP-012", "SCR-0057", "SOP-012 to route tracker"),
        ("SOP-012", "SRC-0020", "SOP-012 to mid-range source"),
        ("SOP-015", "SCR-0005", "SOP-015 to low script"),
        ("SOP-015", "SRC-0030", "SOP-015 to source"),
        ("SOP-017", "SCR-0010", "SOP-017 to low script"),
        ("SOP-017", "SRC-0040", "SOP-017 to high source"),
        ("SOP-023", "SCR-0025", "SOP-023 to mid script"),
        ("SOP-023", "SRC-0001", "SOP-023 to first source"),
        ("SOP-025", "SCR-0035", "SOP-025 to mid script"),
        ("SOP-025", "SRC-0045", "SOP-025 to high source"),
        ("SOP-027", "SCR-0040", "SOP-027 to high script"),
        ("SOP-027", "SRC-0015", "SOP-027 to source"),
        ("SOP-028", "SCR-0030", "SOP-028 to mid script"),
        ("SOP-028", "SRC-0050", "SOP-028 to high source"),
        ("SOP-029", "SCR-0050", "SOP-029 to high script"),
        ("SOP-029", "SRC-0035", "SOP-029 to mid source"),
        ("SOP-G01", "SCR-0001", "SOP-G01 to first script"),
        ("SOP-G01", "SRC-0025", "SOP-G01 to mid source"),
    ]


def log_route(agent, path, total_weight, strengths_list, outcome="success"):
    route_str = " -> ".join(path)
    notes = f"strengths={strengths_list}, weight={total_weight}"
    cmd = [
        "python3", ROUTE_TRACKER, "log",
        "--agent", agent,
        "--task", "strength-pathfinding",
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
    print("=" * 70)
    print("Pathfinder P2 — Strength-Priority Dijkstra")
    print("=" * 70)

    print("\nLoading pointer network...")
    adj, node_domains = load_network()
    total_edges = sum(len(v) for v in adj.values())
    print(f"Loaded {len(node_domains)} nodes, {total_edges} edges")

    scenarios = define_scenarios()
    results = []

    print(f"\nRunning {len(scenarios)} pathfinding scenarios...\n")

    for i, (source, target, description) in enumerate(scenarios):
        print(f"[{i+1:2d}/20] {source} -> {target} ({description})")

        # Strength-priority
        sp_path, sp_str, sp_w, sp_h = strength_dijkstra(adj, source, target)
        # Pure weight
        pw_path, pw_str, pw_w, pw_h = max_weight_dijkstra(adj, source, target)

        sp_data = {"path": None}
        pw_data = {"path": None}
        comparison = {}

        if sp_path:
            sp_int, sp_rev = is_intuitive(sp_path, node_domains)
            sp_doms = [node_domains.get(n, "?") for n in sp_path]
            sp_ss = strength_score(sp_str)
            sp_traps = hits_trap(sp_path, KNOWN_TRAP_NODES)
            sp_data = {
                "path": sp_path, "strengths_used": sp_str, "total_weight": sp_w,
                "hops": sp_h, "strength_score": round(sp_ss, 3),
                "intuitive": sp_int, "domain_revisits": sp_rev,
                "domain_sequence": sp_doms, "trap_nodes_hit": sp_traps,
            }
            print(f"  STR: {' -> '.join(sp_path)}  W={sp_w} H={sp_h} SS={sp_ss:.2f} Int={sp_int} Traps={sp_traps}")

        if pw_path:
            pw_int, pw_rev = is_intuitive(pw_path, node_domains)
            pw_doms = [node_domains.get(n, "?") for n in pw_path]
            pw_ss = strength_score(pw_str)
            pw_traps = hits_trap(pw_path, KNOWN_TRAP_NODES)
            pw_data = {
                "path": pw_path, "strengths_used": pw_str, "total_weight": pw_w,
                "hops": pw_h, "strength_score": round(pw_ss, 3),
                "intuitive": pw_int, "domain_revisits": pw_rev,
                "domain_sequence": pw_doms, "trap_nodes_hit": pw_traps,
            }
            print(f"  WGT: {' -> '.join(pw_path)}  W={pw_w} H={pw_h} SS={pw_ss:.2f} Int={pw_int} Traps={pw_traps}")

        if sp_path and pw_path:
            comparison = {
                "same_path": sp_path == pw_path,
                "strength_fewer_hops": sp_h < pw_h,
                "strength_higher_weight": sp_w > pw_w,
                "strength_better_str_score": sp_ss < pw_ss,
                "strength_more_intuitive": sp_rev < pw_rev,
                "strength_avoids_traps": len(sp_traps) < len(pw_traps),
                "hop_diff": sp_h - pw_h,
                "weight_diff": sp_w - pw_w,
                "str_score_diff": round(sp_ss - pw_ss, 3),
            }
            same = "SAME" if comparison["same_path"] else "DIFF"
            print(f"  CMP: {same} | hops {sp_h}v{pw_h} | weight {sp_w}v{pw_w} | ss {sp_ss:.2f}v{pw_ss:.2f}")

        results.append({
            "scenario": i + 1, "source": source, "target": target,
            "description": description,
            "strength_priority": sp_data,
            "pure_weight": pw_data,
            "comparison": comparison,
        })

    # ---- Aggregate ----
    print("\n" + "=" * 70)
    print("AGGREGATE ANALYSIS")
    print("=" * 70)

    sp_found = [r for r in results if r["strength_priority"].get("path")]
    pw_found = [r for r in results if r["pure_weight"].get("path")]
    both = [r for r in results if r["strength_priority"].get("path") and r["pure_weight"].get("path")]

    same_count = sum(1 for r in both if r["comparison"].get("same_path"))
    diff_count = len(both) - same_count

    fewer_hops = sum(1 for r in both if r["comparison"].get("strength_fewer_hops"))
    more_hops = sum(1 for r in both if r["comparison"]["hop_diff"] > 0)
    same_hops = sum(1 for r in both if r["comparison"]["hop_diff"] == 0)
    better_ss = sum(1 for r in both if r["comparison"].get("strength_better_str_score"))
    higher_w = sum(1 for r in both if r["comparison"].get("strength_higher_weight"))
    more_int = sum(1 for r in both if r["comparison"].get("strength_more_intuitive"))
    avoids = sum(1 for r in both if r["comparison"].get("strength_avoids_traps"))

    sp_int_ct = sum(1 for r in sp_found if r["strength_priority"].get("intuitive"))
    pw_int_ct = sum(1 for r in pw_found if r["pure_weight"].get("intuitive"))

    sp_avg_h = sum(r["strength_priority"]["hops"] for r in sp_found) / len(sp_found) if sp_found else 0
    pw_avg_h = sum(r["pure_weight"]["hops"] for r in pw_found) / len(pw_found) if pw_found else 0
    sp_avg_w = sum(r["strength_priority"]["total_weight"] for r in sp_found) / len(sp_found) if sp_found else 0
    pw_avg_w = sum(r["pure_weight"]["total_weight"] for r in pw_found) / len(pw_found) if pw_found else 0
    sp_avg_ss = sum(r["strength_priority"]["strength_score"] for r in sp_found) / len(sp_found) if sp_found else 0
    pw_avg_ss = sum(r["pure_weight"]["strength_score"] for r in pw_found) / len(pw_found) if pw_found else 0

    sp_traps = sum(len(r["strength_priority"].get("trap_nodes_hit", [])) for r in sp_found)
    pw_traps = sum(len(r["pure_weight"].get("trap_nodes_hit", [])) for r in pw_found)

    sp_all_str = []
    pw_all_str = []
    for r in sp_found:
        sp_all_str.extend(r["strength_priority"].get("strengths_used", []))
    for r in pw_found:
        pw_all_str.extend(r["pure_weight"].get("strengths_used", []))
    sp_str_dist = dict(Counter(sp_all_str))
    pw_str_dist = dict(Counter(pw_all_str))

    print(f"\nPaths found: Strength={len(sp_found)}/20, Weight={len(pw_found)}/20")
    print(f"Same path: {same_count}, Different: {diff_count}")
    print(f"\nStrength-priority wins ({len(both)} comparable):")
    print(f"  Fewer hops:       {fewer_hops}  (same: {same_hops}, more: {more_hops})")
    print(f"  Better str score: {better_ss}")
    print(f"  Higher weight:    {higher_w}")
    print(f"  More intuitive:   {more_int}")
    print(f"  Avoids traps:     {avoids}")
    print(f"\nIntuitive: Str={sp_int_ct}/{len(sp_found)}, Wgt={pw_int_ct}/{len(pw_found)}")
    print(f"Avg hops: Str={sp_avg_h:.2f}, Wgt={pw_avg_h:.2f}")
    print(f"Avg weight: Str={sp_avg_w:.1f}, Wgt={pw_avg_w:.1f}")
    print(f"Avg str score: Str={sp_avg_ss:.3f}, Wgt={pw_avg_ss:.3f} (lower=better)")
    print(f"Trap hits: Str={sp_traps}, Wgt={pw_traps}")
    print(f"Strength dist: Str={sp_str_dist}")
    print(f"               Wgt={pw_str_dist}")

    # Build observations
    observations = []
    if diff_count > 0:
        observations.append(f"Strength-priority chose different paths in {diff_count}/{len(both)} scenarios.")
    if sp_avg_ss < pw_avg_ss:
        observations.append(f"Strength-priority achieves better (lower) avg strength score: {sp_avg_ss:.3f} vs {pw_avg_ss:.3f} — it traverses higher-quality edges.")
    elif sp_avg_ss > pw_avg_ss:
        observations.append(f"Pure-weight achieves better strength score ({pw_avg_ss:.3f} vs {sp_avg_ss:.3f}) — high-weight edges already tend to be primary.")
    else:
        observations.append(f"Both algorithms achieve identical average strength scores ({sp_avg_ss:.3f}).")
    if sp_traps < pw_traps:
        observations.append(f"Strength-priority avoids S3-identified traps better: {sp_traps} vs {pw_traps} traversals.")
    elif sp_traps == pw_traps:
        observations.append(f"Both algorithms hit the same number of trap nodes ({sp_traps}).")
    else:
        observations.append(f"Strength-priority hits MORE traps ({sp_traps} vs {pw_traps}) — strength doesn't help avoid them.")
    if sp_avg_w < pw_avg_w:
        observations.append(f"Strength-priority trades weight ({sp_avg_w:.1f} vs {pw_avg_w:.1f}) for better edge quality — expected tradeoff.")
    if fewer_hops > more_hops:
        observations.append(f"Strength-priority tends toward shorter paths ({fewer_hops} shorter, {same_hops} same, {more_hops} longer) — primary edges are more direct.")
    elif more_hops > fewer_hops:
        observations.append(f"Strength-priority uses more hops ({more_hops} longer, {same_hops} same, {fewer_hops} shorter) — trades directness for quality.")
    if sp_int_ct != pw_int_ct:
        observations.append(f"Intuitive path difference: Strength={sp_int_ct}, Weight={pw_int_ct} out of respective found paths.")

    summary = {
        "total_scenarios": 20,
        "strength_paths_found": len(sp_found),
        "weight_paths_found": len(pw_found),
        "same_path_count": same_count,
        "different_path_count": diff_count,
        "strength_wins": {
            "fewer_hops": fewer_hops,
            "better_strength_score": better_ss,
            "higher_weight": higher_w,
            "more_intuitive": more_int,
            "avoids_traps": avoids,
        },
        "averages": {
            "strength_avg_hops": round(sp_avg_h, 2),
            "weight_avg_hops": round(pw_avg_h, 2),
            "strength_avg_weight": round(sp_avg_w, 2),
            "weight_avg_weight": round(pw_avg_w, 2),
            "strength_avg_str_score": round(sp_avg_ss, 3),
            "weight_avg_str_score": round(pw_avg_ss, 3),
        },
        "trap_analysis": {
            "strength_total_traps_hit": sp_traps,
            "weight_total_traps_hit": pw_traps,
            "known_trap_nodes": sorted(KNOWN_TRAP_NODES),
        },
        "intuitive_paths": {"strength": sp_int_ct, "weight": pw_int_ct},
        "strength_distributions": {
            "strength_priority_paths": sp_str_dist,
            "weight_priority_paths": pw_str_dist,
        },
    }

    report = {
        "report": "pathfind_p2_strength",
        "timestamp": datetime.now().isoformat(),
        "algorithm": "Strength-Priority Dijkstra (minimize strength_tier_sum, then maximize weight)",
        "comparison_algorithm": "Max-Weight Dijkstra (maximize total weight)",
        "description": "Tests whether preferring edges by strength classification leads to faster, more logical, and trap-avoiding paths vs pure weight routing.",
        "scenarios_spec": "10 SOPs x 2 targets each (1 SCR + 1 SRC per SOP)",
        "sops_tested": ["SOP-011", "SOP-012", "SOP-015", "SOP-017", "SOP-023", "SOP-025", "SOP-027", "SOP-028", "SOP-029", "SOP-G01"],
        "summary": summary,
        "observations": observations,
        "paths": results,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {REPORT_PATH}")

    # Log routes
    print("\n--- Logging 20 routes ---")
    agents = ["p2-1", "p2-2", "p2-3", "p2-4"]
    logged = 0
    for i, r in enumerate(results):
        agent = agents[i % 4]
        sp = r["strength_priority"]
        if sp.get("path"):
            ok, msg = log_route(agent, sp["path"], sp["total_weight"], sp.get("strengths_used", []))
        else:
            ok, msg = log_route(agent, [r["source"], r["target"]], 0, [], outcome="no_path")
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {agent}: {r['source']} -> {r['target']}")
        logged += 1

    print(f"\nLogged {logged} routes. Done.")


if __name__ == "__main__":
    main()
