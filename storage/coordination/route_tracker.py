#!/usr/bin/env python3
"""
Agent Route Tracker — Records, analyzes, and stores pointer network traversal paths.

When agents navigate the pointer network to find tools, SOPs, or related resources,
this module logs the route taken. Over time, route analysis reveals:
  - Which pointer paths agents use most (high-traffic routes)
  - Which pointers are never followed (dead routes)
  - Common entry→exit patterns (route signatures)
  - Whether strength classifications match actual usage

Usage:
  # Record a route during agent execution
  python3 route_tracker.py log --agent "coding-team-1" --task "stress-test" \
    --route "SOP-015 -> SCR-0036 -> SCR-0037 -> KB-0042" --outcome "success"

  # Analyze route history
  python3 route_tracker.py analyze

  # Show top routes for a given entry point
  python3 route_tracker.py top --entry "SOP-027"

  # Export route statistics
  python3 route_tracker.py export
"""

import json
import os
import sys
import time
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime

ROUTES_DIR = Path(__file__).parent.parent / "pointer-routes"
ROUTES_LOG = ROUTES_DIR / "route_log.jsonl"
ROUTES_ANALYSIS = ROUTES_DIR / "route_analysis.json"
ROUTES_PATTERNS = ROUTES_DIR / "route_patterns.json"
POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"


def ensure_dirs():
    ROUTES_DIR.mkdir(parents=True, exist_ok=True)


