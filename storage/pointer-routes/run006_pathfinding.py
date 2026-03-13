#!/usr/bin/env python3
"""
Run-006 Division 4A: Pathfinding
Simulates 4 agents: P2-1, P2-2 (strength-priority), P4-1, P4-2 (hybrid).

P2-1/P2-2: Strength-priority Dijkstra
  Edge cost: primary=0, supporting=1, related=2, tangential=3, unclassified=4
  Tiebreaker: higher weight preferred
  Comparison: pure max-weight baseline

P4-1/P4-2: Hybrid pathfinding
  Score = alpha_w*weight + alpha_s*strength_score + alpha_t*tag_coherence
  Three tunings: (0.4,0.3,0.3), (0.5,0.25,0.25), (0.33,0.33,0.33)
"""

import json
import heapq
import subprocess
import sys
from collections import defaultdict, Counter
from datetime import datetime, timezone
from pathlib import Path

NETWORK_PATH = Path("/home/user/Claude/storage/pointer-network.json")
OUTPUT_PATH = Path("/home/user/Claude/storage/pointer-routes/run006_pathfinding_p2_p4.json")
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"

STRENGTH_COST = {
    "primary": 0, "supporting": 1, "related": 2,
    "tangential": 3, "unclassified": 4,
}

STRENGTH_BONUS = {
    "primary": 10, "supporting": 7, "related": 4,
    "tangential": 1, "unclassified": 2,
}

KNOWN_TRAP_NODES = {
    "KB-0004", "KB-0016", "SCR-0019", "SCR-0007", "SCR-0005",
    "SCR-0003", "SCR-0001", "KB-0002", "KB-0009"
}

# 10 SOPs x 2 targets (1 SCR + 1 SRC) = 20 scenarios
SCENARIOS = [
    ("SOP-011", "SCR-0043", "SOP-011 to cross-domain script"),
    ("SOP-011", "SRC-0017", "SOP-011 to source"),
    ("SOP-012", "SCR-0057", "SOP-012 to route tracker script"),
    ("SOP-012", "SRC-0020", "SOP-012 to mid-range source"),
    ("SOP-015", "SCR-0005", "SOP-015 to low script"),
    ("SOP-015", "SRC-0030", "SOP-015 to source"),
    ("SOP-017", "SCR-0010", "SOP-017 to dedup script"),
    ("SOP-017", "SRC-0040", "SOP-017 to high source"),
    ("SOP-023", "SCR-0025", "SOP-023 to mid script"),
    ("SOP-023", "SRC-0001", "SOP-023 to first source"),
    ("SOP-025", "SCR-0035", "SOP-025 to mid script"),
    ("SOP-025", "SRC-0045", "SOP-025 to high source"),
    ("SOP-027", "SCR-0040", "SOP-027 to high script"),
    ("SOP-027", "SRC-0015", "SOP-027 to source"),
    ("SOP-028", "SCR-0030", "SOP-028 to mid script"),
    ("SOP-028", "SRC-0005", "SOP-028 to source"),
    ("SOP-029", "SCR-0050", "SOP-029 to high script"),
    ("SOP-029", "SRC-0035", "SOP-029 to mid source"),
    ("SOP-G01", "SCR-0001", "SOP-G01 to first script"),
    ("SOP-G01", "SRC-0025", "SOP-G01 to mid source"),
]

HYBRID_TUNINGS = {
    "balanced":       (0.4, 0.3, 0.3),
    "weight_heavy":   (0.5, 0.25, 0.25),
    "equal":          (0.33, 0.33, 0.33),
}


def load_network():
    """Load pointer network, build adjacency list and tag index."""
    with open(NETWORK_PATH) as f:
        data = json.load(f)

    adj = defaultdict(list)
    node_domains = {}
    node_tags = {}

    for node_id, node_data in data["nodes"].items():
        node_domains[node_id] = node_data.get("domain", "unknown")
        tags = set(node_data.get("tags", []))
        for ptr in node_data.get("pointers", []):
            for reason in ptr.get("reasons", []):
                if reason.startswith("shared_tags:"):
                    for t in reason.split(":", 1)[1].split(","):
                        tags.add(t.strip())
            adj[node_id].append({
                "to": ptr["to"],
                "weight": ptr.get("weight", 0),
                "strength": ptr.get("strength", "unclassified"),
                "reasons": ptr.get("reasons", []),
            })
        node_tags[node_id] = tags

    return adj, node_domains, node_tags


