#!/usr/bin/env python3
"""
Run-006 Division 5B: Random Searches (Agents RS-5 through RS-8)
Performs random exploratory analysis across the pointer network.
"""

import json
import random
import subprocess
import sys
from collections import defaultdict, Counter
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations

NETWORK_PATH = Path("/home/user/Claude/storage/pointer-network.json")
OUTPUT_PATH = Path("/home/user/Claude/storage/pointer-routes/run006_random_search_b.json")
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"
PATTERNS_PATH = Path("/home/user/Claude/storage/pointer-routes/route_patterns.json")

random.seed(42)  # reproducibility

# Load network
with open(NETWORK_PATH) as f:
    network = json.load(f)

nodes = network["nodes"]
node_ids = list(nodes.keys())

# Build adjacency structures
adj = defaultdict(list)       # node -> [(neighbor, weight, strength, reasons)]
adj_set = defaultdict(set)    # node -> set of neighbors
all_edges = []
all_tags = set()
node_tags = {}                # node -> set of tags

for nid, ndata in nodes.items():
    tags = set()
    for ptr in ndata.get("pointers", []):
        to = ptr["to"]
        w = ptr.get("weight", 1)
        s = ptr.get("strength", "unknown")
        reasons = ptr.get("reasons", [])
        adj[nid].append((to, w, s, reasons))
        adj_set[nid].add(to)
        all_edges.append((nid, to, w, s))
        for r in reasons:
            if r.startswith("shared_tags:"):
                for t in r.replace("shared_tags:", "").split(","):
                    tags.add(t.strip())
                    all_tags.add(t.strip())
    node_tags[nid] = tags

def get_domain(nid):
    if nid.startswith("KB-"): return "KB"
    if nid.startswith("SOP-"): return "SOP"
    if nid.startswith("SCR-"): return "SCR"
    if nid.startswith("SRC-"): return "SRC"
    if nid.startswith("TEAM-"): return "TEAM"
    if nid.startswith("TOOL-"): return "TOOL"
    return "OTHER"

# BFS shortest path
def bfs_path(src, dst, excluded_edges=None):
    """Return shortest path as list of nodes, or None."""
    if src == dst:
        return [src]
    if excluded_edges is None:
        excluded_edges = set()
    visited = {src}
    queue = [(src, [src])]
    while queue:
        cur, path = queue.pop(0)
        for nb in adj_set[cur]:
            if (cur, nb) in excluded_edges:
                continue
            if nb not in visited:
                new_path = path + [nb]
                if nb == dst:
                    return new_path
                visited.add(nb)
                queue.append((nb, new_path))
    return None

def bfs_reachable(start, excluded_edges=None):
    """Return set of all reachable nodes from start."""
    if excluded_edges is None:
        excluded_edges = set()
    visited = {start}
    queue = [start]
    while queue:
        cur = queue.pop(0)
        for nb in adj_set[cur]:
            if (cur, nb) in excluded_edges:
                continue
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
    return visited

def log_route(agent, task, route_nodes, outcome, hops):
    route_str = " -> ".join(route_nodes)
    try:
        subprocess.run([
            "python3", ROUTE_TRACKER, "log",
            "--agent", agent,
            "--task", "random-search",
            "--route", route_str,
            "--outcome", outcome,
            "--hops", str(hops)
        ], capture_output=True, timeout=5)
    except Exception:
        pass

print("=" * 60)
print("Run-006 Division 5B: Random Searches")
print("=" * 60)

results = {
    "run": "run-006",
    "division": "5B",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "network_stats": {
        "total_nodes": len(node_ids),
        "total_edges": len(all_edges),
        "total_unique_tags": len(all_tags),
        "domains": dict(Counter(get_domain(n) for n in node_ids))
    },
    "rs5_tag_discovery": {},
    "rs6_domain_crossing": {},
    "rs7_failure_injection": {},
    "rs8_pattern_mining": {}
}

# ============================================================
# RS-5: Random tag-based discovery
# ============================================================
print("\n[RS-5] Tag-based discovery...")

all_tags_list = sorted(all_tags)
sample_tags = random.sample(all_tags_list, min(20, len(all_tags_list)))

# Build tag->nodes index
tag_to_nodes = defaultdict(set)
for nid, tags in node_tags.items():
    for t in tags:
        tag_to_nodes[t].add(nid)

