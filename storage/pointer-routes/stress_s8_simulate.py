#!/usr/bin/env python3
"""
Stress Team S8 — End-to-end workflow simulation testing.
Simulates 15 complete agent workflows with multi-hop pointer traversals.
"""

import json
import subprocess
import sys
import os
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

BASE = Path(__file__).parent.parent
POINTER_NETWORK = BASE / "pointer-network.json"
ROUTE_TRACKER = BASE / "coordination" / "route_tracker.py"
REPORT_PATH = BASE / "pointer-routes" / "stress_s8_report.json"

try:
    with open(POINTER_NETWORK) as f:
        PN = json.load(f)
except FileNotFoundError:
    print(f"Error: pointer network file not found: {POINTER_NETWORK}", file=sys.stderr)
    sys.exit(1)
NODES = PN["nodes"]


def get_pointers(node_id, target_prefix=None, strength_filter=None):
    """Get pointers from a node, optionally filtered by target prefix and strength."""
    node = NODES.get(node_id, {})
    ptrs = node.get("pointers", [])
    if target_prefix:
        ptrs = [p for p in ptrs if p["to"].startswith(target_prefix)]
    if strength_filter:
        ptrs = [p for p in ptrs if p.get("strength") in strength_filter]
    return sorted(ptrs, key=lambda p: p.get("weight", 0), reverse=True)


def best_pointer(node_id, target_prefix, prefer_strengths=("primary", "supporting")):
    """Find the best pointer from node_id to a target domain prefix."""
    # Try preferred strengths first
    ptrs = get_pointers(node_id, target_prefix, prefer_strengths)
    if ptrs:
        return ptrs[0]
    # Fall back to any pointer
    ptrs = get_pointers(node_id, target_prefix)
    if ptrs:
        return ptrs[0]
    return None


def traverse_chain(start, domain_chain):
    """
    Traverse a chain of domain types starting from a specific node.
    domain_chain is like ["KB", "SCR", "SRC"] meaning:
      start -> best KB node -> best SCR node -> best SRC node
    Returns (route_list, hop_details, success/partial/failure)
    """
    route = [start]
    hops = []
    current = start

    for target_domain in domain_chain:
        ptr = best_pointer(current, target_domain + "-")
        if ptr is None:
            # Try indirect: look for any node that connects current domain to target
            hops.append({
                "from": current,
                "to": f"[missing-{target_domain}]",
                "weight": 0,
                "strength": "missing",
                "found": False,
                "reason": f"No pointer from {current} to {target_domain}-* domain",
                "missing_transition": f"{current.split('-')[0]} -> {target_domain}"
            })
            # Mark as partial - we can't continue this chain
            return route, hops, "partial"

        target_node = ptr["to"]
        if target_node not in NODES:
            hops.append({
                "from": current,
                "to": target_node,
                "weight": ptr.get("weight", 0),
                "strength": ptr.get("strength", "unclassified"),
                "found": False,
                "reason": f"Target node {target_node} not in pointer network"
            })
            return route, hops, "partial"

        hops.append({
            "from": current,
            "to": target_node,
            "weight": ptr.get("weight", 0),
            "strength": ptr.get("strength", "unclassified"),
            "found": True,
            "reasons": ptr.get("reasons", [])
        })
        route.append(target_node)
        current = target_node

    return route, hops, "success"


def evaluate_workflow(route, hops, outcome):
    """Evaluate quality metrics for a completed workflow."""
    total_weight = sum(h["weight"] for h in hops)
    strengths = [h.get("strength", "unknown") for h in hops]
    strength_helpful = any(s in ("primary", "supporting") for s in strengths)

    # Tag coherence: check if tags overlap between consecutive nodes
    tag_coherence_scores = []
    for i in range(len(route) - 1):
        tags_a = set(NODES.get(route[i], {}).get("tags", []))
        tags_b = set(NODES.get(route[i + 1], {}).get("tags", []))
        if tags_a and tags_b:
            overlap = len(tags_a & tags_b)
            total = len(tags_a | tags_b)
            tag_coherence_scores.append(round(overlap / total, 3) if total else 0)
        else:
            tag_coherence_scores.append(0)

    avg_tag_coherence = (
        round(sum(tag_coherence_scores) / len(tag_coherence_scores), 3)
        if tag_coherence_scores else 0
    )

    return {
        "total_weight": total_weight,
        "strength_helpful": strength_helpful,
        "strengths_used": strengths,
        "tag_coherence_per_hop": tag_coherence_scores,
        "avg_tag_coherence": avg_tag_coherence,
        "hop_count": len(hops),
        "chain_complete": outcome == "success"
    }


