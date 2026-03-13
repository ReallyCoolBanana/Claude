#!/usr/bin/env python3
"""
P4 Hybrid Pathfinding — run006

Combines 3 signals for greedy best-first search:
  - Weight score: edge_weight / 10 (normalized 0-1)
  - Strength score: primary=1.0, supporting=0.75, related=0.5, tangential=0.25, unclassified=0.1
  - Tag coherence: Jaccard similarity between source and target node tags (|intersection|/|union|)

Combined score = w1*weight_score + w2*strength_score + w3*tag_coherence

Three weight configurations tested:
  Config A: (0.4, 0.3, 0.3)
  Config B: (0.5, 0.25, 0.25)
  Config C: (0.33, 0.33, 0.33)

20 scenarios: 10 SOPs, each pathfinding to 1 SCR + 1 SRC target.
"""

import json
import subprocess
import heapq
import time
from pathlib import Path
from datetime import datetime, timezone

POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"
OUTPUT_FILE = Path(__file__).parent / "run006_pathfinding_p4.json"

# --- Scoring constants ---
STRENGTH_SCORE = {
    "primary": 1.0,
    "supporting": 0.75,
    "related": 0.5,
    "tangential": 0.25,
    "unclassified": 0.1,
}

CONFIGS = {
    "config_A": (0.4, 0.3, 0.3),
    "config_B": (0.5, 0.25, 0.25),
    "config_C": (0.33, 0.33, 0.33),
}

# --- 20 scenarios: 10 SOPs, each to 1 SCR + 1 SRC ---
SCENARIOS = [
    {"id": "S01", "start": "SOP-011", "target": "SCR-0033", "type": "SCR", "desc": "Coordination SOP to hub-spoke script"},
    {"id": "S02", "start": "SOP-011", "target": "SRC-0001", "type": "SRC", "desc": "Coordination SOP to OpenAlex source"},
    {"id": "S03", "start": "SOP-012", "target": "SCR-0036", "type": "SCR", "desc": "Monitoring SOP to dashboard script"},
    {"id": "S04", "start": "SOP-012", "target": "SRC-0003", "type": "SRC", "desc": "Monitoring SOP to API source"},
    {"id": "S05", "start": "SOP-013", "target": "SCR-0009", "type": "SCR", "desc": "Infrastructure SOP to dedup script"},
    {"id": "S06", "start": "SOP-013", "target": "SRC-0005", "type": "SRC", "desc": "Infrastructure SOP to data source"},
    {"id": "S07", "start": "SOP-017", "target": "SCR-0057", "type": "SCR", "desc": "Data gathering to pointer build script"},
    {"id": "S08", "start": "SOP-017", "target": "SRC-0012", "type": "SRC", "desc": "Data gathering to wiki source"},
    {"id": "S09", "start": "SOP-020", "target": "SCR-0041", "type": "SCR", "desc": "Validation SOP to validator script"},
    {"id": "S10", "start": "SOP-020", "target": "SRC-0015", "type": "SRC", "desc": "Validation SOP to validation source"},
    {"id": "S11", "start": "SOP-023", "target": "SCR-0023", "type": "SCR", "desc": "Search SOP to search script"},
    {"id": "S12", "start": "SOP-023", "target": "SRC-0022", "type": "SRC", "desc": "Search SOP to search source"},
    {"id": "S13", "start": "SOP-027", "target": "SCR-0047", "type": "SCR", "desc": "Wikipedia SOP to enrichment script"},
    {"id": "S14", "start": "SOP-027", "target": "SRC-0030", "type": "SRC", "desc": "Wikipedia SOP to wiki data source"},
    {"id": "S15", "start": "SOP-028", "target": "SCR-0055", "type": "SCR", "desc": "Pointer network SOP to network script"},
    {"id": "S16", "start": "SOP-028", "target": "SRC-0040", "type": "SRC", "desc": "Pointer network SOP to pointer source"},
    {"id": "S17", "start": "SOP-029", "target": "SCR-0044", "type": "SCR", "desc": "Index SOP to index script"},
    {"id": "S18", "start": "SOP-029", "target": "SRC-0045", "type": "SRC", "desc": "Index SOP to index source"},
    {"id": "S19", "start": "SOP-G03", "target": "SCR-0033", "type": "SCR", "desc": "Hub-spoke SOP to coordination script"},
    {"id": "S20", "start": "SOP-G03", "target": "SRC-0048", "type": "SRC", "desc": "Hub-spoke SOP to hub source"},
]