tag_clusters = []
for tag in sample_tags:
    tag_nodes = sorted(tag_to_nodes[tag])
    if len(tag_nodes) < 2:
        tag_clusters.append({
            "tag": tag,
            "node_count": len(tag_nodes),
            "nodes": tag_nodes,
            "internal_edges": 0,
            "connectivity": 0.0,
            "paths_found": 0,
            "avg_path_length": 0
        })
        continue

    # Count internal edges
    internal_edges = 0
    tag_node_set = set(tag_nodes)
    for n in tag_nodes:
        for nb in adj_set[n]:
            if nb in tag_node_set:
                internal_edges += 1

    # Find paths between pairs (sample if too many)
    pairs = list(combinations(tag_nodes, 2))
    if len(pairs) > 15:
        pairs = random.sample(pairs, 15)

    paths_found = 0
    total_path_len = 0
    for a, b in pairs:
        p = bfs_path(a, b)
        if p and len(p) <= 5:  # up to length 4
            paths_found += 1
            total_path_len += len(p) - 1

    max_possible = len(tag_nodes) * (len(tag_nodes) - 1)
    connectivity = internal_edges / max_possible if max_possible > 0 else 0

    cluster = {
        "tag": tag,
        "node_count": len(tag_nodes),
        "nodes": tag_nodes[:20],  # cap for output
        "internal_edges": internal_edges,
        "connectivity": round(connectivity, 4),
        "paths_found": paths_found,
        "avg_path_length": round(total_path_len / paths_found, 2) if paths_found > 0 else 0
    }
    tag_clusters.append(cluster)

# Sort by connectivity
tag_clusters.sort(key=lambda x: x["connectivity"], reverse=True)

# Log top routes for RS-5
for tc in tag_clusters[:5]:
    if tc["node_count"] >= 2:
        route = tc["nodes"][:3]
        log_route("rs-5", "tag-cluster", route, "success", len(route) - 1)

# Cohesion ranking
cohesive_tags = [c for c in tag_clusters if c["connectivity"] > 0]
cohesive_tags.sort(key=lambda x: (x["connectivity"], x["internal_edges"]), reverse=True)

results["rs5_tag_discovery"] = {
    "tags_sampled": len(sample_tags),
    "tag_clusters": tag_clusters,
    "most_cohesive_tags": [
        {"tag": c["tag"], "connectivity": c["connectivity"], "nodes": c["node_count"], "internal_edges": c["internal_edges"]}
        for c in cohesive_tags[:10]
    ],
    "least_cohesive_tags": [
        {"tag": c["tag"], "connectivity": c["connectivity"], "nodes": c["node_count"]}
        for c in tag_clusters if c["connectivity"] == 0 and c["node_count"] > 1
    ][:5]
}

print(f"  Sampled {len(sample_tags)} tags, found {len(cohesive_tags)} cohesive clusters")
print(f"  Most cohesive: {cohesive_tags[0]['tag']} (conn={cohesive_tags[0]['connectivity']}, {cohesive_tags[0]['node_count']} nodes)" if cohesive_tags else "  No cohesive tags found")

# ============================================================
# RS-6: Random domain crossing analysis
# ============================================================
print("\n[RS-6] Domain crossing analysis...")

domains = list(results["network_stats"]["domains"].keys())
domain_nodes = defaultdict(list)
for nid in node_ids:
    domain_nodes[get_domain(nid)].append(nid)

# Generate 30 cross-domain pairs
cross_pairs = []
attempts = 0
while len(cross_pairs) < 30 and attempts < 200:
    d1, d2 = random.sample(domains, 2)
    n1 = random.choice(domain_nodes[d1])
    n2 = random.choice(domain_nodes[d2])
    cross_pairs.append((n1, n2, d1, d2))
    attempts += 1

crossing_results = []
transition_lengths = defaultdict(list)  # "D1->D2" -> [lengths]
bridge_scores = Counter()  # node -> count of times on cross-domain path

for n1, n2, d1, d2 in cross_pairs:
    p = bfs_path(n1, n2)
    transition_key = f"{d1}->{d2}"
    if p:
        plen = len(p) - 1
        transition_lengths[transition_key].append(plen)
        # Track bridges: intermediate nodes from different domains
        for node in p[1:-1]:
            nd = get_domain(node)
            if nd != d1 and nd != d2:
                bridge_scores[node] += 1
            elif nd != d1 or nd != d2:
                bridge_scores[node] += 0.5
        crossing_results.append({
            "from": n1, "to": n2, "domains": transition_key,
            "path": p, "length": plen, "reachable": True
        })
        log_route("rs-6", "domain-crossing", p, "success", plen)
    else:
        crossing_results.append({
            "from": n1, "to": n2, "domains": transition_key,
            "path": None, "length": -1, "reachable": False
        })
        log_route("rs-6", "domain-crossing", [n1, n2], "failure", 0)

