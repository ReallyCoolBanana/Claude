#!/usr/bin/env python3
"""Cross-reference relationship graph query tool.

Usage:
    python graph_query.py --node KB-0001
    python graph_query.py --orphans
    python graph_query.py --hubs
    python graph_query.py --path KB-0001 KB-0025
    python graph_query.py --stats
"""
import argparse, json, os, sys
from collections import defaultdict, deque

GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "relationship_graph.json",
)


def load_graph(path=None):
    fpath = path or GRAPH_PATH
    if not os.path.exists(fpath):
        print(f"Error: graph file not found at {fpath}", file=sys.stderr)
        sys.exit(1)
    with open(fpath) as f:
        return json.load(f)


def build_adjacency(graph):
    adj = defaultdict(set)
    edge_lookup = defaultdict(list)
    for edge in graph["edges"]:
        a, b = edge["from"], edge["to"]
        adj[a].add(b)
        adj[b].add(a)
        edge_lookup[tuple(sorted([a, b]))].append(edge)
    return adj, edge_lookup


def build_node_map(graph):
    return {n["id"]: n for n in graph["nodes"]}


def _edge_extra(e):
    return f" (tags: {', '.join(e['shared'])})" if e["type"] == "shared_tags" else ""


def cmd_node(graph, node_id):
    node_map = build_node_map(graph)
    if node_id not in node_map:
        print(f"Error: node '{node_id}' not found.", file=sys.stderr); sys.exit(1)
    node = node_map[node_id]
    print(f"Node: {node_id}")
    print(f"  Domain: {node['domain']}")
    print(f"  Title:  {node['title']}")
    if node.get("tags"):
        print(f"  Tags:   {', '.join(node['tags'])}")
    print()
    outgoing = [e for e in graph["edges"] if e["from"] == node_id]
    incoming = [e for e in graph["edges"] if e["to"] == node_id]
    if outgoing:
        print("  Outgoing edges:")
        for e in sorted(outgoing, key=lambda x: (x["type"], x["to"])):
            t = node_map.get(e["to"], {}).get("title", "?")
            print(f"    -> {e['to']:12s} [{e['type']}] {t}{_edge_extra(e)}")
    if incoming:
        print("  Incoming edges:")
        for e in sorted(incoming, key=lambda x: (x["type"], x["from"])):
            t = node_map.get(e["from"], {}).get("title", "?")
            print(f"    <- {e['from']:12s} [{e['type']}] {t}{_edge_extra(e)}")
    print(f"\n  Total connections: {len(outgoing) + len(incoming)}")


def cmd_orphans(graph):
    adj, _ = build_adjacency(graph)
    orphans = [n for n in graph["nodes"] if n["id"] not in adj or not adj[n["id"]]]
    if not orphans:
        print("No orphan nodes found."); return
    print(f"Orphan nodes ({len(orphans)} with 0 relationships):\n")
    by_domain = defaultdict(list)
    for o in orphans:
        by_domain[o["domain"]].append(o)
    for domain in sorted(by_domain):
        print(f"  [{domain}]")
        for o in sorted(by_domain[domain], key=lambda x: x["id"]):
            print(f"    {o['id']:12s} {o['title']}")
        print()


def cmd_hubs(graph, top_n=20):
    adj, _ = build_adjacency(graph)
    node_map = build_node_map(graph)
    degrees = sorted(
        [(len(adj.get(n["id"], set())), n["id"]) for n in graph["nodes"]],
        reverse=True,
    )
    count = min(top_n, len(degrees))
    print(f"Top {count} most-connected nodes:\n")
    print(f"  {'Rank':<6}{'ID':<12}{'Deg':<6}{'Domain':<16}Title")
    print(f"  {'----':<6}{'--':<12}{'---':<6}{'------':<16}-----")
    for i, (deg, nid) in enumerate(degrees[:count], 1):
        n = node_map[nid]
        print(f"  {i:<6}{nid:<12}{deg:<6}{n['domain']:<16}{n['title'][:60]}")


