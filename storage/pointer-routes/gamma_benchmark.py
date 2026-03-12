#!/usr/bin/env python3
"""
Team Gamma - Pointer Network Cross-Domain Benchmark
Simulates 20 realistic agent journeys across the pointer network,
measures path efficiency, tag coherence, and identifies gaps.
"""

import json
import subprocess
import sys
import os
from pathlib import Path
from collections import defaultdict, Counter, deque
from datetime import datetime

NETWORK_PATH = Path(__file__).parent.parent / "pointer-network.json"
REPORT_PATH = Path(__file__).parent / "benchmark_gamma_report.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"

# Load the pointer network
try:
    with open(NETWORK_PATH) as f:
        PN = json.load(f)
except FileNotFoundError:
    print(f"Error: pointer network file not found: {NETWORK_PATH}", file=sys.stderr)
    sys.exit(1)
NODES = PN["nodes"]


def get_domain(node_id):
    """Extract domain prefix from node ID."""
    if node_id.startswith("wiki"):
        return "wiki"
    return node_id.split("-")[0]


def get_tags_from_reasons(reasons):
    """Extract tags from pointer reason strings."""
    tags = set()
    for r in reasons:
        if r.startswith("shared_tags:"):
            for t in r.replace("shared_tags:", "").split(","):
                tags.add(t.strip())
    return tags


def get_node_tags(node_id):
    """Get all tags associated with a node by examining all its pointers."""
    node = NODES.get(node_id, {})
    tags = set()
    for ptr in node.get("pointers", []):
        tags |= get_tags_from_reasons(ptr.get("reasons", []))
    return tags


def get_pointers_to_domain(node_id, target_domain_prefix):
    """Get sorted pointers from node_id that point to a specific domain."""
    node = NODES.get(node_id, {})
    results = []
    for ptr in node.get("pointers", []):
        target = ptr["to"]
        if get_domain(target) == target_domain_prefix:
            results.append(ptr)
    results.sort(key=lambda p: p.get("weight", 0), reverse=True)
    return results


def get_all_pointers_sorted(node_id):
    """Get all pointers from a node, sorted by weight descending."""
    node = NODES.get(node_id, {})
    ptrs = list(node.get("pointers", []))
    ptrs.sort(key=lambda p: p.get("weight", 0), reverse=True)
    return ptrs


def tag_overlap(tags_a, tags_b):
    """Jaccard similarity between two tag sets."""
    if not tags_a and not tags_b:
        return 1.0
    if not tags_a or not tags_b:
        return 0.0
    return len(tags_a & tags_b) / len(tags_a | tags_b)


def bfs_shortest_path(start, end):
    """BFS shortest path in the pointer network."""
    if start == end:
        return [start]
    visited = {start}
    queue = deque([[start]])
    while queue:
        path = queue.popleft()
        current = path[-1]
        node = NODES.get(current, {})
        for ptr in node.get("pointers", []):
            nxt = ptr["to"]
            if nxt == end:
                return path + [nxt]
            if nxt not in visited and nxt in NODES:
                visited.add(nxt)
                queue.append(path + [nxt])
    return None  # unreachable


# ============================================================
# Journey simulation
# ============================================================

def simulate_data_gathering(sop_id, journey_num):
    """SOP -> SCR -> SRC: find a data source via a script."""
    path = [sop_id]
    hops_detail = []

    # Hop 1: SOP -> SCR (find best script)
    scr_ptrs = get_pointers_to_domain(sop_id, "SCR")
    if not scr_ptrs:
        # Try any hop that leads closer to SCR
        all_ptrs = get_all_pointers_sorted(sop_id)
        for p in all_ptrs:
            mid = p["to"]
            if get_pointers_to_domain(mid, "SCR"):
                path.append(mid)
                hops_detail.append({"from": sop_id, "to": mid, "weight": p["weight"],
                                     "useful": True, "note": "indirect via " + get_domain(mid)})
                scr_ptrs = get_pointers_to_domain(mid, "SCR")
                break
        if not scr_ptrs:
            return path, hops_detail, "failure", "No SCR reachable from " + sop_id

    best_scr = scr_ptrs[0]
    scr_id = best_scr["to"]
    path.append(scr_id)
    hops_detail.append({"from": path[-2], "to": scr_id, "weight": best_scr["weight"],
                         "useful": True, "note": "script found"})

    # Hop 2: SCR -> SRC (find data source)
    src_ptrs = get_pointers_to_domain(scr_id, "SRC")
    if not src_ptrs:
        return path, hops_detail, "partial", "Script found but no SRC connection"

    best_src = src_ptrs[0]
    src_id = best_src["to"]
    path.append(src_id)
    hops_detail.append({"from": scr_id, "to": src_id, "weight": best_src["weight"],
                         "useful": True, "note": "data source found"})

    return path, hops_detail, "success", "Full path SOP->SCR->SRC"


