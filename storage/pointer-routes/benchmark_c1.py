#!/usr/bin/env python3
"""
Team C1 Benchmark: Stress-test SCR->SCR chains, monitoring workflows, cross-SOP paths.
"""

import json
import subprocess
import sys
from pathlib import Path
from collections import defaultdict, deque

BASE = Path(__file__).parent.parent
PN_PATH = BASE / "pointer-network.json"
ROUTE_TRACKER = BASE / "coordination" / "route_tracker.py"
REPORT_PATH = Path(__file__).parent / "benchmark_c1_report.json"

# Load pointer network
try:
    with open(PN_PATH) as f:
        pn = json.load(f)
except FileNotFoundError:
    print(f"Error: pointer network file not found: {PN_PATH}", file=sys.stderr)
    sys.exit(1)

nodes = pn["nodes"]

# ============================================================
# TASK 1: Map ALL SCR->SCR edges
# ============================================================

scr_nodes = {nid: data for nid, data in nodes.items() if nid.startswith("SCR-")}
scr_ids = set(scr_nodes.keys())

# Build SCR->SCR adjacency
scr_to_scr_edges = []  # (from, to, weight, strength)
scr_adjacency = defaultdict(list)  # from -> [to, ...]
scr_has_companion = set()

for nid, data in scr_nodes.items():
    for ptr in data.get("pointers", []):
        target = ptr["to"]
        if target.startswith("SCR-"):
            scr_to_scr_edges.append({
                "from": nid,
                "to": target,
                "weight": ptr.get("weight", 0),
                "strength": ptr.get("strength", "unclassified"),
                "reasons": ptr.get("reasons", [])
            })
            scr_adjacency[nid].append(target)
            scr_has_companion.add(nid)
            scr_has_companion.add(target)

isolated_scripts = sorted(scr_ids - scr_has_companion)
companion_scripts = sorted(scr_has_companion)

# Find chains: longest paths via BFS from each SCR node
def find_longest_chain_from(start, adj):
    """BFS to find longest chain from start node."""
    best_path = [start]
    queue = deque([(start, [start], {start})])
    while queue:
        current, path, visited = queue.popleft()
        extended = False
        for neighbor in adj.get(current, []):
            if neighbor not in visited:
                extended = True
                new_path = path + [neighbor]
                new_visited = visited | {neighbor}
                queue.append((neighbor, new_path, new_visited))
                if len(new_path) > len(best_path):
                    best_path = new_path
        # no need to track non-extended; best_path captures longest
    return best_path

all_chains = {}
for nid in scr_ids:
    if scr_adjacency.get(nid):
        chain = find_longest_chain_from(nid, scr_adjacency)
        if len(chain) > 1:
            all_chains[nid] = chain

# Group by chain length
chain_lengths = defaultdict(list)
for start, chain in all_chains.items():
    chain_lengths[len(chain)].append(" -> ".join(chain))

max_chain_length = max(chain_lengths.keys()) if chain_lengths else 0

print(f"=== TASK 1: SCR->SCR Edge Analysis ===")
print(f"Total SCR nodes: {len(scr_ids)}")
print(f"SCR->SCR edges: {len(scr_to_scr_edges)}")
print(f"Scripts with companions: {len(companion_scripts)}")
print(f"Isolated scripts: {len(isolated_scripts)}")
print(f"Max chain length: {max_chain_length}")
print()

# ============================================================
# TASK 2: Monitoring SOP workflows - trace SOP -> SCR -> SCR chains
# ============================================================

monitoring_sops = ["SOP-012", "SOP-011", "SOP-014", "SOP-015"]

# Build full adjacency (any node -> SCR, SCR -> SCR)
def trace_monitoring_workflow(sop_id):
    """From a monitoring SOP, find all reachable SCR chains."""
    node_data = nodes.get(sop_id, {})
    pointers = node_data.get("pointers", [])

    # Direct SOP -> SCR links
    scr_links = [p for p in pointers if p["to"].startswith("SCR-")]

    workflows = []
    for scr_ptr in scr_links:
        scr_id = scr_ptr["to"]
        if scr_id not in nodes:
            print(f"  WARNING: SCR target {scr_id} from {sop_id} not found in network, skipping")
            continue
        # Now follow SCR->SCR chains
        chain = find_longest_chain_from(scr_id, scr_adjacency)
        full_path = [sop_id] + chain
        workflows.append({
            "path": full_path,
            "depth": len(full_path),
            "entry_strength": scr_ptr.get("strength", "unclassified"),
            "entry_weight": scr_ptr.get("weight", 0)
        })

    return workflows