def log_route(agent_id: str, task: str, route: list, outcome: str,
              session: str = None, notes: str = None):
    """Append a route traversal record to the route log."""
    ensure_dirs()

    # Build hop-level detail from pointer network
    hops = []
    try:
        with open(POINTER_NETWORK) as f:
            pn = json.load(f)
    except Exception:
        pn = {"nodes": {}}

    for i in range(len(route) - 1):
        src, dst = route[i], route[i + 1]
        # Find the pointer weight and strength
        weight = 0
        strength = "unknown"
        reasons = []
        node = pn.get("nodes", {}).get(src, {})
        for ptr in node.get("pointers", []):
            if ptr["to"] == dst:
                weight = ptr.get("weight", 0)
                strength = ptr.get("strength", "unclassified")
                reasons = ptr.get("reasons", [])
                break

        hops.append({
            "from": src,
            "to": dst,
            "weight": weight,
            "strength": strength,
            "reasons": reasons
        })

    record = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "epoch": time.time(),
        "agent_id": agent_id,
        "task": task,
        "route": route,
        "hops": hops,
        "hop_count": len(hops),
        "entry_node": route[0] if route else None,
        "exit_node": route[-1] if route else None,
        "outcome": outcome,
        "session": session,
        "notes": notes,
        "total_weight": sum(h["weight"] for h in hops),
        "strengths_used": [h["strength"] for h in hops]
    }

    with open(ROUTES_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    print(f"Route logged: {' -> '.join(route)} ({len(hops)} hops, outcome={outcome})")
    return record


def load_routes():
    """Load all route records from the log."""
    if not ROUTES_LOG.exists():
        return []
    routes = []
    with open(ROUTES_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                routes.append(json.loads(line))
    return routes


def analyze_routes():
    """Analyze all logged routes and produce statistics."""
    ensure_dirs()
    routes = load_routes()

    if not routes:
        print("No routes logged yet.")
        return {}

    # Edge frequency: how often each edge is traversed
    edge_freq = Counter()
    edge_outcomes = defaultdict(lambda: {"success": 0, "failure": 0, "partial": 0})
    node_entry_freq = Counter()
    node_exit_freq = Counter()
    strength_usage = Counter()
    route_signatures = Counter()  # entry->exit patterns
    agent_patterns = defaultdict(list)
    task_patterns = defaultdict(list)

    for r in routes:
        entry = r.get("entry_node", "?")
        exit_node = r.get("exit_node", "?")
        outcome = r.get("outcome", "unknown")

        node_entry_freq[entry] += 1
        node_exit_freq[exit_node] += 1
        route_signatures[f"{entry} -> {exit_node}"] += 1
        agent_patterns[r.get("agent_id", "unknown")].append(r)
        task_patterns[r.get("task", "unknown")].append(r)

        for hop in r.get("hops", []):
            edge_key = f"{hop['from']} -> {hop['to']}"
            edge_freq[edge_key] += 1
            edge_outcomes[edge_key][outcome] += 1
            strength_usage[hop.get("strength", "unknown")] += 1

    # Identify high-traffic and dead routes
    high_traffic = [(edge, count) for edge, count in edge_freq.most_common(20)]

    # Build analysis report
    analysis = {
        "generated": datetime.utcnow().isoformat() + "Z",
        "total_routes": len(routes),
        "total_hops": sum(r.get("hop_count", 0) for r in routes),
        "avg_hops_per_route": round(
            sum(r.get("hop_count", 0) for r in routes) / max(len(routes), 1), 2
        ),
        "outcome_distribution": dict(Counter(r.get("outcome", "unknown") for r in routes)),
        "strength_usage": dict(strength_usage),
        "top_entry_nodes": dict(node_entry_freq.most_common(15)),
        "top_exit_nodes": dict(node_exit_freq.most_common(15)),
        "top_edges": [
            {"edge": edge, "traversals": count, "outcomes": dict(edge_outcomes[edge])}
            for edge, count in high_traffic
        ],
        "route_signatures": dict(route_signatures.most_common(20)),
        "agents_tracked": len(agent_patterns),
        "tasks_tracked": len(task_patterns),
        "strength_effectiveness": {}
    }

    # Compute strength effectiveness: what % of each strength level led to success
    for strength_level in ["primary", "supporting", "related", "tangential"]:
        success = 0
        total = 0
        for r in routes:
            for i, hop in enumerate(r.get("hops", [])):
                if hop.get("strength") == strength_level:
                    total += 1
                    if r.get("outcome") == "success":
                        success += 1
        if total > 0:
            analysis["strength_effectiveness"][strength_level] = {
                "total_uses": total,
                "success_rate": round(success / total * 100, 1)
            }

    with open(ROUTES_ANALYSIS, "w") as f:
        json.dump(analysis, f, indent=2)

    print(f"Analysis complete: {len(routes)} routes, {analysis['total_hops']} hops")
    print(f"Strength usage: {dict(strength_usage)}")
    print(f"Top 5 edges:")
    for item in analysis["top_edges"][:5]:
        print(f"  {item['edge']}: {item['traversals']} traversals")

    return analysis


def extract_patterns():
    """Extract reusable route patterns from history."""
    ensure_dirs()
    routes = load_routes()

    if not routes:
        print("No routes to extract patterns from.")
        return {}

    # Group routes by task type
    task_routes = defaultdict(list)
    for r in routes:
        task_routes[r.get("task", "unknown")].append(r)

    patterns = {
        "generated": datetime.utcnow().isoformat() + "Z",
        "total_patterns": 0,
        "task_patterns": {}
    }

    for task, task_records in task_routes.items():
        # Find the most common route for this task
        route_strs = [" -> ".join(r["route"]) for r in task_records]
        common_routes = Counter(route_strs).most_common(3)

        successful = [r for r in task_records if r.get("outcome") == "success"]
        avg_weight = round(
            sum(r.get("total_weight", 0) for r in successful) / max(len(successful), 1), 1
        )

        patterns["task_patterns"][task] = {
            "total_attempts": len(task_records),
            "success_count": len(successful),
            "success_rate": round(len(successful) / max(len(task_records), 1) * 100, 1),
            "avg_weight_on_success": avg_weight,
            "common_routes": [
                {"route": route, "count": count}
                for route, count in common_routes
            ],
            "recommended_route": common_routes[0][0] if common_routes else None,
            "strengths_on_success": dict(Counter(
                s for r in successful
                for s in r.get("strengths_used", [])
            ))
        }
        patterns["total_patterns"] += 1

    with open(ROUTES_PATTERNS, "w") as f:
        json.dump(patterns, f, indent=2)

    print(f"Extracted {patterns['total_patterns']} task patterns")
    return patterns


def get_top_routes(entry_node: str):
    """Show top routes starting from a given entry node."""
    routes = load_routes()
    matching = [r for r in routes if r.get("entry_node") == entry_node]

    if not matching:
        print(f"No routes starting from {entry_node}")
        return []

    print(f"\nRoutes from {entry_node} ({len(matching)} total):")
    route_counter = Counter(" -> ".join(r["route"]) for r in matching)
    for route_str, count in route_counter.most_common(10):
        outcomes = Counter(
            r["outcome"] for r in matching
            if " -> ".join(r["route"]) == route_str
        )
        print(f"  [{count}x] {route_str}")
        print(f"         outcomes: {dict(outcomes)}")

    return matching


def recommend_route(task: str, entry_node: str = None):
    """Recommend a pointer route for a given task, based on history."""
    if ROUTES_PATTERNS.exists():
        with open(ROUTES_PATTERNS) as f:
            patterns = json.load(f)
        if task in patterns.get("task_patterns", {}):
            tp = patterns["task_patterns"][task]
            if tp.get("recommended_route"):
                print(f"Recommended route for '{task}': {tp['recommended_route']}")
                print(f"  (based on {tp['total_attempts']} attempts, "
                      f"{tp['success_rate']}% success rate)")
                return tp["recommended_route"]

    # Fallback: use pointer network strength to suggest a path
    if entry_node:
        try:
            with open(POINTER_NETWORK) as f:
                pn = json.load(f)
            node = pn.get("nodes", {}).get(entry_node, {})
            primary = [p for p in node.get("pointers", [])
                       if p.get("strength") == "primary"]
            supporting = [p for p in node.get("pointers", [])
                          if p.get("strength") == "supporting"]

            if primary:
                targets = [p["to"] for p in primary]
                print(f"No history for '{task}'. Primary tools from {entry_node}: {targets}")
                return targets
            elif supporting:
                targets = [p["to"] for p in supporting]
                print(f"No history for '{task}'. Supporting tools from {entry_node}: {targets}")
                return targets
        except Exception:
            pass

    print(f"No route recommendation available for task='{task}', entry='{entry_node}'")
    return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "log":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--agent", required=True)
        parser.add_argument("--task", required=True)
        parser.add_argument("--route", required=True, help="Node IDs separated by ' -> '")
        parser.add_argument("--outcome", required=True, choices=["success", "failure", "partial"])
        parser.add_argument("--session", default=None)
        parser.add_argument("--notes", default=None)
        args = parser.parse_args(sys.argv[2:])
        route = [x.strip() for x in args.route.split("->")]
        log_route(args.agent, args.task, route, args.outcome, args.session, args.notes)

    elif cmd == "analyze":
        analyze_routes()

    elif cmd == "top":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--entry", required=True)
        args = parser.parse_args(sys.argv[2:])
        get_top_routes(args.entry)

    elif cmd == "patterns":
        extract_patterns()

    elif cmd == "recommend":
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--task", required=True)
        parser.add_argument("--entry", default=None)
        args = parser.parse_args(sys.argv[2:])
        recommend_route(args.task, args.entry)

    elif cmd == "export":
        analyze_routes()
        extract_patterns()
        print(f"\nExported to:\n  {ROUTES_ANALYSIS}\n  {ROUTES_PATTERNS}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: log, analyze, top, patterns, recommend, export")
        sys.exit(1)
