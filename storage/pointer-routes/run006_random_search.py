#!/usr/bin/env python3
"""Run-006 Division 5A: Random Searches (RS-1 through RS-4)"""

import json
import random
import subprocess
import sys
from collections import defaultdict, Counter
from pathlib import Path

random.seed(42)  # reproducible

NETWORK_PATH = "/home/user/Claude/storage/pointer-network.json"
OUTPUT_PATH = "/home/user/Claude/storage/pointer-routes/run006_random_search_a.json"
TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"

def log_route(agent, route_str, outcome, hops):
    try:
        subprocess.run([
            "python3", TRACKER, "log",
            "--agent", agent, "--task", "random-search",
            "--route", route_str, "--outcome", outcome, "--hops", str(hops)
        ], capture_output=True, timeout=5)
    except Exception:
        pass

print("Loading pointer network...")
with open(NETWORK_PATH) as f:
    network = json.load(f)

nodes_data = network["nodes"]
all_node_ids = list(nodes_data.keys())
print(f"Loaded {len(all_node_ids)} nodes")

# Build adjacency for fast lookup
adjacency = {}
all_edges = []
for nid, ndata in nodes_data.items():
    pointers = ndata.get("pointers", [])
    adjacency[nid] = pointers
    for p in pointers:
        all_edges.append((nid, p["to"], p.get("weight", 0), p.get("strength", ""), p.get("reasons", [])))

print(f"Total edges: {len(all_edges)}")

# ============================================================
# RS-1: Random Walk Analysis
# ============================================================
print("\n=== RS-1: Random Walk Analysis ===")

visit_count = Counter()
walk_results = []
interesting_walks = []

for walk_idx in range(50):
    start = random.choice(all_node_ids)
    walk_len = random.randint(5, 15)
    path = [start]
    visit_count[start] += 1

    for step in range(walk_len):
        current = path[-1]
        neighbors = adjacency.get(current, [])
        if not neighbors:
            break
        next_node = random.choice(neighbors)["to"]
        # next_node might not exist in our node list (dangling ref)
        path.append(next_node)
        visit_count[next_node] += 1

    walk_results.append({
        "walk_id": walk_idx,
        "start": start,
        "target_length": walk_len,
        "actual_length": len(path) - 1,
        "path": path,
        "terminated_early": len(path) - 1 < walk_len
    })

    # Log interesting walks (long ones that didn't terminate early)
    if len(path) - 1 >= 10:
        route_str = " -> ".join(path[:6]) + " -> ..."
        log_route("rs-1", route_str, "success", len(path) - 1)
        interesting_walks.append(walk_idx)

# Compute attractor/island analysis
total_visits = sum(visit_count.values())
avg_visits = total_visits / max(len(visit_count), 1)
most_visited = visit_count.most_common(20)
never_visited = [n for n in all_node_ids if visit_count[n] == 0]

# Early termination stats
early_terms = [w for w in walk_results if w["terminated_early"]]

# Path diversity: how many unique nodes across all walks
all_visited_unique = set()
for w in walk_results:
    all_visited_unique.update(w["path"])

# Attractor nodes: visited >= 3x average
attractor_threshold = avg_visits * 3
attractors = [(n, c) for n, c in visit_count.most_common() if c >= attractor_threshold]

rs1_results = {
    "total_walks": 50,
    "total_visits": total_visits,
    "unique_nodes_visited": len(all_visited_unique),
    "coverage_pct": round(100 * len(all_visited_unique) / len(all_node_ids), 1),
    "avg_visits_per_visited_node": round(avg_visits, 2),
    "early_terminations": len(early_terms),
    "early_termination_rate": round(100 * len(early_terms) / 50, 1),
    "top_20_most_visited": [{"node": n, "visits": c} for n, c in most_visited],
    "attractor_nodes": [{"node": n, "visits": c} for n, c in attractors],
    "attractor_count": len(attractors),
    "island_nodes_never_reached": never_visited[:30],
    "island_count": len(never_visited),
    "avg_actual_walk_length": round(sum(w["actual_length"] for w in walk_results) / 50, 2),
    "walk_summaries": [
        {"walk_id": w["walk_id"], "start": w["start"], "length": w["actual_length"],
         "early_stop": w["terminated_early"], "path_preview": w["path"][:6]}
        for w in walk_results
    ]
}
print(f"  Unique nodes visited: {len(all_visited_unique)}/{len(all_node_ids)} ({rs1_results['coverage_pct']}%)")
print(f"  Attractor nodes: {len(attractors)}, Island nodes: {len(never_visited)}")
print(f"  Early terminations: {len(early_terms)}/50")