monitoring_depth = {}
monitoring_workflows_detail = {}

print(f"=== TASK 2: Monitoring SOP Workflow Depth ===")
for sop_id in monitoring_sops:
    if sop_id not in nodes:
        print(f"  WARNING: {sop_id} not found in pointer network, skipping")
        monitoring_depth[sop_id] = 0
        monitoring_workflows_detail[sop_id] = {"total_scr_links": 0, "max_depth": 0, "workflows": []}
        continue
    workflows = trace_monitoring_workflow(sop_id)
    if workflows:
        max_depth = max(w["depth"] for w in workflows)
        monitoring_depth[sop_id] = max_depth
        monitoring_workflows_detail[sop_id] = {
            "total_scr_links": len(workflows),
            "max_depth": max_depth,
            "workflows": [
                {"path": " -> ".join(w["path"]), "depth": w["depth"],
                 "entry_strength": w["entry_strength"]}
                for w in sorted(workflows, key=lambda x: -x["depth"])
            ]
        }
        print(f"  {sop_id}: {len(workflows)} SCR links, max depth={max_depth}")
        for w in sorted(workflows, key=lambda x: -x["depth"])[:3]:
            print(f"    depth={w['depth']}: {' -> '.join(w['path'])}")
    else:
        monitoring_depth[sop_id] = 0
        monitoring_workflows_detail[sop_id] = {
            "total_scr_links": 0, "max_depth": 0, "workflows": []
        }
        print(f"  {sop_id}: NO direct SCR links")
print()

# ============================================================
# TASK 3: Cross-SOP workflows: SOP -> SCR -> SOP
# ============================================================

coordination_sops = ["SOP-G01", "SOP-G02", "SOP-G03", "SOP-013"]
# Also check all SOPs for cross-SOP paths

def find_cross_sop_paths(sop_id):
    """Find paths: SOP -> SCR(s) -> another SOP."""
    node_data = nodes.get(sop_id, {})
    pointers = node_data.get("pointers", [])

    # SOP -> SCR links
    scr_targets = [(p["to"], p.get("strength", "?"), p.get("weight", 0))
                   for p in pointers if p["to"].startswith("SCR-")]

    paths = []
    for scr_id, strength, weight in scr_targets:
        if scr_id not in nodes:
            print(f"  WARNING: SCR target {scr_id} from {sop_id} not found in network, skipping")
            continue
        # Check if this SCR points to any SOP
        scr_data = nodes.get(scr_id, {})
        sop_targets = [(p["to"], p.get("strength", "?"), p.get("weight", 0))
                       for p in scr_data.get("pointers", [])
                       if p["to"].startswith("SOP-") and p["to"] != sop_id]

        for target_sop, t_strength, t_weight in sop_targets:
            paths.append({
                "path": f"{sop_id} -> {scr_id} -> {target_sop}",
                "via_script": scr_id,
                "target_sop": target_sop,
                "leg1_strength": strength,
                "leg2_strength": t_strength,
                "total_weight": weight + t_weight
            })

        # Also check SCR->SCR->SOP (2-hop via scripts)
        for scr_neighbor in scr_adjacency.get(scr_id, []):
            if scr_neighbor not in nodes:
                print(f"  WARNING: SCR neighbor {scr_neighbor} not found in network, skipping")
                continue
            scr2_data = nodes.get(scr_neighbor, {})
            sop_targets2 = [(p["to"], p.get("strength", "?"), p.get("weight", 0))
                            for p in scr2_data.get("pointers", [])
                            if p["to"].startswith("SOP-") and p["to"] != sop_id]
            for target_sop, t_strength, t_weight in sop_targets2:
                paths.append({
                    "path": f"{sop_id} -> {scr_id} -> {scr_neighbor} -> {target_sop}",
                    "via_script": f"{scr_id} -> {scr_neighbor}",
                    "target_sop": target_sop,
                    "leg1_strength": strength,
                    "leg2_strength": t_strength,
                    "total_weight": weight + t_weight
                })

    return paths

