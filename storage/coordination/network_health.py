#!/usr/bin/env python3
"""
Pointer Network Health Check — Continuous monitoring and pass/fail diagnostics.

Computes six core health metrics for the pointer network:
  1. Connectivity Score  — based on components, diameter, bridge edges
  2. Orphan Rate         — nodes with zero incoming pointers
  3. Strength Coverage   — % of edges with strength classification
  4. Route Success Rate  — from route_log.jsonl history
  5. Path Efficiency     — average hops to reach useful resources
  6. Tag Coherence Score — semantic coherence across edges

Produces a structured JSON report with pass/fail verdicts and alerts.

Usage:
    python3 network_health.py                # Full health check, print report
    python3 network_health.py --json         # Output JSON report to stdout
    python3 network_health.py --output FILE  # Write JSON report to FILE
    python3 network_health.py --watch        # Re-run every 60s
    python3 network_health.py --dashboard    # Compact single-line summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = Path(__file__).parent
_STORAGE_DIR = _SCRIPT_DIR.parent
POINTER_NETWORK = _STORAGE_DIR / "pointer-network.json"
ROUTES_DIR = _STORAGE_DIR / "pointer-routes"
ROUTES_LOG = ROUTES_DIR / "route_log.jsonl"
HEALTH_REPORT = ROUTES_DIR / "health_report.json"

# ---------------------------------------------------------------------------
# Thresholds — alerting rules
# ---------------------------------------------------------------------------

THRESHOLDS = {
    # Connectivity
    "max_components": 1,              # Network must be single component
    "max_diameter": 6,                # Diameter above this is WARN
    "critical_diameter": 8,           # Diameter above this is FAIL
    "max_bridge_fraction": 0.05,      # Bridge edges > 5% of total is WARN

    # Orphan rate
    "max_orphan_rate": 0.15,          # >15% orphans is WARN
    "critical_orphan_rate": 0.25,     # >25% orphans is FAIL

    # Strength coverage
    "min_strength_coverage": 0.60,    # <60% classified is WARN
    "critical_strength_coverage": 0.40,  # <40% classified is FAIL

    # Route success
    "min_success_rate": 0.70,         # <70% success rate is WARN
    "critical_success_rate": 0.50,    # <50% is FAIL
    "min_routes_for_eval": 10,        # Need at least 10 routes to evaluate

    # Path efficiency
    "max_avg_hops": 4.0,              # Average hops > 4 is WARN
    "critical_avg_hops": 6.0,         # Average hops > 6 is FAIL

    # Tag coherence
    "min_tag_coherence": 0.25,        # <25% average Jaccard is WARN
    "critical_tag_coherence": 0.10,   # <10% is FAIL

    # Edge growth
    "max_new_unclassified_fraction": 0.20,  # >20% of new edges unclassified is WARN
}


# ---------------------------------------------------------------------------
# Network loading
# ---------------------------------------------------------------------------

def load_pointer_network() -> dict:
    """Load the pointer network JSON."""
    if not POINTER_NETWORK.exists():
        return {"nodes": {}, "stats": {}}
    with open(POINTER_NETWORK) as f:
        return json.load(f)


def load_route_log() -> list[dict]:
    """Load all route log entries."""
    if not ROUTES_LOG.exists():
        return []
    routes = []
    with open(ROUTES_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    routes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return routes


# ---------------------------------------------------------------------------
# Metric 1: Network Connectivity Score
# ---------------------------------------------------------------------------

def compute_connectivity(pn: dict) -> dict:
    """
    Compute connectivity metrics:
    - Number of weakly connected components (treat directed as undirected)
    - Approximate diameter via BFS from sampled nodes
    - Bridge edge detection (edges whose removal disconnects a component)
    """
    nodes = pn.get("nodes", {})
    if not nodes:
        return {
            "components": 0, "diameter": 0, "bridge_count": 0,
            "bridge_fraction": 0.0, "score": 0.0, "status": "FAIL",
            "detail": "No nodes in network"
        }

    # Build undirected adjacency
    adj = defaultdict(set)
    all_edges = set()
    for node_id, node_data in nodes.items():
        for ptr in node_data.get("pointers", []):
            target = ptr["to"]
            adj[node_id].add(target)
            adj[target].add(node_id)
            edge = tuple(sorted([node_id, target]))
            all_edges.add(edge)

    all_node_ids = set(nodes.keys())
    # Include targets that may not be top-level nodes
    for node_id, node_data in nodes.items():
        for ptr in node_data.get("pointers", []):
            all_node_ids.add(ptr["to"])

    # Connected components via BFS
    visited = set()
    components = []
    for start in all_node_ids:
        if start in visited:
            continue
        component = set()
        queue = [start]
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)

    num_components = len(components)

    # Approximate diameter: BFS from up to 10 sampled nodes
    def bfs_distances(start):
        dist = {start: 0}
        queue = [start]
        while queue:
            node = queue.pop(0)
            for neighbor in adj.get(node, []):
                if neighbor not in dist:
                    dist[neighbor] = dist[node] + 1
                    queue.append(neighbor)
        return dist

    # Sample nodes from the largest component
    largest = max(components, key=len)
    sample_nodes = list(largest)[:10]
    max_dist = 0
    for s in sample_nodes:
        dists = bfs_distances(s)
        if dists:
            max_dist = max(max_dist, max(dists.values()))

    diameter = max_dist

    # Bridge detection: simplified — check edges in the largest component
    # An edge is a bridge if removing it increases component count
    # For efficiency, use DFS-based bridge detection on undirected graph
    bridge_count = 0
    if len(largest) <= 500:  # Only compute for manageable sizes
        bridge_count = _count_bridges(adj, largest)
    bridge_fraction = bridge_count / max(len(all_edges), 1)

    # Score: 1.0 = perfect, 0.0 = terrible
    score = 1.0
    if num_components > 1:
        score -= 0.3 * min(num_components - 1, 3)
    if diameter > THRESHOLDS["max_diameter"]:
        score -= 0.1 * (diameter - THRESHOLDS["max_diameter"])
    if bridge_fraction > THRESHOLDS["max_bridge_fraction"]:
        score -= 0.2
    score = max(0.0, min(1.0, score))

    # Status
    if num_components > THRESHOLDS["max_components"]:
        status = "FAIL"
    elif diameter > THRESHOLDS["critical_diameter"]:
        status = "FAIL"
    elif diameter > THRESHOLDS["max_diameter"]:
        status = "WARN"
    elif bridge_fraction > THRESHOLDS["max_bridge_fraction"]:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "components": num_components,
        "component_sizes": sorted([len(c) for c in components], reverse=True),
        "diameter": diameter,
        "bridge_count": bridge_count,
        "bridge_fraction": round(bridge_fraction, 4),
        "total_nodes": len(all_node_ids),
        "total_edges": len(all_edges),
        "score": round(score, 3),
        "status": status,
    }


def _count_bridges(adj: dict, component: set) -> int:
    """Count bridge edges using Tarjan's bridge-finding algorithm."""
    disc = {}
    low = {}
    timer = [0]
    bridges = [0]
    visited = set()

    def dfs(u, parent):
        visited.add(u)
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        for v in adj.get(u, []):
            if v not in component:
                continue
            if v not in visited:
                dfs(v, u)
                low[u] = min(low[u], low[v])
                if low[v] > disc[u]:
                    bridges[0] += 1
            elif v != parent:
                low[u] = min(low[u], disc[v])

    # Start from arbitrary node in component
    start = next(iter(component))
    sys.setrecursionlimit(max(sys.getrecursionlimit(), len(component) + 100))
    try:
        dfs(start, None)
    except RecursionError:
        # Fall back: too deep for recursive approach
        return -1  # Indicates computation failed

    return bridges[0]