def simulate_knowledge_enrichment(sop_id, journey_num):
    """SOP -> SCR -> KB or wiki: find knowledge via a script."""
    path = [sop_id]
    hops_detail = []

    scr_ptrs = get_pointers_to_domain(sop_id, "SCR")
    if not scr_ptrs:
        # Try indirect
        all_ptrs = get_all_pointers_sorted(sop_id)
        for p in all_ptrs:
            mid = p["to"]
            if get_pointers_to_domain(mid, "SCR"):
                path.append(mid)
                hops_detail.append({"from": sop_id, "to": mid, "weight": p["weight"],
                                     "useful": True, "note": "indirect hop"})
                scr_ptrs = get_pointers_to_domain(mid, "SCR")
                break
        if not scr_ptrs:
            return path, hops_detail, "failure", "No SCR reachable"

    best_scr = scr_ptrs[0]
    scr_id = best_scr["to"]
    path.append(scr_id)
    hops_detail.append({"from": path[-2], "to": scr_id, "weight": best_scr["weight"],
                         "useful": True, "note": "script found"})

    # Try KB first, then wiki
    kb_ptrs = get_pointers_to_domain(scr_id, "KB")
    wiki_ptrs = get_pointers_to_domain(scr_id, "wiki")
    knowledge_ptrs = kb_ptrs + wiki_ptrs
    knowledge_ptrs.sort(key=lambda p: p.get("weight", 0), reverse=True)

    if not knowledge_ptrs:
        return path, hops_detail, "partial", "Script found but no KB/wiki connection"

    best_kb = knowledge_ptrs[0]
    kb_id = best_kb["to"]
    path.append(kb_id)
    hops_detail.append({"from": scr_id, "to": kb_id, "weight": best_kb["weight"],
                         "useful": True, "note": f"knowledge found ({get_domain(kb_id)})"})

    return path, hops_detail, "success", "Full path SOP->SCR->KB/wiki"


def simulate_monitoring(sop_id, journey_num):
    """SOP -> SCR -> SCR: find monitoring tool chain."""
    path = [sop_id]
    hops_detail = []

    scr_ptrs = get_pointers_to_domain(sop_id, "SCR")
    if not scr_ptrs:
        all_ptrs = get_all_pointers_sorted(sop_id)
        for p in all_ptrs:
            mid = p["to"]
            if get_pointers_to_domain(mid, "SCR"):
                path.append(mid)
                hops_detail.append({"from": sop_id, "to": mid, "weight": p["weight"],
                                     "useful": True, "note": "indirect hop"})
                scr_ptrs = get_pointers_to_domain(mid, "SCR")
                break
        if not scr_ptrs:
            return path, hops_detail, "failure", "No SCR reachable"

    best_scr = scr_ptrs[0]
    scr_id = best_scr["to"]
    path.append(scr_id)
    hops_detail.append({"from": path[-2], "to": scr_id, "weight": best_scr["weight"],
                         "useful": True, "note": "primary script"})

    # Hop 2: SCR -> another SCR (monitoring companion)
    scr2_ptrs = get_pointers_to_domain(scr_id, "SCR")
    if not scr2_ptrs:
        return path, hops_detail, "partial", "Script found but no companion SCR"

    best_scr2 = scr2_ptrs[0]
    scr2_id = best_scr2["to"]
    path.append(scr2_id)
    hops_detail.append({"from": scr_id, "to": scr2_id, "weight": best_scr2["weight"],
                         "useful": True, "note": "companion script found"})

    return path, hops_detail, "success", "Full path SOP->SCR->SCR"


