#!/usr/bin/env python3
"""
Tag-coherent pathfinding on the pointer network.

Scores edges by Jaccard similarity between current and neighbor node tags.
Tags derived from tag_clusters (307/310 nodes covered).
"""

import json
import heapq
import subprocess
from collections import defaultdict
from datetime import datetime

NETWORK_PATH = "/home/user/Claude/storage/pointer-network.json"
REPORT_PATH = "/home/user/Claude/storage/pointer-routes/pathfind_p3_tags.json"
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"


def load_network(path):
    with open(path) as f:
        data = json.load(f)

    adj = defaultdict(list)
    node_domains = {}
    node_tags = defaultdict(set)

    for tag_name, cluster_info in data.get("tag_clusters", {}).items():
        for node_id in cluster_info.get("nodes", []):
            node_tags[node_id].add(tag_name)

    for node_id, node_data in data["nodes"].items():
        node_domains[node_id] = node_data.get("domain", "unknown")
        for t in node_data.get("tags", []):
            node_tags[node_id].add(t)
        for ptr in node_data.get("pointers", []):
            adj[node_id].append((
                ptr["to"],
                ptr["weight"],
                ptr.get("strength", "unspecified"),
            ))

    return adj, dict(node_tags), node_domains


def jaccard(set_a, set_b):
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a) + len(set_b) - inter
    return inter / union if union else 0.0


# Pre-compute jaccard for all edges
def precompute_edge_jaccard(adj, node_tags):
    """Returns adj_j: node -> [(neighbor, jaccard, weight)]"""
    adj_j = defaultdict(list)
    for node_id, edges in adj.items():
        src_tags = node_tags.get(node_id, set())
        for neighbor, weight, strength in edges:
            nb_tags = node_tags.get(neighbor, set())
            j = jaccard(src_tags, nb_tags)
            adj_j[node_id].append((neighbor, j, weight))
    return adj_j


def tag_coherent_bfs(adj_j, source, target, max_depth=10):
    """
    Greedy best-first search maximizing per-hop Jaccard.
    Uses a compact visited + prev structure. Strict visited check.
    """
    if source == target:
        return [source], []
    if source not in adj_j:
        return None, None

    # (neg_jaccard, neg_weight, depth, current_node)
    visited = set()
    visited.add(source)
    prev = {}
    edge_jacc = {}

    queue = []
    for neighbor, j, weight in adj_j[source]:
        if neighbor not in visited:
            prev[neighbor] = source
            edge_jacc[neighbor] = j
            visited.add(neighbor)
            heapq.heappush(queue, (-j, -weight, 1, neighbor))

    while queue:
        neg_j, neg_w, depth, current = heapq.heappop(queue)

        if current == target:
            path = []
            node = current
            while node != source:
                path.append(node)
                node = prev[node]
            path.append(source)
            path.reverse()
            coherences = [edge_jacc[n] for n in path[1:]]
            return path, coherences

        if depth >= max_depth:
            continue

        for neighbor, j, weight in adj_j.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                prev[neighbor] = current
                edge_jacc[neighbor] = j
                heapq.heappush(queue, (-j, -weight, depth + 1, neighbor))

    return None, None


def max_weight_dijkstra(adj, source, target):
    dist = defaultdict(lambda: -1)
    dist[source] = 0
    prev = {}
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
        for neighbor, edge_weight, strength in adj[u]:
            new_weight = current_w + edge_weight
            if new_weight > dist[neighbor]:
                dist[neighbor] = new_weight
                prev[neighbor] = u
                heapq.heappush(heap, (-new_weight, neighbor))

    if target not in prev and source != target:
        return None, None
    path = []
    node = target
    while node != source:
        path.append(node)
        node = prev[node]
    path.append(source)
    path.reverse()
    return dist[target], path


def strength_bfs(adj, source, target, max_depth=10):
    smap = {"primary": 4, "supporting": 3, "related": 2, "tangential": 1, "unspecified": 0}
    if source == target:
        return [source]

    visited = set()
    visited.add(source)
    prev = {}
    queue = []

    for neighbor, weight, strength in adj.get(source, []):
        if neighbor not in visited:
            visited.add(neighbor)
            prev[neighbor] = source
            heapq.heappush(queue, (-smap.get(strength, 0), -weight, 1, neighbor))

    while queue:
        neg_s, neg_w, depth, current = heapq.heappop(queue)
        if current == target:
            path = []
            node = current
            while node != source:
                path.append(node)
                node = prev[node]
            path.append(source)
            path.reverse()
            return path
        if depth >= max_depth:
            continue
        for neighbor, weight, strength in adj.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                prev[neighbor] = current
                heapq.heappush(queue, (-smap.get(strength, 0), -weight, depth + 1, neighbor))
    return None