cross_sop_paths = {}
print(f"=== TASK 3: Cross-SOP Workflows ===")
for sop_id in coordination_sops:
    paths = find_cross_sop_paths(sop_id)
    cross_sop_paths[sop_id] = {
        "total_paths": len(paths),
        "unique_target_sops": list(set(p["target_sop"] for p in paths)),
        "paths": paths[:20]  # cap for report size
    }
    print(f"  {sop_id}: {len(paths)} cross-SOP paths to {len(set(p['target_sop'] for p in paths))} unique SOPs")
    for p in paths[:3]:
        print(f"    {p['path']} (L1={p['leg1_strength']}, L2={p['leg2_strength']})")

# Check if any coordination SOPs don't exist
for sop_id in coordination_sops:
    if sop_id not in nodes:
        print(f"  WARNING: {sop_id} not found in pointer network!")
print()

# ============================================================
# TASK 4: Simulate 10 monitoring/coordination journeys
# ============================================================

print(f"=== TASK 4: Simulating 10 Journeys ===")

# Build journey definitions
journeys = []

# Journey 1-5: Monitoring tasks (agents c1-1 to c1-3)
# Pick real paths from monitoring workflows
for sop_id in monitoring_sops:
    wf = monitoring_workflows_detail.get(sop_id, {}).get("workflows", [])
    if wf:
        best = wf[0]  # deepest workflow
        path_str = best["path"]
        depth = best["depth"]
        if depth >= 3:
            outcome = "success"
            assessment = f"Deep chain (depth={depth}), strong SCR connectivity"
        elif depth == 2:
            outcome = "partial"
            assessment = f"Shallow chain (depth={depth}), limited SCR->SCR links"
        else:
            outcome = "failure"
            assessment = f"No SCR chain (depth={depth}), isolated monitoring script"
        journeys.append({
            "agent": f"c1-{(len(journeys) % 5) + 1}",
            "task": "monitoring",
            "route": path_str,
            "outcome": outcome,
            "notes": assessment
        })

# Add more monitoring journeys using weaker paths
for sop_id in ["SOP-012", "SOP-011"]:
    wf = monitoring_workflows_detail.get(sop_id, {}).get("workflows", [])
    if len(wf) > 1:
        weak = wf[-1]  # shallowest
        journeys.append({
            "agent": f"c1-{(len(journeys) % 5) + 1}",
            "task": "monitoring",
            "route": weak["path"],
            "outcome": "partial" if weak["depth"] >= 2 else "failure",
            "notes": f"Weak path test: depth={weak['depth']}, entry={weak['entry_strength']}"
        })

# Journey 6-10: Coordination tasks (agents c1-3 to c1-5)
for sop_id in coordination_sops:
    cp = cross_sop_paths.get(sop_id, {}).get("paths", [])
    if cp:
        best = cp[0]
        journeys.append({
            "agent": f"c1-{(len(journeys) % 5) + 1}",
            "task": "coordination",
            "route": best["path"],
            "outcome": "success" if best["total_weight"] >= 6 else "partial",
            "notes": f"Cross-SOP via {best['via_script']}, weight={best['total_weight']}"
        })
    else:
        # SOP not in network or no paths
        journeys.append({
            "agent": f"c1-{(len(journeys) % 5) + 1}",
            "task": "coordination",
            "route": sop_id,
            "outcome": "failure",
            "notes": f"{sop_id} has no cross-SOP script paths (missing or isolated)"
        })

# Trim to exactly 10
journeys = journeys[:10]
# If fewer than 10, pad with repeat monitoring tests using different agents
while len(journeys) < 10:
    idx = len(journeys)
    journeys.append({
        "agent": f"c1-{(idx % 5) + 1}",
        "task": "monitoring",
        "route": "SOP-012",
        "outcome": "failure",
        "notes": "Fallback journey - no available chain"
    })

