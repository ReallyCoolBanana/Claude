#!/usr/bin/env python3
"""Stress Team S5 — Deep graph analysis of pointer-network.json."""

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

NETWORK_PATH = Path(__file__).parent.parent / "pointer-network.json"
OUTPUT_PATH = Path(__file__).parent / "stress_s5_report.json"


def load_network():
    with open(NETWORK_PATH) as f:
        return json.load(f)


def build_adjacency(pn):
    """Build directed adjacency list and collect all nodes."""
    adj = defaultdict(set)       # directed: src -> {dst, ...}
    adj_undirected = defaultdict(set)
    all_nodes = set(pn["nodes"].keys())
    edges = []

    for src, node_data in pn["nodes"].items():
        for ptr in node_data.get("pointers", []):
            dst = ptr["to"]
            all_nodes.add(dst)
            adj[src].add(dst)
            adj_undirected[src].add(dst)
            adj_undirected[dst].add(src)
            edges.append((src, dst, ptr.get("weight", 0), ptr.get("strength", "unknown")))

    return adj, adj_undirected, all_nodes, edges


def get_domain(node_id, pn):
    """Extract domain from node data or infer from prefix."""
    node_data = pn.get("nodes", {}).get(node_id, {})
    if "domain" in node_data:
        return node_data["domain"]
    # Infer from prefix
    if node_id.startswith("KB-"):
        return "knowledge-base"
    elif node_id.startswith("SOP-"):
        return "sop"
    elif node_id.startswith("SCR-"):
        return "script"
    elif node_id.startswith("TEAM-"):
        return "team"
    elif node_id.startswith("SRC-"):
        return "source"
    elif node_id.startswith("wiki-"):
        return "wiki"
    elif node_id.startswith("API-"):
        return "api-tool"
    return "unknown"


def bfs_shortest_paths(adj_undirected, start, all_nodes):
    """BFS from start, return dict of distances."""
    dist = {start: 0}
    queue = deque([start])
    while queue:
        u = queue.popleft()
        for v in adj_undirected.get(u, []):
            if v not in dist:
                dist[v] = dist[u] + 1
                queue.append(v)
    return dist


def compute_diameter(adj_undirected, all_nodes):
    """Compute diameter (longest shortest path) via BFS from every node."""
    print("Computing diameter (BFS from all nodes)...")
    max_dist = 0
    max_pairs = []
    # Track eccentricity for each node
    eccentricity = {}
    # For connected component analysis
    component_sizes = []
    visited_global = set()

    nodes_list = sorted(all_nodes)

    for i, node in enumerate(nodes_list):
        if i % 50 == 0:
            print(f"  BFS progress: {i}/{len(nodes_list)}")
        dist = bfs_shortest_paths(adj_undirected, node, all_nodes)
        reachable = {k: v for k, v in dist.items() if v < float('inf')}

        if node not in visited_global:
            comp = set(reachable.keys())
            component_sizes.append(len(comp))
            visited_global.update(comp)

        if reachable:
            ecc = max(reachable.values())
            eccentricity[node] = ecc
            if ecc > max_dist:
                max_dist = ecc
                max_pairs = []
            if ecc == max_dist:
                farthest = [k for k, v in reachable.items() if v == ecc]
                for f in farthest:
                    pair = tuple(sorted([node, f]))
                    if pair not in max_pairs:
                        max_pairs.append(pair)

    # Deduplicate pairs
    unique_pairs = list(set(max_pairs))

    return {
        "diameter": max_dist,
        "most_distant_pairs": [list(p) for p in unique_pairs[:20]],
        "num_distant_pairs": len(unique_pairs),
        "radius": min(eccentricity.values()) if eccentricity else 0,
        "num_connected_components": len(component_sizes),
        "component_sizes": sorted(component_sizes, reverse=True)[:10],
        "eccentricity_distribution": _distribution(list(eccentricity.values()))
    }