def simulate_coordination(sop_id, journey_num):
    """SOP -> SCR -> SOP: cross-SOP workflow."""
    path = [sop_id]
    hops_detail = []

    scr_ptrs = get_pointers_to_domain(sop_id, "SCR")
    if not scr_ptrs:
        # Direct SOP->SOP fallback
        sop_ptrs = get_pointers_to_domain(sop_id, "SOP")
        if sop_ptrs:
            best = sop_ptrs[0]
            path.append(best["to"])
            hops_detail.append({"from": sop_id, "to": best["to"], "weight": best["weight"],
                                 "useful": True, "note": "direct SOP->SOP (no SCR bridge)"})
            return path, hops_detail, "partial", "Direct SOP link, no SCR intermediary"
        return path, hops_detail, "failure", "No SCR or SOP reachable"

    best_scr = scr_ptrs[0]
    scr_id = best_scr["to"]
    path.append(scr_id)
    hops_detail.append({"from": sop_id, "to": scr_id, "weight": best_scr["weight"],
                         "useful": True, "note": "script found"})

    # SCR -> SOP
    sop_ptrs = get_pointers_to_domain(scr_id, "SOP")
    if not sop_ptrs:
        return path, hops_detail, "partial", "Script found but no SOP cross-link"

    best_sop = sop_ptrs[0]
    sop2_id = best_sop["to"]
    path.append(sop2_id)
    hops_detail.append({"from": scr_id, "to": sop2_id, "weight": best_sop["weight"],
                         "useful": True, "note": "cross-SOP workflow link"})

    return path, hops_detail, "success", "Full path SOP->SCR->SOP"


def simulate_indexing(sop_id, journey_num):
    """SOP -> SCR -> multiple targets: fan-out discovery."""
    path = [sop_id]
    hops_detail = []

    scr_ptrs = get_pointers_to_domain(sop_id, "SCR")
    if not scr_ptrs:
        all_ptrs = get_all_pointers_sorted(sop_id)
        for p in all_ptrs:
            mid = p["to"]
            if get_pointers_to_domain(mid, "SCR"):
                path.append(mid)
                hops_detail.append({"from": sop_id, "to": mid, "weight": p["weight"],
                                     "useful": True, "note": "indirect hop"})
                scr_ptrs = get_pointers_to_domain(mid, "SCR")
                break
        if not scr_ptrs:
            return path, hops_detail, "failure", "No SCR reachable"

    best_scr = scr_ptrs[0]
    scr_id = best_scr["to"]
    path.append(scr_id)
    hops_detail.append({"from": path[-2], "to": scr_id, "weight": best_scr["weight"],
                         "useful": True, "note": "script found"})

    # Fan-out: collect all reachable domains from SCR
    all_ptrs = get_all_pointers_sorted(scr_id)
    domains_reached = set()
    fan_out_targets = []
    for p in all_ptrs[:5]:  # Top 5 pointers
        dom = get_domain(p["to"])
        domains_reached.add(dom)
        fan_out_targets.append(p["to"])

    if len(domains_reached) >= 2:
        # Add the best fan-out target to the path
        path.append(fan_out_targets[0])
        hops_detail.append({"from": scr_id, "to": fan_out_targets[0],
                             "weight": all_ptrs[0]["weight"],
                             "useful": True,
                             "note": f"fan-out: {len(domains_reached)} domains reachable ({','.join(sorted(domains_reached))})"})
        return path, hops_detail, "success", f"Fan-out to {len(domains_reached)} domains"
    elif fan_out_targets:
        path.append(fan_out_targets[0])
        hops_detail.append({"from": scr_id, "to": fan_out_targets[0],
                             "weight": all_ptrs[0]["weight"],
                             "useful": True,
                             "note": f"limited fan-out: only {','.join(sorted(domains_reached))}"})
        return path, hops_detail, "partial", "Limited fan-out reach"
    else:
        return path, hops_detail, "partial", "Script is a dead end"


# ============================================================
# Journey definitions: 20 journeys across 5 task types, 4 each
# ============================================================