# ---------------------------------------------------------------------------
# Metric 2: Orphan Rate
# ---------------------------------------------------------------------------

def compute_orphan_rate(pn: dict) -> dict:
    """
    Compute orphan rate: nodes that have no incoming pointers.
    A high orphan rate means many nodes are unreachable from the network.
    """
    nodes = pn.get("nodes", {})
    if not nodes:
        return {"orphan_count": 0, "total_nodes": 0, "rate": 0.0,
                "status": "FAIL", "detail": "No nodes"}

    # Count incoming pointers per node
    incoming = Counter()
    all_node_ids = set(nodes.keys())

    for node_id, node_data in nodes.items():
        for ptr in node_data.get("pointers", []):
            target = ptr["to"]
            incoming[target] += 1
            all_node_ids.add(target)

    # Orphans = nodes with zero incoming
    orphan_count = 0
    orphan_list = []
    for node_id in all_node_ids:
        if incoming[node_id] == 0:
            orphan_count += 1
            if len(orphan_list) < 20:
                orphan_list.append(node_id)

    total = len(all_node_ids)
    rate = orphan_count / max(total, 1)

    if rate > THRESHOLDS["critical_orphan_rate"]:
        status = "FAIL"
    elif rate > THRESHOLDS["max_orphan_rate"]:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "orphan_count": orphan_count,
        "total_nodes": total,
        "rate": round(rate, 4),
        "orphan_sample": sorted(orphan_list)[:10],
        "status": status,
    }


