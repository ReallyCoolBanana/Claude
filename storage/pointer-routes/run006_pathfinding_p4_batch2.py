#!/usr/bin/env python3
"""
P4 Helper Agent 2 — Hybrid Pathfinding Batch 2
Scoring: hybrid_score = w1*weight_norm + w2*strength_score + w3*tag_coherence
  - weight_norm = edge_weight / 10
  - strength_score: primary=1.0, supporting=0.75, related=0.5, tangential=0.25, unclassified=0.1
  - tag_coherence: Jaccard similarity of source/target node tags

3 configs: A=(0.4,0.3,0.3), B=(0.5,0.25,0.25), C=(0.33,0.33,0.33)
10 scenarios from SOP-012..016 (2 each: 1 SCR target + 1 SRC target)
Greedy best-first, max 10 hops.
"""

import json, heapq, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone

POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"
OUTPUT_FILE = Path(__file__).parent / "run006_pathfinding_p4_batch2.json"

STRENGTH_SCORE = {
    "primary": 1.0,
    "supporting": 0.75,
    "related": 0.5,
    "tangential": 0.25,
    "unclassified": 0.1,
}

CONFIGS = {
    "A": (0.4, 0.3, 0.3),
    "B": (0.5, 0.25, 0.25),
    "C": (0.33, 0.33, 0.33),
}

# 10 scenarios: 2 per SOP (1 SCR + 1 SRC target)
# Pick non-trivial targets (2+ hops preferred for SRC since none are direct)
SCENARIOS = [
    {"id": "S01", "sop": "SOP-012", "start": "SOP-012", "target": "SCR-0036", "target_type": "SCR", "desc": "SOP-012 to coordination script SCR-0036 (2-hop)"},
    {"id": "S02", "sop": "SOP-012", "start": "SOP-012", "target": "SRC-0051", "target_type": "SRC", "desc": "SOP-012 to data source SRC-0051 (multi-hop)"},
    {"id": "S03", "sop": "SOP-013", "start": "SOP-013", "target": "SCR-0054", "target_type": "SCR", "desc": "SOP-013 to automation script SCR-0054 (2-hop)"},
    {"id": "S04", "sop": "SOP-013", "start": "SOP-013", "target": "SRC-0033", "target_type": "SRC", "desc": "SOP-013 to API source SRC-0033 (multi-hop)"},
    {"id": "S05", "sop": "SOP-014", "start": "SOP-014", "target": "SCR-0003", "target_type": "SCR", "desc": "SOP-014 to validation script SCR-0003 (2-hop)"},
    {"id": "S06", "sop": "SOP-014", "start": "SOP-014", "target": "SRC-0050", "target_type": "SRC", "desc": "SOP-014 to data source SRC-0050 (multi-hop)"},
    {"id": "S07", "sop": "SOP-015", "start": "SOP-015", "target": "SCR-0037", "target_type": "SCR", "desc": "SOP-015 to stress-test script SCR-0037 (2-hop)"},
    {"id": "S08", "sop": "SOP-015", "start": "SOP-015", "target": "SRC-0001", "target_type": "SRC", "desc": "SOP-015 to OpenAlex source SRC-0001 (multi-hop)"},
    {"id": "S09", "sop": "SOP-016", "start": "SOP-016", "target": "SCR-0009", "target_type": "SCR", "desc": "SOP-016 to dedup script SCR-0009 (2-hop)"},
    {"id": "S10", "sop": "SOP-016", "start": "SOP-016", "target": "SRC-0034", "target_type": "SRC", "desc": "SOP-016 to source SRC-0034 (multi-hop)"},
]

MAX_HOPS = 10


def load_network():
    with open(POINTER_NETWORK) as f:
        return json.load(f)


def get_node_tags(pn, node_id):
    """Get tags for a node from explicit tags or inferred from shared_tags reasons."""
    node = pn.get("nodes", {}).get(node_id, {})
    tags = set(node.get("tags", []))
    if not tags:
        for ptr in node.get("pointers", []):
            for reason in ptr.get("reasons", []):
                if reason.startswith("shared_tags:"):
                    for t in reason.split(":", 1)[1].split(","):
                        tags.add(t.strip())
    return tags


