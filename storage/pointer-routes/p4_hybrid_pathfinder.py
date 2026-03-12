#!/usr/bin/env python3
"""
P4 Hybrid/Adaptive Pathfinder — combines weight, strength, and tag signals.

Score = (weight * alpha_w) + (strength_bonus * alpha_s) + (tag_coherence * alpha_t)

Strength bonuses: primary=10, supporting=7, related=4, tangential=1, unclassified=2
Tag coherence: Jaccard similarity between current node tags and target node tags, scaled to 10.
"""

import json
import os
import sys
import heapq
from collections import defaultdict
from pathlib import Path

POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"

# Strength bonus map
STRENGTH_BONUS = {
    "primary": 10,
    "supporting": 7,
    "related": 4,
    "tangential": 1,
    "unclassified": 2,
}

# 10 pathfinding tasks (SOP-based, same style as P2/P3)
TASKS = [
    {"name": "coordination-to-scripts", "start": "SOP-011", "target": "SCR-0033", "desc": "Find coordination script from SOP-011"},
    {"name": "monitoring-to-kb", "start": "SOP-012", "target": "KB-0023", "desc": "Navigate monitoring SOP to knowledge base"},
    {"name": "infra-to-team", "start": "SOP-013", "target": "TEAM-0022", "desc": "Infrastructure SOP to team log"},
    {"name": "data-gathering-pipeline", "start": "SOP-017", "target": "SCR-0009", "desc": "Data gathering to dedup script"},
    {"name": "search-to-wiki", "start": "SOP-023", "target": "wiki-ai:AI_agent", "desc": "Search SOP to Wikipedia source"},
    {"name": "wikipedia-enrichment", "start": "SOP-027", "target": "KB-0001", "desc": "Wikipedia enrichment to KB root"},
    {"name": "validation-chain", "start": "SOP-020", "target": "SCR-0033", "desc": "Validation SOP to sqlite script"},
    {"name": "pointer-network-build", "start": "SOP-028", "target": "SCR-0057", "desc": "Pointer network SOP to build script"},
    {"name": "cross-domain-hub", "start": "SOP-G03", "target": "SCR-0033", "desc": "Hub-spoke SOP to coordination script"},
    {"name": "index-to-monitor", "start": "SOP-029", "target": "SOP-012", "desc": "Index SOP to monitoring SOP"},
]

# 5 alpha tunings
TUNINGS = {
    "pure_weight":    (1.0, 0.0, 0.0),
    "weight_heavy":   (0.6, 0.2, 0.2),
    "balanced":       (0.4, 0.3, 0.3),
    "strength_heavy": (0.2, 0.6, 0.2),
    "tag_heavy":      (0.2, 0.2, 0.6),
}


def load_network():
    with open(POINTER_NETWORK) as f:
        return json.load(f)


def get_node_tags(pn, node_id):
    """Get tags for a node. Only SOP nodes have explicit tags, but we can
    infer tags from shared_tags reasons on incoming pointers."""
    node = pn.get("nodes", {}).get(node_id, {})
    tags = set(node.get("tags", []))
    # If no explicit tags, infer from pointer reasons
    if not tags:
        for ptr in node.get("pointers", []):
            for reason in ptr.get("reasons", []):
                if reason.startswith("shared_tags:"):
                    for t in reason.split(":", 1)[1].split(","):
                        tags.add(t.strip())
    return tags


def tag_coherence(pn, current_id, neighbor_id, target_id):
    """Compute tag coherence: how well does moving to neighbor align with target?
    Returns 0-10 score based on Jaccard similarity of neighbor tags with target tags."""
    neighbor_tags = get_node_tags(pn, neighbor_id)
    target_tags = get_node_tags(pn, target_id)
    if not neighbor_tags and not target_tags:
        return 5.0  # neutral when no tag info
    if not neighbor_tags or not target_tags:
        return 2.0  # slight penalty for missing tags
    intersection = neighbor_tags & target_tags
    union = neighbor_tags | target_tags
    jaccard = len(intersection) / len(union) if union else 0
    return jaccard * 10.0


def edge_score(pn, src_id, pointer, target_id, alpha_w, alpha_s, alpha_t):
    """Compute hybrid score for an edge."""
    weight = pointer.get("weight", 0)
    strength = pointer.get("strength", "unclassified")
    s_bonus = STRENGTH_BONUS.get(strength, 2)
    t_coh = tag_coherence(pn, src_id, pointer["to"], target_id)
    score = (weight * alpha_w) + (s_bonus * alpha_s) + (t_coh * alpha_t)
    return score


