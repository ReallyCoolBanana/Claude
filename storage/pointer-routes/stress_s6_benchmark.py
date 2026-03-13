#!/usr/bin/env python3
"""
Stress Team S6 — Routing Strategy Benchmark

Compares 3 routing strategies across 60 journeys (10 SOPs x 2 target types x 3 strategies):
  1. Weight routing: follow highest-weight pointer
  2. Tag routing: follow pointer to node with highest Jaccard tag overlap with STARTING node
  3. Hybrid routing: score = weight * (1 + tag_overlap)

Each journey starts from an SOP and seeks either an SCR or SRC node.
"""

import json
import subprocess
import sys
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).parent.parent
POINTER_NETWORK = BASE / "pointer-network.json"
ROUTE_TRACKER = BASE / "coordination" / "route_tracker.py"
REPORT_OUT = BASE / "pointer-routes" / "stress_s6_report.json"

MAX_HOPS = 15  # prevent infinite loops


def load_network():
    with open(POINTER_NETWORK) as f:
        return json.load(f)


def extract_node_tags(pn):
    """Build tag sets for all nodes. SOPs have explicit tags; others inferred from shared_tags reasons."""
    node_tags = {}
    nodes = pn["nodes"]

    # First pass: explicit tags
    for nid, ndata in nodes.items():
        if "tags" in ndata and ndata["tags"]:
            node_tags[nid] = set(ndata["tags"])
        else:
            node_tags[nid] = set()

    # Second pass: infer tags from shared_tags reasons on incoming pointers
    for nid, ndata in nodes.items():
        for ptr in ndata.get("pointers", []):
            target = ptr["to"]
            for reason in ptr.get("reasons", []):
                if reason.startswith("shared_tags:"):
                    tags = reason.split(":", 1)[1].split(",")
                    # Both source and target share these tags
                    node_tags.setdefault(nid, set()).update(tags)
                    node_tags.setdefault(target, set()).update(tags)

    return node_tags


def jaccard(set_a, set_b):
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def route_weight(pn, start, target_prefix, node_tags, max_hops=MAX_HOPS):
    """Weight routing: always follow highest-weight pointer."""
    path = [start]
    visited = {start}
    current = start

    for _ in range(max_hops):
        node = pn["nodes"].get(current, {})
        pointers = node.get("pointers", [])
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

        if current.startswith(target_prefix):
            return path, True

    return path, False


def route_tag(pn, start, target_prefix, node_tags, max_hops=MAX_HOPS):
    """Tag routing: follow pointer to node with highest Jaccard similarity to starting node's tags."""
    start_tags = node_tags.get(start, set())
    path = [start]
    visited = {start}
    current = start

    for _ in range(max_hops):
        node = pn["nodes"].get(current, {})
        pointers = node.get("pointers", [])
        if not pointers:
            break

        candidates = [p for p in pointers if p["to"] not in visited]
        if not candidates:
            break

        # Pick highest tag overlap with START node
        best = max(candidates, key=lambda p: jaccard(start_tags, node_tags.get(p["to"], set())))
        current = best["to"]
        path.append(current)
        visited.add(current)

        if current.startswith(target_prefix):
            return path, True

    return path, False


def route_hybrid(pn, start, target_prefix, node_tags, alpha=1.0, max_hops=MAX_HOPS):
    """Hybrid routing: score = weight * (1 + alpha * tag_overlap)."""
    start_tags = node_tags.get(start, set())
    path = [start]
    visited = {start}
    current = start

    for _ in range(max_hops):
        node = pn["nodes"].get(current, {})
        pointers = node.get("pointers", [])
        if not pointers:
            break

        candidates = [p for p in pointers if p["to"] not in visited]
        if not candidates:
            break

        def score(p):
            w = p.get("weight", 0)
            j = jaccard(start_tags, node_tags.get(p["to"], set()))
            return w * (1 + alpha * j)

        best = max(candidates, key=score)
        current = best["to"]
        path.append(current)
        visited.add(current)

        if current.startswith(target_prefix):
            return path, True

    return path, False


def compute_path_coherence(path, node_tags):
    """Average Jaccard similarity between consecutive nodes in path."""
    if len(path) < 2:
        return 0.0
    similarities = []
    for i in range(len(path) - 1):
        similarities.append(jaccard(node_tags.get(path[i], set()), node_tags.get(path[i+1], set())))
    return sum(similarities) / len(similarities)


def compute_total_weight(pn, path):
    """Sum weights along the path."""
    total = 0
    for i in range(len(path) - 1):
        node = pn["nodes"].get(path[i], {})
        for ptr in node.get("pointers", []):
            if ptr["to"] == path[i+1]:
                total += ptr.get("weight", 0)
                break
    return total