# Log each journey via route_tracker.py
journey_results = []
for j in journeys:
    cmd = [
        sys.executable, str(ROUTE_TRACKER), "log",
        "--agent", j["agent"],
        "--task", j["task"],
        "--route", j["route"],
        "--outcome", j["outcome"],
        "--notes", j["notes"]
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(f"  Logged: agent={j['agent']} task={j['task']} route={j['route']} outcome={j['outcome']}")
    if result.stdout.strip():
        print(f"    {result.stdout.strip()}")
    if result.stderr.strip():
        print(f"    STDERR: {result.stderr.strip()}")
    journey_results.append({
        **j,
        "tracker_stdout": result.stdout.strip(),
        "tracker_returncode": result.returncode
    })

print()

# ============================================================
# TASK 5: Write benchmark report
# ============================================================

# Compile strength distribution of SCR->SCR edges
strength_dist = defaultdict(int)
for e in scr_to_scr_edges:
    strength_dist[e["strength"]] += 1

report = {
    "benchmark": "C1 - SCR Chain Stress Test",
    "generated": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    "run_context": "Run-002: Follow-up to Run-001 finding 25% monitoring success due to weak SCR->SCR chains",

    "task_1_scr_to_scr_analysis": {
        "total_scr_nodes": len(scr_ids),
        "total_scr_to_scr_edges": len(scr_to_scr_edges),
        "scripts_with_companions": len(companion_scripts),
        "isolated_scripts_count": len(isolated_scripts),
        "companion_ratio": round(len(companion_scripts) / max(len(scr_ids), 1) * 100, 1),
        "max_chain_length": max_chain_length,
        "strength_distribution": dict(strength_dist),
        "scr_to_scr_chains": {
            start: " -> ".join(chain)
            for start, chain in sorted(all_chains.items(), key=lambda x: -len(x[1]))[:30]
        },
        "chain_length_distribution": {
            str(length): len(chains) for length, chains in sorted(chain_lengths.items())
        },
        "isolated_scripts": isolated_scripts
    },

    "task_2_monitoring_depth": {
        "sops_analyzed": monitoring_sops,
        "max_depth_per_sop": monitoring_depth,
        "overall_max_depth": max(monitoring_depth.values()) if monitoring_depth else 0,
        "workflows": monitoring_workflows_detail
    },

    "task_3_cross_sop_paths": {
        "sops_analyzed": coordination_sops,
        "viable_paths_per_sop": {
            sop: data["total_paths"] for sop, data in cross_sop_paths.items()
        },
        "unique_target_sops_per_sop": {
            sop: data["unique_target_sops"] for sop, data in cross_sop_paths.items()
        },
        "detail": cross_sop_paths
    },

    "task_4_simulated_journeys": {
        "total_journeys": len(journey_results),
        "outcomes": {
            "success": sum(1 for j in journey_results if j["outcome"] == "success"),
            "partial": sum(1 for j in journey_results if j["outcome"] == "partial"),
            "failure": sum(1 for j in journey_results if j["outcome"] == "failure"),
        },
        "monitoring_success_rate": round(
            sum(1 for j in journey_results if j["task"] == "monitoring" and j["outcome"] == "success") /
            max(sum(1 for j in journey_results if j["task"] == "monitoring"), 1) * 100, 1
        ),
        "coordination_success_rate": round(
            sum(1 for j in journey_results if j["task"] == "coordination" and j["outcome"] == "success") /
            max(sum(1 for j in journey_results if j["task"] == "coordination"), 1) * 100, 1
        ),
        "journeys": journey_results
    },

    "findings": {
        "scr_chain_health": (
            "GOOD" if len(companion_scripts) / max(len(scr_ids), 1) > 0.5
            else "WEAK" if len(companion_scripts) / max(len(scr_ids), 1) > 0.25
            else "CRITICAL"
        ),
        "monitoring_chain_depth": (
            "GOOD" if max(monitoring_depth.values(), default=0) >= 4
            else "MODERATE" if max(monitoring_depth.values(), default=0) >= 3
            else "SHALLOW"
        ),
        "cross_sop_connectivity": (
            "GOOD" if sum(d["total_paths"] for d in cross_sop_paths.values()) > 10
            else "LIMITED" if sum(d["total_paths"] for d in cross_sop_paths.values()) > 3
            else "POOR"
        ),
        "run001_comparison": "See monitoring_success_rate vs Run-001's 25%"
    }
}

with open(REPORT_PATH, "w") as f:
    json.dump(report, f, indent=2)

print(f"=== TASK 5: Report Written ===")
print(f"  Output: {REPORT_PATH}")
print(f"  SCR chain health: {report['findings']['scr_chain_health']}")
print(f"  Monitoring depth: {report['findings']['monitoring_chain_depth']}")
print(f"  Cross-SOP connectivity: {report['findings']['cross_sop_connectivity']}")
print(f"  Monitoring success rate: {report['task_4_simulated_journeys']['monitoring_success_rate']}%")
print(f"  Coordination success rate: {report['task_4_simulated_journeys']['coordination_success_rate']}%")