# -------------------------------------------------------
# P2: Strength-Priority Dijkstra
# -------------------------------------------------------

def strength_dijkstra(adj, source, target):
    """Dijkstra minimizing (strength_tier_sum, -weight_sum)."""
    if source == target:
        return [source], [], 0, 0

    heap = [(0, 0, 0, source)]  # (str_sum, -weight_sum, counter, node)
    counter = 1
    settled = set()
    best_cost = {source: (0, 0)}
    prev = {}

    while heap:
        str_sum, neg_w, _, node = heapq.heappop(heap)
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

        for edge in adj.get(node, []):
            nb = edge["to"]
            if nb in settled:
                continue
            tier = STRENGTH_COST.get(edge["strength"], 4)
            new_cost = (str_sum + tier, neg_w - edge["weight"])
            old = best_cost.get(nb)
            if old is None or new_cost < old:
                best_cost[nb] = new_cost
                prev[nb] = (node, edge["strength"])
                heapq.heappush(heap, (new_cost[0], new_cost[1], counter, nb))
                counter += 1

    return None, None, None, None


def max_weight_dijkstra(adj, source, target):
    """Pure max-weight Dijkstra baseline."""
    if source == target:
        return [source], [], 0, 0

    dist = defaultdict(lambda: -1)
    dist[source] = 0
    prev = {}
    edge_str = {}
    heap = [(0, 0, source)]
    counter = 1
    visited = set()

    while heap:
        neg_w, _, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)
        current_w = -neg_w
        if u == target:
            break
        for edge in adj.get(u, []):
            nb = edge["to"]
            nw = current_w + edge["weight"]
            if nw > dist[nb]:
                dist[nb] = nw
                prev[nb] = u
                edge_str[nb] = edge["strength"]
                heapq.heappush(heap, (-nw, counter, nb))
                counter += 1

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


# -------------------------------------------------------
# P4: Hybrid Pathfinding
# -------------------------------------------------------

def tag_coherence(node_tags, neighbor_id, target_id):
    """Jaccard similarity of neighbor tags with target tags, scaled 0-10."""
    nb_tags = node_tags.get(neighbor_id, set())
    tgt_tags = node_tags.get(target_id, set())
    if not nb_tags and not tgt_tags:
        return 5.0
    if not nb_tags or not tgt_tags:
        return 2.0
    intersection = nb_tags & tgt_tags
    union = nb_tags | tgt_tags
    return (len(intersection) / len(union)) * 10.0 if union else 0.0


def hybrid_dijkstra(adj, node_tags, source, target, alpha_w, alpha_s, alpha_t, max_hops=10):
    """Dijkstra maximizing: alpha_w*weight + alpha_s*strength_bonus + alpha_t*tag_coherence."""
    if source == target:
        return [source], [], 0, 0

    counter = 0
    heap = [(0.0, counter, 0, source)]
    counter += 1
    visited = set()
    best_score = {source: 0.0}
    prev = {}

    while heap:
        neg_score, _, hops, node = heapq.heappop(heap)
        if node in visited:
            continue
        visited.add(node)

        if node == target:
            path, details = [], []
            n = target
            while n != source:
                path.append(n)
                pred, info = prev[n]
                details.append(info)
                n = pred
            path.append(source)
            path.reverse()
            details.reverse()
            return path, details, -neg_score, len(path) - 1

        if hops >= max_hops:
            continue

        for edge in adj.get(node, []):
            nb = edge["to"]
            if nb in visited:
                continue

            w = edge["weight"]
            s_bonus = STRENGTH_BONUS.get(edge["strength"], 2)
            t_coh = tag_coherence(node_tags, nb, target)
            e_score = alpha_w * w + alpha_s * s_bonus + alpha_t * t_coh

            new_score = -neg_score + e_score
            if new_score > best_score.get(nb, -1):
                best_score[nb] = new_score
                prev[nb] = (node, {
                    "from": node, "to": nb,
                    "weight": w, "strength": edge["strength"],
                    "tag_coherence": round(t_coh, 3),
                    "edge_score": round(e_score, 3),
                })
                heapq.heappush(heap, (-new_score, counter, hops + 1, nb))
                counter += 1

    return None, None, None, None