SOP_IDS = sorted([nid for nid in NODES if nid.startswith("SOP-")])

TASK_TYPES = [
    ("data-gathering", simulate_data_gathering),
    ("knowledge-enrichment", simulate_knowledge_enrichment),
    ("monitoring", simulate_monitoring),
    ("coordination", simulate_coordination),
    ("indexing", simulate_indexing),
]

# Assign SOPs to journeys - spread across all available SOPs
def build_journey_plan():
    journeys = []
    sop_idx = 0
    for task_type, sim_fn in TASK_TYPES:
        for i in range(4):
            sop = SOP_IDS[sop_idx % len(SOP_IDS)]
            sop_idx += 1
            agent_num = (len(journeys) // 4) + 1  # gamma-1 through gamma-5
            journeys.append({
                "journey_num": len(journeys) + 1,
                "agent": f"gamma-{agent_num}",
                "task_type": task_type,
                "sop": sop,
                "sim_fn": sim_fn,
            })
    return journeys


# ============================================================
# Tag coherence analysis
# ============================================================

def measure_tag_coherence(path):
    """Measure how tag relevance evolves along a path."""
    if len(path) < 2:
        return {"coherent": True, "scores": [], "avg": 1.0, "drop_detected": False}

    scores = []
    start_tags = get_node_tags(path[0])

    for i in range(1, len(path)):
        current_tags = get_node_tags(path[i])
        # Compare against starting node tags
        score = tag_overlap(start_tags, current_tags)
        scores.append(round(score, 3))

    # Check for sharp drops (> 50% decrease between consecutive hops)
    drop_detected = False
    for i in range(1, len(scores)):
        if scores[i-1] > 0 and scores[i] < scores[i-1] * 0.5:
            drop_detected = True
            break

    avg = round(sum(scores) / len(scores), 3) if scores else 1.0
    return {
        "coherent": avg >= 0.1,
        "scores": scores,
        "avg": avg,
        "drop_detected": drop_detected,
    }


# ============================================================
# Path efficiency metrics
# ============================================================

def compute_path_efficiency():
    """Compute cross-domain connectivity metrics."""
    domain_pairs = [
        ("SOP", "SCR"), ("SOP", "SRC"), ("SOP", "KB"), ("SOP", "wiki"),
        ("SCR", "SRC"), ("SCR", "KB"), ("SCR", "wiki"), ("SCR", "SOP"),
        ("SRC", "KB"), ("SRC", "wiki"),
    ]

    results = {}
    for d1, d2 in domain_pairs:
        nodes_d1 = [n for n in NODES if get_domain(n) == d1][:10]
        nodes_d2 = [n for n in NODES if get_domain(n) == d2][:10]

        hops_list = []
        reachable = 0
        total = 0
        for n1 in nodes_d1:
            for n2 in nodes_d2:
                total += 1
                # Check direct pointer first
                direct = any(p["to"] == n2 for p in NODES.get(n1, {}).get("pointers", []))
                if direct:
                    hops_list.append(1)
                    reachable += 1
                else:
                    # Check 2-hop
                    found_2hop = False
                    for p in NODES.get(n1, {}).get("pointers", []):
                        mid = p["to"]
                        if mid in NODES:
                            if any(p2["to"] == n2 for p2 in NODES.get(mid, {}).get("pointers", [])):
                                hops_list.append(2)
                                reachable += 1
                                found_2hop = True
                                break
                    if not found_2hop:
                        # Mark as 3+ or unreachable (skip full BFS for performance)
                        pass

        pair_key = f"{d1}->{d2}"
        avg_hops = round(sum(hops_list) / len(hops_list), 2) if hops_list else None
        results[pair_key] = {
            "sampled_pairs": total,
            "reachable_in_2hops": reachable,
            "reachable_pct": round(reachable / total * 100, 1) if total else 0,
            "avg_hops": avg_hops,
            "direct_connections": sum(1 for h in hops_list if h == 1),
            "two_hop_connections": sum(1 for h in hops_list if h == 2),
        }

    return results


def compute_dead_end_rate():
    """Compute how often following pointers leads to dead ends by domain."""
    dead_ends = {}
    for domain_prefix in ["SOP", "SCR", "SRC", "KB", "wiki"]:
        nodes_in_domain = [n for n in NODES if get_domain(n) == domain_prefix]
        total = len(nodes_in_domain)
        dead = 0
        low_out = 0
        for nid in nodes_in_domain:
            ptrs = NODES.get(nid, {}).get("pointers", [])
            if len(ptrs) == 0:
                dead += 1
            elif len(ptrs) <= 2:
                low_out += 1
        dead_ends[domain_prefix] = {
            "total_nodes": total,
            "zero_outgoing": dead,
            "low_outgoing_le2": low_out,
            "dead_end_rate_pct": round(dead / total * 100, 1) if total else 0,
        }
    return dead_ends


def find_cross_domain_gaps():
    """Identify domain pairs that are poorly connected."""
    domain_prefixes = ["SOP", "SCR", "SRC", "KB", "wiki", "TEAM"]
    gap_matrix = {}

    for d1 in domain_prefixes:
        nodes_d1 = [n for n in NODES if get_domain(n) == d1]
        for d2 in domain_prefixes:
            if d1 == d2:
                continue
            # Count how many d1 nodes have at least one pointer to d2
            has_link = 0
            for nid in nodes_d1:
                if any(get_domain(p["to"]) == d2 for p in NODES.get(nid, {}).get("pointers", [])):
                    has_link += 1
            pct = round(has_link / len(nodes_d1) * 100, 1) if nodes_d1 else 0
            gap_matrix[f"{d1}->{d2}"] = {
                "source_count": len(nodes_d1),
                "with_link": has_link,
                "coverage_pct": pct,
            }

    # Flag pairs with < 30% coverage as gaps
    gaps = {k: v for k, v in gap_matrix.items() if v["coverage_pct"] < 30}
    return gaps, gap_matrix


# ============================================================
# Main execution
# ============================================================

def main():
    print("=" * 60)
    print("Team Gamma - Pointer Network Cross-Domain Benchmark")
    print("=" * 60)

    plan = build_journey_plan()
    journey_results = []
    tag_issues = []

    # Run all 20 journeys
    for j in plan:
        path, hops_detail, outcome, notes = j["sim_fn"](j["sop"], j["journey_num"])
        tag_coh = measure_tag_coherence(path)

        result = {
            "journey_num": j["journey_num"],
            "agent": j["agent"],
            "task_type": j["task_type"],
            "start_sop": j["sop"],
            "path": path,
            "path_str": " -> ".join(path),
            "hop_count": len(path) - 1,
            "outcome": outcome,
            "notes": notes,
            "hops_detail": hops_detail,
            "tag_coherence": tag_coh,
            "efficiency_score": round(sum(h["weight"] for h in hops_detail) / max(len(hops_detail), 1), 2),
        }
        journey_results.append(result)

        if tag_coh["drop_detected"] or not tag_coh["coherent"]:
            tag_issues.append({
                "journey": j["journey_num"],
                "path": result["path_str"],
                "coherence_scores": tag_coh["scores"],
                "avg_coherence": tag_coh["avg"],
                "issue": "sharp_drop" if tag_coh["drop_detected"] else "low_coherence",
            })

        status = "OK" if outcome == "success" else outcome.upper()
        print(f"  J{j['journey_num']:02d} [{j['agent']}] {j['task_type']:25s} {result['path_str']:60s} -> {status}")

    # Log routes via route_tracker
    print("\nLogging routes...")
    for r in journey_results:
        route_str = " -> ".join(r["path"])
        cmd = [
            sys.executable, str(ROUTE_TRACKER), "log",
            "--agent", r["agent"],
            "--task", r["task_type"],
            "--route", route_str,
            "--outcome", r["outcome"],
            "--notes", r["notes"],
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except Exception as e:
            print(f"  Warning: failed to log route for J{r['journey_num']}: {e}")

    # Compute metrics
    print("\nComputing path efficiency...")
    pair_efficiency = compute_path_efficiency()

    print("Computing dead-end rates...")
    dead_ends = compute_dead_end_rate()

    print("Finding cross-domain gaps...")
    gaps, full_matrix = find_cross_domain_gaps()

    # Summary statistics
    outcomes = Counter(r["outcome"] for r in journey_results)
    avg_hops = round(sum(r["hop_count"] for r in journey_results) / len(journey_results), 2)
    avg_efficiency = round(sum(r["efficiency_score"] for r in journey_results) / len(journey_results), 2)
    avg_coherence = round(sum(r["tag_coherence"]["avg"] for r in journey_results) / len(journey_results), 3)

    by_task = {}
    for tt, _ in TASK_TYPES:
        tt_results = [r for r in journey_results if r["task_type"] == tt]
        by_task[tt] = {
            "total": len(tt_results),
            "success": sum(1 for r in tt_results if r["outcome"] == "success"),
            "partial": sum(1 for r in tt_results if r["outcome"] == "partial"),
            "failure": sum(1 for r in tt_results if r["outcome"] == "failure"),
            "avg_hops": round(sum(r["hop_count"] for r in tt_results) / len(tt_results), 2),
            "avg_efficiency": round(sum(r["efficiency_score"] for r in tt_results) / len(tt_results), 2),
        }

    # Build recommendations
    recommendations = []

    # Check for poorly connected domain pairs
    for pair, info in sorted(gaps.items(), key=lambda x: x[1]["coverage_pct"]):
        src_dom, dst_dom = pair.split("->")
        recommendations.append(
            f"Improve {src_dom}->{dst_dom} connectivity (only {info['coverage_pct']}% of {src_dom} nodes link to {dst_dom})"
        )

    # Check task type failures
    for tt, stats in by_task.items():
        if stats["failure"] > 0:
            recommendations.append(
                f"Task '{tt}' had {stats['failure']} failures - add explicit cross-domain pointers for this pattern"
            )
        if stats["partial"] > 0:
            recommendations.append(
                f"Task '{tt}' had {stats['partial']} partial results - strengthen intermediate connections"
            )

    # Tag coherence recommendations
    if tag_issues:
        recommendations.append(
            f"Tag coherence issues detected on {len(tag_issues)} paths - consider adding shared tags to bridge nodes"
        )

    # Dead end recommendations
    for dom, info in dead_ends.items():
        if info["dead_end_rate_pct"] > 10:
            recommendations.append(
                f"Domain '{dom}' has {info['dead_end_rate_pct']}% dead-end rate ({info['zero_outgoing']} nodes with no outgoing pointers)"
            )

    # Build final report
    report = {
        "benchmark": "Team Gamma - Cross-Domain Pointer Traversal",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "network_stats": PN.get("stats", {}),
        "summary": {
            "total_journeys": len(journey_results),
            "outcomes": dict(outcomes),
            "success_rate_pct": round(outcomes.get("success", 0) / len(journey_results) * 100, 1),
            "avg_hops": avg_hops,
            "avg_efficiency_score": avg_efficiency,
            "avg_tag_coherence": avg_coherence,
        },
        "by_task_type": by_task,
        "journeys": journey_results,
        "path_efficiency": {
            "domain_pair_reachability": pair_efficiency,
            "dead_end_rates": dead_ends,
        },
        "cross_domain_gaps": {
            "poorly_connected_pairs": gaps,
            "full_coverage_matrix": full_matrix,
        },
        "tag_coherence_issues": tag_issues,
        "recommendations": recommendations,
    }

    # Write report
    os.makedirs(REPORT_PATH.parent, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'=' * 60}")
    print(f"RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"  Journeys: {len(journey_results)}")
    print(f"  Success: {outcomes.get('success', 0)}, Partial: {outcomes.get('partial', 0)}, Failure: {outcomes.get('failure', 0)}")
    print(f"  Success rate: {report['summary']['success_rate_pct']}%")
    print(f"  Avg hops: {avg_hops}")
    print(f"  Avg efficiency score: {avg_efficiency}")
    print(f"  Avg tag coherence: {avg_coherence}")
    print(f"  Tag coherence issues: {len(tag_issues)}")
    print(f"  Cross-domain gaps: {len(gaps)} pairs below 30% coverage")
    print(f"  Recommendations: {len(recommendations)}")
    print(f"\n  Report written to: {REPORT_PATH}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
