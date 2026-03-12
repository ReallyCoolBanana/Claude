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
from datetime import datetime, timezone

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if line:
                try:
                    routes.append(json.loads(line))
                except json.JSONDecodeError as e:
                    print(f"Warning: skipping corrupt JSONL line {line_num}: {e}",
                          file=sys.stderr)
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
    edge_outcomes = defaultdict(lambda: defaultdict(int))
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
        "generated": datetime.now(timezone.utc).isoformat(),
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
        "generated": datetime.now(timezone.utc).isoformat(),
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

        # Score routes by count * success_rate (enhancement #4)
        route_counts = Counter(route_strs)
        scored_routes = []
        for route_str, count in route_counts.most_common():
            route_records = [r for r in task_records if " -> ".join(r["route"]) == route_str]
            route_successes = sum(1 for r in route_records if r.get("outcome") == "success")
            route_success_rate = route_successes / max(len(route_records), 1)
            score = count * route_success_rate
            scored_routes.append((route_str, count, score, round(route_success_rate * 100, 1)))
        scored_routes.sort(key=lambda x: x[2], reverse=True)

        patterns["task_patterns"][task] = {
            "total_attempts": len(task_records),
            "success_count": len(successful),
            "success_rate": round(len(successful) / max(len(task_records), 1) * 100, 1),
            "avg_weight_on_success": avg_weight,
            "common_routes": [
                {"route": route, "count": count, "score": score,
                 "route_success_rate": sr}
                for route, count, score, sr in scored_routes[:3]
            ],
            "recommended_route": scored_routes[0][0] if scored_routes else None,
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
    """Recommend a pointer route for a given task, based on history.

    Bug fixes applied:
    - #1: When entry_node is provided, filter pattern routes to those starting
          from entry_node. Fall back to strength-based routing if none match.
    - #2: Add 'related' as a third fallback tier in strength-based routing.
    - #3: When entry_node is None and no patterns exist, search all SOP nodes
          for primary/supporting pointers and suggest the best-connected one.
    - #4: Routes are scored by count * success_rate (see extract_patterns).
    """
    # Check pattern-based recommendations
    if ROUTES_PATTERNS.exists():
        with open(ROUTES_PATTERNS) as f:
            patterns = json.load(f)
        if task in patterns.get("task_patterns", {}):
            tp = patterns["task_patterns"][task]
            common_routes = tp.get("common_routes", [])

            if entry_node and common_routes:
                # Fix #1: Filter to routes starting from entry_node
                matching = [r for r in common_routes
                            if r["route"].startswith(entry_node)]
                if matching:
                    # Pick the highest-scored matching route
                    best = max(matching, key=lambda r: r.get("score", r.get("count", 0)))
                    print(f"Recommended route for '{task}' from {entry_node}: {best['route']}")
                    print(f"  (score={best.get('score', 'N/A')}, "
                          f"based on {tp['total_attempts']} attempts, "
                          f"{tp['success_rate']}% overall success rate)")
                    return best["route"]
                # No pattern matches entry_node — fall through to strength-based
            elif not entry_node and tp.get("recommended_route"):
                # No entry_node specified, return global best
                print(f"Recommended route for '{task}': {tp['recommended_route']}")
                print(f"  (based on {tp['total_attempts']} attempts, "
                      f"{tp['success_rate']}% success rate)")
                return tp["recommended_route"]

    # Fallback: use pointer network strength to suggest a path
    try:
        with open(POINTER_NETWORK) as f:
            pn = json.load(f)
    except Exception:
        pn = {"nodes": {}}

    # Fix #5: Validate that entry_node exists in the pointer network
    if entry_node and entry_node not in pn.get("nodes", {}):
        print(f"Warning: entry node '{entry_node}' not found in pointer network")
        # Continue with pattern-based recommendation only (already attempted above)
        print(f"No route recommendation available for task='{task}', entry='{entry_node}'")
        return None

    if entry_node:
        node = pn.get("nodes", {}).get(entry_node, {})
        primary = [p for p in node.get("pointers", [])
                   if p.get("strength") == "primary"]
        supporting = [p for p in node.get("pointers", [])
                      if p.get("strength") == "supporting"]
        # Fix #2: Add 'related' as third fallback tier
        related = [p for p in node.get("pointers", [])
                   if p.get("strength") == "related"]

        if primary:
            targets = [p["to"] for p in primary]
            print(f"No history for '{task}'. Primary tools from {entry_node}: {targets}")
            return targets
        elif supporting:
            targets = [p["to"] for p in supporting]
            print(f"No history for '{task}'. Supporting tools from {entry_node}: {targets}")
            return targets
        elif related:
            targets = [p["to"] for p in related]
            print(f"No history for '{task}'. Related tools from {entry_node}: {targets}")
            return targets
    else:
        # Fix #3+#4: When entry_node is None and no patterns, search all SOP nodes
        # Fix #4: Score by pointer_count * (1 + tag_match_bonus) for task relevance
        task_keywords = set(task.lower().replace("-", " ").replace("_", " ").split())
        best_node = None
        best_score = 0
        for node_id, node_data in pn.get("nodes", {}).items():
            if not node_id.startswith("SOP-"):
                continue
            pointers = node_data.get("pointers", [])
            relevant = [p for p in pointers
                        if p.get("strength") in ("primary", "supporting")]
            pointer_count = len(relevant)
            if pointer_count == 0:
                continue
            # Check tag overlap with task keywords
            tags = node_data.get("tags", [])
            tag_words = set()
            for tag in tags:
                tag_words.update(tag.lower().replace("-", " ").replace("_", " ").split())
            tag_matches = len(task_keywords & tag_words)
            score = pointer_count * (1 + tag_matches)
            if score > best_score:
                best_score = score
                best_node = node_id
        if best_node:
            node_data = pn["nodes"][best_node]
            targets = [p["to"] for p in node_data.get("pointers", [])
                       if p.get("strength") in ("primary", "supporting")]
            print(f"No history for '{task}'. Best-connected SOP: {best_node} "
                  f"with score {best_score} (pointer+tag relevance): {targets}")
            return targets

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