# -------------------------------------------------------
# Analysis helpers
# -------------------------------------------------------

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


def avg_strength_score(strengths):
    if not strengths:
        return 0.0
    return sum(STRENGTH_COST.get(s, 4) for s in strengths) / len(strengths)


def hits_trap(path):
    return [n for n in path[1:-1] if n in KNOWN_TRAP_NODES]


PENDING_LOGS = []

def log_route_cmd(agent, task_name, route_str, outcome, hops, notes=""):
    """Queue a route log for batch execution later."""
    PENDING_LOGS.append({
        "agent": agent, "task": task_name,
        "route": route_str, "outcome": outcome, "notes": notes,
    })
    return True


def flush_route_logs():
    """Execute all pending route log commands via route_tracker.py."""
    print(f"\n--- Logging {len(PENDING_LOGS)} routes via route_tracker.py ---")
    ok_count = 0
    for entry in PENDING_LOGS:
        cmd = [
            "python3", ROUTE_TRACKER, "log",
            "--agent", entry["agent"], "--task", entry["task"],
            "--route", entry["route"], "--outcome", entry["outcome"],
        ]
        if entry.get("notes"):
            cmd.extend(["--notes", entry["notes"]])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                ok_count += 1
                print(f"  [OK] {entry['agent']}: {entry['route'][:80]}")
            else:
                print(f"  [FAIL] {entry['agent']}: {r.stderr[:100] if r.stderr else 'unknown error'}")
        except Exception as e:
            print(f"  [ERR] {entry['agent']}: {e}")
    print(f"  Logged {ok_count}/{len(PENDING_LOGS)} routes successfully.")
    PENDING_LOGS.clear()


# -------------------------------------------------------
# P2 runner
# -------------------------------------------------------

def run_p2_scenarios(adj, node_domains):
    print("=" * 70)
    print("P2-1/P2-2: Strength-Priority Dijkstra vs Max-Weight Baseline")
    print("=" * 70)

    results = []
    for i, (source, target, desc) in enumerate(SCENARIOS):
        agent = "p2-1" if i % 2 == 0 else "p2-2"
        print(f"  [{agent}] [{i+1:2d}/20] {source} -> {target}: {desc}")

        sp_path, sp_str, sp_w, sp_h = strength_dijkstra(adj, source, target)
        mw_path, mw_str, mw_w, mw_h = max_weight_dijkstra(adj, source, target)

        sp_data = {"found": False}
        mw_data = {"found": False}
        comparison = {}

        if sp_path:
            sp_int, sp_rev = is_intuitive(sp_path, node_domains)
            sp_ss = avg_strength_score(sp_str)
            sp_traps = hits_trap(sp_path)
            sp_data = {
                "found": True, "path": sp_path, "strengths": sp_str,
                "total_weight": sp_w, "hops": sp_h,
                "avg_strength_cost": round(sp_ss, 3),
                "intuitive": sp_int, "domain_revisits": sp_rev,
                "domains": [node_domains.get(n, "?") for n in sp_path],
                "trap_nodes_hit": sp_traps,
            }
            route_str = " -> ".join(sp_path)
            log_route_cmd(agent, "strength-pathfinding", route_str, "success", sp_h,
                         f"weight={sp_w},str_score={sp_ss:.3f}")
            print(f"    STR: {route_str}  W={sp_w} H={sp_h} SS={sp_ss:.2f}")
        else:
            log_route_cmd(agent, "strength-pathfinding", f"{source} -> {target}", "no_path", 0)
            print(f"    STR: NO PATH")

        if mw_path:
            mw_int, mw_rev = is_intuitive(mw_path, node_domains)
            mw_ss = avg_strength_score(mw_str)
            mw_traps = hits_trap(mw_path)
            mw_data = {
                "found": True, "path": mw_path, "strengths": mw_str,
                "total_weight": mw_w, "hops": mw_h,
                "avg_strength_cost": round(mw_ss, 3),
                "intuitive": mw_int, "domain_revisits": mw_rev,
                "domains": [node_domains.get(n, "?") for n in mw_path],
                "trap_nodes_hit": mw_traps,
            }
            print(f"    MWB: {' -> '.join(mw_path)}  W={mw_w} H={mw_h} SS={mw_ss:.2f}")
        else:
            print(f"    MWB: NO PATH")

        if sp_path and mw_path:
            comparison = {
                "same_path": sp_path == mw_path,
                "str_fewer_hops": sp_h < mw_h,
                "str_better_strength": sp_ss < mw_ss,
                "str_higher_weight": sp_w > mw_w,
                "str_more_intuitive": sp_rev < mw_rev,
                "str_avoids_traps": len(sp_traps) < len(mw_traps),
                "hop_diff": sp_h - mw_h,
                "weight_diff": sp_w - mw_w,
                "strength_score_diff": round(sp_ss - mw_ss, 3),
            }

        results.append({
            "scenario_id": i + 1, "source": source, "target": target,
            "description": desc, "agent": agent,
            "strength_priority": sp_data, "max_weight_baseline": mw_data,
            "comparison": comparison,
        })

    return results