# Compute stats
transition_stats = {}
for tk, lengths in sorted(transition_lengths.items()):
    transition_stats[tk] = {
        "count": len(lengths),
        "avg_length": round(sum(lengths) / len(lengths), 2),
        "min_length": min(lengths),
        "max_length": max(lengths)
    }

# Easiest and hardest transitions
sorted_transitions = sorted(transition_stats.items(), key=lambda x: x[1]["avg_length"])
easiest = sorted_transitions[:5] if sorted_transitions else []
hardest = sorted_transitions[-5:] if sorted_transitions else []

top_bridges = bridge_scores.most_common(15)

all_path_lengths = [c["length"] for c in crossing_results if c["reachable"]]
overall_avg = round(sum(all_path_lengths) / len(all_path_lengths), 2) if all_path_lengths else 0

results["rs6_domain_crossing"] = {
    "pairs_tested": len(cross_pairs),
    "reachable_pairs": sum(1 for c in crossing_results if c["reachable"]),
    "unreachable_pairs": sum(1 for c in crossing_results if not c["reachable"]),
    "overall_avg_path_length": overall_avg,
    "transition_stats": transition_stats,
    "easiest_transitions": [{"transition": t, **s} for t, s in easiest],
    "hardest_transitions": [{"transition": t, **s} for t, s in hardest],
    "top_bridges": [{"node": n, "domain": get_domain(n), "bridge_score": s} for n, s in top_bridges],
    "sample_crossings": crossing_results[:10]
}

print(f"  Tested {len(cross_pairs)} pairs, {sum(1 for c in crossing_results if c['reachable'])} reachable")
print(f"  Avg cross-domain path length: {overall_avg}")
print(f"  Top bridge: {top_bridges[0][0]} (score={top_bridges[0][1]})" if top_bridges else "  No bridges found")

# ============================================================
# RS-7: Random failure injection
# ============================================================
print("\n[RS-7] Failure injection...")

# Sample 20 random edges
sampled_edges = random.sample(all_edges, min(20, len(all_edges)))

# First compute baseline: full reachable count from a reference node
ref_node = node_ids[0]
baseline_reach = len(bfs_reachable(ref_node))

edge_criticality = []
for src, dst, w, s in sampled_edges:
    excluded = {(src, dst)}

    # Check connectivity impact: sample 10 random pairs
    test_pairs = random.sample(node_ids, min(20, len(node_ids)))
    pairs = [(test_pairs[i], test_pairs[i+1]) for i in range(0, len(test_pairs)-1, 2)]

    broken_paths = 0
    affected_pairs = []
    for a, b in pairs:
        # Check if path exists without this edge
        p_with = bfs_path(a, b)
        p_without = bfs_path(a, b, excluded_edges=excluded)
        if p_with and not p_without:
            broken_paths += 1
            affected_pairs.append((a, b))
        elif p_with and p_without and len(p_without) > len(p_with):
            broken_paths += 0.5  # degraded but not broken

    # Check if removing edge disconnects src from dst
    direct_disconnect = bfs_path(src, dst, excluded_edges=excluded) is None

    criticality = {
        "edge": f"{src} -> {dst}",
        "weight": w,
        "strength": s,
        "broken_paths": broken_paths,
        "pairs_tested": len(pairs),
        "direct_disconnect": direct_disconnect,
        "criticality_score": round(broken_paths / len(pairs) * 100, 1) if pairs else 0,
        "affected_pairs": [f"{a}->{b}" for a, b in affected_pairs[:3]]
    }
    edge_criticality.append(criticality)

    outcome = "critical" if broken_paths > 0 else "resilient"
    log_route("rs-7", "failure-injection", [src, dst], outcome, 1)

edge_criticality.sort(key=lambda x: x["criticality_score"], reverse=True)

critical_edges = [e for e in edge_criticality if e["criticality_score"] > 0]
resilient_edges = [e for e in edge_criticality if e["criticality_score"] == 0]

results["rs7_failure_injection"] = {
    "edges_tested": len(sampled_edges),
    "critical_edges_found": len(critical_edges),
    "resilient_edges": len(resilient_edges),
    "network_resilience_score": round(len(resilient_edges) / len(sampled_edges) * 100, 1) if sampled_edges else 0,
    "edge_rankings": edge_criticality,
    "most_critical": edge_criticality[:5],
    "summary": {
        "avg_criticality": round(sum(e["criticality_score"] for e in edge_criticality) / len(edge_criticality), 2) if edge_criticality else 0,
        "max_criticality": edge_criticality[0]["criticality_score"] if edge_criticality else 0,
        "direct_disconnects": sum(1 for e in edge_criticality if e["direct_disconnect"])
    }
}