def compute_path_coherence(path, node_tags):
    if not path or len(path) < 2:
        return [], 0.0
    coherences = []
    for i in range(len(path) - 1):
        a = node_tags.get(path[i], set())
        b = node_tags.get(path[i + 1], set())
        coherences.append(jaccard(a, b))
    avg = sum(coherences) / len(coherences) if coherences else 0.0
    return coherences, avg


def define_scenarios():
    return [
        ("SOP-011", "SCR-0043", "SOP to data-gathering script"),
        ("SOP-011", "SRC-0051", "SOP to distant source"),
        ("SOP-012", "SCR-0057", "monitoring to route tracker"),
        ("SOP-012", "SRC-0001", "monitoring to first source"),
        ("SOP-013", "SCR-0001", "SOP to first script"),
        ("SOP-013", "SCR-0030", "SOP to mid script"),
        ("SOP-014", "SRC-0020", "SOP to source"),
        ("SOP-014", "SCR-0050", "SOP to high script"),
        ("SOP-015", "SRC-0040", "SOP to source"),
        ("SOP-015", "SCR-0010", "SOP to data script"),
        ("SOP-016", "SRC-0030", "SOP to source"),
        ("SOP-016", "SCR-0025", "SOP to script"),
        ("SOP-017", "SRC-0015", "data SOP to source"),
        ("SOP-017", "SCR-0040", "data SOP to script"),
        ("SOP-018", "SRC-0045", "KB SOP to source"),
        ("SOP-018", "SCR-0005", "KB SOP to script"),
        ("SOP-019", "SRC-0050", "env SOP to source"),
        ("SOP-019", "SCR-0035", "env SOP to script"),
        ("SOP-020", "SCR-0055", "bugfix SOP to script"),
        ("SOP-020", "SRC-0001", "bugfix SOP to source"),
    ]