# ---------------------------------------------------------------------------
# Metric 3: Strength Coverage
# ---------------------------------------------------------------------------

def compute_strength_coverage(pn: dict) -> dict:
    """
    Compute what fraction of edges have a strength classification
    (primary, supporting, related, tangential) vs unclassified/missing.
    """
    nodes = pn.get("nodes", {})
    total_edges = 0
    classified = 0
    by_strength = Counter()

    for node_id, node_data in nodes.items():
        for ptr in node_data.get("pointers", []):
            total_edges += 1
            strength = ptr.get("strength", "unclassified")
            by_strength[strength] += 1
            if strength in ("primary", "supporting", "related", "tangential"):
                classified += 1

    coverage = classified / max(total_edges, 1)

    if coverage < THRESHOLDS["critical_strength_coverage"]:
        status = "FAIL"
    elif coverage < THRESHOLDS["min_strength_coverage"]:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "total_edges": total_edges,
        "classified": classified,
        "unclassified": total_edges - classified,
        "coverage": round(coverage, 4),
        "by_strength": dict(by_strength.most_common()),
        "status": status,
    }


# ---------------------------------------------------------------------------
# Metric 4: Route Success Rate
# ---------------------------------------------------------------------------

def compute_route_success(routes: list[dict]) -> dict:
    """
    Compute success rate from route logs.
    """
    if len(routes) < THRESHOLDS["min_routes_for_eval"]:
        return {
            "total_routes": len(routes),
            "success_count": 0,
            "success_rate": None,
            "status": "SKIP",
            "detail": f"Insufficient routes ({len(routes)} < {THRESHOLDS['min_routes_for_eval']})"
        }

    outcomes = Counter(r.get("outcome", "unknown") for r in routes)
    success = outcomes.get("success", 0)
    rate = success / max(len(routes), 1)

    # Recent trend: last 20 routes
    recent = routes[-20:]
    recent_outcomes = Counter(r.get("outcome", "unknown") for r in recent)
    recent_success = recent_outcomes.get("success", 0)
    recent_rate = recent_success / max(len(recent), 1)

    if rate < THRESHOLDS["critical_success_rate"]:
        status = "FAIL"
    elif rate < THRESHOLDS["min_success_rate"]:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "total_routes": len(routes),
        "success_count": success,
        "success_rate": round(rate, 4),
        "outcome_distribution": dict(outcomes),
        "recent_20_success_rate": round(recent_rate, 4),
        "trend": "improving" if recent_rate > rate else (
            "declining" if recent_rate < rate - 0.05 else "stable"),
        "status": status,
    }


# ---------------------------------------------------------------------------
# Metric 5: Path Efficiency
# ---------------------------------------------------------------------------

