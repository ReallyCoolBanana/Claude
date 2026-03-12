#!/usr/bin/env python3
"""Tag-coherent pathfinding on the pointer network."""

import json
import heapq
import subprocess
import gc
from collections import defaultdict
from datetime import datetime

NETWORK_PATH = "/home/user/Claude/storage/pointer-network.json"
REPORT_PATH = "/home/user/Claude/storage/pointer-routes/pathfind_p3_tags.json"
ROUTE_TRACKER = "/home/user/Claude/storage/coordination/route_tracker.py"

def load_data():
    with open(NETWORK_PATH) as f:
        data = json.load(f)
    node_tags = defaultdict(set)
    for tag_name, ci in data.get("tag_clusters", {}).items():
        for nid in ci.get("nodes", []):
            node_tags[nid].add(tag_name)
    adj = {}
    node_domains = {}
    for nid, nd in data["nodes"].items():
        node_domains[nid] = nd.get("domain", "unknown")
        for t in nd.get("tags", []):
            node_tags[nid].add(t)
        adj[nid] = [(p["to"], p["weight"], p.get("strength", "unspecified")) for p in nd.get("pointers", [])]
    del data
    gc.collect()
    return adj, dict(node_tags), node_domains

def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)

def precompute_jac(adj, node_tags):
    r = {}
    for nid, edges in adj.items():
        st = node_tags.get(nid, set())
        r[nid] = [(nb, jaccard(st, node_tags.get(nb, set())), w) for nb, w, _ in edges]
    return r

def tag_bfs(adj_j, src, tgt, md=10):
    if src == tgt:
        return [src], []
    if src not in adj_j:
        return None, None
    vis = {src}
    prev = {}
    ej = {}
    q = []
    for nb, j, w in adj_j[src]:
        if nb not in vis:
            vis.add(nb)
            prev[nb] = src
            ej[nb] = j
            heapq.heappush(q, (-j, -w, 1, nb))
    while q:
        _, _, d, cur = heapq.heappop(q)
        if cur == tgt:
            p = []
            n = cur
            while n != src:
                p.append(n)
                n = prev[n]
            p.append(src)
            p.reverse()
            return p, [ej[x] for x in p[1:]]
        if d >= md:
            continue
        for nb, j, w in adj_j.get(cur, []):
            if nb not in vis:
                vis.add(nb)
                prev[nb] = cur
                ej[nb] = j
                heapq.heappush(q, (-j, -w, d + 1, nb))
    return None, None

def weight_dij(adj, src, tgt):
    """Max-weight Dijkstra with bounded heap (only push improvements)."""
    dist = {src: 0}
    prev = {}
    heap = [(0, src)]
    vis = set()
    while heap:
        nw, u = heapq.heappop(heap)
        cw = -nw
        if u in vis:
            continue
        vis.add(u)
        if u == tgt:
            break
        for nb, ew, _ in adj.get(u, []):
            if nb in vis:
                continue
            nwt = cw + ew
            old = dist.get(nb, -1)
            if nwt > old:
                dist[nb] = nwt
                prev[nb] = u
                heapq.heappush(heap, (-nwt, nb))
    if tgt not in prev and src != tgt:
        return None, None
    p = []
    n = tgt
    while n != src:
        p.append(n)
        n = prev[n]
    p.append(src)
    p.reverse()
    return dist[tgt], p

def str_bfs(adj, src, tgt, md=10):
    sm = {"primary": 4, "supporting": 3, "related": 2, "tangential": 1, "unspecified": 0}
    if src == tgt:
        return [src]
    vis = {src}
    prev = {}
    q = []
    for nb, w, s in adj.get(src, []):
        if nb not in vis:
            vis.add(nb)
            prev[nb] = src
            heapq.heappush(q, (-sm.get(s, 0), -w, 1, nb))
    while q:
        _, _, d, cur = heapq.heappop(q)
        if cur == tgt:
            p = []
            n = cur
            while n != src:
                p.append(n)
                n = prev[n]
            p.append(src)
            p.reverse()
            return p
        if d >= md:
            continue
        for nb, w, s in adj.get(cur, []):
            if nb not in vis:
                vis.add(nb)
                prev[nb] = cur
                heapq.heappush(q, (-sm.get(s, 0), -w, d + 1, nb))
    return None