def log_route(agent, path, coherence_score, outcome="success"):
    route_str = " -> ".join(path)
    notes = f"coherence={coherence_score:.4f}"
    cmd = [
        "python3", ROUTE_TRACKER, "log",
        "--agent", agent, "--task", "tag-pathfinding",
        "--route", route_str, "--outcome", outcome, "--notes", notes
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.returncode == 0, result.stdout.strip()
    except Exception as e:
        return False, str(e)


def main():
    print("Loading pointer network...")
    adj, node_tags, node_domains = load_network(NETWORK_PATH)
    print(f"Loaded {len(node_domains)} nodes, {sum(len(v) for v in adj.values())} edges")

    tagged_count = sum(1 for t in node_tags.values() if t)
    print(f"Nodes with tags: {tagged_count}/{len(node_domains)}")

    print("Pre-computing edge Jaccard similarities...")
    adj_j = precompute_edge_jaccard(adj, node_tags)
    total_edges = sum(len(v) for v in adj_j.values())
    print(f"Pre-computed {total_edges} edge Jaccard scores")

    scenarios = define_scenarios()
    results = []
    all_tag_highways = []
    all_tag_deserts = []

    print(f"\nRunning {len(scenarios)} scenarios...\n")

    for i, (source, target, description) in enumerate(scenarios):
        print(f"[{i+1:2d}/20] {source} -> {target} ({description})")

        tag_path, tag_coherences = tag_coherent_bfs(adj_j, source, target)
        weight_total, weight_path = max_weight_dijkstra(adj, source, target)
        weight_coherences, weight_avg = compute_path_coherence(weight_path, node_tags) if weight_path else ([], 0.0)
        strength_path = strength_bfs(adj, source, target)
        strength_coherences, strength_avg = compute_path_coherence(strength_path, node_tags) if strength_path else ([], 0.0)

        if tag_path:
            tag_avg = sum(tag_coherences) / len(tag_coherences) if tag_coherences else 0.0
            print(f"  TAG:      {' -> '.join(tag_path)} | coh={[round(c,3) for c in tag_coherences]} avg={tag_avg:.4f}")

            is_highway = all(c > 0.5 for c in tag_coherences) if tag_coherences else False
            if is_highway:
                all_tag_highways.append({
                    "scenario": i + 1, "source": source, "target": target,
                    "path": tag_path,
                    "coherences": [round(c, 4) for c in tag_coherences],
                    "avg_coherence": round(tag_avg, 4)
                })
                print(f"    ** TAG HIGHWAY **")

            for j_idx in range(len(tag_coherences)):
                if tag_coherences[j_idx] == 0.0:
                    a, b = tag_path[j_idx], tag_path[j_idx + 1]
                    all_tag_deserts.append({
                        "from_node": a, "to_node": b,
                        "from_domain": node_domains.get(a, "?"),
                        "to_domain": node_domains.get(b, "?"),
                        "from_tags": sorted(node_tags.get(a, set())),
                        "to_tags": sorted(node_tags.get(b, set())),
                        "scenario": i + 1
                    })
                    print(f"    ** TAG DESERT ** {a} -> {b}")
        else:
            tag_avg = 0.0
            tag_coherences = []
            print(f"  TAG:      NO PATH")

        if weight_path:
            print(f"  WEIGHT:   {' -> '.join(weight_path)} | coh={weight_avg:.4f}")
        if strength_path:
            print(f"  STRENGTH: {' -> '.join(strength_path)} | coh={strength_avg:.4f}")

        result = {
            "scenario": i + 1, "source": source, "target": target,
            "description": description,
            "tag_path": {
                "path": tag_path,
                "hops": len(tag_path) - 1 if tag_path else None,
                "hop_coherences": [round(c, 4) for c in tag_coherences] if tag_coherences else None,
                "avg_coherence": round(tag_avg, 4) if tag_path else None,
                "is_tag_highway": all(c > 0.5 for c in tag_coherences) if tag_coherences else False,
                "has_tag_desert": any(c == 0.0 for c in tag_coherences) if tag_coherences else False,
                "domain_sequence": [node_domains.get(n, "?") for n in tag_path] if tag_path else None
            },
            "weight_path": {
                "path": weight_path,
                "total_weight": weight_total,
                "hops": len(weight_path) - 1 if weight_path else None,
                "hop_coherences": [round(c, 4) for c in weight_coherences],
                "avg_coherence": round(weight_avg, 4) if weight_path else None
            },
            "strength_path": {
                "path": strength_path,
                "hops": len(strength_path) - 1 if strength_path else None,
                "hop_coherences": [round(c, 4) for c in strength_coherences],
                "avg_coherence": round(strength_avg, 4) if strength_path else None
            },
            "comparison": {
                "tag_vs_weight_coherence": round(tag_avg - weight_avg, 4) if tag_path and weight_path else None,
                "tag_vs_strength_coherence": round(tag_avg - strength_avg, 4) if tag_path and strength_path else None,
            }
        }
        results.append(result)
        print()

    # Log routes
    print("Logging routes...")
    agents = ["p3-1", "p3-2", "p3-3", "p3-4"]
    for i, r in enumerate(results):
        agent = agents[i % 4]
        tp = r["tag_path"]
        if tp["path"]:
            ok, _ = log_route(agent, tp["path"], tp["avg_coherence"])
        else:
            ok, _ = log_route(agent, [r["source"], r["target"]], 0.0, "no_path")
        print(f"  {'OK' if ok else 'FAIL'} {agent}: {r['source']} -> {r['target']}")

    # Network-wide analysis
    print("\nNetwork-wide edge analysis...")
    highway_edges = []
    desert_edges = []
    all_jaccards = []
    desert_transitions = defaultdict(int)

    for node_id, edges in adj_j.items():
        for neighbor, j, weight in edges:
            all_jaccards.append(j)
            if j > 0.5:
                highway_edges.append({
                    "from": node_id, "to": neighbor,
                    "jaccard": round(j, 4), "weight": weight,
                    "shared_tags": sorted(node_tags.get(node_id, set()) & node_tags.get(neighbor, set()))
                })
            elif j == 0.0 and node_tags.get(node_id) and node_tags.get(neighbor):
                desert_transitions[f"{node_domains.get(node_id,'?')} -> {node_domains.get(neighbor,'?')}"] += 1
                desert_edges.append({"from": node_id, "to": neighbor, "weight": weight})

    highway_edges.sort(key=lambda x: -x["jaccard"])

    print(f"Highway edges (>0.5): {len(highway_edges)}")
    print(f"Desert edges (0.0): {len(desert_edges)}")
    print(f"Avg Jaccard: {sum(all_jaccards)/len(all_jaccards):.4f}")

    # Summary
    tag_found = [r for r in results if r["tag_path"]["path"]]
    weight_found = [r for r in results if r["weight_path"]["path"]]
    strength_found = [r for r in results if r["strength_path"]["path"]]

    avg_tag_c = sum(r["tag_path"]["avg_coherence"] for r in tag_found) / len(tag_found) if tag_found else 0
    avg_weight_c = sum(r["weight_path"]["avg_coherence"] for r in weight_found) / len(weight_found) if weight_found else 0
    avg_strength_c = sum(r["strength_path"]["avg_coherence"] for r in strength_found) / len(strength_found) if strength_found else 0
    avg_tag_h = sum(r["tag_path"]["hops"] for r in tag_found) / len(tag_found) if tag_found else 0
    avg_weight_h = sum(r["weight_path"]["hops"] for r in weight_found) / len(weight_found) if weight_found else 0
    avg_strength_h = sum(r["strength_path"]["hops"] for r in strength_found) / len(strength_found) if strength_found else 0

    observations = [
        f"Tag-coherent BFS: {len(tag_found)}/20 paths, avg coherence={avg_tag_c:.4f}, avg hops={avg_tag_h:.1f}",
        f"Weight Dijkstra: {len(weight_found)}/20 paths, avg coherence={avg_weight_c:.4f}, avg hops={avg_weight_h:.1f}",
        f"Strength BFS: {len(strength_found)}/20 paths, avg coherence={avg_strength_c:.4f}, avg hops={avg_strength_h:.1f}",
        f"Tag coherence improvement over weight: {avg_tag_c - avg_weight_c:+.4f}",
        f"Tag coherence improvement over strength: {avg_tag_c - avg_strength_c:+.4f}",
        f"{len(all_tag_highways)} scenario paths are full tag highways (>0.5 all hops)",
        f"{len(all_tag_deserts)} tag desert transitions in scenarios",
        f"Network: {len(highway_edges)} highway edges, {len(desert_edges)} desert edges",
        f"Network avg Jaccard: {sum(all_jaccards)/len(all_jaccards):.4f}",
    ]

    print("\n=== SUMMARY ===")
    for o in observations:
        print(f"  {o}")

    report = {
        "report": "pathfind_p3_tags",
        "timestamp": datetime.now().isoformat(),
        "algorithm": "Tag-coherent BFS (Jaccard similarity, greedy best-first)",
        "description": "Pathfinding maximizing tag overlap between consecutive hops",
        "tag_source": "tag_clusters (307/310 nodes) + explicit node tags",
        "summary": {
            "total_scenarios": 20,
            "tag_paths_found": len(tag_found),
            "weight_paths_found": len(weight_found),
            "strength_paths_found": len(strength_found),
            "avg_tag_coherence": round(avg_tag_c, 4),
            "avg_weight_coherence": round(avg_weight_c, 4),
            "avg_strength_coherence": round(avg_strength_c, 4),
            "tag_vs_weight_improvement": round(avg_tag_c - avg_weight_c, 4),
            "tag_vs_strength_improvement": round(avg_tag_c - avg_strength_c, 4),
            "avg_tag_hops": round(avg_tag_h, 2),
            "avg_weight_hops": round(avg_weight_h, 2),
            "avg_strength_hops": round(avg_strength_h, 2),
            "tag_highways_in_scenarios": len(all_tag_highways),
            "tag_deserts_in_scenarios": len(all_tag_deserts),
            "network_highway_edges": len(highway_edges),
            "network_desert_edges": len(desert_edges)
        },
        "paths": results,
        "tag_highways": all_tag_highways,
        "tag_deserts_in_paths": all_tag_deserts,
        "network_analysis": {
            "top_highway_edges": highway_edges[:20],
            "desert_domain_transitions": dict(sorted(desert_transitions.items(), key=lambda x: -x[1])),
            "total_highway_edges": len(highway_edges),
            "total_desert_edges": len(desert_edges),
            "edge_jaccard_avg": round(sum(all_jaccards) / len(all_jaccards), 4)
        },
        "observations": observations
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
