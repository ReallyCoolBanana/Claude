#!/usr/bin/env python3
"""Run-006 Stress Testing: Agents S5-1, S6-1, S6-2, S8-1
All analyses in one script for the pointer network."""

import json, random, subprocess, sys, time
from collections import defaultdict, deque
from itertools import combinations
from datetime import datetime

random.seed(42)

# Load pointer network
with open("/home/user/Claude/storage/pointer-network.json") as f:
    pn = json.load(f)

nodes_data = pn["nodes"]
all_nodes = list(nodes_data.keys())

# Build adjacency structures
adj = defaultdict(list)       # node -> [(neighbor, weight, strength, reasons)]
adj_undir = defaultdict(set)  # undirected for components/diameter

for nid, ndata in nodes_data.items():
    for ptr in ndata.get("pointers", []):
        to = ptr["to"]
        w = ptr.get("weight", 1)
        s = ptr.get("strength", "related")
        reasons = ptr.get("reasons", [])
        adj[nid].append((to, w, s, reasons))
        adj_undir[nid].add(to)
        adj_undir[to].add(nid)

print(f"Loaded {len(all_nodes)} nodes, building graph...")

# ============================================================
# AGENT S5-1: Deep Graph Analysis
# ============================================================
print("\n=== AGENT S5-1: Deep Graph Analysis ===")

# 1. Connected components (BFS on undirected graph)
visited = set()
components = []
for n in all_nodes:
    if n not in visited:
        comp = []
        q = deque([n])
        visited.add(n)
        while q:
            cur = q.popleft()
            comp.append(cur)
            for nb in adj_undir[cur]:
                if nb not in visited:
                    visited.add(nb)
                    q.append(nb)
        components.append(comp)

components.sort(key=len, reverse=True)
print(f"Connected components: {len(components)}")
for i, c in enumerate(components[:5]):
    print(f"  Component {i}: {len(c)} nodes")

# 2. Graph diameter (BFS shortest paths on largest component)
# Use undirected graph, unweighted BFS for diameter
largest_comp = set(components[0])

def bfs_distances(start, node_set):
    dist = {start: 0}
    q = deque([start])
    while q:
        cur = q.popleft()
        for nb in adj_undir[cur]:
            if nb in node_set and nb not in dist:
                dist[nb] = dist[cur] + 1
                q.append(nb)
    return dist

# Sample nodes for diameter estimation (full BFS from each is O(n*(n+m)))
# With 310 nodes this is feasible
print("Computing diameter (all-pairs BFS on largest component)...")
max_dist = 0
diameter_pair = ("", "")
eccentricities = {}

for n in largest_comp:
    dists = bfs_distances(n, largest_comp)
    ecc = max(dists.values()) if dists else 0
    eccentricities[n] = ecc
    if ecc > max_dist:
        max_dist = ecc
        farthest = max(dists, key=dists.get)
        diameter_pair = (n, farthest)

diameter = max_dist
radius = min(eccentricities.values()) if eccentricities else 0
center_nodes = [n for n, e in eccentricities.items() if e == radius]
print(f"Diameter: {diameter} (between {diameter_pair[0]} and {diameter_pair[1]})")
print(f"Radius: {radius}, Center nodes: {len(center_nodes)}")

# 3. Betweenness centrality (Brandes algorithm on directed graph)
print("Computing betweenness centrality...")
betweenness = defaultdict(float)

for s in all_nodes:
    # BFS from s
    stack = []
    pred = defaultdict(list)
    sigma = defaultdict(int)
    sigma[s] = 1
    dist = {s: 0}
    q = deque([s])
    while q:
        v = q.popleft()
        stack.append(v)
        for (w, _, _, _) in adj[v]:
            if w not in dist:
                dist[w] = dist[v] + 1
                q.append(w)
            if dist.get(w) == dist[v] + 1:
                sigma[w] += sigma[v]
                pred[w].append(v)
    delta = defaultdict(float)
    while stack:
        w = stack.pop()
        for v in pred[w]:
            delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
        if w != s:
            betweenness[w] += delta[w]

# Normalize
n_nodes = len(all_nodes)
norm = (n_nodes - 1) * (n_nodes - 2)
if norm > 0:
    for k in betweenness:
        betweenness[k] /= norm