def path_coh(path, node_tags):
    if not path or len(path) < 2:
        return [], 0.0
    c = []
    for i in range(len(path) - 1):
        c.append(jaccard(node_tags.get(path[i], set()), node_tags.get(path[i + 1], set())))
    return c, sum(c) / len(c) if c else 0.0

def main():
    print("Loading...")
    adj, node_tags, node_domains = load_data()
    print(f"Nodes: {len(node_domains)}, Edges: {sum(len(v) for v in adj.values())}")
    print(f"Tagged: {sum(1 for t in node_tags.values() if t)}/{len(node_domains)}")

    print("Precomputing Jaccard...")
    adj_j = precompute_jac(adj, node_tags)

    scenarios = [
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

    results = []
    highways = []
    deserts_list = []

    for i, (src, tgt, desc) in enumerate(scenarios):
        print(f"[{i+1:2d}/20] {src} -> {tgt}")

        tp, tc = tag_bfs(adj_j, src, tgt)
        wt, wp = weight_dij(adj, src, tgt)
        sp = str_bfs(adj, src, tgt)

        wc, wa = path_coh(wp, node_tags) if wp else ([], 0.0)
        sc, sa = path_coh(sp, node_tags) if sp else ([], 0.0)
        ta = sum(tc) / len(tc) if tc else 0.0

        if tp:
            print(f"  TAG: {' -> '.join(tp)} coh={ta:.4f}")
            is_hw = all(c > 0.5 for c in tc) if tc else False
            if is_hw:
                highways.append({"scenario": i+1, "source": src, "target": tgt,
                    "path": tp, "coherences": [round(c, 4) for c in tc],
                    "avg_coherence": round(ta, 4)})
                print("    HIGHWAY")
            for j in range(len(tc)):
                if tc[j] == 0.0:
                    deserts_list.append({"from_node": tp[j], "to_node": tp[j+1],
                        "from_domain": node_domains.get(tp[j], "?"),
                        "to_domain": node_domains.get(tp[j+1], "?"),
                        "from_tags": sorted(node_tags.get(tp[j], set())),
                        "to_tags": sorted(node_tags.get(tp[j+1], set())),
                        "scenario": i+1})
                    print(f"    DESERT {tp[j]} -> {tp[j+1]}")
        else:
            print("  TAG: NO PATH")

        if wp:
            print(f"  WGT: {' -> '.join(wp)} coh={wa:.4f} wt={wt}")
        if sp:
            print(f"  STR: {' -> '.join(sp)} coh={sa:.4f}")

        results.append({
            "scenario": i+1, "source": src, "target": tgt, "description": desc,
            "tag_path": {
                "path": tp, "hops": len(tp)-1 if tp else None,
                "hop_coherences": [round(c, 4) for c in tc] if tc else None,
                "avg_coherence": round(ta, 4) if tp else None,
                "is_tag_highway": bool(tp and tc and all(c > 0.5 for c in tc)),
                "has_tag_desert": bool(tc and any(c == 0.0 for c in tc)),
                "domain_sequence": [node_domains.get(n, "?") for n in tp] if tp else None
            },
            "weight_path": {
                "path": wp, "total_weight": wt,
                "hops": len(wp)-1 if wp else None,
                "hop_coherences": [round(c, 4) for c in wc],
                "avg_coherence": round(wa, 4) if wp else None
            },
            "strength_path": {
                "path": sp, "hops": len(sp)-1 if sp else None,
                "hop_coherences": [round(c, 4) for c in sc],
                "avg_coherence": round(sa, 4) if sp else None
            },
            "comparison": {
                "tag_vs_weight_coherence": round(ta - wa, 4) if tp and wp else None,
                "tag_vs_strength_coherence": round(ta - sa, 4) if tp and sp else None
            }
        })
        print()

    # Log routes
    print("Logging routes...")
    agents = ["p3-1", "p3-2", "p3-3", "p3-4"]
    for i, r in enumerate(results):
        agent = agents[i % 4]
        tp = r["tag_path"]
        if tp["path"]:
            route_str = " -> ".join(tp["path"])
            coh = tp["avg_coherence"]
            outcome = "success"
        else:
            route_str = r["source"] + " -> " + r["target"]
            coh = 0.0
            outcome = "no_path"
        cmd = ["python3", ROUTE_TRACKER, "log", "--agent", agent, "--task", "tag-pathfinding",
               "--route", route_str, "--outcome", outcome, "--notes", f"coherence={coh:.4f}"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            print(f"  {'OK' if res.returncode == 0 else 'FAIL'} {agent}: {r['source']} -> {r['target']}")
        except Exception as e:
            print(f"  ERR {agent}: {e}")

    # Network-wide analysis
    print("\nNetwork analysis...")
    hw_edges = []
    ds_count = 0
    all_j = []
    dt = defaultdict(int)

    for nid, edges in adj_j.items():
        for nb, j, w in edges:
            all_j.append(j)
            if j > 0.5:
                hw_edges.append({"from": nid, "to": nb, "jaccard": round(j, 4), "weight": w,
                    "shared_tags": sorted(node_tags.get(nid, set()) & node_tags.get(nb, set()))})
            elif j == 0.0 and node_tags.get(nid) and node_tags.get(nb):
                ds_count += 1
                dt[f"{node_domains.get(nid, '?')} -> {node_domains.get(nb, '?')}"] += 1

    hw_edges.sort(key=lambda x: -x["jaccard"])

    tf = [r for r in results if r["tag_path"]["path"]]
    wf = [r for r in results if r["weight_path"]["path"]]
    sf = [r for r in results if r["strength_path"]["path"]]

    atc = sum(r["tag_path"]["avg_coherence"] for r in tf) / len(tf) if tf else 0
    awc = sum(r["weight_path"]["avg_coherence"] for r in wf) / len(wf) if wf else 0
    asc = sum(r["strength_path"]["avg_coherence"] for r in sf) / len(sf) if sf else 0
    ath = sum(r["tag_path"]["hops"] for r in tf) / len(tf) if tf else 0
    awh = sum(r["weight_path"]["hops"] for r in wf) / len(wf) if wf else 0
    ash = sum(r["strength_path"]["hops"] for r in sf) / len(sf) if sf else 0

    obs = [
        f"Tag BFS: {len(tf)}/20 paths, avg coh={atc:.4f}, avg hops={ath:.1f}",
        f"Weight Dijkstra: {len(wf)}/20 paths, avg coh={awc:.4f}, avg hops={awh:.1f}",
        f"Strength BFS: {len(sf)}/20 paths, avg coh={asc:.4f}, avg hops={ash:.1f}",
        f"Tag coherence gain over weight: {atc-awc:+.4f}",
        f"Tag coherence gain over strength: {atc-asc:+.4f}",
        f"{len(highways)} scenario paths are tag highways (>0.5 all hops)",
        f"{len(deserts_list)} tag desert transitions in scenarios",
        f"Network: {len(hw_edges)} highway edges (>0.5), {ds_count} desert edges (0.0)",
        f"Network avg Jaccard: {sum(all_j)/len(all_j):.4f}",
    ]

    print("\n=== SUMMARY ===")
    for o in obs:
        print(f"  {o}")

    report = {
        "report": "pathfind_p3_tags",
        "timestamp": datetime.now().isoformat(),
        "algorithm": "Tag-coherent BFS (Jaccard similarity, greedy best-first)",
        "description": "Pathfinding maximizing tag overlap between consecutive hops",
        "tag_source": "tag_clusters (307/310 nodes) + explicit node tags",
        "summary": {
            "total_scenarios": 20,
            "tag_paths_found": len(tf),
            "weight_paths_found": len(wf),
            "strength_paths_found": len(sf),
            "avg_tag_coherence": round(atc, 4),
            "avg_weight_coherence": round(awc, 4),
            "avg_strength_coherence": round(asc, 4),
            "tag_vs_weight_improvement": round(atc - awc, 4),
            "tag_vs_strength_improvement": round(atc - asc, 4),
            "avg_tag_hops": round(ath, 2),
            "avg_weight_hops": round(awh, 2),
            "avg_strength_hops": round(ash, 2),
            "tag_highways_in_scenarios": len(highways),
            "tag_deserts_in_scenarios": len(deserts_list),
            "network_highway_edges": len(hw_edges),
            "network_desert_edges": ds_count
        },
        "paths": results,
        "tag_highways": highways,
        "tag_deserts_in_paths": deserts_list,
        "network_analysis": {
            "top_highway_edges": hw_edges[:20],
            "desert_domain_transitions": dict(sorted(dt.items(), key=lambda x: -x[1])),
            "total_highway_edges": len(hw_edges),
            "total_desert_edges": ds_count,
            "edge_jaccard_avg": round(sum(all_j) / len(all_j), 4)
        },
        "observations": obs
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to {REPORT_PATH}")

if __name__ == "__main__":
    main()