def compute_path_efficiency(pn: dict, routes: list[dict]) -> dict:
    """
    Average hops to reach useful resources (SCR/SRC nodes) from SOP nodes.
    Uses both:
    - Static analysis: BFS from SOP nodes to SCR/SRC
    - Dynamic analysis: actual route log hops
    """
    nodes = pn.get("nodes", {})

    # Static: BFS from each SOP to nearest SCR/SRC
    adj = defaultdict(list)
    for node_id, node_data in nodes.items():
        for ptr in node_data.get("pointers", []):
            adj[node_id].append((ptr["to"], ptr.get("weight", 0)))

    sop_nodes = [n for n in nodes if n.startswith("SOP-")]
    resource_prefixes = ("SCR-", "SRC-")

    hop_counts = []
    for sop in sop_nodes:
        # BFS for shortest path to any resource
        visited = {sop}
        queue = [(sop, 0)]
        found = False
        while queue:
            current, hops = queue.pop(0)
            if hops > 0 and any(current.startswith(p) for p in resource_prefixes):
                hop_counts.append(hops)
                found = True
                break
            for target, _ in adj.get(current, []):
                if target not in visited:
                    visited.add(target)
                    queue.append((target, hops + 1))
        if not found:
            hop_counts.append(float("inf"))

    finite_hops = [h for h in hop_counts if h != float("inf")]
    static_avg = sum(finite_hops) / max(len(finite_hops), 1) if finite_hops else 0

    # Dynamic: from route logs
    dynamic_hops = [r.get("hop_count", 0) for r in routes if r.get("outcome") == "success"]
    dynamic_avg = sum(dynamic_hops) / max(len(dynamic_hops), 1) if dynamic_hops else 0

    avg_hops = static_avg if not dynamic_hops else (static_avg + dynamic_avg) / 2

    if avg_hops > THRESHOLDS["critical_avg_hops"]:
        status = "FAIL"
    elif avg_hops > THRESHOLDS["max_avg_hops"]:
        status = "WARN"
    else:
        status = "PASS"

    return {
        "static_avg_hops_sop_to_resource": round(static_avg, 2),
        "dynamic_avg_hops_on_success": round(dynamic_avg, 2),
        "combined_avg_hops": round(avg_hops, 2),
        "sop_count": len(sop_nodes),
        "unreachable_sops": len([h for h in hop_counts if h == float("inf")]),
        "successful_routes_sampled": len(dynamic_hops),
        "status": status,
    }


# ---------------------------------------------------------------------------
# Metric 6: Tag Coherence Score
# ---------------------------------------------------------------------------