def _distribution(values):
    """Simple distribution summary."""
    if not values:
        return {}
    from collections import Counter
    c = Counter(values)
    return {str(k): v for k, v in sorted(c.items())}


def compute_degree_centrality(adj, all_nodes, edges):
    """Compute in-degree, out-degree, and total degree centrality."""
    print("Computing degree centrality...")
    in_deg = defaultdict(int)
    out_deg = defaultdict(int)

    for src, dst, w, s in edges:
        out_deg[src] += 1
        in_deg[dst] += 1

    total_deg = {}
    for n in all_nodes:
        total_deg[n] = in_deg[n] + out_deg[n]

    n = len(all_nodes)
    # Normalized centrality = degree / (2*(n-1)) for directed graph
    norm = 2 * (n - 1) if n > 1 else 1

    centrality = []
    for node in all_nodes:
        centrality.append({
            "node": node,
            "in_degree": in_deg[node],
            "out_degree": out_deg[node],
            "total_degree": total_deg[node],
            "centrality": round(total_deg[node] / norm, 6)
        })

    centrality.sort(key=lambda x: x["total_degree"], reverse=True)
    top_15 = centrality[:15]
    bottom_15 = centrality[-15:]

    return {
        "top_15": top_15,
        "bottom_15": bottom_15,
        "mean_degree": round(sum(total_deg[n] for n in all_nodes) / max(len(all_nodes), 1), 2),
        "max_degree": max(total_deg.values()) if total_deg else 0,
        "min_degree": min(total_deg.values()) if total_deg else 0,
    }


def find_bridges(adj_undirected, all_nodes):
    """Find bridge edges in undirected graph using Tarjan's algorithm."""
    print("Finding bridge edges...")
    visited = set()
    disc = {}
    low = {}
    parent = {}
    bridges = []
    timer = [0]

    def dfs(u):
        visited.add(u)
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        for v in adj_undirected.get(u, []):
            if v not in visited:
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if low[v] > disc[u]:
                    bridges.append((u, v))
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    # Need iterative DFS to avoid stack overflow on large graphs
    def dfs_iterative(start):
        stack = [(start, iter(sorted(adj_undirected.get(start, []))), False)]
        visited.add(start)
        disc[start] = low[start] = timer[0]
        timer[0] += 1

        while stack:
            u, neighbors, returning = stack[-1]
            try:
                v = next(neighbors)
                if v not in visited:
                    visited.add(v)
                    parent[v] = u
                    disc[v] = low[v] = timer[0]
                    timer[0] += 1
                    stack.append((v, iter(sorted(adj_undirected.get(v, []))), False))
                elif v != parent.get(u):
                    low[u] = min(low[u], disc[v])
            except StopIteration:
                stack.pop()
                if stack:
                    p = stack[-1][0]
                    low[p] = min(low[p], low[u])
                    if low[u] > disc[p]:
                        bridges.append((p, u))

    for node in sorted(all_nodes):
        if node not in visited:
            parent[node] = None
            dfs_iterative(node)

    return {
        "bridge_count": len(bridges),
        "bridges": [{"from": a, "to": b} for a, b in bridges[:50]],
        "note": f"Showing first 50 of {len(bridges)}" if len(bridges) > 50 else "complete list"
    }