# ============================================================
# RS-2: Random Source-Target Searches (BFS)
# ============================================================
print("\n=== RS-2: Random Source-Target Searches ===")

# Build simple adjacency (just target node ids)
adj_simple = defaultdict(set)
for nid, ndata in nodes_data.items():
    for p in ndata.get("pointers", []):
        adj_simple[nid].add(p["to"])

def bfs_path(src, tgt):
    if src == tgt:
        return [src]
    visited = {src}
    queue = [(src, [src])]
    while queue:
        current, path = queue.pop(0)
        for neighbor in adj_simple.get(current, set()):
            if neighbor == tgt:
                return path + [neighbor]
            if neighbor not in visited and neighbor in nodes_data:
                visited.add(neighbor)
                if len(path) < 20:  # max depth
                    queue.append((neighbor, path + [neighbor]))
    return None

search_results = []
reachable_count = 0
path_lengths = []

for i in range(30):
    src = random.choice(all_node_ids)
    tgt = random.choice(all_node_ids)
    while tgt == src:
        tgt = random.choice(all_node_ids)

    path = bfs_path(src, tgt)
    reachable = path is not None
    plen = len(path) - 1 if path else None

    if reachable:
        reachable_count += 1
        path_lengths.append(plen)
        route_str = " -> ".join(path[:7])
        if len(path) > 7:
            route_str += " -> ..."
        log_route("rs-2", route_str, "success", plen)
    else:
        log_route("rs-2", f"{src} -> ??? -> {tgt}", "unreachable", 0)

    search_results.append({
        "pair_id": i,
        "source": src,
        "target": tgt,
        "reachable": reachable,
        "path_length": plen,
        "path_preview": path[:8] if path else None
    })

unreachable_pairs = [s for s in search_results if not s["reachable"]]

rs2_results = {
    "total_pairs": 30,
    "reachable": reachable_count,
    "unreachable": 30 - reachable_count,
    "reachability_rate": round(100 * reachable_count / 30, 1),
    "avg_path_length": round(sum(path_lengths) / max(len(path_lengths), 1), 2),
    "min_path_length": min(path_lengths) if path_lengths else None,
    "max_path_length": max(path_lengths) if path_lengths else None,
    "path_length_distribution": dict(Counter(path_lengths)),
    "unreachable_pairs": unreachable_pairs,
    "all_searches": search_results
}
print(f"  Reachability: {reachable_count}/30 ({rs2_results['reachability_rate']}%)")
print(f"  Avg path length: {rs2_results['avg_path_length']}")

# ============================================================
# RS-3: Random Subgraph Sampling
# ============================================================
print("\n=== RS-3: Random Subgraph Sampling ===")

subgraph_results = []

for sg_idx in range(20):
    center = random.choice(all_node_ids)

    # Collect all nodes within 2 hops
    hop1 = set()
    for p in adjacency.get(center, []):
        if p["to"] in nodes_data:
            hop1.add(p["to"])

    hop2 = set()
    for n1 in hop1:
        for p in adjacency.get(n1, []):
            if p["to"] in nodes_data:
                hop2.add(p["to"])

    sg_nodes = {center} | hop1 | hop2

    # Compute subgraph edges (edges where both endpoints are in subgraph)
    sg_edges = []
    all_tags = []
    all_weights = []
    strength_counts = Counter()

    for n in sg_nodes:
        for p in adjacency.get(n, []):
            if p["to"] in sg_nodes:
                sg_edges.append((n, p["to"]))
                all_weights.append(p.get("weight", 0))
                strength_counts[p.get("strength", "none")] += 1
                for r in p.get("reasons", []):
                    if r.startswith("shared_tags:"):
                        for tag in r.replace("shared_tags:", "").split(","):
                            all_tags.append(tag.strip())

    n_nodes = len(sg_nodes)
    n_edges = len(sg_edges)
    max_edges = n_nodes * (n_nodes - 1)
    density = round(n_edges / max_edges, 4) if max_edges > 0 else 0
    avg_weight = round(sum(all_weights) / max(len(all_weights), 1), 2)
    tag_counts = Counter(all_tags)
    top_tags = tag_counts.most_common(5)
    dominant_strength = strength_counts.most_common(1)[0] if strength_counts else ("none", 0)

    subgraph_results.append({
        "subgraph_id": sg_idx,
        "center": center,
        "center_domain": nodes_data[center].get("domain", "unknown"),
        "num_nodes": n_nodes,
        "num_hop1": len(hop1),
        "num_hop2": len(hop2),
        "num_edges": n_edges,
        "density": density,
        "avg_weight": avg_weight,
        "top_tags": [{"tag": t, "count": c} for t, c in top_tags],
        "dominant_strength": {"strength": dominant_strength[0], "count": dominant_strength[1]},
        "strength_distribution": dict(strength_counts)
    })

