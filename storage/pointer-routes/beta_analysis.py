#!/usr/bin/env python3
"""Team Beta: Pointer network structural analysis."""
import json
from collections import defaultdict, Counter
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
PN_PATH = _SCRIPT_DIR.parent / "pointer-network.json"

with open(PN_PATH) as f:
    pn = json.load(f)

nodes = pn["nodes"]
node_ids = set(nodes.keys())

# Build edge structures
edges = []  # (src, dst, weight, reasons)
outgoing = defaultdict(list)  # node -> [(dst, weight)]
incoming = defaultdict(list)  # node -> [(src, weight)]
edge_set = set()  # (src, dst)
edge_weights = {}  # (src, dst) -> weight

for src, data in nodes.items():
    for ptr in data.get("pointers", []):
        dst = ptr["to"]
        w = ptr.get("weight", 0)
        reasons = ptr.get("reasons", [])
        edges.append((src, dst, w, reasons))
        outgoing[src].append((dst, w))
        incoming[dst].append((src, w))

        key = (src, dst)
        if key in edge_set:
            # duplicate
            pass
        edge_set.add(key)
        edge_weights[key] = w

print(f"Nodes: {len(node_ids)}")
print(f"Total edges (incl dups): {len(edges)}")
print(f"Unique edges: {len(edge_set)}")

# ============================================================
# 1. ORPHAN ANALYSIS - nodes with no incoming pointers
# ============================================================
def get_type(node_id):
    if node_id.startswith("SCR-"): return "SCR"
    if node_id.startswith("KB-"): return "KB"
    if node_id.startswith("WIKI-") or node_id.startswith("wiki-"): return "WIKI"
    if node_id.startswith("SRC-"): return "SRC"
    if node_id.startswith("TEAM-"): return "TEAM"
    if node_id.startswith("SOP-"): return "SOP"
    return "OTHER"

orphans = [n for n in node_ids if n not in incoming or len(incoming[n]) == 0]
orphans.sort()
orphan_by_type = defaultdict(list)
for o in orphans:
    orphan_by_type[get_type(o)].append(o)

print(f"\n=== ORPHAN ANALYSIS ===")
print(f"Total orphans: {len(orphans)}")
for t, lst in sorted(orphan_by_type.items()):
    print(f"  {t}: {len(lst)}")
    # Show first few
    for n in lst[:5]:
        out_count = len(outgoing.get(n, []))
        print(f"    {n} (outgoing: {out_count})")
    if len(lst) > 5:
        print(f"    ... and {len(lst) - 5} more")

# Categorize orphans by criticality
critical_orphans = []  # SCR, KB, SOP - should have incoming
notable_orphans = []   # TEAM, SRC - likely should have incoming
leaf_orphans = []      # WIKI - may be legitimate leaves

for o in orphans:
    t = get_type(o)
    if t in ("SCR", "KB", "SOP"):
        critical_orphans.append({"id": o, "type": t, "outgoing": len(outgoing.get(o, []))})
    elif t in ("TEAM", "SRC"):
        notable_orphans.append({"id": o, "type": t, "outgoing": len(outgoing.get(o, []))})
    else:
        leaf_orphans.append({"id": o, "type": t, "outgoing": len(outgoing.get(o, []))})

# ============================================================
# 2. RECIPROCITY CHECK - non-reciprocal high-weight edges
# ============================================================
print(f"\n=== RECIPROCITY CHECK ===")
non_reciprocal_high = []
for (src, dst), w in edge_weights.items():
    if w >= 5:
        reverse = (dst, src)
        if reverse not in edge_set:
            non_reciprocal_high.append({
                "from": src, "to": dst, "weight": w,
                "missing_reverse": True
            })

non_reciprocal_high.sort(key=lambda x: -x["weight"])
print(f"Non-reciprocal edges with weight >= 5: {len(non_reciprocal_high)}")
for e in non_reciprocal_high[:10]:
    print(f"  {e['from']} -> {e['to']} (w={e['weight']})")

# ============================================================
# 3. ISLAND DETECTION - BFS from SOPs
# ============================================================
print(f"\n=== ISLAND DETECTION ===")

# Build undirected adjacency for connectivity
undirected = defaultdict(set)
for (src, dst) in edge_set:
    undirected[src].add(dst)
    undirected[dst].add(src)

