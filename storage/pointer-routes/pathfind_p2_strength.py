#!/usr/bin/env python3
"""
Pathfinder P2 — Strength-priority pathfinding on the pointer network.

Algorithm: Modified Dijkstra with lexicographic cost (strength_tier_sum, -weight_sum).
Prefers edges classified as primary > supporting > related > tangential > unclassified.
Within same strength tier, prefers higher weight.

Compares to pure max-weight Dijkstra on the same 20 scenarios.
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
            adj[node_id].append((
                ptr["to"], ptr["weight"],
                ptr.get("strength", "unclassified")
            ))
    del data
    return adj, node_domains


def strength_dijkstra(adj, source, target):
    """
    Dijkstra minimizing (strength_tier_sum, -weight_sum).
    Lower tier sum = more primary/supporting edges.
    Higher weight sum = stronger connections (tiebreaker).
    """
    if source == target:
        return [source], [], 0, 0

    heap = [(0, 0, source)]  # (str_sum, -weight_sum, node)
    settled = set()
    best_cost = {source: (0, 0)}
    prev = {}  # node -> (predecessor, strength)

    while heap:
        str_sum, neg_w, node = heapq.heappop(heap)
        if node in settled:
            continue
        settled.add(node)

        if node == target:
            path, strengths = [], []
            n = target
            while n != source:
                path.append(n)
                pred, s = prev[n]
                strengths.append(s)
                n = pred
            path.append(source)
            path.reverse()
            strengths.reverse()
            return path, strengths, -neg_w, len(path) - 1

        for nb, w, s in adj.get(node, []):
            if nb in settled:
                continue
            tier = STRENGTH_ORDER.get(s, 4)
            new_cost = (str_sum + tier, neg_w - w)
            old = best_cost.get(nb)
            if old is None or new_cost < old:
                best_cost[nb] = new_cost
                prev[nb] = (node, s)
                heapq.heappush(heap, (new_cost[0], new_cost[1], nb))

    return None, None, None, None


def max_weight_dijkstra(adj, source, target):
    """Pure weight-based maximum-weight Dijkstra."""
    if source == target:
        return [source], [], 0, 0

    dist = defaultdict(lambda: -1)
    dist[source] = 0
    prev = {}
    edge_str = {}
    heap = [(0, source)]
    visited = set()

    while heap:
        neg_w, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        current_w = -neg_w
        if u == target:
            break
        for nb, w, s in adj.get(u, []):
            nw = current_w + w
            if nw > dist[nb]:
                dist[nb] = nw
                prev[nb] = u
                edge_str[nb] = s
                heapq.heappush(heap, (-nw, nb))

    if target not in prev and source != target:
        return None, None, None, None

    path = []
    n = target
    while n != source:
        path.append(n)
        n = prev[n]
    path.append(source)
    path.reverse()
    strengths = [edge_str.get(p, "unknown") for p in path[1:]]
    return path, strengths, dist[target], len(path) - 1


def is_intuitive(path, node_domains):
    domains = [node_domains.get(n, "unknown") for n in path]
    seen = []
    revisits = 0
    for d in domains:
        if d in seen and d != seen[-1]:
            revisits += 1
        if not seen or seen[-1] != d:
            seen.append(d)
    return revisits == 0, revisits


def strength_score(strengths):
    if not strengths:
        return 0.0
    return sum(STRENGTH_ORDER.get(s, 4) for s in strengths) / len(strengths)


KNOWN_TRAP_NODES = {
    "KB-0004", "KB-0016", "SCR-0019", "SCR-0007", "SCR-0005",
    "SCR-0003", "SCR-0001", "KB-0002", "KB-0009"
}


def hits_trap(path):
    return [n for n in path[1:-1] if n in KNOWN_TRAP_NODES]


def define_scenarios():
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
        "--agent", agent, "--task", "strength-pathfinding",
        "--route", route_str, "--outcome", outcome, "--notes", notes
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return r.returncode == 0, r.stdout.strip()
    except Exception as e:
        return False, str(e)


def main():
    print("=" * 70)
    print("Pathfinder P2 — Strength-Priority Dijkstra")
    print("=" * 70)

    adj, node_domains = load_network()
    print(f"Loaded {len(node_domains)} nodes, {sum(len(v) for v in adj.values())} edges\n")

    scenarios = define_scenarios()
    results = []

    for i, (source, target, desc) in enumerate(scenarios):
        print(f"[{i+1:2d}/20] {source} -> {target}")

        sp_path, sp_str, sp_w, sp_h = strength_dijkstra(adj, source, target)
        pw_path, pw_str, pw_w, pw_h = max_weight_dijkstra(adj, source, target)

        sp_data = {"path": None}
        pw_data = {"path": None}
        cmp = {}

        if sp_path:
            sp_int, sp_rev = is_intuitive(sp_path, node_domains)
            sp_ss = strength_score(sp_str)
            sp_traps = hits_trap(sp_path)
            sp_data = {
                "path": sp_path, "strengths_used": sp_str,
                "total_weight": sp_w, "hops": sp_h,
                "strength_score": round(sp_ss, 3),
                "intuitive": sp_int, "domain_revisits": sp_rev,
                "domain_sequence": [node_domains.get(n, "?") for n in sp_path],
                "trap_nodes_hit": sp_traps,
            }
            print(f"  STR: {' -> '.join(sp_path)}  W={sp_w} H={sp_h} SS={sp_ss:.2f} Traps={sp_traps}")
        else:
            print("  STR: NO PATH")

        if pw_path:
            pw_int, pw_rev = is_intuitive(pw_path, node_domains)
            pw_ss = strength_score(pw_str)
            pw_traps = hits_trap(pw_path)
            pw_data = {
                "path": pw_path, "strengths_used": pw_str,
                "total_weight": pw_w, "hops": pw_h,
                "strength_score": round(pw_ss, 3),
                "intuitive": pw_int, "domain_revisits": pw_rev,
                "domain_sequence": [node_domains.get(n, "?") for n in pw_path],
                "trap_nodes_hit": pw_traps,
            }
            print(f"  WGT: {' -> '.join(pw_path)}  W={pw_w} H={pw_h} SS={pw_ss:.2f} Traps={pw_traps}")
        else:
            print("  WGT: NO PATH")

        if sp_path and pw_path:
            cmp = {
                "same_path": sp_path == pw_path,
                "strength_fewer_hops": sp_h < pw_h,
                "strength_same_hops": sp_h == pw_h,
                "strength_higher_weight": sp_w > pw_w,
                "strength_better_str_score": sp_ss < pw_ss,
                "strength_more_intuitive": sp_rev < pw_rev,
                "strength_avoids_traps": len(sp_traps) < len(pw_traps),
                "hop_diff": sp_h - pw_h,
                "weight_diff": sp_w - pw_w,
                "str_score_diff": round(sp_ss - pw_ss, 3),
            }
            tag = "SAME" if cmp["same_path"] else "DIFF"
            print(f"  CMP: {tag} hops:{sp_h}v{pw_h} W:{sp_w}v{pw_w} SS:{sp_ss:.2f}v{pw_ss:.2f}")

        results.append({
            "scenario": i + 1, "source": source, "target": target,
            "description": desc,
            "strength_priority": sp_data,
            "pure_weight": pw_data,
            "comparison": cmp,
        })

    # ---- Aggregate ----
    print("\n" + "=" * 70)
    print("AGGREGATE ANALYSIS")
    print("=" * 70)

    sp_found = [r for r in results if r["strength_priority"].get("path")]
    pw_found = [r for r in results if r["pure_weight"].get("path")]
    both = [r for r in results if r["strength_priority"].get("path") and r["pure_weight"].get("path")]

    same_ct = sum(1 for r in both if r["comparison"].get("same_path"))
    diff_ct = len(both) - same_ct
    fewer_h = sum(1 for r in both if r["comparison"].get("strength_fewer_hops"))
    more_h = sum(1 for r in both if r["comparison"]["hop_diff"] > 0)
    same_h = sum(1 for r in both if r["comparison"]["hop_diff"] == 0)
    better_ss = sum(1 for r in both if r["comparison"].get("strength_better_str_score"))
    higher_w = sum(1 for r in both if r["comparison"].get("strength_higher_weight"))
    more_int = sum(1 for r in both if r["comparison"].get("strength_more_intuitive"))
    avoids = sum(1 for r in both if r["comparison"].get("strength_avoids_traps"))

    def avg(lst, key):
        vals = [r[key] for r in lst]
        return sum(vals) / len(vals) if vals else 0

    sp_avg_h = avg([r["strength_priority"] for r in sp_found], "hops")
    pw_avg_h = avg([r["pure_weight"] for r in pw_found], "hops")
    sp_avg_w = avg([r["strength_priority"] for r in sp_found], "total_weight")
    pw_avg_w = avg([r["pure_weight"] for r in pw_found], "total_weight")
    sp_avg_ss = avg([r["strength_priority"] for r in sp_found], "strength_score")
    pw_avg_ss = avg([r["pure_weight"] for r in pw_found], "strength_score")

    sp_int_ct = sum(1 for r in sp_found if r["strength_priority"].get("intuitive"))
    pw_int_ct = sum(1 for r in pw_found if r["pure_weight"].get("intuitive"))

    sp_traps = sum(len(r["strength_priority"].get("trap_nodes_hit", [])) for r in sp_found)
    pw_traps = sum(len(r["pure_weight"].get("trap_nodes_hit", [])) for r in pw_found)

    sp_all_str, pw_all_str = [], []
    for r in sp_found:
        sp_all_str.extend(r["strength_priority"].get("strengths_used", []))
    for r in pw_found:
        pw_all_str.extend(r["pure_weight"].get("strengths_used", []))

    print(f"\nPaths found: Str={len(sp_found)}/20, Wgt={len(pw_found)}/20")
    print(f"Same path: {same_ct}, Different: {diff_ct}")
    print(f"\nStrength wins ({len(both)} comparable):")
    print(f"  Fewer hops:       {fewer_h} (same:{same_h}, more:{more_h})")
    print(f"  Better str score: {better_ss}")
    print(f"  Higher weight:    {higher_w}")
    print(f"  More intuitive:   {more_int}")
    print(f"  Avoids traps:     {avoids}")
    print(f"\nIntuitive: Str={sp_int_ct}/{len(sp_found)}, Wgt={pw_int_ct}/{len(pw_found)}")
    print(f"Avg hops: Str={sp_avg_h:.2f}, Wgt={pw_avg_h:.2f}")
    print(f"Avg weight: Str={sp_avg_w:.1f}, Wgt={pw_avg_w:.1f}")
    print(f"Avg str score: Str={sp_avg_ss:.3f}, Wgt={pw_avg_ss:.3f}")
    print(f"Traps: Str={sp_traps}, Wgt={pw_traps}")
    print(f"Str dist (strength): {dict(Counter(sp_all_str))}")
    print(f"Str dist (weight):   {dict(Counter(pw_all_str))}")

    # Observations
    obs = []
    if diff_ct > 0:
        obs.append(f"Strength-priority chose different paths in {diff_ct}/{len(both)} scenarios.")
    if sp_avg_ss < pw_avg_ss:
        obs.append(f"Strength-priority achieves better avg strength score ({sp_avg_ss:.3f} vs {pw_avg_ss:.3f}) — traverses higher-quality edges.")
    elif sp_avg_ss > pw_avg_ss:
        obs.append(f"Pure-weight paths have better strength score ({pw_avg_ss:.3f} vs {sp_avg_ss:.3f}) — high-weight edges already tend to be primary.")
    if sp_traps < pw_traps:
        obs.append(f"Strength-priority avoids S3-identified traps better: {sp_traps} vs {pw_traps}.")
    elif sp_traps == pw_traps:
        obs.append(f"Both hit same number of trap nodes ({sp_traps}).")
    else:
        obs.append(f"Strength-priority hits MORE traps ({sp_traps} vs {pw_traps}).")
    if sp_avg_w < pw_avg_w:
        obs.append(f"Strength-priority trades weight ({sp_avg_w:.1f} vs {pw_avg_w:.1f}) for edge quality — expected tradeoff.")
    if fewer_h > more_h:
        obs.append(f"Strength-priority finds shorter paths ({fewer_h} shorter vs {more_h} longer).")
    elif more_h > fewer_h:
        obs.append(f"Strength-priority uses more hops ({more_h} longer vs {fewer_h} shorter) — trades directness for quality.")
    if sp_int_ct != pw_int_ct:
        obs.append(f"Intuitive path difference: Str={sp_int_ct}, Wgt={pw_int_ct}.")

    summary = {
        "total_scenarios": 20,
        "strength_paths_found": len(sp_found),
        "weight_paths_found": len(pw_found),
        "same_path_count": same_ct,
        "different_path_count": diff_ct,
        "strength_wins": {
            "fewer_hops": fewer_h, "better_strength_score": better_ss,
            "higher_weight": higher_w, "more_intuitive": more_int,
            "avoids_traps": avoids,
        },
        "averages": {
            "strength_avg_hops": round(sp_avg_h, 2), "weight_avg_hops": round(pw_avg_h, 2),
            "strength_avg_weight": round(sp_avg_w, 2), "weight_avg_weight": round(pw_avg_w, 2),
            "strength_avg_str_score": round(sp_avg_ss, 3), "weight_avg_str_score": round(pw_avg_ss, 3),
        },
        "trap_analysis": {
            "strength_traps_hit": sp_traps, "weight_traps_hit": pw_traps,
            "known_trap_nodes": sorted(KNOWN_TRAP_NODES),
        },
        "intuitive_paths": {"strength": sp_int_ct, "weight": pw_int_ct},
        "strength_distributions": {
            "strength_priority": dict(Counter(sp_all_str)),
            "weight_priority": dict(Counter(pw_all_str)),
        },
    }

    report = {
        "report": "pathfind_p2_strength",
        "timestamp": datetime.now().isoformat(),
        "algorithm": "Strength-Priority Dijkstra: minimize (strength_tier_sum, -weight_sum) lexicographically",
        "comparison_algorithm": "Max-Weight Dijkstra: maximize total edge weight",
        "description": "Tests whether preferring edges by strength classification (primary>supporting>related>tangential>unclassified) leads to faster, more logical, and trap-avoiding paths vs pure weight routing.",
        "scenarios_spec": "10 SOPs x 2 targets each (1 SCR + 1 SRC per SOP)",
        "sops_tested": ["SOP-011", "SOP-012", "SOP-015", "SOP-017", "SOP-023", "SOP-025", "SOP-027", "SOP-028", "SOP-029", "SOP-G01"],
        "summary": summary,
        "observations": obs,
        "paths": results,
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {REPORT_PATH}")

    # Log routes
    print("\n--- Logging 20 routes ---")
    agents = ["p2-1", "p2-2", "p2-3", "p2-4"]
    for i, r in enumerate(results):
        agent = agents[i % 4]
        sp = r["strength_priority"]
        if sp.get("path"):
            ok, _ = log_route(agent, sp["path"], sp["total_weight"], sp.get("strengths_used", []))
        else:
            ok, _ = log_route(agent, [r["source"], r["target"]], 0, [], outcome="no_path")
        print(f"  [{'OK' if ok else 'FAIL'}] {agent}: {r['source']} -> {r['target']}")

    print("\nDone.")


if __name__ == "__main__":
    main()