def jaccard(set_a, set_b):
    if not set_a and not set_b:
        return 0.5  # neutral
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def hybrid_score(pn, src_id, pointer, target_id, w1, w2, w3):
    """
    hybrid = w1*weight_norm + w2*strength_score + w3*tag_coherence
    weight_norm = edge_weight / 10
    strength_score from lookup
    tag_coherence = Jaccard(neighbor_tags, target_tags)
    """
    weight_norm = pointer.get("weight", 0) / 10.0
    strength = STRENGTH_SCORE.get(pointer.get("strength", "unclassified"), 0.1)

    neighbor_tags = get_node_tags(pn, pointer["to"])
    target_tags = get_node_tags(pn, target_id)
    tag_coh = jaccard(neighbor_tags, target_tags)

    score = w1 * weight_norm + w2 * strength + w3 * tag_coh
    return score, weight_norm, strength, tag_coh


def greedy_best_first(pn, start, target, w1, w2, w3, max_hops=MAX_HOPS):
    """Greedy best-first search maximizing hybrid score per hop."""
    if start not in pn.get("nodes", {}):
        return None, 0, []
    if start == target:
        return [start], 0, []

    # Priority queue: (-accumulated_score, counter, hop_count, current, path, details)
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

            score, wn, ss, tc = hybrid_score(pn, current, ptr, target, w1, w2, w3)
            new_path = path + [neighbor]
            new_details = details + [{
                "from": current,
                "to": neighbor,
                "weight": ptr.get("weight", 0),
                "weight_norm": round(wn, 3),
                "strength": ptr.get("strength", "unclassified"),
                "strength_score": round(ss, 3),
                "tag_coherence": round(tc, 3),
                "hybrid_score": round(score, 4),
            }]
            counter += 1
            heapq.heappush(pq, (neg_score - score, counter, hops + 1, neighbor, new_path, new_details))

    return None, 0, []