def run_benchmark():
    pn = load_network()
    node_tags = extract_node_tags(pn)

    # Select 10 SOPs
    sop_nodes = sorted([k for k in pn["nodes"] if k.startswith("SOP-")])[:10]
    target_types = [("SCR", "SCR-"), ("SRC", "SRC-")]
    strategies = {
        "weight": lambda pn, start, tp, nt: route_weight(pn, start, tp, nt),
        "tag": lambda pn, start, tp, nt: route_tag(pn, start, tp, nt),
        "hybrid": lambda pn, start, tp, nt: route_hybrid(pn, start, tp, nt),
    }

    all_journeys = []
    journey_id = 0

    for sop in sop_nodes:
        for target_name, target_prefix in target_types:
            for strat_name, strat_fn in strategies.items():
                path, reached = strat_fn(pn, sop, target_prefix, node_tags)
                coherence = compute_path_coherence(path, node_tags)
                total_weight = compute_total_weight(pn, path)
                hops = len(path) - 1

                journey = {
                    "id": journey_id,
                    "sop": sop,
                    "target_type": target_name,
                    "strategy": strat_name,
                    "path": path,
                    "reached": reached,
                    "hops": hops,
                    "coherence": round(coherence, 4),
                    "total_weight": total_weight,
                }
                all_journeys.append(journey)
                journey_id += 1

    # Also run hybrid with different alpha values for tuning analysis
    hybrid_tuning = {}
    for alpha in [0.5, 1.0, 2.0, 3.0, 5.0]:
        results = []
        for sop in sop_nodes:
            for target_name, target_prefix in target_types:
                path, reached = route_hybrid(pn, sop, target_prefix, node_tags, alpha=alpha)
                coherence = compute_path_coherence(path, node_tags)
                total_weight = compute_total_weight(pn, path)
                results.append({
                    "reached": reached,
                    "hops": len(path) - 1,
                    "coherence": round(coherence, 4),
                    "total_weight": total_weight,
                })
        successes = sum(1 for r in results if r["reached"])
        hybrid_tuning[f"alpha_{alpha}"] = {
            "alpha": alpha,
            "success_rate": round(successes / len(results) * 100, 1),
            "avg_hops": round(sum(r["hops"] for r in results) / len(results), 2),
            "avg_coherence": round(sum(r["coherence"] for r in results) / len(results), 4),
            "avg_weight": round(sum(r["total_weight"] for r in results) / len(results), 2),
        }

    return all_journeys, hybrid_tuning, sop_nodes


def log_journeys(journeys):
    """Log all journeys via route_tracker.py CLI."""
    agent_map = {
        "weight": {"SCR": "s6-1", "SRC": "s6-2"},
        "tag": {"SCR": "s6-3", "SRC": "s6-4"},
        "hybrid": {"SCR": "s6-5", "SRC": "s6-6"},
    }

    for j in journeys:
        agent = agent_map[j["strategy"]][j["target_type"]]
        task = f"{j['strategy']}-routing"
        route_str = " -> ".join(j["path"])
        outcome = "success" if j["reached"] else "failure"
        notes = f"strategy={j['strategy']}, target={j['target_type']}, coherence={j['coherence']}, weight={j['total_weight']}, hops={j['hops']}"

        cmd = [
            sys.executable, str(ROUTE_TRACKER), "log",
            "--agent", agent,
            "--task", task,
            "--route", route_str,
            "--outcome", outcome,
            "--notes", notes,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE))
        if result.returncode != 0:
            print(f"ERROR logging journey {j['id']}: {result.stderr}")
        else:
            print(result.stdout.strip())