def adaptive_pathfind(pn, start, target, alpha_w, alpha_s, alpha_t, max_hops=8):
    """A* style greedy best-first search using hybrid scoring.

    Uses negative score as cost (we want to maximize score, so minimize negative).
    Returns (path, total_score, hop_details) or (None, 0, []) if no path found.
    """
    if start not in pn.get("nodes", {}):
        return None, 0, []
    if start == target:
        return [start], 0, []

    # Priority queue: (-accumulated_score, counter, hop_count, current_node, path, hop_details)
    counter = 0
    pq = [(0, counter, 0, start, [start], [])]
    visited = set()

    while pq:
        neg_score, _, hops, current, path, details = heapq.heappop(pq)

        if current == target:
            return path, -neg_score, details

        if current in visited:
            continue
        visited.add(current)

        if hops >= max_hops:
            continue

        node = pn.get("nodes", {}).get(current, {})
        for ptr in node.get("pointers", []):
            neighbor = ptr["to"]
            if neighbor in visited:
                continue
            if neighbor not in pn.get("nodes", {}):
                continue

            e_score = edge_score(pn, current, ptr, target, alpha_w, alpha_s, alpha_t)
            new_path = path + [neighbor]
            new_details = details + [{
                "from": current,
                "to": neighbor,
                "weight": ptr.get("weight", 0),
                "strength": ptr.get("strength", "unclassified"),
                "tag_coherence": round(tag_coherence(pn, current, neighbor, target), 2),
                "edge_score": round(e_score, 2),
            }]
            counter += 1
            # Use negative score because heapq is a min-heap
            heapq.heappush(pq, (neg_score - e_score, counter, hops + 1, neighbor, new_path, new_details))

    # No path found within max_hops
    return None, 0, []


def run_all_tunings(pn):
    """Run all 10 tasks across all 5 tunings. Returns structured results."""
    results = {}
    for tuning_name, (aw, as_, at) in TUNINGS.items():
        tuning_results = []
        for task in TASKS:
            path, total_score, details = adaptive_pathfind(
                pn, task["start"], task["target"], aw, as_, at
            )
            success = path is not None
            hop_count = len(details) if details else 0
            total_weight = sum(d["weight"] for d in details) if details else 0
            avg_tag_coh = (sum(d["tag_coherence"] for d in details) / hop_count) if hop_count > 0 else 0
            strengths_used = [d["strength"] for d in details] if details else []

            tuning_results.append({
                "task": task["name"],
                "start": task["start"],
                "target": task["target"],
                "desc": task["desc"],
                "success": success,
                "path": path,
                "path_str": " -> ".join(path) if path else "NO_PATH",
                "hop_count": hop_count,
                "total_score": round(total_score, 2),
                "total_weight": total_weight,
                "avg_tag_coherence": round(avg_tag_coh, 2),
                "strengths_used": strengths_used,
                "details": details,
            })

        results[tuning_name] = tuning_results
    return results


def evaluate_tunings(results):
    """Score each tuning across all dimensions and find the optimal one."""
    summaries = {}
    for tuning_name, tasks in results.items():
        successes = sum(1 for t in tasks if t["success"])
        total = len(tasks)
        successful_tasks = [t for t in tasks if t["success"]]
        avg_hops = (sum(t["hop_count"] for t in successful_tasks) / len(successful_tasks)) if successful_tasks else 999
        avg_tag = (sum(t["avg_tag_coherence"] for t in successful_tasks) / len(successful_tasks)) if successful_tasks else 0
        avg_weight = (sum(t["total_weight"] for t in successful_tasks) / len(successful_tasks)) if successful_tasks else 0
        avg_score = (sum(t["total_score"] for t in successful_tasks) / len(successful_tasks)) if successful_tasks else 0

        # Composite ranking score:
        # - success rate (0-1) * 40
        # - efficiency (inverse hops, normalized) * 20
        # - tag coherence (0-10 scaled to 0-1) * 20
        # - weight accumulation (normalized to 0-1 by dividing by max possible ~50) * 20
        success_score = (successes / total) * 40
        efficiency_score = (1 / max(avg_hops, 1)) * 20 * 3  # scale so 3-hop gets ~20
        tag_score = (avg_tag / 10) * 20
        weight_score = min(avg_weight / 30, 1) * 20  # cap at 30

        composite = success_score + efficiency_score + tag_score + weight_score

        summaries[tuning_name] = {
            "alpha": list(TUNINGS[tuning_name]),
            "successes": successes,
            "total": total,
            "success_rate": round(successes / total * 100, 1),
            "avg_hops": round(avg_hops, 2),
            "avg_tag_coherence": round(avg_tag, 2),
            "avg_weight": round(avg_weight, 2),
            "avg_score": round(avg_score, 2),
            "composite_score": round(composite, 2),
        }

    # Sort by composite score
    ranked = sorted(summaries.items(), key=lambda x: x[1]["composite_score"], reverse=True)
    best_name = ranked[0][0]
    return summaries, ranked, best_name