# Find connected components
visited = set()
components = []

def bfs(start):
    q = [start]
    comp = set()
    while q:
        node = q.pop(0)
        if node in visited:
            continue
        visited.add(node)
        comp.add(node)
        for neighbor in undirected.get(node, set()):
            if neighbor not in visited and neighbor in node_ids:
                q.append(neighbor)
    return comp

for n in sorted(node_ids):
    if n not in visited:
        comp = bfs(n)
        components.append(comp)

components.sort(key=len, reverse=True)
print(f"Connected components: {len(components)}")
for i, comp in enumerate(components):
    types = Counter(get_type(n) for n in comp)
    print(f"  Component {i}: {len(comp)} nodes - {dict(types)}")

# BFS reachability from SOPs (directed)
sop_nodes = [n for n in node_ids if n.startswith("SOP-")]
sop_reachable = set()
for sop in sop_nodes:
    q = [sop]
    seen = set()
    while q:
        node = q.pop(0)
        if node in seen:
            continue
        seen.add(node)
        sop_reachable.add(node)
        for dst, w in outgoing.get(node, []):
            if dst not in seen and dst in node_ids:
                q.append(dst)

unreachable_from_sops = node_ids - sop_reachable
print(f"\nNodes reachable from any SOP (directed): {len(sop_reachable)}")
print(f"Nodes NOT reachable from any SOP: {len(unreachable_from_sops)}")
if unreachable_from_sops:
    unreach_types = Counter(get_type(n) for n in unreachable_from_sops)
    print(f"  Types: {dict(unreach_types)}")
    for n in sorted(unreachable_from_sops)[:15]:
        print(f"    {n}")

# Disconnected clusters (components beyond the main one)
disconnected_clusters = []
if len(components) > 1:
    for comp in components[1:]:
        disconnected_clusters.append({
            "nodes": sorted(comp),
            "size": len(comp),
            "types": dict(Counter(get_type(n) for n in comp))
        })

# ============================================================
# 4. WEIGHT ANOMALIES
# ============================================================
print(f"\n=== WEIGHT ANOMALIES ===")

# Zero-weight edges
zero_weight = [(src, dst) for (src, dst), w in edge_weights.items() if w == 0]
print(f"Zero-weight edges: {len(zero_weight)}")
for src, dst in zero_weight[:10]:
    print(f"  {src} -> {dst}")

# Duplicate edges
dup_counter = Counter((src, dst) for src, dst, w, r in edges)
duplicates = [(k, v) for k, v in dup_counter.items() if v > 1]
print(f"Duplicate edges: {len(duplicates)}")
for (src, dst), count in duplicates[:10]:
    print(f"  {src} -> {dst}: {count}x")

# Skewed weight distribution
skewed_nodes = []
for n in node_ids:
    weights = [w for _, w in outgoing.get(n, [])]
    if len(weights) >= 3:
        max_w = max(weights)
        min_w = min(weights)
        avg_w = sum(weights) / len(weights)
        if max_w >= 8 and min_w <= 1 and max_w >= avg_w * 3:
            skewed_nodes.append({
                "node": n,
                "max_weight": max_w,
                "min_weight": min_w,
                "avg_weight": round(avg_w, 1),
                "edge_count": len(weights)
            })

skewed_nodes.sort(key=lambda x: -x["max_weight"])
print(f"Nodes with skewed weight distribution: {len(skewed_nodes)}")
for s in skewed_nodes[:10]:
    print(f"  {s['node']}: max={s['max_weight']}, min={s['min_weight']}, avg={s['avg_weight']}")

# Dangling references (edges pointing to non-existent nodes)
dangling = [(src, dst) for (src, dst) in edge_set if dst not in node_ids]
print(f"Dangling references (to non-existent nodes): {len(dangling)}")
for src, dst in dangling[:10]:
    print(f"  {src} -> {dst}")