# -------------------------------------------------------
# P4 runner
# -------------------------------------------------------

def run_p4_scenarios(adj, node_domains, node_tags):
    print("\n" + "=" * 70)
    print("P4-1/P4-2: Hybrid Pathfinding (weight + strength + tag coherence)")
    print("=" * 70)

    all_tuning_results = {}

    for tuning_name, (aw, as_, at) in HYBRID_TUNINGS.items():
        print(f"\n--- Tuning: {tuning_name} (w={aw}, s={as_}, t={at}) ---")
        tuning_results = []

        for i, (source, target, desc) in enumerate(SCENARIOS):
            agent = "p4-1" if i % 2 == 0 else "p4-2"

            path, details, total_score, hops = hybrid_dijkstra(
                adj, node_tags, source, target, aw, as_, at
            )

            result = {"found": False, "scenario_id": i + 1, "source": source,
                      "target": target, "description": desc, "agent": agent}

            if path:
                strengths = [d["strength"] for d in details]
                avg_tc = sum(d["tag_coherence"] for d in details) / len(details) if details else 0
                total_w = sum(d["weight"] for d in details) if details else 0
                p_int, p_rev = is_intuitive(path, node_domains)
                traps = hits_trap(path)

                result.update({
                    "found": True, "path": path, "hops": hops,
                    "total_hybrid_score": round(total_score, 3),
                    "total_weight": total_w,
                    "avg_tag_coherence": round(avg_tc, 3),
                    "strengths": strengths,
                    "avg_strength_cost": round(avg_strength_score(strengths), 3),
                    "intuitive": p_int, "domain_revisits": p_rev,
                    "domains": [node_domains.get(n, "?") for n in path],
                    "trap_nodes_hit": traps,
                    "edge_details": details,
                })

                route_str = " -> ".join(path)
                log_route_cmd(agent, "hybrid-pathfinding", route_str, "success", hops,
                             f"tuning={tuning_name},score={total_score:.2f},alpha=({aw},{as_},{at})")
                print(f"  [{agent}] [{i+1:2d}/20] {source}->{target}: {route_str} H={hops} S={total_score:.2f}")
            else:
                log_route_cmd(agent, "hybrid-pathfinding", f"{source} -> {target}", "no_path", 0,
                             f"tuning={tuning_name}")
                print(f"  [{agent}] [{i+1:2d}/20] {source}->{target}: NO PATH")

            tuning_results.append(result)

        all_tuning_results[tuning_name] = tuning_results

    return all_tuning_results


# -------------------------------------------------------
# Aggregate analysis
# -------------------------------------------------------

