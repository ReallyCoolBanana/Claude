#!/usr/bin/env python3
"""Stress Team S1: High-volume traversal stress test.
Simulates 66 journeys (3 types x 22 SOPs) and logs them via route_tracker.
"""

import json
import os
import sys
import random
import subprocess
from pathlib import Path
from collections import Counter, defaultdict

random.seed(42)  # Reproducible random walks

BASE = Path(__file__).parent.parent
POINTER_NETWORK = BASE / "pointer-network.json"
ROUTE_TRACKER = BASE / "coordination" / "route_tracker.py"
REPORT_FILE = Path(__file__).parent / "stress_s1_report.json"

with open(POINTER_NETWORK) as f:
    pn = json.load(f)

nodes = pn["nodes"]

# All 22 SOPs
sops = sorted([k for k in nodes if k.startswith("SOP-")])
assert len(sops) == 22, f"Expected 22 SOPs, got {len(sops)}"

# Assign SOPs to agents s1-1..s1-8 round-robin
agent_assignments = {}
for i, sop in enumerate(sops):
    agent_id = f"s1-{(i % 8) + 1}"
    agent_assignments[sop] = agent_id


def get_pointers_sorted(node_id):
    """Get pointers for a node, sorted by weight descending."""
    node = nodes.get(node_id, {})
    ptrs = node.get("pointers", [])
    return sorted(ptrs, key=lambda p: p.get("weight", 0), reverse=True)


def classify_outcome(node_id):
    """Classify outcome based on what type of node we ended at."""
    if node_id.startswith("SCR-") or node_id.startswith("SRC-"):
        return "success"
    elif node_id.startswith("KB-") or node_id.startswith("TEAM-"):
        return "partial"
    else:
        return "failure"


def journey_primary_path(sop):
    """Follow primary/supporting strength pointers. If none, follow highest weight."""
    path = [sop]
    visited = {sop}
    current = sop
    max_hops = 5
    strengths_used = []

    for _ in range(max_hops):
        ptrs = get_pointers_sorted(current)
        if not ptrs:
            break

        # Try primary first, then supporting, then highest weight
        chosen = None
        for strength in ["primary", "supporting"]:
            candidates = [p for p in ptrs if p.get("strength") == strength and p["to"] not in visited]
            if candidates:
                chosen = candidates[0]
                break

        if not chosen:
            # Fall back to highest weight not visited
            candidates = [p for p in ptrs if p["to"] not in visited]
            if candidates:
                chosen = candidates[0]
            else:
                break

        strengths_used.append(chosen.get("strength", "unclassified"))
        current = chosen["to"]
        visited.add(current)
        path.append(current)

        # Stop if we reached a useful endpoint
        outcome = classify_outcome(current)
        if outcome == "success":
            break

    return path, strengths_used


def journey_exploration(sop):
    """Follow the 2nd-highest weight pointer at each hop, 3 hops deep."""
    path = [sop]
    visited = {sop}
    current = sop
    strengths_used = []

    for _ in range(3):
        ptrs = get_pointers_sorted(current)
        candidates = [p for p in ptrs if p["to"] not in visited]
        if len(candidates) < 2:
            # Use whatever is available
            if candidates:
                chosen = candidates[0]
            else:
                break
        else:
            chosen = candidates[1]  # 2nd highest

        strengths_used.append(chosen.get("strength", "unclassified"))
        current = chosen["to"]
        visited.add(current)
        path.append(current)

    return path, strengths_used


def journey_random_walk(sop):
    """Follow a random pointer at each hop for 3 hops."""
    path = [sop]
    visited = {sop}
    current = sop
    strengths_used = []

    for _ in range(3):
        ptrs = get_pointers_sorted(current)
        candidates = [p for p in ptrs if p["to"] not in visited]
        if not candidates:
            break

        chosen = random.choice(candidates)
        strengths_used.append(chosen.get("strength", "unclassified"))
        current = chosen["to"]
        visited.add(current)
        path.append(current)

    return path, strengths_used


def log_route(agent, task, route, outcome, notes):
    """Log a route via route_tracker.py."""
    route_str = " -> ".join(route)
    cmd = [
        sys.executable, str(ROUTE_TRACKER), "log",
        "--agent", agent,
        "--task", task,
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR logging route: {result.stderr}", file=sys.stderr)
    else:
        print(result.stdout.strip())
    return result.returncode == 0


# Run all 66 journeys
journey_types = [
    ("primary-path", journey_primary_path),
    ("exploration", journey_exploration),
    ("random-walk", journey_random_walk),
]

all_results = []
edge_counter = Counter()
outcome_by_type = defaultdict(lambda: Counter())
total_hops = 0
total_routes = 0

for sop in sops:
    agent = agent_assignments[sop]
    for jtype, jfunc in journey_types:
        path, strengths_used = jfunc(sop)
        outcome = classify_outcome(path[-1])
        hops = len(path) - 1
        notes = f"hops={hops}, strengths={','.join(strengths_used) if strengths_used else 'none'}"

        success = log_route(agent, jtype, path, outcome, notes)

        # Track stats
        for i in range(len(path) - 1):
            edge_counter[f"{path[i]} -> {path[i+1]}"] += 1
        outcome_by_type[jtype][outcome] += 1
        total_hops += hops
        total_routes += 1

        all_results.append({
            "sop": sop,
            "agent": agent,
            "journey_type": jtype,
            "path": path,
            "outcome": outcome,
            "hops": hops,
            "strengths_used": strengths_used
        })

# Build summary report
avg_hops = round(total_hops / max(total_routes, 1), 2)
most_traversed = [{"edge": e, "count": c} for e, c in edge_counter.most_common(15)]

report = {
    "test": "stress-s1",
    "description": "High-volume traversal stress test: 3 journey types x 22 SOPs = 66 journeys",
    "total_routes_logged": total_routes,
    "avg_hops": avg_hops,
    "total_hops": total_hops,
    "outcome_distribution_by_journey_type": {
        jtype: dict(outcome_by_type[jtype]) for jtype in ["primary-path", "exploration", "random-walk"]
    },
    "overall_outcome_distribution": dict(
        Counter(r["outcome"] for r in all_results)
    ),
    "most_traversed_edges": most_traversed,
    "agent_distribution": dict(Counter(r["agent"] for r in all_results)),
    "journeys": all_results
}

with open(REPORT_FILE, "w") as f:
    json.dump(report, f, indent=2)

print(f"\n=== STRESS S1 COMPLETE ===")
print(f"Total routes logged: {total_routes}")
print(f"Avg hops: {avg_hops}")
print(f"Outcomes: {dict(Counter(r['outcome'] for r in all_results))}")
print(f"By type:")
for jtype in ["primary-path", "exploration", "random-walk"]:
    print(f"  {jtype}: {dict(outcome_by_type[jtype])}")
print(f"Top 5 edges:")
for item in most_traversed[:5]:
    print(f"  {item['edge']}: {item['count']}")
print(f"\nReport written to: {REPORT_FILE}")