# ============================================================
# BUILD REPORT
# ============================================================
report = {
    "analysis_timestamp": "2026-03-12",
    "agent": "Team Beta",
    "network_stats": {
        "total_nodes": len(node_ids),
        "total_edges": len(edges),
        "unique_edges": len(edge_set),
        "connected_components": len(components),
        "main_component_size": len(components[0]) if components else 0
    },
    "orphan_nodes": {
        "total": len(orphans),
        "by_type": {t: sorted(lst) for t, lst in orphan_by_type.items()},
        "critical": critical_orphans,
        "notable": notable_orphans,
        "leaf_candidates": leaf_orphans,
        "recommendations": [
            f"Add incoming pointers to {len(critical_orphans)} critical orphans (SCR/KB/SOP nodes)",
            f"Review {len(notable_orphans)} TEAM/SRC orphans for missing references",
            f"{len(leaf_orphans)} WIKI orphans may be legitimate leaf nodes"
        ]
    },
    "missing_backlinks": {
        "total_non_reciprocal_high_weight": len(non_reciprocal_high),
        "edges": non_reciprocal_high[:50],
        "recommendations": [
            f"Add {len(non_reciprocal_high)} reverse edges for high-weight non-reciprocal links",
            "Priority: edges with weight >= 8 should almost certainly be bidirectional"
        ]
    },
    "disconnected_clusters": {
        "total_components": len(components),
        "main_component_size": len(components[0]) if components else 0,
        "isolated_clusters": disconnected_clusters,
        "unreachable_from_sops": {
            "count": len(unreachable_from_sops),
            "by_type": dict(Counter(get_type(n) for n in unreachable_from_sops)),
            "nodes": sorted(unreachable_from_sops)
        },
        "recommendations": [
            f"{len(components) - 1} disconnected clusters found" if len(components) > 1 else "Network is fully connected (undirected)",
            f"{len(unreachable_from_sops)} nodes unreachable via directed traversal from SOPs"
        ]
    },
    "weight_anomalies": {
        "zero_weight_edges": [{"from": s, "to": d} for s, d in zero_weight],
        "duplicate_edges": [{"from": s, "to": d, "count": c} for (s, d), c in duplicates],
        "skewed_distributions": skewed_nodes[:20],
        "dangling_references": [{"from": s, "to": d} for s, d in dangling],
        "recommendations": [
            f"Fix {len(zero_weight)} zero-weight edges (broken pointers)",
            f"Remove {len(duplicates)} duplicate edges",
            f"Review {len(skewed_nodes)} nodes with heavily skewed weight distributions",
            f"Fix {len(dangling)} dangling references to non-existent nodes"
        ]
    },
    "recommendations": [
        f"CRITICAL: {len(critical_orphans)} SCR/KB/SOP nodes have zero incoming pointers - these are unreachable knowledge",
        f"HIGH: {len(non_reciprocal_high)} high-weight edges lack reverse links - network navigation is one-way",
        f"MEDIUM: {len(unreachable_from_sops)} nodes unreachable from SOPs via directed traversal",
        f"LOW: {len(zero_weight)} zero-weight edges, {len(duplicates)} duplicates, {len(dangling)} dangling refs"
    ]
}

with open(_SCRIPT_DIR / "benchmark_beta_report.json", "w") as f:
    json.dump(report, f, indent=2)

print(f"\n=== REPORT WRITTEN ===")
print(json.dumps(report["recommendations"], indent=2))

# Print sample routes for logging
print(f"\n=== SAMPLE ROUTES FOR LOGGING ===")
# Route 1: Orphan detection path
if orphans:
    sample_orphan = orphans[0]
    out_targets = [d for d, w in outgoing.get(sample_orphan, [])[:3]]
    route1 = [sample_orphan] + out_targets
    print(f"Route 1 (orphan scan): {' -> '.join(route1)}")

# Route 2: Reciprocity check path
if non_reciprocal_high:
    e = non_reciprocal_high[0]
    route2 = [e["from"], e["to"]]
    print(f"Route 2 (reciprocity): {' -> '.join(route2)}")

# Route 3: Component traversal
if components:
    comp0_sample = sorted(components[0])[:4]
    print(f"Route 3 (main component): {' -> '.join(comp0_sample)}")

# Route 4: Weight anomaly check
if skewed_nodes:
    sn = skewed_nodes[0]["node"]
    targets = [d for d, w in outgoing.get(sn, [])[:3]]
    route4 = [sn] + targets
    print(f"Route 4 (weight anomaly): {' -> '.join(route4)}")