top10_betweenness = sorted(betweenness.items(), key=lambda x: -x[1])[:10]
print("Top-10 betweenness centrality:")
for nid, bc in top10_betweenness:
    print(f"  {nid}: {bc:.6f} (domain: {nodes_data[nid]['domain']})")

# 4. Clustering coefficient (directed: fraction of neighbor pairs that are connected)
print("Computing clustering coefficient...")
clustering = {}
for n in all_nodes:
    neighbors = set(to for (to, _, _, _) in adj[n])
    k = len(neighbors)
    if k < 2:
        clustering[n] = 0.0
        continue
    links = 0
    for u in neighbors:
        for (v, _, _, _) in adj[u]:
            if v in neighbors:
                links += 1
    clustering[n] = links / (k * (k - 1)) if k * (k - 1) > 0 else 0.0

avg_clustering = sum(clustering.values()) / len(clustering) if clustering else 0
top10_clustering = sorted(clustering.items(), key=lambda x: -x[1])[:10]
print(f"Average clustering coefficient: {avg_clustering:.6f}")

# Degree distribution
in_degree = defaultdict(int)
out_degree = defaultdict(int)
for nid in all_nodes:
    out_degree[nid] = len(adj[nid])
    for (to, _, _, _) in adj[nid]:
        in_degree[to] += 1

top10_in = sorted(in_degree.items(), key=lambda x: -x[1])[:10]
top10_out = sorted(out_degree.items(), key=lambda x: -x[1])[:10]

s5_results = {
    "agent": "S5-1",
    "task": "deep_graph_analysis",
    "timestamp": datetime.now().isoformat(),
    "graph_stats": {
        "total_nodes": len(all_nodes),
        "total_directed_edges": sum(len(adj[n]) for n in all_nodes),
        "connected_components": len(components),
        "component_sizes": [len(c) for c in components],
        "largest_component_size": len(components[0]),
    },
    "diameter": {
        "value": diameter,
        "pair": list(diameter_pair),
        "radius": radius,
        "center_node_count": len(center_nodes),
        "center_nodes_sample": center_nodes[:10],
    },
    "betweenness_centrality_top10": [
        {"node": nid, "centrality": round(bc, 6), "domain": nodes_data[nid]["domain"]}
        for nid, bc in top10_betweenness
    ],
    "clustering_coefficient": {
        "average": round(avg_clustering, 6),
        "top10": [{"node": n, "coefficient": round(c, 6)} for n, c in top10_clustering],
    },
    "degree_distribution": {
        "avg_in_degree": round(sum(in_degree.values()) / len(all_nodes), 2),
        "avg_out_degree": round(sum(out_degree.values()) / len(all_nodes), 2),
        "top10_in_degree": [{"node": n, "in_degree": d} for n, d in top10_in],
        "top10_out_degree": [{"node": n, "out_degree": d} for n, d in top10_out],
    },
}
print("S5-1 analysis complete.\n")


# ============================================================
# AGENTS S6-1 and S6-2: Routing Strategy Benchmark
# ============================================================
print("=== AGENTS S6-1 & S6-2: Routing Strategy Benchmark ===")

sop_nodes = sorted([n for n in all_nodes if n.startswith("SOP")])
target_nodes = [n for n in all_nodes if not n.startswith("SOP")]

def extract_tags_from_reasons(reasons):
    tags = set()
    for r in reasons:
        if r.startswith("shared_tags:"):
            tags.update(r.split(":", 1)[1].split(","))
    return tags

def get_node_tags(nid):
    """Collect all tags associated with a node from its outgoing edges."""
    tags = set()
    for (_, _, _, reasons) in adj[nid]:
        tags.update(extract_tags_from_reasons(reasons))
    return tags

def jaccard(s1, s2):
    if not s1 and not s2:
        return 0.0
    return len(s1 & s2) / len(s1 | s2)

STRENGTH_ORDER = {"primary": 0, "supporting": 1, "related": 2}
MAX_HOPS = 20

def route_max_weight(start, target):
    """Greedy: always follow highest weight edge."""
    path = [start]
    visited = {start}
    current = start
    for _ in range(MAX_HOPS):
        if current == target:
            return path, True
        neighbors = [(to, w) for (to, w, _, _) in adj[current] if to not in visited]
        if not neighbors:
            return path, False
        # Check if target is a direct neighbor
        for to, w in neighbors:
            if to == target:
                path.append(target)
                return path, True
        # Greedy: pick highest weight
        best = max(neighbors, key=lambda x: x[1])
        path.append(best[0])
        visited.add(best[0])
        current = best[0]
    return path, current == target