def load_network():
    with open(POINTER_NETWORK) as f:
        return json.load(f)


def get_node_tags(pn, node_id):
    """Get tags for a node. Uses explicit tags, or infers from shared_tags reasons."""
    node = pn.get("nodes", {}).get(node_id, {})
    tags = set(node.get("tags", []))
    if not tags:
        for ptr in node.get("pointers", []):
            for reason in ptr.get("reasons", []):
                if reason.startswith("shared_tags:"):
                    for t in reason.split(":", 1)[1].split(","):
                        tags.add(t.strip())
    return tags


def jaccard_similarity(set_a, set_b):
    """Jaccard similarity: |intersection| / |union|, or 0 if both empty."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def compute_edge_score(pn, src_id, pointer, target_id, w1, w2, w3):
    """
    Combined score for traversing an edge:
      weight_score = edge_weight / 10  (normalized 0-1)
      strength_score = lookup from STRENGTH_SCORE map
      tag_coherence = Jaccard(source_node_tags, target_node_tags)
        where source = pointer neighbor, target = final destination
    """
    weight_score = min(pointer.get("weight", 0) / 10.0, 1.0)
    strength = pointer.get("strength", "unclassified")
    strength_score = STRENGTH_SCORE.get(strength, 0.1)

    neighbor_tags = get_node_tags(pn, pointer["to"])
    target_tags = get_node_tags(pn, target_id)
    tag_coh = jaccard_similarity(neighbor_tags, target_tags)

    combined = w1 * weight_score + w2 * strength_score + w3 * tag_coh
    return combined, weight_score, strength_score, tag_coh


def greedy_best_first(pn, start, target, w1, w2, w3, max_hops=8):
    """
    Greedy best-first search maximizing combined score.
    Uses a max-heap (via negated scores in min-heap).
    Returns (path, total_score, hop_details) or (None, 0, []).
    """
    if start not in pn.get("nodes", {}):
        return None, 0, []
    if target not in pn.get("nodes", {}):
        return None, 0, []
    if start == target:
        return [start], 0, []

    counter = 0
    # (neg_score, counter, hops, current, path, details)
    pq = [(0.0, counter, 0, start, [start], [])]
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

            combined, ws, ss, tc = compute_edge_score(pn, current, ptr, target, w1, w2, w3)
            new_path = path + [neighbor]
            new_details = details + [{
                "from": current,
                "to": neighbor,
                "edge_weight": ptr.get("weight", 0),
                "weight_score": round(ws, 4),
                "strength": ptr.get("strength", "unclassified"),
                "strength_score": round(ss, 4),
                "tag_coherence": round(tc, 4),
                "combined_score": round(combined, 4),
            }]
            counter += 1
            heapq.heappush(pq, (neg_score - combined, counter, hops + 1, neighbor, new_path, new_details))

    return None, 0, []


def log_route(route_str, outcome, hops, config_name, scenario_id):
    """Log route via route_tracker.py."""
    notes = f"config={config_name}, scenario={scenario_id}"
    cmd = [
        "python3", str(ROUTE_TRACKER), "log",
        "--agent", "p4-helper-1",
        "--task", "hybrid-pathfinding",
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception as e:
        return f"ERROR: {e}"


def run_all():
    print("=== P4 Hybrid Pathfinding (run006) ===")
    pn = load_network()
    print(f"Loaded {len(pn['nodes'])} nodes")

    # Validate scenarios
    valid_scenarios = []
    for s in SCENARIOS:
        if s["start"] not in pn["nodes"]:
            print(f"  SKIP {s['id']}: start {s['start']} not in network")
            continue
        if s["target"] not in pn["nodes"]:
            print(f"  SKIP {s['id']}: target {s['target']} not in network")
            continue
        valid_scenarios.append(s)

    print(f"Valid scenarios: {len(valid_scenarios)}/{len(SCENARIOS)}")
    print(f"Configs: {list(CONFIGS.keys())}")
    print(f"Total runs: {len(valid_scenarios) * len(CONFIGS)}")
    print()

    all_results = {}
    config_summaries = {}
    route_logs = []

    for config_name, (w1, w2, w3) in CONFIGS.items():
        print(f"\n--- {config_name} (w1={w1}, w2={w2}, w3={w3}) ---")
        config_results = []

        for scenario in valid_scenarios:
            path, total_score, details = greedy_best_first(
                pn, scenario["start"], scenario["target"], w1, w2, w3
            )
            success = path is not None
            hop_count = len(details)
            path_str = " -> ".join(path) if path else "NO_PATH"
            outcome = "success" if success else "failure"

            # Compute per-hop averages
            avg_weight_score = 0
            avg_strength_score = 0
            avg_tag_coherence = 0
            if details:
                avg_weight_score = sum(d["weight_score"] for d in details) / len(details)
                avg_strength_score = sum(d["strength_score"] for d in details) / len(details)
                avg_tag_coherence = sum(d["tag_coherence"] for d in details) / len(details)

            result = {
                "scenario_id": scenario["id"],
                "start": scenario["start"],
                "target": scenario["target"],
                "target_type": scenario["type"],
                "desc": scenario["desc"],
                "success": success,
                "path": path,
                "path_str": path_str,
                "hop_count": hop_count,
                "total_combined_score": round(total_score, 4),
                "avg_weight_score": round(avg_weight_score, 4),
                "avg_strength_score": round(avg_strength_score, 4),
                "avg_tag_coherence": round(avg_tag_coherence, 4),
                "hop_details": details,
            }
            config_results.append(result)

            status = "OK" if success else "FAIL"
            print(f"  [{status}] {scenario['id']} {scenario['start']}->{scenario['target']}: "
                  f"{path_str} ({hop_count} hops, score={round(total_score, 3)})")

            # Log route
            log_msg = log_route(path_str, outcome, hop_count, config_name, scenario["id"])
            route_logs.append({
                "config": config_name,
                "scenario": scenario["id"],
                "route": path_str,
                "outcome": outcome,
                "hops": hop_count,
                "log_msg": log_msg,
            })

        all_results[config_name] = config_results

        # Summarize config
        successes = sum(1 for r in config_results if r["success"])
        successful = [r for r in config_results if r["success"]]
        total = len(config_results)
        avg_hops = sum(r["hop_count"] for r in successful) / max(len(successful), 1)
        avg_score = sum(r["total_combined_score"] for r in successful) / max(len(successful), 1)
        avg_ws = sum(r["avg_weight_score"] for r in successful) / max(len(successful), 1)
        avg_ss = sum(r["avg_strength_score"] for r in successful) / max(len(successful), 1)
        avg_tc = sum(r["avg_tag_coherence"] for r in successful) / max(len(successful), 1)

        config_summaries[config_name] = {
            "weights": {"w1_weight": w1, "w2_strength": w2, "w3_tag_coherence": w3},
            "successes": successes,
            "failures": total - successes,
            "total": total,
            "success_rate_pct": round(successes / max(total, 1) * 100, 1),
            "avg_hops": round(avg_hops, 2),
            "avg_combined_score": round(avg_score, 4),
            "avg_weight_score": round(avg_ws, 4),
            "avg_strength_score": round(avg_ss, 4),
            "avg_tag_coherence": round(avg_tc, 4),
        }

    # --- Determine best config ---
    # Rank by: success_rate first, then avg_combined_score, then lowest avg_hops
    ranked = sorted(
        config_summaries.items(),
        key=lambda x: (
            x[1]["success_rate_pct"],
            x[1]["avg_combined_score"],
            -x[1]["avg_hops"],
        ),
        reverse=True,
    )

    best_config = ranked[0][0]

    print("\n" + "=" * 80)
    print("CONFIG COMPARISON")
    print("=" * 80)
    for rank, (name, summary) in enumerate(ranked, 1):
        marker = " <<< BEST" if name == best_config else ""
        print(f"\n  #{rank} {name} (w={summary['weights']}){marker}")
        print(f"      Success: {summary['successes']}/{summary['total']} ({summary['success_rate_pct']}%)")
        print(f"      Avg hops: {summary['avg_hops']}")
        print(f"      Avg combined score: {summary['avg_combined_score']}")
        print(f"      Avg weight score: {summary['avg_weight_score']}")
        print(f"      Avg strength score: {summary['avg_strength_score']}")
        print(f"      Avg tag coherence: {summary['avg_tag_coherence']}")

    # --- Cross-config comparison: per-scenario ---
    print("\n" + "=" * 80)
    print("PER-SCENARIO COMPARISON (best config highlighted)")
    print("=" * 80)
    scenario_comparison = []
    for scenario in valid_scenarios:
        row = {"scenario_id": scenario["id"], "start": scenario["start"], "target": scenario["target"]}
        best_scenario_score = -1
        best_scenario_config = None
        for config_name in CONFIGS:
            for r in all_results[config_name]:
                if r["scenario_id"] == scenario["id"]:
                    row[config_name] = {
                        "success": r["success"],
                        "hops": r["hop_count"],
                        "score": r["total_combined_score"],
                        "path": r["path_str"],
                    }
                    if r["success"] and r["total_combined_score"] > best_scenario_score:
                        best_scenario_score = r["total_combined_score"]
                        best_scenario_config = config_name
        row["best_config_for_scenario"] = best_scenario_config
        scenario_comparison.append(row)
        sid = scenario["id"]
        print(f"  {sid}: best={best_scenario_config or 'NONE'}", end="")
        for cn in CONFIGS:
            if cn in row:
                s = row[cn]
                status = "OK" if s["success"] else "FAIL"
                print(f"  | {cn}=[{status} h={s['hops']} s={s['score']:.3f}]", end="")
        print()

    # --- Build final report ---
    report = {
        "meta": {
            "agent": "p4-helper-1",
            "experiment": "hybrid-pathfinding-run006",
            "date": datetime.now(timezone.utc).isoformat(),
            "description": "P4 hybrid pathfinding combining weight, strength, and tag coherence signals",
        },
        "scoring": {
            "formula": "combined = w1 * (edge_weight/10) + w2 * strength_score + w3 * jaccard(source_tags, target_tags)",
            "weight_score": "edge_weight / 10, clamped to [0, 1]",
            "strength_scores": STRENGTH_SCORE,
            "tag_coherence": "Jaccard similarity = |intersection(source_tags, target_tags)| / |union(source_tags, target_tags)|",
        },
        "configs_tested": {k: {"w1_weight": v[0], "w2_strength": v[1], "w3_tag_coherence": v[2]} for k, v in CONFIGS.items()},
        "scenarios": SCENARIOS,
        "config_summaries": config_summaries,
        "config_ranking": [
            {"rank": i + 1, "config": name, "summary": summary}
            for i, (name, summary) in enumerate(ranked)
        ],
        "best_config": {
            "name": best_config,
            "weights": config_summaries[best_config]["weights"],
            "success_rate_pct": config_summaries[best_config]["success_rate_pct"],
            "avg_combined_score": config_summaries[best_config]["avg_combined_score"],
            "avg_hops": config_summaries[best_config]["avg_hops"],
        },
        "scenario_comparison": scenario_comparison,
        "per_config_results": all_results,
        "route_logs": route_logs,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to: {OUTPUT_FILE}")

    return report


if __name__ == "__main__":
    run_all()