# Classify rich vs sparse
densities = [s["density"] for s in subgraph_results]
avg_density = sum(densities) / len(densities)
rich = [s for s in subgraph_results if s["density"] > avg_density * 1.5]
sparse = [s for s in subgraph_results if s["density"] < avg_density * 0.5]

# Log some interesting subgraphs
for sg in sorted(subgraph_results, key=lambda x: x["density"], reverse=True)[:3]:
    log_route("rs-3", f"subgraph({sg['center']}, r=2) nodes={sg['num_nodes']} density={sg['density']}", "success", 2)

rs3_results = {
    "total_subgraphs": 20,
    "avg_density": round(avg_density, 4),
    "avg_nodes": round(sum(s["num_nodes"] for s in subgraph_results) / 20, 1),
    "avg_edges": round(sum(s["num_edges"] for s in subgraph_results) / 20, 1),
    "rich_neighborhoods": [{"center": s["center"], "density": s["density"], "nodes": s["num_nodes"]} for s in rich],
    "sparse_neighborhoods": [{"center": s["center"], "density": s["density"], "nodes": s["num_nodes"]} for s in sparse],
    "rich_count": len(rich),
    "sparse_count": len(sparse),
    "subgraphs": subgraph_results
}
print(f"  Avg density: {avg_density:.4f}, Rich: {len(rich)}, Sparse: {len(sparse)}")

# ============================================================
# RS-4: Random Edge Stress Test
# ============================================================
print("\n=== RS-4: Random Edge Stress Test ===")

VALID_STRENGTHS = {"primary", "related", "supporting", "tangential", ""}
WEIGHT_RANGE = (0, 10)

sampled_edges = random.sample(all_edges, min(100, len(all_edges)))

anomalies = []
weight_dist = Counter()
strength_dist = Counter()
tag_overlap_counts = []
valid_count = 0

for idx, (src, tgt, weight, strength, reasons) in enumerate(sampled_edges):
    issues = []

    # Check endpoints exist
    if src not in nodes_data:
        issues.append(f"source {src} not in nodes")
    if tgt not in nodes_data:
        issues.append(f"target {tgt} not in nodes")

    # Check weight range
    if not (WEIGHT_RANGE[0] <= weight <= WEIGHT_RANGE[1]):
        issues.append(f"weight {weight} out of range {WEIGHT_RANGE}")

    # Check strength validity
    if strength and strength not in VALID_STRENGTHS:
        issues.append(f"invalid strength: {strength}")

    # Check for self-loops
    if src == tgt:
        issues.append("self-loop detected")

    # Check reasons not empty
    if not reasons:
        issues.append("no reasons provided")

    if issues:
        anomalies.append({"edge": f"{src} -> {tgt}", "issues": issues})
    else:
        valid_count += 1

    weight_dist[weight] += 1
    strength_dist[strength if strength else "none"] += 1

    # Tag overlap: count shared tags in reasons
    tag_count = sum(1 for r in reasons if r.startswith("shared_tags:"))
    tag_overlap_counts.append(tag_count)

# Log anomalies
for a in anomalies[:5]:
    log_route("rs-4", a["edge"], "anomaly", 1)

