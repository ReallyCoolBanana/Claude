#!/usr/bin/env python3
"""
Team A1 — Full strength classification audit of pointer-network.json
Re-audit after run-001 repairs.
"""

import json
import subprocess
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

BASE = Path(__file__).parent.parent
PN_PATH = BASE / "pointer-network.json"
ROUTE_TRACKER = BASE / "coordination" / "route_tracker.py"
REPORT_PATH = Path(__file__).parent / "benchmark_a1_report.json"

# Weight-to-strength rules
def expected_strength(weight):
    if weight >= 7:
        return "primary"
    elif 4 <= weight <= 6:
        return "supporting"
    elif 2 <= weight <= 3:
        return "related"
    elif weight == 1:
        return "tangential"
    return "unclassified"

def log_route(agent, task, route_str, outcome, notes):
    """Call route_tracker.py log"""
    cmd = [
        sys.executable, str(ROUTE_TRACKER), "log",
        "--agent", agent,
        "--task", task,
        "--route", route_str,
        "--outcome", outcome,
        "--notes", notes
    ]
    subprocess.run(cmd, capture_output=True, text=True)

def main():
    with open(PN_PATH) as f:
        pn = json.load(f)

    nodes = pn.get("nodes", {})

    # Identify all SOP nodes
    sop_nodes = {nid: ndata for nid, ndata in nodes.items() if nid.startswith("SOP-")}
    # Identify SCR and TOOL nodes
    scr_tool_nodes = {nid: ndata for nid, ndata in nodes.items()
                      if nid.startswith("SCR-") or nid.startswith("TOOL-")}

    print(f"Total nodes: {len(nodes)}")
    print(f"SOP nodes: {len(sop_nodes)}")
    print(f"SCR/TOOL nodes: {len(scr_tool_nodes)}")

    # =========================================================================
    # TASK 1 & 2: Audit ALL edges in the entire network
    # =========================================================================
    total_edges = 0
    classified_edges = 0
    unclassified_edges = 0
    violations = []  # weight/strength mismatches
    all_edge_audit = []

    for nid, ndata in nodes.items():
        for ptr in ndata.get("pointers", []):
            total_edges += 1
            weight = ptr.get("weight", 0)
            strength = ptr.get("strength")
            target = ptr["to"]

            if strength:
                classified_edges += 1
                exp = expected_strength(weight)
                if strength != exp:
                    violations.append({
                        "from": nid,
                        "to": target,
                        "weight": weight,
                        "actual_strength": strength,
                        "expected_strength": exp
                    })
            else:
                unclassified_edges += 1

    print(f"\n=== EDGE AUDIT ===")
    print(f"Total edges: {total_edges}")
    print(f"Classified: {classified_edges}")
    print(f"Unclassified: {unclassified_edges}")
    print(f"Violations (strength != expected for weight): {len(violations)}")

    if violations:
        print("\nViolations:")
        for v in violations[:50]:  # show first 50
            print(f"  {v['from']} -> {v['to']}: weight={v['weight']}, "
                  f"strength={v['actual_strength']}, expected={v['expected_strength']}")
        if len(violations) > 50:
            print(f"  ... and {len(violations) - 50} more")

    # =========================================================================
    # TASK 1: Per-SOP audit — primary pointer to SCR/TOOL
    # =========================================================================
    sop_audit = {}
    sops_missing_primary = []
    sops_with_primary = []

    for sop_id in sorted(sop_nodes.keys()):
        sop_data = sop_nodes[sop_id]
        pointers = sop_data.get("pointers", [])
        tags = set(sop_data.get("tags", []))

        # Find primary pointers that target SCR/TOOL nodes
        primary_to_scr_tool = []
        all_primaries = []
        for ptr in pointers:
            if ptr.get("strength") == "primary":
                all_primaries.append(ptr)
                if ptr["to"].startswith("SCR-") or ptr["to"].startswith("TOOL-"):
                    primary_to_scr_tool.append(ptr)

        # Strength distribution for this SOP
        strength_dist = defaultdict(int)
        for ptr in pointers:
            s = ptr.get("strength", "unclassified")
            strength_dist[s] += 1

        # Check weight/strength consistency for this SOP's edges
        sop_violations = [v for v in violations if v["from"] == sop_id]

        has_primary_scr = len(primary_to_scr_tool) > 0
        if has_primary_scr:
            sops_with_primary.append(sop_id)
        else:
            sops_missing_primary.append(sop_id)

        sop_audit[sop_id] = {
            "tags": list(tags),
            "total_pointers": len(pointers),
            "strength_distribution": dict(strength_dist),
            "has_primary_to_scr_tool": has_primary_scr,
            "primary_targets_scr_tool": [p["to"] for p in primary_to_scr_tool],
            "all_primary_targets": [p["to"] for p in all_primaries],
            "violations": sop_violations,
            "violation_count": len(sop_violations)
        }

    print(f"\n=== SOP AUDIT ===")
    print(f"SOPs with primary -> SCR/TOOL: {len(sops_with_primary)}")
    print(f"SOPs MISSING primary -> SCR/TOOL: {len(sops_missing_primary)}")
    if sops_missing_primary:
        print(f"  Missing: {sops_missing_primary}")

    # =========================================================================
    # TASK 3: Tag relevance check — do primary targets share tags with SOP?
    # =========================================================================
    tag_relevance = {}
    for sop_id, audit in sop_audit.items():
        sop_tags = set(audit["tags"])
        target_checks = []
        for target_id in audit["primary_targets_scr_tool"]:
            target_node = nodes.get(target_id, {})
            target_tags = set(target_node.get("tags", []))
            shared = sop_tags & target_tags
            target_checks.append({
                "target": target_id,
                "target_tags": list(target_tags),
                "shared_tags": list(shared),
                "shared_count": len(shared),
                "relevant": len(shared) > 0
            })
        tag_relevance[sop_id] = {
            "sop_tags": list(sop_tags),
            "primary_target_checks": target_checks,
            "all_relevant": all(tc["relevant"] for tc in target_checks) if target_checks else False,
            "any_relevant": any(tc["relevant"] for tc in target_checks) if target_checks else False
        }

    irrelevant_primaries = []
    for sop_id, tr in tag_relevance.items():
        for tc in tr["primary_target_checks"]:
            if not tc["relevant"]:
                irrelevant_primaries.append({
                    "sop": sop_id,
                    "target": tc["target"],
                    "sop_tags": tr["sop_tags"],
                    "target_tags": tc["target_tags"]
                })

    print(f"\n=== TAG RELEVANCE ===")
    print(f"Primary pointers with NO shared tags (irrelevant): {len(irrelevant_primaries)}")
    for ip in irrelevant_primaries:
        print(f"  {ip['sop']} -> {ip['target']}")
        print(f"    SOP tags: {ip['sop_tags']}")
        print(f"    Target tags: {ip['target_tags']}")

    # =========================================================================
    # TASK 4: Log routes via route_tracker
    # =========================================================================
    print(f"\n=== LOGGING ROUTES ===")
    agent_counter = 0
    sop_list = sorted(sop_audit.keys())
    # Distribute SOPs across agents a1-1 through a1-6
    for i, sop_id in enumerate(sop_list):
        agent_num = (i % 6) + 1
        agent_id = f"a1-{agent_num}"
        audit = sop_audit[sop_id]

        if audit["primary_targets_scr_tool"]:
            primary_target = audit["primary_targets_scr_tool"][0]
            route_str = f"{sop_id} -> {primary_target}"
            tr = tag_relevance[sop_id]
            relevant_checks = tr["primary_target_checks"]
            first_check = relevant_checks[0] if relevant_checks else {}
            shared = first_check.get("shared_count", 0)

            if audit["violation_count"] > 0:
                outcome = "failure"
                notes = (f"has_primary=true, violations={audit['violation_count']}, "
                         f"shared_tags={shared}")
            elif not first_check.get("relevant", False):
                outcome = "partial"
                notes = f"has_primary=true, no_shared_tags, target={primary_target}"
            else:
                outcome = "success"
                notes = (f"has_primary=true, shared_tags={shared}, "
                         f"strengths={audit['strength_distribution']}")
        else:
            # No primary to SCR/TOOL
            if audit["all_primary_targets"]:
                primary_target = audit["all_primary_targets"][0]
            else:
                # Find highest weight pointer
                pointers = nodes[sop_id].get("pointers", [])
                if pointers:
                    best = max(pointers, key=lambda p: p.get("weight", 0))
                    primary_target = best["to"]
                else:
                    primary_target = "NONE"
            route_str = f"{sop_id} -> {primary_target}"
            outcome = "failure"
            notes = f"MISSING primary->SCR/TOOL, all_primaries={audit['all_primary_targets']}"

        log_route(agent_id, "strength-audit-v2", route_str, outcome, notes)
        print(f"  [{agent_id}] {route_str} -> {outcome}")

    # =========================================================================
    # TASK 5: Write report
    # =========================================================================
    # Violation summary by type
    violation_summary = defaultdict(int)
    for v in violations:
        key = f"{v['actual_strength']}_should_be_{v['expected_strength']}"
        violation_summary[key] += 1

    report = {
        "report_id": "benchmark_a1_report",
        "generated": datetime.utcnow().isoformat() + "Z",
        "team": "A1",
        "task": "strength-audit-v2",
        "description": "Re-audit of ALL strength classifications in pointer network after run-001 repairs",
        "network_stats": {
            "total_nodes": len(nodes),
            "sop_nodes": len(sop_nodes),
            "scr_tool_nodes": len(scr_tool_nodes),
            "total_edges": total_edges,
            "classified_edges": classified_edges,
            "unclassified_edges": unclassified_edges,
            "classification_rate": round(classified_edges / max(total_edges, 1) * 100, 2)
        },
        "strength_audit": {
            "total_violations": len(violations),
            "violation_summary": dict(violation_summary),
            "violations": violations
        },
        "sop_audit": {
            "total_sops": len(sop_nodes),
            "sops_with_primary_to_scr_tool": len(sops_with_primary),
            "sops_missing_primary_to_scr_tool": len(sops_missing_primary),
            "missing_list": sops_missing_primary,
            "per_sop": sop_audit
        },
        "tag_relevance": {
            "irrelevant_primary_pointers": len(irrelevant_primaries),
            "details": irrelevant_primaries,
            "per_sop": tag_relevance
        },
        "route_logging": {
            "agents_used": ["a1-1", "a1-2", "a1-3", "a1-4", "a1-5", "a1-6"],
            "total_routes_logged": len(sop_list),
            "task": "strength-audit-v2"
        },
        "verdict": {
            "all_sops_have_primary_scr_tool": len(sops_missing_primary) == 0,
            "zero_weight_strength_violations": len(violations) == 0,
            "all_primaries_tag_relevant": len(irrelevant_primaries) == 0,
            "overall": "PASS" if (len(sops_missing_primary) == 0 and
                                   len(violations) == 0 and
                                   len(irrelevant_primaries) == 0) else "FAIL"
        }
    }

    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== VERDICT ===")
    print(f"Overall: {report['verdict']['overall']}")
    print(f"  All SOPs have primary->SCR/TOOL: {report['verdict']['all_sops_have_primary_scr_tool']}")
    print(f"  Zero weight/strength violations: {report['verdict']['zero_weight_strength_violations']}")
    print(f"  All primaries tag-relevant: {report['verdict']['all_primaries_tag_relevant']}")
    print(f"\nReport written to: {REPORT_PATH}")

if __name__ == "__main__":
    main()