def build_report(journeys, hybrid_tuning, sop_nodes):
    """Build the final report."""
    # Strategy comparison
    strategy_stats = {}
    for strat in ["weight", "tag", "hybrid"]:
        strat_journeys = [j for j in journeys if j["strategy"] == strat]
        successes = sum(1 for j in strat_journeys if j["reached"])
        total = len(strat_journeys)
        strategy_stats[strat] = {
            "total_journeys": total,
            "successes": successes,
            "failures": total - successes,
            "success_rate": round(successes / total * 100, 1) if total else 0,
            "avg_hops": round(sum(j["hops"] for j in strat_journeys) / total, 2) if total else 0,
            "avg_coherence": round(sum(j["coherence"] for j in strat_journeys) / total, 4) if total else 0,
            "avg_weight": round(sum(j["total_weight"] for j in strat_journeys) / total, 2) if total else 0,
            "scr_success_rate": round(
                sum(1 for j in strat_journeys if j["reached"] and j["target_type"] == "SCR") /
                max(sum(1 for j in strat_journeys if j["target_type"] == "SCR"), 1) * 100, 1),
            "src_success_rate": round(
                sum(1 for j in strat_journeys if j["reached"] and j["target_type"] == "SRC") /
                max(sum(1 for j in strat_journeys if j["target_type"] == "SRC"), 1) * 100, 1),
        }

    # Determine best strategy
    scores = {}
    for strat, stats in strategy_stats.items():
        # Composite: success_rate (primary), then coherence, then fewer hops
        scores[strat] = (
            stats["success_rate"],
            stats["avg_coherence"],
            -stats["avg_hops"],  # fewer hops is better
            stats["avg_weight"],
        )
    best = max(scores, key=lambda s: scores[s])
    runner_up = sorted(scores, key=lambda s: scores[s], reverse=True)[1]

    best_stats = strategy_stats[best]
    runner_stats = strategy_stats[runner_up]

    justification_parts = [
        f"{best} routing achieves {best_stats['success_rate']}% success rate",
        f"vs {runner_up} at {runner_stats['success_rate']}%",
    ]
    if best_stats["avg_coherence"] > runner_stats["avg_coherence"]:
        justification_parts.append(
            f"with higher tag coherence ({best_stats['avg_coherence']:.4f} vs {runner_stats['avg_coherence']:.4f})"
        )
    if best_stats["avg_hops"] < runner_stats["avg_hops"]:
        justification_parts.append(
            f"and fewer hops ({best_stats['avg_hops']:.1f} vs {runner_stats['avg_hops']:.1f})"
        )
    justification = "; ".join(justification_parts) + "."

    # Per-SOP results
    per_sop = {}
    for sop in sop_nodes:
        sop_results = {}
        for strat in ["weight", "tag", "hybrid"]:
            sj = [j for j in journeys if j["sop"] == sop and j["strategy"] == strat]
            successes = sum(1 for j in sj if j["reached"])
            sop_results[strat] = {
                "successes": successes,
                "total": len(sj),
                "success_rate": round(successes / len(sj) * 100, 1) if sj else 0,
                "avg_hops": round(sum(j["hops"] for j in sj) / len(sj), 2) if sj else 0,
                "avg_coherence": round(sum(j["coherence"] for j in sj) / len(sj), 4) if sj else 0,
                "avg_weight": round(sum(j["total_weight"] for j in sj) / len(sj), 2) if sj else 0,
                "paths": [" -> ".join(j["path"]) for j in sj],
            }
        # Best strategy for this SOP
        sop_best = max(sop_results, key=lambda s: (
            sop_results[s]["success_rate"],
            sop_results[s]["avg_coherence"],
            -sop_results[s]["avg_hops"],
        ))
        per_sop[sop] = {
            "results": sop_results,
            "best_strategy": sop_best,
        }

    report = {
        "benchmark": "stress-s6-routing-strategy-comparison",
        "date": "2026-03-12",
        "description": "Compares weight, tag (Jaccard), and hybrid routing across 60 journeys from 10 SOPs seeking SCR or SRC targets.",
        "total_journeys": len(journeys),
        "strategy_comparison": strategy_stats,
        "best_strategy": {
            "name": best,
            "justification": justification,
            "composite_ranking": {s: i+1 for i, s in enumerate(sorted(scores, key=lambda s: scores[s], reverse=True))}
        },
        "per_sop_results": per_sop,
        "hybrid_tuning": {
            "description": "Hybrid score = weight * (1 + alpha * jaccard). Tested alpha values to find optimal balance.",
            "results": hybrid_tuning,
            "best_alpha": max(hybrid_tuning.values(), key=lambda v: (v["success_rate"], v["avg_coherence"]))["alpha"],
        },
        "all_journeys": journeys,
    }

    return report


def main():
    print("=" * 60)
    print("Stress S6: Routing Strategy Benchmark")
    print("=" * 60)

    print("\n[1/3] Running 60 journeys across 3 strategies...")
    journeys, hybrid_tuning, sop_nodes = run_benchmark()
    print(f"  Completed {len(journeys)} journeys")

    # Quick summary
    for strat in ["weight", "tag", "hybrid"]:
        sj = [j for j in journeys if j["strategy"] == strat]
        successes = sum(1 for j in sj if j["reached"])
        print(f"  {strat}: {successes}/{len(sj)} reached target")

    print("\n[2/3] Logging all journeys via route_tracker.py...")
    log_journeys(journeys)

    print("\n[3/3] Building report...")
    report = build_report(journeys, hybrid_tuning, sop_nodes)

    REPORT_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_OUT, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  Report written to {REPORT_OUT}")

    # Print summary
    print("\n" + "=" * 60)
    print("RESULTS SUMMARY")
    print("=" * 60)
    sc = report["strategy_comparison"]
    for s in ["weight", "tag", "hybrid"]:
        st = sc[s]
        print(f"  {s:8s}: success={st['success_rate']:5.1f}%  hops={st['avg_hops']:.1f}  "
              f"coherence={st['avg_coherence']:.4f}  weight={st['avg_weight']:.1f}  "
              f"SCR={st['scr_success_rate']:.0f}%  SRC={st['src_success_rate']:.0f}%")

    best = report["best_strategy"]
    print(f"\n  BEST: {best['name']} — {best['justification']}")

    ht = report["hybrid_tuning"]
    print(f"  Best hybrid alpha: {ht['best_alpha']}")

    return report


if __name__ == "__main__":
    main()