def analyze_p2(results):
    sp_found = [r for r in results if r["strength_priority"]["found"]]
    mw_found = [r for r in results if r["max_weight_baseline"]["found"]]
    both = [r for r in results if r["strength_priority"]["found"] and r["max_weight_baseline"]["found"]]

    same_ct = sum(1 for r in both if r["comparison"].get("same_path"))
    fewer_h = sum(1 for r in both if r["comparison"].get("str_fewer_hops"))
    better_ss = sum(1 for r in both if r["comparison"].get("str_better_strength"))
    higher_w = sum(1 for r in both if r["comparison"].get("str_higher_weight"))
    more_int = sum(1 for r in both if r["comparison"].get("str_more_intuitive"))
    avoids = sum(1 for r in both if r["comparison"].get("str_avoids_traps"))

    def safe_avg(items, key):
        vals = [i[key] for i in items if key in i]
        return sum(vals) / len(vals) if vals else 0

    sp_metrics = [r["strength_priority"] for r in sp_found]
    mw_metrics = [r["max_weight_baseline"] for r in mw_found]

    sp_all_str, mw_all_str = [], []
    for r in sp_found:
        sp_all_str.extend(r["strength_priority"].get("strengths", []))
    for r in mw_found:
        mw_all_str.extend(r["max_weight_baseline"].get("strengths", []))

    return {
        "paths_found": {"strength": len(sp_found), "max_weight": len(mw_found), "both": len(both)},
        "same_path_count": same_ct,
        "different_path_count": len(both) - same_ct,
        "strength_wins": {
            "fewer_hops": fewer_h, "better_strength_score": better_ss,
            "higher_weight": higher_w, "more_intuitive": more_int,
            "avoids_traps": avoids,
        },
        "averages": {
            "strength_avg_hops": round(safe_avg(sp_metrics, "hops"), 2),
            "weight_avg_hops": round(safe_avg(mw_metrics, "hops"), 2),
            "strength_avg_weight": round(safe_avg(sp_metrics, "total_weight"), 2),
            "weight_avg_weight": round(safe_avg(mw_metrics, "total_weight"), 2),
            "strength_avg_str_cost": round(safe_avg(sp_metrics, "avg_strength_cost"), 3),
            "weight_avg_str_cost": round(safe_avg(mw_metrics, "avg_strength_cost"), 3),
        },
        "intuitive_paths": {
            "strength": sum(1 for r in sp_found if r["strength_priority"].get("intuitive")),
            "max_weight": sum(1 for r in mw_found if r["max_weight_baseline"].get("intuitive")),
        },
        "trap_analysis": {
            "strength_traps": sum(len(r["strength_priority"].get("trap_nodes_hit", [])) for r in sp_found),
            "weight_traps": sum(len(r["max_weight_baseline"].get("trap_nodes_hit", [])) for r in mw_found),
        },
        "strength_distributions": {
            "strength_priority": dict(Counter(sp_all_str)),
            "max_weight": dict(Counter(mw_all_str)),
        },
    }


def analyze_p4(all_tuning_results):
    tuning_summaries = {}

    for tname, results in all_tuning_results.items():
        found = [r for r in results if r["found"]]
        n_found = len(found)
        n_total = len(results)

        if n_found == 0:
            tuning_summaries[tname] = {
                "alpha": list(HYBRID_TUNINGS[tname]),
                "paths_found": 0, "total_scenarios": n_total,
                "success_rate": 0.0,
            }
            continue

        avg_hops = sum(r["hops"] for r in found) / n_found
        avg_score = sum(r["total_hybrid_score"] for r in found) / n_found
        avg_weight = sum(r["total_weight"] for r in found) / n_found
        avg_tc = sum(r["avg_tag_coherence"] for r in found) / n_found
        avg_sc = sum(r["avg_strength_cost"] for r in found) / n_found
        n_intuitive = sum(1 for r in found if r.get("intuitive"))
        n_traps = sum(len(r.get("trap_nodes_hit", [])) for r in found)

        all_str = []
        for r in found:
            all_str.extend(r.get("strengths", []))

        success_score = (n_found / n_total) * 40
        efficiency_score = (1 / max(avg_hops, 1)) * 20 * 3
        tag_score = (avg_tc / 10) * 20
        weight_score = min(avg_weight / 30, 1) * 20
        composite = success_score + efficiency_score + tag_score + weight_score

        tuning_summaries[tname] = {
            "alpha": list(HYBRID_TUNINGS[tname]),
            "paths_found": n_found, "total_scenarios": n_total,
            "success_rate": round(n_found / n_total * 100, 1),
            "avg_hops": round(avg_hops, 2),
            "avg_hybrid_score": round(avg_score, 2),
            "avg_weight": round(avg_weight, 2),
            "avg_tag_coherence": round(avg_tc, 3),
            "avg_strength_cost": round(avg_sc, 3),
            "intuitive_paths": n_intuitive,
            "traps_hit": n_traps,
            "strength_distribution": dict(Counter(all_str)),
            "composite_score": round(composite, 2),
        }

    ranked = sorted(tuning_summaries.items(), key=lambda x: x[1].get("composite_score", 0), reverse=True)
    best = ranked[0][0] if ranked else None
    return tuning_summaries, ranked, best