def compute_tag_coherence(pn: dict) -> dict:
    """
    Measure tag coherence across edges: for each edge, compute Jaccard
    similarity of the tags between source and target nodes.
    High coherence = edges connect semantically related nodes.
    """
    nodes = pn.get("nodes", {})

    def get_tags(node_id: str) -> set:
        node = nodes.get(node_id, {})
        return set(node.get("tags", []))

    coherence_scores = []
    by_strength = defaultdict(list)

    for node_id, node_data in nodes.items():
        src_tags = get_tags(node_id)
        for ptr in node_data.get("pointers", []):
            target = ptr["to"]
            dst_tags = get_tags(target)
            if src_tags or dst_tags:
                union = src_tags | dst_tags
                intersection = src_tags & dst_tags
                jaccard = len(intersection) / len(union) if union else 0.0
            else:
                jaccard = 0.0
            coherence_scores.append(jaccard)
            strength = ptr.get("strength", "unclassified")
            by_strength[strength].append(jaccard)

    if not coherence_scores:
        return {"avg_coherence": 0.0, "status": "FAIL", "detail": "No edges"}

    avg = sum(coherence_scores) / len(coherence_scores)

    # Per-strength averages
    strength_avgs = {}
    for strength, scores in by_strength.items():
        strength_avgs[strength] = round(sum(scores) / len(scores), 4) if scores else 0.0

    if avg < THRESHOLDS["critical_tag_coherence"]:
        status = "FAIL"
    elif avg < THRESHOLDS["min_tag_coherence"]:
        status = "WARN"
    else:
        status = "PASS"

    # Edges with zero coherence
    zero_coherence = sum(1 for s in coherence_scores if s == 0.0)

    return {
        "avg_coherence": round(avg, 4),
        "median_coherence": round(sorted(coherence_scores)[len(coherence_scores) // 2], 4),
        "zero_coherence_edges": zero_coherence,
        "zero_coherence_fraction": round(zero_coherence / max(len(coherence_scores), 1), 4),
        "by_strength": strength_avgs,
        "total_edges_evaluated": len(coherence_scores),
        "status": status,
    }


# ---------------------------------------------------------------------------
# Alerting Engine
# ---------------------------------------------------------------------------

def generate_alerts(metrics: dict) -> list[dict]:
    """
    Generate alert objects based on metric results and thresholds.
    Each alert has: severity (CRITICAL, WARNING, INFO), metric, message.
    """
    alerts = []

    def alert(severity: str, metric: str, message: str):
        alerts.append({"severity": severity, "metric": metric, "message": message})

    # Connectivity alerts
    conn = metrics.get("connectivity", {})
    if conn.get("components", 1) > 1:
        alert("CRITICAL", "connectivity",
              f"Network has {conn['components']} disconnected components (expected 1)")
    if conn.get("diameter", 0) > THRESHOLDS["critical_diameter"]:
        alert("CRITICAL", "connectivity",
              f"Network diameter {conn['diameter']} exceeds critical threshold {THRESHOLDS['critical_diameter']}")
    elif conn.get("diameter", 0) > THRESHOLDS["max_diameter"]:
        alert("WARNING", "connectivity",
              f"Network diameter {conn['diameter']} exceeds warning threshold {THRESHOLDS['max_diameter']}")
    if conn.get("bridge_fraction", 0) > THRESHOLDS["max_bridge_fraction"]:
        alert("WARNING", "connectivity",
              f"Bridge edge fraction {conn['bridge_fraction']:.1%} — single-point-of-failure risk")

    # Orphan alerts
    orphan = metrics.get("orphan_rate", {})
    if orphan.get("rate", 0) > THRESHOLDS["critical_orphan_rate"]:
        alert("CRITICAL", "orphan_rate",
              f"Orphan rate {orphan['rate']:.1%} exceeds critical threshold "
              f"({THRESHOLDS['critical_orphan_rate']:.0%}). "
              f"{orphan.get('orphan_count', 0)} nodes have no incoming pointers")
    elif orphan.get("rate", 0) > THRESHOLDS["max_orphan_rate"]:
        alert("WARNING", "orphan_rate",
              f"Orphan rate {orphan['rate']:.1%} exceeds warning threshold "
              f"({THRESHOLDS['max_orphan_rate']:.0%})")

    # Strength coverage alerts
    strength = metrics.get("strength_coverage", {})
    if strength.get("coverage", 1) < THRESHOLDS["critical_strength_coverage"]:
        alert("CRITICAL", "strength_coverage",
              f"Only {strength['coverage']:.1%} of edges classified — "
              f"{strength.get('unclassified', 0)} unclassified edges")
    elif strength.get("coverage", 1) < THRESHOLDS["min_strength_coverage"]:
        alert("WARNING", "strength_coverage",
              f"Strength coverage {strength['coverage']:.1%} below target "
              f"({THRESHOLDS['min_strength_coverage']:.0%})")

    # Route success alerts
    route = metrics.get("route_success", {})
    if route.get("status") != "SKIP":
        if route.get("success_rate", 1) < THRESHOLDS["critical_success_rate"]:
            alert("CRITICAL", "route_success",
                  f"Route success rate {route['success_rate']:.1%} below critical threshold")
        elif route.get("success_rate", 1) < THRESHOLDS["min_success_rate"]:
            alert("WARNING", "route_success",
                  f"Route success rate {route['success_rate']:.1%} below target "
                  f"({THRESHOLDS['min_success_rate']:.0%})")
        if route.get("trend") == "declining":
            alert("WARNING", "route_success",
                  f"Route success trend is declining (recent: {route.get('recent_20_success_rate', 0):.1%})")

    # Path efficiency alerts
    eff = metrics.get("path_efficiency", {})
    if eff.get("combined_avg_hops", 0) > THRESHOLDS["critical_avg_hops"]:
        alert("CRITICAL", "path_efficiency",
              f"Average path {eff['combined_avg_hops']:.1f} hops exceeds critical threshold")
    elif eff.get("combined_avg_hops", 0) > THRESHOLDS["max_avg_hops"]:
        alert("WARNING", "path_efficiency",
              f"Average path {eff['combined_avg_hops']:.1f} hops exceeds warning threshold")
    if eff.get("unreachable_sops", 0) > 0:
        alert("CRITICAL", "path_efficiency",
              f"{eff['unreachable_sops']} SOP nodes cannot reach any resource")

    # Tag coherence alerts
    tag = metrics.get("tag_coherence", {})
    if tag.get("avg_coherence", 1) < THRESHOLDS["critical_tag_coherence"]:
        alert("CRITICAL", "tag_coherence",
              f"Tag coherence {tag['avg_coherence']:.3f} below critical threshold")
    elif tag.get("avg_coherence", 1) < THRESHOLDS["min_tag_coherence"]:
        alert("WARNING", "tag_coherence",
              f"Tag coherence {tag['avg_coherence']:.3f} below target "
              f"({THRESHOLDS['min_tag_coherence']})")
    if tag.get("zero_coherence_fraction", 0) > 0.5:
        alert("WARNING", "tag_coherence",
              f"{tag['zero_coherence_fraction']:.1%} of edges have zero tag overlap")

    return alerts


# ---------------------------------------------------------------------------
# Overall Verdict
# ---------------------------------------------------------------------------

def compute_verdict(metrics: dict, alerts: list[dict]) -> dict:
    """Compute overall pass/fail verdict from individual metrics."""
    statuses = []
    for key, val in metrics.items():
        if isinstance(val, dict) and "status" in val:
            statuses.append(val["status"])

    critical_count = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    warning_count = sum(1 for a in alerts if a["severity"] == "WARNING")

    if critical_count > 0 or "FAIL" in statuses:
        overall = "FAIL"
    elif warning_count > 0 or "WARN" in statuses:
        overall = "WARN"
    else:
        overall = "PASS"

    # Health score: weighted average of individual scores
    weights = {
        "connectivity": 0.25,
        "strength_coverage": 0.20,
        "route_success": 0.20,
        "path_efficiency": 0.15,
        "tag_coherence": 0.10,
        "orphan_rate": 0.10,
    }

    health_score = 0.0
    total_weight = 0.0
    for metric_name, weight in weights.items():
        m = metrics.get(metric_name, {})
        if m.get("status") == "SKIP":
            continue
        if "score" in m:
            health_score += m["score"] * weight
            total_weight += weight
        elif "coverage" in m:
            health_score += m["coverage"] * weight
            total_weight += weight
        elif "success_rate" in m and m["success_rate"] is not None:
            health_score += m["success_rate"] * weight
            total_weight += weight
        elif "rate" in m:
            # Orphan rate: lower is better
            health_score += (1.0 - m["rate"]) * weight
            total_weight += weight
        elif "avg_coherence" in m:
            health_score += min(m["avg_coherence"] / 0.5, 1.0) * weight
            total_weight += weight

    if total_weight > 0:
        health_score = health_score / total_weight

    return {
        "overall": overall,
        "health_score": round(health_score, 3),
        "critical_alerts": critical_count,
        "warning_alerts": warning_count,
        "metrics_pass": statuses.count("PASS"),
        "metrics_warn": statuses.count("WARN"),
        "metrics_fail": statuses.count("FAIL"),
        "metrics_skip": statuses.count("SKIP"),
    }


# ---------------------------------------------------------------------------
# Full Health Check
# ---------------------------------------------------------------------------

def run_health_check() -> dict:
    """Run all health metrics and produce a complete report."""
    pn = load_pointer_network()
    routes = load_route_log()

    metrics = {
        "connectivity": compute_connectivity(pn),
        "orphan_rate": compute_orphan_rate(pn),
        "strength_coverage": compute_strength_coverage(pn),
        "route_success": compute_route_success(routes),
        "path_efficiency": compute_path_efficiency(pn, routes),
        "tag_coherence": compute_tag_coherence(pn),
    }

    alerts = generate_alerts(metrics)
    verdict = compute_verdict(metrics, alerts)

    report = {
        "generated": datetime.now(tz=timezone.utc).isoformat(),
        "pointer_network_file": str(POINTER_NETWORK),
        "route_log_file": str(ROUTES_LOG),
        "verdict": verdict,
        "metrics": metrics,
        "alerts": alerts,
        "thresholds": THRESHOLDS,
    }

    return report


# ---------------------------------------------------------------------------
# Output Formatting
# ---------------------------------------------------------------------------

def format_text_report(report: dict) -> str:
    """Format the health report as human-readable text."""
    lines = []
    v = report["verdict"]
    ts = report["generated"]

    lines.append("=" * 60)
    lines.append("  POINTER NETWORK HEALTH CHECK")
    lines.append(f"  Generated: {ts}")
    lines.append(f"  Verdict:   {v['overall']}  (score: {v['health_score']:.1%})")
    lines.append("=" * 60)
    lines.append("")

    # Metrics summary
    metrics = report["metrics"]
    metric_labels = {
        "connectivity": "Connectivity",
        "orphan_rate": "Orphan Rate",
        "strength_coverage": "Strength Coverage",
        "route_success": "Route Success",
        "path_efficiency": "Path Efficiency",
        "tag_coherence": "Tag Coherence",
    }

    lines.append("  METRICS:")
    for key, label in metric_labels.items():
        m = metrics.get(key, {})
        status = m.get("status", "?")
        detail = ""
        if key == "connectivity":
            detail = f"components={m.get('components', '?')}, diameter={m.get('diameter', '?')}"
        elif key == "orphan_rate":
            detail = f"{m.get('orphan_count', '?')}/{m.get('total_nodes', '?')} ({m.get('rate', 0):.1%})"
        elif key == "strength_coverage":
            detail = f"{m.get('classified', '?')}/{m.get('total_edges', '?')} ({m.get('coverage', 0):.1%})"
        elif key == "route_success":
            if m.get("success_rate") is not None:
                detail = f"{m.get('success_count', '?')}/{m.get('total_routes', '?')} ({m['success_rate']:.1%})"
            else:
                detail = m.get("detail", "n/a")
        elif key == "path_efficiency":
            detail = f"avg={m.get('combined_avg_hops', '?')} hops"
        elif key == "tag_coherence":
            detail = f"avg={m.get('avg_coherence', '?')}"

        indicator = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}.get(status, "[????]")
        lines.append(f"    {indicator} {label:<22} {detail}")

    lines.append("")

    # Alerts
    alerts = report.get("alerts", [])
    if alerts:
        lines.append("  ALERTS:")
        for a in alerts:
            sev = a["severity"]
            lines.append(f"    [{sev}] {a['message']}")
        lines.append("")
    else:
        lines.append("  No alerts.")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def format_dashboard_line(report: dict) -> str:
    """Single-line dashboard summary for embedding in other dashboards."""
    v = report["verdict"]
    m = report["metrics"]

    parts = [
        f"NET-HEALTH: {v['overall']}",
        f"score={v['health_score']:.0%}",
        f"comp={m.get('connectivity', {}).get('components', '?')}",
        f"diam={m.get('connectivity', {}).get('diameter', '?')}",
        f"orphan={m.get('orphan_rate', {}).get('rate', 0):.0%}",
        f"strength={m.get('strength_coverage', {}).get('coverage', 0):.0%}",
    ]

    sr = m.get("route_success", {}).get("success_rate")
    if sr is not None:
        parts.append(f"routes={sr:.0%}")

    parts.append(f"hops={m.get('path_efficiency', {}).get('combined_avg_hops', '?')}")
    parts.append(f"coherence={m.get('tag_coherence', {}).get('avg_coherence', 0):.2f}")

    if v.get("critical_alerts", 0) > 0:
        parts.append(f"CRIT={v['critical_alerts']}")
    if v.get("warning_alerts", 0) > 0:
        parts.append(f"WARN={v['warning_alerts']}")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Integration helper for dashboard.py
# ---------------------------------------------------------------------------

def render_network_health_section() -> list[str]:
    """
    Render a dashboard section compatible with dashboard.py's render pattern.
    Returns a list of strings (lines) that can be appended to the dashboard output.

    Integration in dashboard.py:
        from network_health import render_network_health_section
        # In render_dashboard():
        sections.extend(render_network_health_section())
    """
    try:
        report = run_health_check()
    except Exception as e:
        return [
            "-" * 28 + "  NETWORK HEALTH  " + "-" * 26,
            f"  Error computing health: {e}",
            "",
        ]

    v = report["verdict"]
    m = report["metrics"]

    lines = [
        "-" * 28 + "  NETWORK HEALTH  " + "-" * 26,
        f"  Verdict: {v['overall']}  (health score: {v['health_score']:.1%})",
    ]

    # Compact metric summary
    conn = m.get("connectivity", {})
    lines.append(
        f"  Connectivity: {conn.get('status', '?')} "
        f"(components={conn.get('components', '?')}, "
        f"diameter={conn.get('diameter', '?')})"
    )

    orphan = m.get("orphan_rate", {})
    lines.append(
        f"  Orphans: {orphan.get('status', '?')} "
        f"({orphan.get('orphan_count', '?')}/{orphan.get('total_nodes', '?')} = "
        f"{orphan.get('rate', 0):.1%})"
    )

    strength = m.get("strength_coverage", {})
    lines.append(
        f"  Strength: {strength.get('status', '?')} "
        f"({strength.get('coverage', 0):.1%} classified)"
    )

    route = m.get("route_success", {})
    if route.get("success_rate") is not None:
        lines.append(
            f"  Routes: {route.get('status', '?')} "
            f"({route['success_rate']:.1%} success, trend={route.get('trend', '?')})"
        )

    eff = m.get("path_efficiency", {})
    lines.append(
        f"  Efficiency: {eff.get('status', '?')} "
        f"(avg {eff.get('combined_avg_hops', '?')} hops)"
    )

    tag = m.get("tag_coherence", {})
    lines.append(
        f"  Coherence: {tag.get('status', '?')} "
        f"(avg Jaccard={tag.get('avg_coherence', 0):.3f})"
    )

    # Show alerts if any
    alerts = report.get("alerts", [])
    critical = [a for a in alerts if a["severity"] == "CRITICAL"]
    if critical:
        lines.append(f"  (!) {len(critical)} critical alert(s):")
        for a in critical[:3]:
            msg = a["message"]
            if len(msg) > 60:
                msg = msg[:57] + "..."
            lines.append(f"      {msg}")

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Pointer Network Health Check",
    )
    parser.add_argument("--json", action="store_true",
                        help="Output JSON report")
    parser.add_argument("--output", type=str, default=None,
                        help="Write JSON report to file")
    parser.add_argument("--dashboard", action="store_true",
                        help="Single-line dashboard summary")
    parser.add_argument("--watch", action="store_true",
                        help="Re-run every 60 seconds")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="Watch interval in seconds")
    args = parser.parse_args()

    def run_once():
        report = run_health_check()

        if args.output:
            with open(args.output, "w") as f:
                json.dump(report, f, indent=2)
            print(f"Report written to {args.output}")

        if args.dashboard:
            print(format_dashboard_line(report))
        elif args.json:
            print(json.dumps(report, indent=2))
        else:
            print(format_text_report(report))

        return report

    if args.watch:
        try:
            while True:
                sys.stdout.write('\033[2J\033[H')
                sys.stdout.flush()
                run_once()
                print(f"\n  [Refreshing every {args.interval}s -- Ctrl+C to stop]")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        run_once()


if __name__ == "__main__":
    main()