def log_route(route_str, outcome, hops, config_name, total_score, scenario_id):
    """Log route via route_tracker.py"""
    notes = f"config={config_name}, scenario={scenario_id}, total_hybrid_score={total_score}"
    cmd = [
        sys.executable, str(ROUTE_TRACKER), "log",
        "--agent", "p4-helper-2",
        "--task", "hybrid-pathfinding",
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def main():
    print("=== P4 Helper Agent 2: Hybrid Pathfinding Batch 2 ===")
    print("Loading pointer network...")
    pn = load_network()
    print(f"Loaded {len(pn['nodes'])} nodes")

    all_results = {}
    route_log_outputs = []

    for config_name, (w1, w2, w3) in CONFIGS.items():
        print(f"\n{'='*60}")
        print(f"CONFIG {config_name}: w1={w1}, w2={w2}, w3={w3}")
        print(f"{'='*60}")

        config_results = []
        for scenario in SCENARIOS:
            path, total_score, details = greedy_best_first(
                pn, scenario["start"], scenario["target"], w1, w2, w3
            )
            success = path is not None
            hop_count = len(details) if details else 0
            path_str = " -> ".join(path) if path else "NO_PATH"

            # Compute aggregates
            total_weight = sum(d["weight"] for d in details) if details else 0
            avg_tag_coh = (sum(d["tag_coherence"] for d in details) / hop_count) if hop_count > 0 else 0
            avg_hybrid = (sum(d["hybrid_score"] for d in details) / hop_count) if hop_count > 0 else 0
            strengths_used = [d["strength"] for d in details] if details else []

            status = "OK" if success else "FAIL"
            print(f"  [{status}] {scenario['id']}: {scenario['start']} -> {scenario['target']} "
                  f"| {path_str} | {hop_count} hops, score={round(total_score, 3)}")

            result = {
                "scenario_id": scenario["id"],
                "sop": scenario["sop"],
                "start": scenario["start"],
                "target": scenario["target"],
                "target_type": scenario["target_type"],
                "desc": scenario["desc"],
                "success": success,
                "path": path,
                "path_str": path_str,
                "hop_count": hop_count,
                "total_hybrid_score": round(total_score, 4),
                "total_weight": total_weight,
                "avg_tag_coherence": round(avg_tag_coh, 4),
                "avg_hybrid_per_hop": round(avg_hybrid, 4),
                "strengths_used": strengths_used,
                "hop_details": details,
            }
            config_results.append(result)

            # Log route
            outcome = "success" if success else "failure"
            log_output = log_route(path_str, outcome, hop_count, config_name, round(total_score, 4), scenario["id"])
            route_log_outputs.append({
                "config": config_name,
                "scenario": scenario["id"],
                "log_output": log_output,
            })

        all_results[config_name] = config_results

    # Compute config summaries
    print(f"\n{'='*60}")
    print("CONFIG COMPARISON")
    print(f"{'='*60}")

    config_summaries = {}
    for config_name, results in all_results.items():
        successes = sum(1 for r in results if r["success"])
        successful = [r for r in results if r["success"]]
        total = len(results)
        avg_hops = (sum(r["hop_count"] for r in successful) / len(successful)) if successful else 999
        avg_score = (sum(r["total_hybrid_score"] for r in successful) / len(successful)) if successful else 0
        avg_tag = (sum(r["avg_tag_coherence"] for r in successful) / len(successful)) if successful else 0
        avg_weight = (sum(r["total_weight"] for r in successful) / len(successful)) if successful else 0

        # Composite: success_rate*40 + efficiency*20 + avg_score*20 + tag_coherence*20
        success_pct = successes / total
        efficiency = (1 / max(avg_hops, 1)) * 3  # normalized
        composite = success_pct * 40 + efficiency * 20 + (avg_score / max(avg_score, 1)) * 20 + (avg_tag) * 20

        summary = {
            "weights": list(CONFIGS[config_name]),
            "successes": successes,
            "total": total,
            "success_rate": round(success_pct * 100, 1),
            "avg_hops": round(avg_hops, 2),
            "avg_total_hybrid_score": round(avg_score, 4),
            "avg_tag_coherence": round(avg_tag, 4),
            "avg_weight": round(avg_weight, 2),
            "composite_score": round(composite, 2),
        }
        config_summaries[config_name] = summary

        w1, w2, w3 = CONFIGS[config_name]
        print(f"\n  Config {config_name} (w1={w1}, w2={w2}, w3={w3}):")
        print(f"    Success: {successes}/{total} ({summary['success_rate']}%)")
        print(f"    Avg hops: {summary['avg_hops']}")
        print(f"    Avg hybrid score: {summary['avg_total_hybrid_score']}")
        print(f"    Avg tag coherence: {summary['avg_tag_coherence']}")
        print(f"    Composite: {summary['composite_score']}")

    # Rank configs
    ranked = sorted(config_summaries.items(), key=lambda x: x[1]["composite_score"], reverse=True)
    best_config = ranked[0][0]
    print(f"\n  BEST CONFIG: {best_config} (composite={ranked[0][1]['composite_score']})")

    # Build report
    report = {
        "agent": "p4-helper-2",
        "experiment": "hybrid-pathfinding-batch2",
        "date": "2026-03-13",
        "description": "Hybrid pathfinding on pointer network using 3 weight configs across 10 SOP-based scenarios (SOP-012..016, 1 SCR + 1 SRC each)",
        "scoring_formula": "hybrid_score = w1*weight_norm + w2*strength_score + w3*tag_coherence",
        "scoring_components": {
            "weight_norm": "edge_weight / 10",
            "strength_score": STRENGTH_SCORE,
            "tag_coherence": "Jaccard similarity of source/target node tags",
        },
        "configs": {k: {"w1": v[0], "w2": v[1], "w3": v[2]} for k, v in CONFIGS.items()},
        "scenarios": SCENARIOS,
        "max_hops": MAX_HOPS,
        "algorithm": "greedy best-first (max-heap on cumulative hybrid score)",
        "config_summaries": config_summaries,
        "config_ranking": [
            {"rank": i + 1, "config": name, "composite_score": s["composite_score"]}
            for i, (name, s) in enumerate(ranked)
        ],
        "best_config": {
            "name": best_config,
            "weights": list(CONFIGS[best_config]),
            "composite_score": config_summaries[best_config]["composite_score"],
        },
        "all_results": all_results,
        "route_log_outputs": route_log_outputs,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to: {OUTPUT_FILE}")

    return report


if __name__ == "__main__":
    main()