print(f"  Tested {len(sampled_edges)} edges: {len(critical_edges)} critical, {len(resilient_edges)} resilient")
print(f"  Network resilience: {results['rs7_failure_injection']['network_resilience_score']}%")

# ============================================================
# RS-8: Random pattern mining
# ============================================================
print("\n[RS-8] Pattern mining...")

def random_walk(start, length):
    """Random walk of given length from start node."""
    path = [start]
    cur = start
    for _ in range(length):
        neighbors = list(adj_set[cur])
        if not neighbors:
            break
        cur = random.choice(neighbors)
        path.append(cur)
    return path

# Sample 40 random paths of length 3-5
sampled_paths = []
for _ in range(40):
    start = random.choice(node_ids)
    length = random.randint(3, 5)
    path = random_walk(start, length)
    if len(path) >= 3:
        sampled_paths.append(path)

# Extract domain-type patterns
domain_patterns = []
for path in sampled_paths:
    dp = [get_domain(n) for n in path]
    domain_patterns.append("->".join(dp))

# Count patterns
pattern_counts = Counter(domain_patterns)

# Extract subsequences of length 2 and 3
subseq2 = Counter()
subseq3 = Counter()
for path in sampled_paths:
    dp = [get_domain(n) for n in path]
    for i in range(len(dp) - 1):
        subseq2[f"{dp[i]}->{dp[i+1]}"] += 1
    for i in range(len(dp) - 2):
        subseq3[f"{dp[i]}->{dp[i+1]}->{dp[i+2]}"] += 1

# Load existing route_patterns for comparison
try:
    with open(PATTERNS_PATH) as f:
        existing_patterns = json.load(f)
except Exception:
    existing_patterns = {}

# Log interesting routes
for path in sampled_paths[:10]:
    log_route("rs-8", "pattern-mining", path, "success", len(path) - 1)

results["rs8_pattern_mining"] = {
    "paths_sampled": len(sampled_paths),
    "avg_path_length": round(sum(len(p) for p in sampled_paths) / len(sampled_paths), 2) if sampled_paths else 0,
    "full_patterns": [{"pattern": p, "count": c} for p, c in pattern_counts.most_common(20)],
    "top_2step_patterns": [{"pattern": p, "count": c} for p, c in subseq2.most_common(15)],
    "top_3step_patterns": [{"pattern": p, "count": c} for p, c in subseq3.most_common(15)],
    "unique_full_patterns": len(pattern_counts),
    "unique_2step": len(subseq2),
    "unique_3step": len(subseq3),
    "sample_paths": [
        {"nodes": p, "domain_pattern": "->".join(get_domain(n) for n in p)}
        for p in sampled_paths[:10]
    ],
    "comparison_with_existing": {
        "existing_pattern_count": existing_patterns.get("total_patterns", 0),
        "existing_tasks": list(existing_patterns.get("task_patterns", {}).keys())[:10],
        "note": "Existing patterns are task-based; mined patterns are structural domain-type sequences"
    }
}

print(f"  Sampled {len(sampled_paths)} paths")
print(f"  Top 2-step: {subseq2.most_common(3)}")
print(f"  Top 3-step: {subseq3.most_common(3)}")

# ============================================================
# Final summary
# ============================================================
results["summary"] = {
    "rs5_finding": f"Most cohesive tag: '{cohesive_tags[0]['tag']}' with connectivity {cohesive_tags[0]['connectivity']} across {cohesive_tags[0]['node_count']} nodes" if cohesive_tags else "No cohesive tags",
    "rs6_finding": f"Avg cross-domain path length: {overall_avg}. Top bridge node: {top_bridges[0][0]}" if top_bridges else "No bridges found",
    "rs7_finding": f"Network resilience: {results['rs7_failure_injection']['network_resilience_score']}%. {len(critical_edges)} of {len(sampled_edges)} tested edges are critical",
    "rs8_finding": f"Most common 2-step pattern: {subseq2.most_common(1)[0][0]} ({subseq2.most_common(1)[0][1]} occurrences)" if subseq2 else "No patterns found"
}

# Write output
with open(OUTPUT_PATH, "w") as f:
    json.dump(results, f, indent=2, default=str)

print(f"\n{'=' * 60}")
print(f"Results written to {OUTPUT_PATH}")
print(f"{'=' * 60}")
for k, v in results["summary"].items():
    print(f"  {k}: {v}")