def compute_domain_distances(adj_undirected, all_nodes, pn):
    """Compute average shortest path between domains."""
    print("Computing domain crossing costs...")
    # Group nodes by domain
    domain_nodes = defaultdict(list)
    for n in all_nodes:
        d = get_domain(n, pn)
        domain_nodes[d].append(n)

    domains = sorted(domain_nodes.keys())
    print(f"  Domains found: {domains}")
    print(f"  Domain sizes: {[(d, len(domain_nodes[d])) for d in domains]}")

    # Precompute all BFS distances (we already do this for diameter, but
    # let's be efficient and sample if too large)
    # For each domain pair, sample up to 30 nodes per domain
    MAX_SAMPLE = 30
    import random
    random.seed(42)

    dist_cache = {}

    def get_dist(src):
        if src not in dist_cache:
            dist_cache[src] = bfs_shortest_paths(adj_undirected, src, all_nodes)
        return dist_cache[src]

    matrix = {}
    for i, d1 in enumerate(domains):
        for j, d2 in enumerate(domains):
            if i > j:
                continue
            nodes1 = domain_nodes[d1]
            nodes2 = domain_nodes[d2]
            if d1 == d2 and len(nodes1) < 2:
                matrix[f"{d1} <-> {d2}"] = None
                continue

            sample1 = random.sample(nodes1, min(MAX_SAMPLE, len(nodes1)))
            sample2 = random.sample(nodes2, min(MAX_SAMPLE, len(nodes2)))

            total_dist = 0
            count = 0
            unreachable = 0
            for s in sample1:
                dists = get_dist(s)
                for t in sample2:
                    if s == t:
                        continue
                    if t in dists:
                        total_dist += dists[t]
                        count += 1
                    else:
                        unreachable += 1

            avg = round(total_dist / count, 3) if count > 0 else None
            key = f"{d1} <-> {d2}"
            matrix[key] = {
                "avg_shortest_path": avg,
                "pairs_sampled": count,
                "unreachable_pairs": unreachable
            }
            print(f"  {key}: avg={avg}, sampled={count}, unreachable={unreachable}")

    # Also build a clean matrix
    clean_matrix = {}
    for d1 in domains:
        clean_matrix[d1] = {}
        for d2 in domains:
            k1 = f"{d1} <-> {d2}"
            k2 = f"{d2} <-> {d1}"
            entry = matrix.get(k1) or matrix.get(k2)
            if entry and entry.get("avg_shortest_path") is not None:
                clean_matrix[d1][d2] = entry["avg_shortest_path"]
            else:
                clean_matrix[d1][d2] = None

    return {
        "domain_sizes": {d: len(domain_nodes[d]) for d in domains},
        "pairwise_distances": matrix,
        "distance_matrix": clean_matrix
    }


def compute_clustering_coefficient(adj_undirected, all_nodes):
    """Compute local and global clustering coefficients (undirected)."""
    print("Computing clustering coefficients...")
    local_cc = {}
    total_cc = 0.0
    counted = 0

    nodes_list = sorted(all_nodes)
    for i, node in enumerate(nodes_list):
        if i % 50 == 0:
            print(f"  Clustering progress: {i}/{len(nodes_list)}")
        neighbors = adj_undirected.get(node, set())
        k = len(neighbors)
        if k < 2:
            local_cc[node] = 0.0
            continue
        # Count edges among neighbors
        neighbor_list = list(neighbors)
        edges_among = 0
        for a_idx in range(len(neighbor_list)):
            for b_idx in range(a_idx + 1, len(neighbor_list)):
                if neighbor_list[b_idx] in adj_undirected.get(neighbor_list[a_idx], set()):
                    edges_among += 1
        possible = k * (k - 1) / 2
        cc = edges_among / possible if possible > 0 else 0
        local_cc[node] = round(cc, 6)
        total_cc += cc
        counted += 1

    avg_cc = round(total_cc / counted, 6) if counted > 0 else 0

    # Get top/bottom
    sorted_cc = sorted(local_cc.items(), key=lambda x: x[1], reverse=True)
    top_10 = [{"node": n, "clustering_coefficient": c} for n, c in sorted_cc[:10]]
    # Bottom non-zero
    nonzero = [(n, c) for n, c in sorted_cc if c > 0]
    bottom_10_nonzero = [{"node": n, "clustering_coefficient": c}
                          for n, c in nonzero[-10:]] if nonzero else []
    zero_count = sum(1 for c in local_cc.values() if c == 0)

    # Distribution buckets
    buckets = {"0": 0, "0-0.1": 0, "0.1-0.2": 0, "0.2-0.3": 0, "0.3-0.4": 0,
               "0.4-0.5": 0, "0.5-0.6": 0, "0.6-0.7": 0, "0.7-0.8": 0,
               "0.8-0.9": 0, "0.9-1.0": 0, "1.0": 0}
    for c in local_cc.values():
        if c == 0:
            buckets["0"] += 1
        elif c == 1.0:
            buckets["1.0"] += 1
        elif c < 0.1:
            buckets["0-0.1"] += 1
        elif c < 0.2:
            buckets["0.1-0.2"] += 1
        elif c < 0.3:
            buckets["0.2-0.3"] += 1
        elif c < 0.4:
            buckets["0.3-0.4"] += 1
        elif c < 0.5:
            buckets["0.4-0.5"] += 1
        elif c < 0.6:
            buckets["0.5-0.6"] += 1
        elif c < 0.7:
            buckets["0.6-0.7"] += 1
        elif c < 0.8:
            buckets["0.7-0.8"] += 1
        elif c < 0.9:
            buckets["0.8-0.9"] += 1
        else:
            buckets["0.9-1.0"] += 1

    return {
        "global_clustering_coefficient": avg_cc,
        "nodes_with_zero_clustering": zero_count,
        "nodes_with_nonzero_clustering": counted - zero_count if counted > zero_count else len(nonzero),
        "top_10_most_clustered": top_10,
        "bottom_10_nonzero_clustered": bottom_10_nonzero,
        "distribution": buckets,
        "interpretation": (
            "high" if avg_cc > 0.5 else
            "moderate" if avg_cc > 0.2 else
            "low" if avg_cc > 0.05 else
            "very sparse"
        )
    }


