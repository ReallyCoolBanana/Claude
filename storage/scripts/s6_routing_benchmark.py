#!/usr/bin/env python3
"""
Stress Team S6 — Routing Strategy Benchmark
Compares weight-based, tag-based, and hybrid routing across 60 journeys.
"""

import json
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"
ROUTE_TRACKER = Path(__file__).parent.parent / "coordination" / "route_tracker.py"
REPORT_PATH = Path(__file__).parent.parent / "pointer-routes" / "stress_s6_report.json"

# Load pointer network
with open(POINTER_NETWORK) as f:
    PN = json.load(f)

NODES = PN["nodes"]
TAG_CLUSTERS = PN.get("tag_clusters", {})

# Build node -> tags mapping from tag_clusters
NODE_TAGS = {}
for tag, cluster in TAG_CLUSTERS.items():
    for node_id in cluster.get("nodes", []):
        if node_id not in NODE_TAGS:
            NODE_TAGS[node_id] = set()
        NODE_TAGS[node_id].add(tag)

# Also incorporate explicit node tags
for node_id, node_data in NODES.items():
    if "tags" in node_data:
        if node_id not in NODE_TAGS:
            NODE_TAGS[node_id] = set()
        NODE_TAGS[node_id].update(node_data["tags"])


def get_tags(node_id):
    """Get tags for a node."""
    return NODE_TAGS.get(node_id, set())