def log_route_cli(agent_id, task, route, outcome, notes):
    """Log route via the route_tracker CLI."""
    route_str = " -> ".join(route)
    cmd = [
        sys.executable, str(ROUTE_TRACKER), "log",
        "--agent", agent_id,
        "--task", task,
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(BASE.parent))
    print(f"  CLI: {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"  ERR: {result.stderr.strip()}")


def run_all_workflows():
    """Run all 15 workflow simulations."""
    all_results = []
    workflow_num = 0

    # ================================================================
    # 1. RESEARCH WORKFLOW (3 sims): SOP -> KB -> SCR -> SRC
    # ================================================================
    sop_nodes = sorted([n for n in NODES if n.startswith("SOP-")])
    research_starts = [sop_nodes[0], sop_nodes[3], sop_nodes[6]]  # Spread across SOPs

    for i, start in enumerate(research_starts):
        workflow_num += 1
        agent = f"s8-{((workflow_num - 1) % 5) + 1}"
        print(f"\n=== Workflow {workflow_num}: RESEARCH from {start} ===")

        route, hops, outcome = traverse_chain(start, ["KB", "SCR", "SRC"])
        metrics = evaluate_workflow(route, hops, outcome)

        result = {
            "workflow_num": workflow_num,
            "workflow_type": "research",
            "agent": agent,
            "expected_chain": f"SOP -> KB -> SCR -> SRC",
            "start_node": start,
            "route": route,
            "hops": hops,
            "outcome": outcome,
            "metrics": metrics
        }
        all_results.append(result)

        notes = f"workflow=research, chain_complete={'yes' if outcome == 'success' else 'no'}"
        log_route_cli(agent, "research-workflow", route, outcome, notes)
        print(f"  Route: {' -> '.join(route)}")
        print(f"  Outcome: {outcome}, Weight: {metrics['total_weight']}, Coherence: {metrics['avg_tag_coherence']}")

    # ================================================================
    # 2. BUILD WORKFLOW (3 sims): SOP -> SCR -> SCR -> SOP
    # ================================================================
    build_starts = [sop_nodes[1], sop_nodes[4], sop_nodes[7]]

    for i, start in enumerate(build_starts):
        workflow_num += 1
        agent = f"s8-{((workflow_num - 1) % 5) + 1}"
        print(f"\n=== Workflow {workflow_num}: BUILD from {start} ===")

        route, hops, outcome = traverse_chain(start, ["SCR", "SCR", "SOP"])
        metrics = evaluate_workflow(route, hops, outcome)

        result = {
            "workflow_num": workflow_num,
            "workflow_type": "build",
            "agent": agent,
            "expected_chain": f"SOP -> SCR -> SCR -> SOP",
            "start_node": start,
            "route": route,
            "hops": hops,
            "outcome": outcome,
            "metrics": metrics
        }
        all_results.append(result)

        notes = f"workflow=build, chain_complete={'yes' if outcome == 'success' else 'no'}"
        log_route_cli(agent, "build-workflow", route, outcome, notes)
        print(f"  Route: {' -> '.join(route)}")
        print(f"  Outcome: {outcome}, Weight: {metrics['total_weight']}, Coherence: {metrics['avg_tag_coherence']}")

    # ================================================================
    # 3. ENRICHMENT WORKFLOW (3 sims): SOP -> SCR -> wiki -> SCR
    # ================================================================
    enrichment_starts = ["SOP-025", "SOP-026", "SOP-027"]

    for i, start in enumerate(enrichment_starts):
        workflow_num += 1
        agent = f"s8-{((workflow_num - 1) % 5) + 1}"
        print(f"\n=== Workflow {workflow_num}: ENRICHMENT from {start} ===")

        route, hops, outcome = traverse_chain(start, ["SCR", "wiki", "SCR"])
        metrics = evaluate_workflow(route, hops, outcome)

        result = {
            "workflow_num": workflow_num,
            "workflow_type": "enrichment",
            "agent": agent,
            "expected_chain": f"SOP -> SCR -> wiki -> SCR",
            "start_node": start,
            "route": route,
            "hops": hops,
            "outcome": outcome,
            "metrics": metrics
        }
        all_results.append(result)

        notes = f"workflow=enrichment, chain_complete={'yes' if outcome == 'success' else 'no'}"
        log_route_cli(agent, "enrichment-workflow", route, outcome, notes)
        print(f"  Route: {' -> '.join(route)}")
        print(f"  Outcome: {outcome}, Weight: {metrics['total_weight']}, Coherence: {metrics['avg_tag_coherence']}")

    # ================================================================
    # 4. DISCOVERY WORKFLOW (3 sims): TEAM -> SOP -> SCR
    # ================================================================
    team_nodes = sorted([n for n in NODES if n.startswith("TEAM-")])
    # Find TEAM nodes that have SOP pointers
    team_with_sop = []
    for tn in team_nodes:
        sop_ptrs = get_pointers(tn, "SOP-")
        if sop_ptrs:
            team_with_sop.append(tn)
    discovery_starts = team_with_sop[:3] if len(team_with_sop) >= 3 else team_nodes[:3]

    for i, start in enumerate(discovery_starts):
        workflow_num += 1
        agent = f"s8-{((workflow_num - 1) % 5) + 1}"
        print(f"\n=== Workflow {workflow_num}: DISCOVERY from {start} ===")

        route, hops, outcome = traverse_chain(start, ["SOP", "SCR"])
        metrics = evaluate_workflow(route, hops, outcome)

        result = {
            "workflow_num": workflow_num,
            "workflow_type": "discovery",
            "agent": agent,
            "expected_chain": f"TEAM -> SOP -> SCR",
            "start_node": start,
            "route": route,
            "hops": hops,
            "outcome": outcome,
            "metrics": metrics
        }
        all_results.append(result)

        notes = f"workflow=discovery, chain_complete={'yes' if outcome == 'success' else 'no'}"
        log_route_cli(agent, "discovery-workflow", route, outcome, notes)
        print(f"  Route: {' -> '.join(route)}")
        print(f"  Outcome: {outcome}, Weight: {metrics['total_weight']}, Coherence: {metrics['avg_tag_coherence']}")

    # ================================================================
    # 5. FULL-STACK WORKFLOW (3 sims): SOP -> SCR -> SRC -> KB -> TEAM (5+ hops)
    # ================================================================
    fullstack_starts = [sop_nodes[2], sop_nodes[5], sop_nodes[8]]

    for i, start in enumerate(fullstack_starts):
        workflow_num += 1
        agent = f"s8-{((workflow_num - 1) % 5) + 1}"
        print(f"\n=== Workflow {workflow_num}: FULL-STACK from {start} ===")

        # 5+ hops: SOP -> SCR -> SRC -> KB -> TEAM -> SOP (6 hops total to ensure 5+)
        route, hops, outcome = traverse_chain(start, ["SCR", "SRC", "KB", "TEAM", "SOP"])
        metrics = evaluate_workflow(route, hops, outcome)

        result = {
            "workflow_num": workflow_num,
            "workflow_type": "full-stack",
            "agent": agent,
            "expected_chain": f"SOP -> SCR -> SRC -> KB -> TEAM -> SOP",
            "start_node": start,
            "route": route,
            "hops": hops,
            "outcome": outcome,
            "metrics": metrics
        }
        all_results.append(result)

        notes = f"workflow=full-stack, chain_complete={'yes' if outcome == 'success' else 'no'}, hops={metrics['hop_count']}"
        log_route_cli(agent, "fullstack-workflow", route, outcome, notes)
        print(f"  Route: {' -> '.join(route)}")
        print(f"  Outcome: {outcome}, Weight: {metrics['total_weight']}, Coherence: {metrics['avg_tag_coherence']}")

    return all_results


def build_report(all_results):
    """Build the final stress test report."""
    # Workflow success rates by type
    by_type = defaultdict(list)
    for r in all_results:
        by_type[r["workflow_type"]].append(r)

    workflow_success_rates = {}
    for wtype, results in by_type.items():
        successes = sum(1 for r in results if r["outcome"] == "success")
        partials = sum(1 for r in results if r["outcome"] == "partial")
        failures = sum(1 for r in results if r["outcome"] == "failure")
        workflow_success_rates[wtype] = {
            "total": len(results),
            "success": successes,
            "partial": partials,
            "failure": failures,
            "success_rate_pct": round(successes / len(results) * 100, 1),
            "completion_rate_pct": round((successes + partials) / len(results) * 100, 1)
        }

    # Chain completion analysis: which domain transitions succeed/fail
    transition_stats = defaultdict(lambda: {"success": 0, "failure": 0, "total": 0})
    missing_transitions = Counter()  # Track actual missing domain->domain gaps
    for r in all_results:
        for hop in r["hops"]:
            if hop.get("missing_transition"):
                # Use the actual intended transition for failed hops
                key = hop["missing_transition"]
                transition_stats[key]["total"] += 1
                transition_stats[key]["failure"] += 1
                missing_transitions[key] += 1
            else:
                src_domain = hop["from"].split("-")[0]
                dst_domain = hop["to"].split("-")[0]
                key = f"{src_domain} -> {dst_domain}"
                transition_stats[key]["total"] += 1
                if hop.get("found", True):
                    transition_stats[key]["success"] += 1
                else:
                    transition_stats[key]["failure"] += 1

    chain_completion = {}
    for key, stats in sorted(transition_stats.items()):
        chain_completion[key] = {
            "total_attempts": stats["total"],
            "successful": stats["success"],
            "failed": stats["failure"],
            "success_rate_pct": round(stats["success"] / stats["total"] * 100, 1) if stats["total"] else 0
        }

    # Bottleneck transitions: sort by failure rate
    bottlenecks = []
    for key, stats in transition_stats.items():
        if stats["failure"] > 0:
            bottlenecks.append({
                "transition": key,
                "failures": stats["failure"],
                "total": stats["total"],
                "failure_rate_pct": round(stats["failure"] / stats["total"] * 100, 1)
            })
    bottlenecks.sort(key=lambda b: b["failure_rate_pct"], reverse=True)

    # Strength analysis
    strength_counts = Counter()
    strength_on_success = Counter()
    for r in all_results:
        for s in r["metrics"]["strengths_used"]:
            strength_counts[s] += 1
            if r["outcome"] == "success":
                strength_on_success[s] += 1

    # Tag coherence summary
    all_coherences = [r["metrics"]["avg_tag_coherence"] for r in all_results]
    success_coherences = [r["metrics"]["avg_tag_coherence"] for r in all_results if r["outcome"] == "success"]
    partial_coherences = [r["metrics"]["avg_tag_coherence"] for r in all_results if r["outcome"] == "partial"]

    # Recommendations
    recommendations = []

    if bottlenecks:
        for b in bottlenecks[:3]:
            recommendations.append(
                f"Add more {b['transition']} pointers — {b['failure_rate_pct']}% failure rate "
                f"({b['failures']}/{b['total']} attempts failed)"
            )

    # Check if unclassified strengths are common
    unclassified = strength_counts.get("unclassified", 0)
    if unclassified > 0:
        total_strengths = sum(strength_counts.values())
        pct = round(unclassified / total_strengths * 100, 1)
        recommendations.append(
            f"Classify {unclassified} unclassified pointer strengths ({pct}% of all hops) — "
            f"agents cannot use strength-based routing on these edges"
        )

    avg_coherence = round(sum(all_coherences) / len(all_coherences), 3) if all_coherences else 0
    if avg_coherence < 0.2:
        recommendations.append(
            f"Improve tag coherence across multi-hop paths (current avg: {avg_coherence}) — "
            f"agents traversing long chains lose topical context"
        )

    # Check for missing cross-domain coverage
    expected_transitions = ["SOP -> SCR", "SOP -> KB", "SCR -> SRC", "SCR -> wiki",
                            "TEAM -> SOP", "KB -> SCR", "SRC -> KB", "wiki -> SCR"]
    missing = [t for t in expected_transitions if t not in chain_completion]
    if missing:
        recommendations.append(
            f"No traversals tested for: {', '.join(missing)} — these may be undertested"
        )

    # Weight analysis
    all_weights = [r["metrics"]["total_weight"] for r in all_results]
    success_weights = [r["metrics"]["total_weight"] for r in all_results if r["outcome"] == "success"]

    report = {
        "report_id": "stress-s8-e2e-workflows",
        "generated": datetime.utcnow().isoformat() + "Z",
        "team": "Stress Team S8",
        "description": "End-to-end workflow simulation testing — 15 complete agent task flows with multi-hop pointer traversals",
        "summary": {
            "total_workflows": len(all_results),
            "successful": sum(1 for r in all_results if r["outcome"] == "success"),
            "partial": sum(1 for r in all_results if r["outcome"] == "partial"),
            "failed": sum(1 for r in all_results if r["outcome"] == "failure"),
            "total_hops_attempted": sum(len(r["hops"]) for r in all_results),
            "avg_weight": round(sum(all_weights) / len(all_weights), 1) if all_weights else 0,
            "avg_tag_coherence": avg_coherence
        },
        "workflow_results": [
            {
                "workflow_num": r["workflow_num"],
                "workflow_type": r["workflow_type"],
                "agent": r["agent"],
                "expected_chain": r["expected_chain"],
                "start_node": r["start_node"],
                "actual_route": " -> ".join(r["route"]),
                "outcome": r["outcome"],
                "hop_count": r["metrics"]["hop_count"],
                "total_weight": r["metrics"]["total_weight"],
                "strengths_used": r["metrics"]["strengths_used"],
                "strength_helpful": r["metrics"]["strength_helpful"],
                "tag_coherence_per_hop": r["metrics"]["tag_coherence_per_hop"],
                "avg_tag_coherence": r["metrics"]["avg_tag_coherence"],
                "chain_complete": r["metrics"]["chain_complete"],
                "hops_detail": r["hops"]
            }
            for r in all_results
        ],
        "workflow_success_rates": workflow_success_rates,
        "chain_completion_analysis": chain_completion,
        "bottleneck_transitions": bottlenecks if bottlenecks else [{"note": "No bottleneck transitions detected — all attempted transitions succeeded"}],
        "strength_analysis": {
            "distribution": dict(strength_counts),
            "on_successful_workflows": dict(strength_on_success),
            "effectiveness": {
                s: round(strength_on_success.get(s, 0) / strength_counts[s] * 100, 1)
                for s in strength_counts if strength_counts[s] > 0
            }
        },
        "tag_coherence_summary": {
            "overall_avg": avg_coherence,
            "on_success": round(sum(success_coherences) / len(success_coherences), 3) if success_coherences else 0,
            "on_partial": round(sum(partial_coherences) / len(partial_coherences), 3) if partial_coherences else 0,
            "observation": (
                "Tag coherence tends to degrade in multi-hop traversals as agents move between distant domains"
                if avg_coherence < 0.3 else
                "Tag coherence holds reasonably well across multi-hop traversals"
            )
        },
        "weight_analysis": {
            "avg_total_weight": round(sum(all_weights) / len(all_weights), 1) if all_weights else 0,
            "avg_on_success": round(sum(success_weights) / len(success_weights), 1) if success_weights else 0,
            "min_weight": min(all_weights) if all_weights else 0,
            "max_weight": max(all_weights) if all_weights else 0
        },
        "recommendations": recommendations
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}")
    print(f"REPORT WRITTEN: {REPORT_PATH}")
    print(f"{'='*60}")
    print(f"Total workflows: {report['summary']['total_workflows']}")
    print(f"  Success: {report['summary']['successful']}")
    print(f"  Partial: {report['summary']['partial']}")
    print(f"  Failed:  {report['summary']['failed']}")
    print(f"Avg weight: {report['summary']['avg_weight']}")
    print(f"Avg tag coherence: {report['summary']['avg_tag_coherence']}")
    print(f"\nRecommendations:")
    for i, rec in enumerate(recommendations, 1):
        print(f"  {i}. {rec}")

    return report


if __name__ == "__main__":
    results = run_all_workflows()
    build_report(results)