def cmd_path(graph, start_id, end_id):
    node_map = build_node_map(graph)
    for nid in (start_id, end_id):
        if nid not in node_map:
            print(f"Error: node '{nid}' not found.", file=sys.stderr); sys.exit(1)
    if start_id == end_id:
        print(f"Start and end are the same node: {start_id}"); return
    adj, edge_lookup = build_adjacency(graph)
    visited = {start_id}
    parent = {start_id: None}
    queue = deque([start_id])
    while queue:
        current = queue.popleft()
        if current == end_id:
            break
        for nb in sorted(adj.get(current, set())):
            if nb not in visited:
                visited.add(nb)
                parent[nb] = current
                queue.append(nb)
    if end_id not in parent:
        print(f"No path found between {start_id} and {end_id}."); return
    path = []
    cur = end_id
    while cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.reverse()
    print(f"Shortest path from {start_id} to {end_id} ({len(path)-1} hops):\n")
    for i, nid in enumerate(path):
        n = node_map[nid]
        print(f"  [{i}] {nid:<12} ({n['domain']}) {n['title']}")
        if i < len(path) - 1:
            key = tuple(sorted([nid, path[i + 1]]))
            for e in edge_lookup.get(key, []):
                d = "--->" if e["from"] == nid else "<---"
                extra = f" ({', '.join(e['shared'])})" if e["type"] == "shared_tags" else ""
                print(f"       {d} [{e['type']}]{extra}")


def cmd_stats(graph):
    adj, _ = build_adjacency(graph)
    n_nodes = len(graph["nodes"])
    n_edges = len(graph["edges"])
    type_counts = defaultdict(int)
    for e in graph["edges"]:
        type_counts[e["type"]] += 1
    domain_counts = defaultdict(int)
    for n in graph["nodes"]:
        domain_counts[n["domain"]] += 1
    degrees = [len(adj.get(n["id"], set())) for n in graph["nodes"]]
    orphan_count = sum(1 for d in degrees if d == 0)
    avg_deg = sum(degrees) / len(degrees) if degrees else 0
    max_deg = max(degrees) if degrees else 0
    max_node = next((n["id"] for n in graph["nodes"]
                     if len(adj.get(n["id"], set())) == max_deg), "?")
    print("=== Relationship Graph Statistics ===\n")
    print(f"  Total nodes:    {n_nodes}")
    print(f"  Total edges:    {n_edges}")
    print(f"  Avg degree:     {avg_deg:.1f}")
    print(f"  Max degree:     {max_deg} ({max_node})")
    print(f"  Orphan nodes:   {orphan_count}\n")
    print("  Nodes by domain:")
    for d in sorted(domain_counts):
        print(f"    {d:<16} {domain_counts[d]}")
    print("\n  Edges by type:")
    for t in sorted(type_counts):
        print(f"    {t:<24} {type_counts[t]}")
    # Connected components
    visited, components = set(), 0
    for node in graph["nodes"]:
        nid = node["id"]
        if nid not in visited:
            components += 1
            stack = [nid]
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                stack.extend(nb for nb in adj.get(cur, set()) if nb not in visited)
    print(f"\n  Connected components: {components}")
    if n_nodes > 1:
        density = n_edges / (n_nodes * (n_nodes - 1) / 2)
        print(f"  Graph density:        {density:.4f}")


def main():
    p = argparse.ArgumentParser(description="Query the cross-reference relationship graph.")
    p.add_argument("--node", metavar="ID", help="Show all connections for an entry")
    p.add_argument("--orphans", action="store_true", help="Find entries with 0 relationships")
    p.add_argument("--hubs", action="store_true", help="Most-connected entries by degree")
    p.add_argument("--path", nargs=2, metavar=("FROM", "TO"), help="Shortest path between entries")
    p.add_argument("--stats", action="store_true", help="Summary statistics")
    p.add_argument("--graph-file", metavar="PATH", help="Path to relationship_graph.json")
    p.add_argument("--top", type=int, default=20, help="Results for --hubs (default: 20)")
    args = p.parse_args()
    if not any([args.node, args.orphans, args.hubs, args.path, args.stats]):
        p.print_help(); sys.exit(0)
    graph = load_graph(args.graph_file)
    if args.node:
        cmd_node(graph, args.node)
    elif args.orphans:
        cmd_orphans(graph)
    elif args.hubs:
        cmd_hubs(graph, args.top)
    elif args.path:
        cmd_path(graph, args.path[0], args.path[1])
    elif args.stats:
        cmd_stats(graph)


if __name__ == "__main__":
    main()