def main():
    print("=== P4 Hybrid Pathfinder ===")
    print("Loading pointer network...")
    pn = load_network()
    print(f"Loaded {len(pn['nodes'])} nodes")

    # Verify tasks are reachable
    for task in TASKS:
        if task["start"] not in pn["nodes"]:
            print(f"WARNING: start {task['start']} not in network")
        if task["target"] not in pn["nodes"]:
            print(f"WARNING: target {task['target']} not in network")

    print(f"\nRunning {len(TUNINGS)} tunings x {len(TASKS)} tasks = {len(TUNINGS) * len(TASKS)} paths\n")

    results = run_all_tunings(pn)
    summaries, ranked, best_name = evaluate_tunings(results)

    # Print results
    print("=" * 80)
    print("TUNING COMPARISON")
    print("=" * 80)
    for name, summary in ranked:
        marker = " <<< BEST" if name == best_name else ""
        print(f"\n{name} (alpha={summary['alpha']}){marker}")
        print(f"  Success: {summary['successes']}/{summary['total']} ({summary['success_rate']}%)")
        print(f"  Avg hops: {summary['avg_hops']}")
        print(f"  Avg tag coherence: {summary['avg_tag_coherence']}")
        print(f"  Avg weight: {summary['avg_weight']}")
        print(f"  Avg hybrid score: {summary['avg_score']}")
        print(f"  Composite score: {summary['composite_score']}")

    # Show best tuning's paths
    print(f"\n{'=' * 80}")
    print(f"BEST TUNING: {best_name} (alpha={TUNINGS[best_name]})")
    print(f"{'=' * 80}")
    for t in results[best_name]:
        status = "OK" if t["success"] else "FAIL"
        print(f"  [{status}] {t['task']}: {t['path_str']} ({t['hop_count']} hops, score={t['total_score']})")

    # Build report
    report = {
        "agent": "p4-pathfinder",
        "experiment": "hybrid-adaptive-pathfinding",
        "date": "2026-03-12",
        "description": "Tests 5 alpha tunings (weight/strength/tag) across 10 SOP-based pathfinding tasks",
        "scoring_formula": "score = (weight * alpha_w) + (strength_bonus * alpha_s) + (tag_coherence * alpha_t)",
        "strength_bonuses": STRENGTH_BONUS,
        "tunings_tested": {k: {"alpha_weight": v[0], "alpha_strength": v[1], "alpha_tag": v[2]} for k, v in TUNINGS.items()},
        "tasks": [{"name": t["name"], "start": t["start"], "target": t["target"], "desc": t["desc"]} for t in TASKS],
        "tuning_summaries": summaries,
        "tuning_ranking": [{"rank": i+1, "tuning": name, "composite_score": s["composite_score"]} for i, (name, s) in enumerate(ranked)],
        "optimal_tuning": {
            "name": best_name,
            "alpha": list(TUNINGS[best_name]),
            "composite_score": summaries[best_name]["composite_score"],
        },
        "best_tuning_paths": results[best_name],
        "all_results": results,
    }

    report_path = Path(__file__).parent / "pathfind_p4_hybrid.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to: {report_path}")

    # Output for route logging
    print(f"\n{'=' * 80}")
    print("ROUTE LOG COMMANDS (best tuning paths):")
    print(f"{'=' * 80}")
    agent_cycle = ["p4-1", "p4-2", "p4-3"]
    for i, t in enumerate(results[best_name]):
        agent = agent_cycle[i % 3]
        route_str = t["path_str"]
        outcome = "success" if t["success"] else "failure"
        alpha_str = f"({TUNINGS[best_name][0]},{TUNINGS[best_name][1]},{TUNINGS[best_name][2]})"
        notes = f"tuning={best_name} alpha={alpha_str}, score={t['total_score']}"
        print(f'python3 storage/coordination/route_tracker.py log --agent "{agent}" --task "hybrid-pathfinding" --route "{route_str}" --outcome "{outcome}" --notes "{notes}"')

    return report, results, best_name


if __name__ == "__main__":
    report, results, best_name = main()