def generate_observations(p2_analysis, p4_summaries, p4_best):
    obs = []
    a = p2_analysis

    if a["different_path_count"] > 0:
        obs.append(f"Strength-priority diverges from max-weight in {a['different_path_count']}/{a['paths_found']['both']} scenarios.")
    if a["averages"]["strength_avg_str_cost"] < a["averages"]["weight_avg_str_cost"]:
        obs.append(f"Strength-priority uses higher-quality edges (avg cost {a['averages']['strength_avg_str_cost']:.3f} vs {a['averages']['weight_avg_str_cost']:.3f}).")
    if a["averages"]["strength_avg_weight"] < a["averages"]["weight_avg_weight"]:
        obs.append(f"Strength-priority trades weight ({a['averages']['strength_avg_weight']:.1f} vs {a['averages']['weight_avg_weight']:.1f}) for edge quality.")
    if a["trap_analysis"]["strength_traps"] < a["trap_analysis"]["weight_traps"]:
        obs.append(f"Strength-priority avoids traps better ({a['trap_analysis']['strength_traps']} vs {a['trap_analysis']['weight_traps']}).")
    elif a["trap_analysis"]["strength_traps"] == a["trap_analysis"]["weight_traps"]:
        obs.append(f"Both methods hit equal trap nodes ({a['trap_analysis']['strength_traps']}).")

    if p4_best:
        best_s = p4_summaries[p4_best]
        obs.append(f"Best hybrid tuning: {p4_best} (alpha={best_s['alpha']}) with composite score {best_s['composite_score']}.")
        for tname, ts in p4_summaries.items():
            if tname != p4_best:
                obs.append(f"  {tname} (alpha={ts['alpha']}): composite={ts.get('composite_score', 0)}, "
                          f"avg_hops={ts.get('avg_hops', 'N/A')}, avg_score={ts.get('avg_hybrid_score', 'N/A')}.")

    if p4_best and p4_summaries[p4_best].get("avg_hops"):
        p4_hops = p4_summaries[p4_best]["avg_hops"]
        p2_hops = a["averages"]["strength_avg_hops"]
        if p4_hops < p2_hops:
            obs.append(f"Hybrid pathfinding achieves fewer hops ({p4_hops:.2f}) than strength-priority ({p2_hops:.2f}).")
        elif p4_hops > p2_hops:
            obs.append(f"Strength-priority achieves fewer hops ({p2_hops:.2f}) than hybrid ({p4_hops:.2f}).")
        else:
            obs.append(f"Both methods achieve similar hop counts (~{p2_hops:.2f}).")

    return obs