def route_tag_coherent(start, target):
    """Maximize Jaccard similarity of tags between consecutive nodes."""
    path = [start]
    visited = {start}
    current = start
    target_tags = get_node_tags(target)
    for _ in range(MAX_HOPS):
        if current == target:
            return path, True
        cur_tags = get_node_tags(current)
        neighbors = [(to, w, s, r) for (to, w, s, r) in adj[current] if to not in visited]
        if not neighbors:
            return path, False
        for to, _, _, _ in neighbors:
            if to == target:
                path.append(target)
                return path, True
        # Score by Jaccard with both current and target tags
        scored = []
        for to, w, s, reasons in neighbors:
            nb_tags = get_node_tags(to)
            edge_tags = extract_tags_from_reasons(reasons)
            # Blend: coherence with current + similarity to target
            j_cur = jaccard(cur_tags | edge_tags, nb_tags)
            j_target = jaccard(target_tags, nb_tags)
            scored.append((to, 0.4 * j_cur + 0.6 * j_target))
        best = max(scored, key=lambda x: x[1])
        path.append(best[0])
        visited.add(best[0])
        current = best[0]
    return path, current == target

def route_strength_priority(start, target):
    """Prefer primary > supporting > related edges."""
    path = [start]
    visited = {start}
    current = start
    for _ in range(MAX_HOPS):
        if current == target:
            return path, True
        neighbors = [(to, w, s, r) for (to, w, s, r) in adj[current] if to not in visited]
        if not neighbors:
            return path, False
        for to, _, _, _ in neighbors:
            if to == target:
                path.append(target)
                return path, True
        # Sort by strength (primary first), then weight
        neighbors.sort(key=lambda x: (STRENGTH_ORDER.get(x[2], 3), -x[1]))
        best = neighbors[0]
        path.append(best[0])
        visited.add(best[0])
        current = best[0]
    return path, current == target

strategies = {
    "max_weight": route_max_weight,
    "tag_coherent": route_tag_coherent,
    "strength_priority": route_strength_priority,
}

def generate_pairs(agent_name, count=10):
    pairs = []
    attempts = 0
    while len(pairs) < count and attempts < 100:
        sop = random.choice(sop_nodes)
        tgt = random.choice(target_nodes)
        if sop != tgt and (sop, tgt) not in pairs:
            pairs.append((sop, tgt))
        attempts += 1
    return pairs

s6_1_pairs = generate_pairs("S6-1", 10)
s6_2_pairs = generate_pairs("S6-2", 10)