def jaccard(set_a, set_b):
    """Jaccard similarity between two sets."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def get_pointers(node_id):
    """Get outgoing pointers for a node."""
    node = NODES.get(node_id, {})
    return node.get("pointers", [])


def route_weight(start_node, target_type, max_hops=15):
    """Weight routing: always follow highest-weight pointer."""
    path = [start_node]
    visited = {start_node}
    current = start_node

    for _ in range(max_hops):
        if current.startswith(target_type):
            return path, True
        pointers = get_pointers(current)
        if not pointers:
            break
        # Filter out visited nodes
        candidates = [p for p in pointers if p["to"] not in visited]
        if not candidates:
            break
        # Pick highest weight
        best = max(candidates, key=lambda p: p.get("weight", 0))
        current = best["to"]
        path.append(current)
        visited.add(current)

    # Check if final node matches target
    reached = path[-1].startswith(target_type) if path else False
    return path, reached


def route_tag(start_node, target_type, max_hops=15):
    """Tag routing: follow pointer to node with highest tag overlap (Jaccard) with start node."""
    path = [start_node]
    visited = {start_node}
    current = start_node
    start_tags = get_tags(start_node)

    for _ in range(max_hops):
        if current.startswith(target_type):
            return path, True
        pointers = get_pointers(current)
        if not pointers:
            break
        candidates = [p for p in pointers if p["to"] not in visited]
        if not candidates:
            break
        # Pick by highest Jaccard similarity with START node
        best = max(candidates, key=lambda p: jaccard(start_tags, get_tags(p["to"])))
        current = best["to"]
        path.append(current)
        visited.add(current)

    reached = path[-1].startswith(target_type) if path else False
    return path, reached


def route_hybrid(start_node, target_type, alpha=1.0, max_hops=15):
    """Hybrid routing: score = weight * (1 + alpha * tag_overlap)."""
    path = [start_node]
    visited = {start_node}
    current = start_node
    start_tags = get_tags(start_node)

    for _ in range(max_hops):
        if current.startswith(target_type):
            return path, True
        pointers = get_pointers(current)
        if not pointers:
            break
        candidates = [p for p in pointers if p["to"] not in visited]
        if not candidates:
            break
        # Hybrid score
        def score(p):
            w = p.get("weight", 0)
            tag_sim = jaccard(start_tags, get_tags(p["to"]))
            return w * (1 + alpha * tag_sim)
        best = max(candidates, key=score)
        current = best["to"]
        path.append(current)
        visited.add(current)

    reached = path[-1].startswith(target_type) if path else False
    return path, reached


def compute_path_coherence(path):
    """Average Jaccard similarity between consecutive nodes."""
    if len(path) < 2:
        return 0.0
    similarities = []
    for i in range(len(path) - 1):
        similarities.append(jaccard(get_tags(path[i]), get_tags(path[i + 1])))
    return sum(similarities) / len(similarities)


def compute_total_weight(path):
    """Sum of edge weights along path."""
    total = 0
    for i in range(len(path) - 1):
        src, dst = path[i], path[i + 1]
        for p in get_pointers(src):
            if p["to"] == dst:
                total += p.get("weight", 0)
                break
    return total


def run_benchmark():
    """Run all 60 journeys across 3 strategies."""
    # Select 10 SOPs
    all_sops = sorted([k for k in NODES if k.startswith("SOP-")])
    sops = all_sops[:10]
    print(f"Using SOPs: {sops}")

    target_types = ["SCR-", "SRC-"]  # SCR = scripts, SRC = sources
    strategies = {
        "weight": route_weight,
        "tag": route_tag,
        "hybrid": lambda start, target, **kw: route_hybrid(start, target, alpha=1.0, **kw),
    }

    all_journeys = []
    agent_counter = 0

    for strategy_name, strategy_fn in strategies.items():
        for sop in sops:
            for target_type in target_types:
                agent_num = (agent_counter % 6) + 1
                agent_id = f"s6-{agent_num}"
                agent_counter += 1

                path, reached = strategy_fn(sop, target_type)
                hops = len(path) - 1
                coherence = round(compute_path_coherence(path), 4)
                total_weight = compute_total_weight(path)

                target_label = "SCR" if target_type == "SCR-" else "SRC"
                journey = {
                    "strategy": strategy_name,
                    "start_sop": sop,
                    "target_type": target_label,
                    "path": path,
                    "reached": reached,
                    "hops": hops,
                    "coherence": coherence,
                    "total_weight": total_weight,
                    "agent_id": agent_id,
                }
                all_journeys.append(journey)

                outcome = "success" if reached else "failure"
                route_str = " -> ".join(path)
                task_str = f"{strategy_name}-routing"
                notes_str = f"strategy={strategy_name}, coherence={coherence}, target={target_label}, reached={reached}"

                print(f"  [{strategy_name}] {sop} -> {target_label}: "
                      f"{'OK' if reached else 'FAIL'} in {hops} hops, "
                      f"coherence={coherence}, weight={total_weight}")

                # Log via route_tracker
                cmd = [
                    sys.executable, str(ROUTE_TRACKER), "log",
                    "--agent", agent_id,
                    "--task", task_str,
                    "--route", route_str,
                    "--outcome", outcome,
                    "--notes", notes_str,
                ]
                subprocess.run(cmd, capture_output=True, text=True)

    return all_journeys


def analyze_results(journeys):
    """Produce the final report."""
    strategies = ["weight", "tag", "hybrid"]

    # --- Strategy comparison ---
    strategy_comparison = {}
    for s in strategies:
        s_journeys = [j for j in journeys if j["strategy"] == s]
        successes = sum(1 for j in s_journeys if j["reached"])
        total = len(s_journeys)
        strategy_comparison[s] = {
            "total_journeys": total,
            "successes": successes,
            "success_rate": round(successes / max(total, 1) * 100, 1),
            "avg_hops": round(sum(j["hops"] for j in s_journeys) / max(total, 1), 2),
            "avg_coherence": round(sum(j["coherence"] for j in s_journeys) / max(total, 1), 4),
            "avg_weight": round(sum(j["total_weight"] for j in s_journeys) / max(total, 1), 2),
            "avg_hops_on_success": round(
                sum(j["hops"] for j in s_journeys if j["reached"]) / max(successes, 1), 2
            ),
        }

    # --- Per-SOP results ---
    all_sops = sorted(set(j["start_sop"] for j in journeys))
    per_sop_results = {}
    for sop in all_sops:
        sop_data = {}
        best_strat = None
        best_score = -1
        for s in strategies:
            sj = [j for j in journeys if j["strategy"] == s and j["start_sop"] == sop]
            successes = sum(1 for j in sj if j["reached"])
            avg_coh = round(sum(j["coherence"] for j in sj) / max(len(sj), 1), 4)
            avg_hops = round(sum(j["hops"] for j in sj) / max(len(sj), 1), 2)
            # Score: success_count * 10 + coherence * 5 - hops
            score = successes * 10 + avg_coh * 5 - avg_hops
            sop_data[s] = {
                "successes": successes,
                "of": len(sj),
                "avg_coherence": avg_coh,
                "avg_hops": avg_hops,
                "composite_score": round(score, 2),
            }
            if score > best_score:
                best_score = score
                best_strat = s
        sop_data["best_strategy"] = best_strat
        per_sop_results[sop] = sop_data

    # --- Hybrid tuning ---
    alphas = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0]
    all_sops_list = sorted(set(j["start_sop"] for j in journeys))
    target_types = ["SCR-", "SRC-"]
    hybrid_tuning = {}
    for alpha in alphas:
        successes = 0
        total = 0
        total_coherence = 0
        total_hops = 0
        total_weight = 0
        for sop in all_sops_list:
            for target_type in target_types:
                path, reached = route_hybrid(sop, target_type, alpha=alpha)
                total += 1
                if reached:
                    successes += 1
                total_coherence += compute_path_coherence(path)
                total_hops += len(path) - 1
                total_weight += compute_total_weight(path)
        hybrid_tuning[f"alpha={alpha}"] = {
            "success_rate": round(successes / max(total, 1) * 100, 1),
            "avg_hops": round(total_hops / max(total, 1), 2),
            "avg_coherence": round(total_coherence / max(total, 1), 4),
            "avg_weight": round(total_weight / max(total, 1), 2),
        }

    # --- Determine best strategy ---
    # Rank by: success_rate (primary), coherence (secondary), fewer hops (tertiary)
    ranked = sorted(
        strategies,
        key=lambda s: (
            strategy_comparison[s]["success_rate"],
            strategy_comparison[s]["avg_coherence"],
            -strategy_comparison[s]["avg_hops"],
        ),
        reverse=True,
    )
    best = ranked[0]

    # Count per-SOP wins
    sop_wins = defaultdict(int)
    for sop, data in per_sop_results.items():
        sop_wins[data["best_strategy"]] += 1

    justification_parts = [
        f"{best} routing achieved {strategy_comparison[best]['success_rate']}% success rate",
        f"avg coherence {strategy_comparison[best]['avg_coherence']}",
        f"avg hops {strategy_comparison[best]['avg_hops']}",
        f"won {sop_wins.get(best, 0)}/{len(all_sops)} per-SOP comparisons",
    ]

    report = {
        "benchmark": "Stress Team S6 - Routing Strategy Comparison",
        "date": "2026-03-12",
        "methodology": {
            "strategies": ["weight", "tag", "hybrid"],
            "journeys_per_strategy": 20,
            "total_journeys": 60,
            "sops_used": all_sops_list,
            "targets": ["SCR (scripts)", "SRC (sources)"],
            "tag_source": "tag_clusters from pointer-network.json",
            "hybrid_formula": "score = weight * (1 + alpha * jaccard_similarity)",
            "max_hops": 15,
        },
        "strategy_comparison": strategy_comparison,
        "best_strategy": {
            "name": best,
            "justification": "; ".join(justification_parts),
            "ranking": ranked,
            "per_sop_wins": dict(sop_wins),
        },
        "per_sop_results": per_sop_results,
        "hybrid_tuning": {
            "description": "Effect of varying alpha in hybrid formula: score = weight * (1 + alpha * jaccard)",
            "alpha_0_equals_pure_weight": True,
            "results": hybrid_tuning,
            "best_alpha": min(
                hybrid_tuning.items(),
                key=lambda x: (
                    -x[1]["success_rate"],
                    -x[1]["avg_coherence"],
                    x[1]["avg_hops"],
                ),
            )[0],
        },
        "all_journeys": [
            {
                "strategy": j["strategy"],
                "start": j["start_sop"],
                "target": j["target_type"],
                "path": " -> ".join(j["path"]),
                "reached": j["reached"],
                "hops": j["hops"],
                "coherence": j["coherence"],
                "total_weight": j["total_weight"],
            }
            for j in journeys
        ],
    }

    return report


def main():
    print("=" * 70)
    print("Stress Team S6 — Routing Strategy Benchmark")
    print("=" * 70)

    print("\nRunning 60 journeys (3 strategies x 10 SOPs x 2 targets)...\n")
    journeys = run_benchmark()

    print("\n" + "=" * 70)
    print("Analyzing results...")
    report = analyze_results(journeys)

    # Write report
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport written to: {REPORT_PATH}")

    # Print summary
    sc = report["strategy_comparison"]
    print("\n--- Strategy Comparison ---")
    for s in ["weight", "tag", "hybrid"]:
        d = sc[s]
        print(f"  {s:8s}: success={d['success_rate']}%, "
              f"avg_hops={d['avg_hops']}, "
              f"coherence={d['avg_coherence']}, "
              f"weight={d['avg_weight']}")

    best = report["best_strategy"]
    print(f"\nBest strategy: {best['name']}")
    print(f"  {best['justification']}")

    ht = report["hybrid_tuning"]
    print(f"\nBest hybrid alpha: {ht['best_alpha']}")
    for alpha_key, data in ht["results"].items():
        print(f"  {alpha_key}: success={data['success_rate']}%, "
              f"coherence={data['avg_coherence']}, hops={data['avg_hops']}")


if __name__ == "__main__":
    main()