def main():
    print("Run-006 Division 4A: Pathfinding")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Scenarios: {len(SCENARIOS)}")
    print()

    adj, node_domains, node_tags = load_network()
    print(f"Network: {len(node_domains)} nodes, {sum(len(v) for v in adj.values())} edges\n")

    # Verify scenario nodes
    missing = set()
    for src, tgt, _ in SCENARIOS:
        if src not in node_domains:
            missing.add(src)
        if tgt not in node_domains:
            missing.add(tgt)
    if missing:
        print(f"WARNING: Missing nodes: {missing}")

    # Run P2
    p2_results = run_p2_scenarios(adj, node_domains)
    p2_analysis = analyze_p2(p2_results)

    # Run P4
    p4_all_results = run_p4_scenarios(adj, node_domains, node_tags)
    p4_summaries, p4_ranked, p4_best = analyze_p4(p4_all_results)

    # Cross-method observations
    observations = generate_observations(p2_analysis, p4_summaries, p4_best)

    print("\n" + "=" * 70)
    print("COMPARATIVE ANALYSIS")
    print("=" * 70)
    print("\nP2 Summary:")
    print(f"  Paths found: {p2_analysis['paths_found']}")
    print(f"  Same/Different: {p2_analysis['same_path_count']}/{p2_analysis['different_path_count']}")
    print(f"  Strength wins: {p2_analysis['strength_wins']}")
    print(f"  Avg hops: str={p2_analysis['averages']['strength_avg_hops']}, wgt={p2_analysis['averages']['weight_avg_hops']}")
    print(f"  Intuitive: {p2_analysis['intuitive_paths']}")

    print("\nP4 Tuning Ranking:")
    for i, (tname, ts) in enumerate(p4_ranked):
        marker = " <<< BEST" if tname == p4_best else ""
        print(f"  {i+1}. {tname} (alpha={ts['alpha']}): composite={ts.get('composite_score', 0)}, "
              f"found={ts['paths_found']}/{ts['total_scenarios']}, "
              f"hops={ts.get('avg_hops', 'N/A')}, score={ts.get('avg_hybrid_score', 'N/A')}{marker}")

    print("\nObservations:")
    for o in observations:
        print(f"  - {o}")

    # Flush all route logs
    flush_route_logs()

    # Build final report
    report = {
        "report": "run006_pathfinding_p2_p4",
        "run": "Run-006",
        "division": "4A",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": ["p2-1", "p2-2", "p4-1", "p4-2"],
        "scenarios_count": len(SCENARIOS),
        "scenarios_spec": "10 SOPs x 2 targets each (1 SCR + 1 SRC per SOP)",
        "sops_tested": sorted(set(s[0] for s in SCENARIOS)),
        "p2_strength_priority": {
            "algorithm": "Modified Dijkstra: cost = (sum_strength_tier, -sum_weight) lexicographic",
            "edge_costs": STRENGTH_COST,
            "baseline": "Pure max-weight Dijkstra",
            "summary": p2_analysis,
            "scenarios": p2_results,
        },
        "p4_hybrid": {
            "algorithm": "Dijkstra maximizing: alpha_w*weight + alpha_s*strength_bonus + alpha_t*tag_coherence",
            "strength_bonuses": STRENGTH_BONUS,
            "tunings_tested": {k: {"alpha_weight": v[0], "alpha_strength": v[1], "alpha_tag": v[2]}
                              for k, v in HYBRID_TUNINGS.items()},
            "tuning_summaries": p4_summaries,
            "tuning_ranking": [{"rank": i+1, "tuning": n, "composite_score": s.get("composite_score", 0)}
                              for i, (n, s) in enumerate(p4_ranked)],
            "best_tuning": p4_best,
            "scenarios_by_tuning": {
                tname: [
                    {k: v for k, v in r.items() if k != "edge_details"}
                    for r in results
                ]
                for tname, results in p4_all_results.items()
            },
        },
        "comparative_analysis": {
            "observations": observations,
            "method_comparison": {
                "p2_avg_hops": p2_analysis["averages"]["strength_avg_hops"],
                "p2_avg_weight": p2_analysis["averages"]["strength_avg_weight"],
                "p2_avg_strength_cost": p2_analysis["averages"]["strength_avg_str_cost"],
                "p2_intuitive_rate": f"{p2_analysis['intuitive_paths']['strength']}/{p2_analysis['paths_found']['strength']}",
                "p4_best_tuning": p4_best,
                "p4_best_avg_hops": p4_summaries.get(p4_best, {}).get("avg_hops"),
                "p4_best_avg_weight": p4_summaries.get(p4_best, {}).get("avg_weight"),
                "p4_best_avg_tag_coherence": p4_summaries.get(p4_best, {}).get("avg_tag_coherence"),
                "p4_best_composite": p4_summaries.get(p4_best, {}).get("composite_score"),
            },
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {OUTPUT_PATH}")
    print("Done.")


if __name__ == "__main__":
    main()