def run_benchmark(agent_name, pairs):
    results = []
    for sop, target in pairs:
        pair_results = {}
        for sname, sfunc in strategies.items():
            path, success = sfunc(sop, target)
            total_weight = 0
            for i in range(len(path) - 1):
                for (to, w, _, _) in adj[path[i]]:
                    if to == path[i + 1]:
                        total_weight += w
                        break
            hops = len(path) - 1
            pair_results[sname] = {
                "route": " -> ".join(path),
                "hops": hops,
                "total_weight": total_weight,
                "success": success,
            }
            # Log route via route_tracker
            outcome = "success" if success else "failure"
            route_str = " -> ".join(path)
            try:
                subprocess.run(
                    [
                        "python3",
                        "/home/user/Claude/storage/coordination/route_tracker.py",
                        "log",
                        "--agent", agent_name.lower().replace("-", "_"),
                        "--task", "strategy-benchmark",
                        "--route", route_str,
                        "--outcome", outcome,
                        "--hops", str(hops),
                    ],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
        results.append({
            "start": sop,
            "target": target,
            "strategies": pair_results,
        })
    return results

print("S6-1: Running 10 SOP->target benchmarks...")
s6_1_results = run_benchmark("s6-1", s6_1_pairs)
print("S6-2: Running 10 SOP->target benchmarks...")
s6_2_results = run_benchmark("s6-2", s6_2_pairs)

# Aggregate stats per strategy
def aggregate_stats(results_list):
    stats = {}
    for sname in strategies:
        successes = 0
        total_hops = 0
        total_weight = 0
        count = 0
        for r in results_list:
            sr = r["strategies"][sname]
            count += 1
            if sr["success"]:
                successes += 1
                total_hops += sr["hops"]
                total_weight += sr["total_weight"]
        stats[sname] = {
            "total_pairs": count,
            "successes": successes,
            "failures": count - successes,
            "success_rate": round(successes / count, 3) if count else 0,
            "avg_hops_on_success": round(total_hops / successes, 2) if successes else None,
            "avg_weight_on_success": round(total_weight / successes, 2) if successes else None,
        }
    return stats

all_s6 = s6_1_results + s6_2_results
combined_stats = aggregate_stats(all_s6)

print("\nCombined Strategy Stats (20 pairs):")
for sname, st in combined_stats.items():
    print(f"  {sname}: success={st['success_rate']:.0%}, avg_hops={st['avg_hops_on_success']}, avg_weight={st['avg_weight_on_success']}")

s6_results = {
    "agents": ["S6-1", "S6-2"],
    "task": "routing_strategy_benchmark",
    "timestamp": datetime.now().isoformat(),
    "s6_1": {"pairs": s6_1_results, "stats": aggregate_stats(s6_1_results)},
    "s6_2": {"pairs": s6_2_results, "stats": aggregate_stats(s6_2_results)},
    "combined_stats": combined_stats,
}
print("S6 benchmark complete.\n")


# ============================================================
# AGENT S8-1: End-to-End Workflow Simulation
# ============================================================
print("=== AGENT S8-1: End-to-End Workflow Simulation ===")

WORKFLOWS = [
    {"name": "Find data processing script via SOP", "start": "SOP-017", "target_domain": "scripts", "goal": "data processing"},
    {"name": "Find market research source via SOP", "start": "SOP-018", "target_domain": "sources", "goal": "market research"},
    {"name": "Find bug fix script from workflow SOP", "start": "SOP-020", "target_domain": "scripts", "goal": "bug fix"},
    {"name": "Find infrastructure audit tool", "start": "SOP-015", "target_domain": "scripts", "goal": "infrastructure"},
    {"name": "Find monitoring source from SOP", "start": "SOP-012", "target_domain": "sources", "goal": "monitoring"},
    {"name": "Navigate from environment setup to wikipedia data", "start": "SOP-019", "target_domain": "wikipedia", "goal": "environment"},
    {"name": "Find delegation script from SOP", "start": "SOP-022", "target_domain": "scripts", "goal": "delegation"},
    {"name": "Find data quality tool", "start": "SOP-024", "target_domain": "scripts", "goal": "data quality"},
    {"name": "Find research integration source", "start": "SOP-025", "target_domain": "sources", "goal": "research"},
    {"name": "Find pointer network ops tool", "start": "SOP-028", "target_domain": "scripts", "goal": "pointer network"},
    {"name": "Find pathfinding strategy script", "start": "SOP-030", "target_domain": "scripts", "goal": "pathfinding"},
    {"name": "Locate wikipedia toolkit source", "start": "SOP-026", "target_domain": "sources", "goal": "wikipedia"},
    {"name": "Find failure recovery script", "start": "SOP-021", "target_domain": "scripts", "goal": "failure recovery"},
    {"name": "Cross-team data to knowledge base", "start": "SOP-016", "target_domain": "knowledge-base", "goal": "cross-team"},
    {"name": "Agent route tracking to scripts", "start": "SOP-029", "target_domain": "scripts", "goal": "route tracking"},
]

def simulate_workflow(wf, max_steps=15):
    """BFS-like greedy traversal from start to any node in target domain."""
    start = wf["start"]
    target_domain = wf["target_domain"]

    if start not in nodes_data:
        return {"workflow": wf["name"], "success": False, "reason": "start not found", "steps": 0, "path": []}

    path = [start]
    visited = {start}
    current = start

    for step in range(max_steps):
        # Check if we've reached target domain
        if nodes_data[current]["domain"] == target_domain:
            return {
                "workflow": wf["name"],
                "success": True,
                "steps": len(path) - 1,
                "path": path,
                "found_node": current,
                "found_domain": target_domain,
            }

        # Get neighbors, prefer those in target domain or closer to it
        neighbors = [(to, w, s, r) for (to, w, s, r) in adj[current] if to not in visited and to in nodes_data]
        if not neighbors:
            return {
                "workflow": wf["name"],
                "success": False,
                "reason": "dead end",
                "steps": len(path) - 1,
                "path": path,
            }

        # Check if any neighbor is in target domain
        domain_matches = [n for n in neighbors if nodes_data[n[0]]["domain"] == target_domain]
        if domain_matches:
            best = max(domain_matches, key=lambda x: x[1])
            path.append(best[0])
            return {
                "workflow": wf["name"],
                "success": True,
                "steps": len(path) - 1,
                "path": path,
                "found_node": best[0],
                "found_domain": target_domain,
            }

        # Otherwise, pick highest weight neighbor (prefer primary strength)
        neighbors.sort(key=lambda x: (STRENGTH_ORDER.get(x[2], 3), -x[1]))
        best = neighbors[0]
        path.append(best[0])
        visited.add(best[0])
        current = best[0]

    return {
        "workflow": wf["name"],
        "success": False,
        "reason": "max steps exceeded",
        "steps": len(path) - 1,
        "path": path,
    }

workflow_results = []
for wf in WORKFLOWS:
    result = simulate_workflow(wf)
    workflow_results.append(result)
    status = "OK" if result["success"] else "FAIL"
    steps = result["steps"]
    print(f"  [{status}] {wf['name']}: {steps} steps, path={' -> '.join(result['path'][:6])}{'...' if len(result['path'])>6 else ''}")

completed = sum(1 for r in workflow_results if r["success"])
avg_steps = sum(r["steps"] for r in workflow_results if r["success"]) / max(completed, 1)

# Bottleneck analysis: which nodes appear most in paths
node_freq = defaultdict(int)
for r in workflow_results:
    for n in r["path"][1:]:  # skip start
        node_freq[n] += 1
bottleneck_nodes = sorted(node_freq.items(), key=lambda x: -x[1])[:10]

print(f"\nWorkflow completion: {completed}/{len(WORKFLOWS)} ({completed/len(WORKFLOWS):.0%})")
print(f"Average steps on success: {avg_steps:.2f}")
print("Top bottleneck nodes:")
for n, freq in bottleneck_nodes[:5]:
    print(f"  {n}: appeared in {freq} paths (domain: {nodes_data.get(n, {}).get('domain', '?')})")

s8_results = {
    "agent": "S8-1",
    "task": "end_to_end_workflow_simulation",
    "timestamp": datetime.now().isoformat(),
    "total_workflows": len(WORKFLOWS),
    "completed": completed,
    "failed": len(WORKFLOWS) - completed,
    "completion_rate": round(completed / len(WORKFLOWS), 3),
    "avg_steps_on_success": round(avg_steps, 2),
    "bottleneck_nodes": [
        {"node": n, "frequency": freq, "domain": nodes_data.get(n, {}).get("domain", "?")}
        for n, freq in bottleneck_nodes
    ],
    "workflows": workflow_results,
}


# ============================================================
# WRITE COMBINED REPORT
# ============================================================
report = {
    "run": "run-006",
    "division": "1B",
    "timestamp": datetime.now().isoformat(),
    "agents": ["S5-1", "S6-1", "S6-2", "S8-1"],
    "s5_1_graph_analysis": s5_results,
    "s6_routing_benchmark": s6_results,
    "s8_1_workflow_simulation": s8_results,
    "summary": {
        "graph_diameter": diameter,
        "connected_components": len(components),
        "avg_clustering_coefficient": round(avg_clustering, 6),
        "top_betweenness_node": top10_betweenness[0][0] if top10_betweenness else None,
        "routing_best_strategy": max(combined_stats.items(), key=lambda x: x[1]["success_rate"])[0],
        "routing_success_rates": {k: v["success_rate"] for k, v in combined_stats.items()},
        "workflow_completion_rate": round(completed / len(WORKFLOWS), 3),
        "workflow_avg_steps": round(avg_steps, 2),
        "primary_bottleneck": bottleneck_nodes[0][0] if bottleneck_nodes else None,
    },
}

output_path = "/home/user/Claude/storage/pointer-routes/run006_stress_s5_s6_s8.json"
with open(output_path, "w") as f:
    json.dump(report, f, indent=2)
print(f"\nReport written to {output_path}")
print("All agents complete.")