rs4_results = {
    "total_edges_sampled": len(sampled_edges),
    "valid_edges": valid_count,
    "anomalies_found": len(anomalies),
    "anomaly_rate": round(100 * len(anomalies) / len(sampled_edges), 1),
    "weight_distribution": {str(k): v for k, v in sorted(weight_dist.items())},
    "weight_stats": {
        "min": min(e[2] for e in sampled_edges),
        "max": max(e[2] for e in sampled_edges),
        "avg": round(sum(e[2] for e in sampled_edges) / len(sampled_edges), 2),
        "median": sorted(e[2] for e in sampled_edges)[len(sampled_edges) // 2]
    },
    "strength_distribution": dict(strength_dist),
    "tag_overlap_stats": {
        "avg_tag_reasons": round(sum(tag_overlap_counts) / len(tag_overlap_counts), 2),
        "edges_with_shared_tags": sum(1 for t in tag_overlap_counts if t > 0),
        "edges_without_shared_tags": sum(1 for t in tag_overlap_counts if t == 0)
    },
    "anomalies": anomalies
}
print(f"  Valid: {valid_count}/{len(sampled_edges)}, Anomalies: {len(anomalies)}")
print(f"  Weight avg: {rs4_results['weight_stats']['avg']}, Strength dist: {dict(strength_dist)}")

# ============================================================
# Compile final report
# ============================================================
print("\n=== Compiling Report ===")

report = {
    "run": "run-006",
    "division": "5A",
    "task": "Random Searches",
    "timestamp": "2026-03-12",
    "agents": {
        "RS-1": {
            "task": "Random Walk Analysis",
            "summary": {
                "total_walks": 50,
                "coverage": f"{rs1_results['coverage_pct']}% of network visited",
                "attractors": len(attractors),
                "islands": len(never_visited),
                "early_termination_rate": f"{rs1_results['early_termination_rate']}%",
                "avg_walk_length": rs1_results["avg_actual_walk_length"]
            },
            "results": rs1_results
        },
        "RS-2": {
            "task": "Random Source-Target Searches",
            "summary": {
                "reachability": f"{rs2_results['reachability_rate']}%",
                "avg_path_length": rs2_results["avg_path_length"],
                "unreachable_pairs": len(unreachable_pairs),
                "connectivity": "high" if rs2_results["reachability_rate"] > 80 else "moderate" if rs2_results["reachability_rate"] > 50 else "low"
            },
            "results": rs2_results
        },
        "RS-3": {
            "task": "Random Subgraph Sampling",
            "summary": {
                "avg_density": rs3_results["avg_density"],
                "avg_subgraph_size": rs3_results["avg_nodes"],
                "rich_neighborhoods": rs3_results["rich_count"],
                "sparse_neighborhoods": rs3_results["sparse_count"]
            },
            "results": rs3_results
        },
        "RS-4": {
            "task": "Random Edge Stress Test",
            "summary": {
                "edges_tested": len(sampled_edges),
                "valid": valid_count,
                "anomalies": len(anomalies),
                "avg_weight": rs4_results["weight_stats"]["avg"],
                "dominant_strength": strength_dist.most_common(1)[0][0] if strength_dist else "none"
            },
            "results": rs4_results
        }
    },
    "cross_agent_findings": {
        "network_health": "good" if len(anomalies) == 0 and rs2_results["reachability_rate"] > 80 else "needs_attention",
        "key_observations": []
    }
}

# Add cross-agent observations
obs = report["cross_agent_findings"]["key_observations"]
if rs1_results["attractor_count"] > 0:
    obs.append(f"RS-1 found {rs1_results['attractor_count']} attractor nodes that dominate random walks")
if len(never_visited) > len(all_node_ids) * 0.3:
    obs.append(f"RS-1: {len(never_visited)} nodes ({round(100*len(never_visited)/len(all_node_ids),1)}%) never reached in 50 walks - potential isolation")
if rs2_results["reachability_rate"] < 100:
    obs.append(f"RS-2: {30-reachable_count} unreachable pairs suggest connectivity gaps")
if len(anomalies) > 0:
    obs.append(f"RS-4: {len(anomalies)} edge anomalies detected")
else:
    obs.append("RS-4: All 100 sampled edges passed validation - clean data")
obs.append(f"RS-3: Network neighborhoods vary from density {min(densities):.4f} to {max(densities):.4f}")

with open(OUTPUT_PATH, "w") as f:
    json.dump(report, f, indent=2)

print(f"\nResults written to {OUTPUT_PATH}")
print("Done.")