def main():
    print("=" * 60)
    print("Stress Team S5 — Deep Graph Analysis")
    print("=" * 60)

    pn = load_network()
    adj, adj_undirected, all_nodes, edges = build_adjacency(pn)

    print(f"\nNetwork: {len(all_nodes)} nodes, {len(edges)} directed edges")

    # 1. Diameter
    diameter_result = compute_diameter(adj_undirected, all_nodes)
    print(f"\nDiameter: {diameter_result['diameter']}")
    print(f"Radius: {diameter_result['radius']}")
    print(f"Components: {diameter_result['num_connected_components']}")
    print(f"Distant pairs (sample): {diameter_result['most_distant_pairs'][:5]}")

    # 2. Centrality
    centrality_result = compute_degree_centrality(adj, all_nodes, edges)
    print(f"\nTop 5 central nodes:")
    for c in centrality_result["top_15"][:5]:
        print(f"  {c['node']}: total_degree={c['total_degree']} (in={c['in_degree']}, out={c['out_degree']})")

    # 3. Bridges
    bridges_result = find_bridges(adj_undirected, all_nodes)
    print(f"\nBridge edges (single points of failure): {bridges_result['bridge_count']}")
    for b in bridges_result["bridges"][:5]:
        print(f"  {b['from']} -- {b['to']}")

    # 4. Domain distances
    domain_result = compute_domain_distances(adj_undirected, all_nodes, pn)
    print(f"\nDomain distance matrix computed for {len(domain_result['domain_sizes'])} domains")

    # 5. Clustering
    clustering_result = compute_clustering_coefficient(adj_undirected, all_nodes)
    print(f"\nGlobal clustering coefficient: {clustering_result['global_clustering_coefficient']}")
    print(f"Interpretation: {clustering_result['interpretation']}")

    # Build final report
    report = {
        "team": "Stress Team S5",
        "task": "Deep graph analysis (BFS/DFS) on pointer-network",
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "network_summary": {
            "total_nodes": len(all_nodes),
            "total_directed_edges": len(edges),
            "stats_from_file": pn.get("stats", {})
        },
        "1_diameter": diameter_result,
        "2_degree_centrality": centrality_result,
        "3_bridge_edges": bridges_result,
        "4_domain_crossing_costs": domain_result,
        "5_clustering_coefficient": clustering_result
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nReport written to: {OUTPUT_PATH}")
    return report


if __name__ == "__main__":
    report = main()
