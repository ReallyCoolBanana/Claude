#!/usr/bin/env python3
"""
Network Version Tracker — Snapshots, diffs, and changelogs for the pointer network.

Tracks the evolution of the pointer network over time by:
  - Taking snapshots of current network state (metrics + structure hash)
  - Comparing any two snapshots to produce a structured diff
  - Generating changelogs from sequential snapshots
  - Supporting schema migration tracking

Usage:
  # Take a snapshot of current network state
  python3 network_version.py snapshot

  # Compare two snapshots
  python3 network_version.py diff <snapshot_a> <snapshot_b>

  # Generate changelog from all snapshots
  python3 network_version.py changelog

  # Show latest snapshot summary
  python3 network_version.py latest

  # Validate schema compatibility
  python3 network_version.py check-schema

Snapshots are stored in: storage/pointer-routes/snapshots/
"""

import json
import hashlib
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
NETWORK_PATH = REPO_ROOT / "storage" / "pointer-network.json"
SNAPSHOTS_DIR = REPO_ROOT / "storage" / "pointer-routes" / "snapshots"
ARCHIVE_DIR = REPO_ROOT / "storage" / "pointer-routes" / "archive"

# Schema compatibility matrix — which versions can be diffed against each other
COMPATIBLE_SCHEMAS = {
    "1.0": ["1.0", "1.1"],
    "1.1": ["1.0", "1.1", "1.2"],
    "1.2": ["1.1", "1.2"],
}

# Known schema versions and their expected top-level keys
SCHEMA_DEFINITIONS = {
    "1.0": {"required": ["nodes", "stats"], "optional": ["description", "built"]},
    "1.1": {
        "required": ["schema_version", "nodes", "stats"],
        "optional": ["description", "built", "tag_clusters", "strength_levels"],
    },
}


def load_network(path=None):
    """Load the pointer network JSON."""
    p = Path(path) if path else NETWORK_PATH
    with open(p) as f:
        return json.load(f)


def compute_structure_hash(network):
    """Compute a deterministic hash of the network structure (nodes + edges).

    This captures structural identity regardless of metadata changes.
    """
    h = hashlib.sha256()
    nodes = network.get("nodes", {})
    for node_id in sorted(nodes.keys()):
        h.update(node_id.encode())
        node = nodes[node_id]
        h.update(node.get("domain", "").encode())
        for ptr in sorted(node.get("pointers", []), key=lambda p: p.get("to", "")):
            h.update(ptr.get("to", "").encode())
            h.update(str(ptr.get("weight", 0)).encode())
            h.update(ptr.get("strength", "unclassified").encode())
    return h.hexdigest()


def compute_metrics(network):
    """Extract key metrics from the network for versioning."""
    nodes = network.get("nodes", {})
    stats = network.get("stats", {})

    # Strength distribution
    strength_dist = Counter()
    weight_values = []
    edge_count = 0
    classified_count = 0

    for node_id, node in nodes.items():
        for ptr in node.get("pointers", []):
            edge_count += 1
            s = ptr.get("strength", "unclassified")
            strength_dist[s] += 1
            if s != "unclassified":
                classified_count += 1
            weight_values.append(ptr.get("weight", 0))

    # Domain distribution
    domain_dist = Counter()
    for node_id, node in nodes.items():
        domain_dist[node.get("domain", "unknown")] += 1

    # Orphan detection (nodes with no pointers AND no incoming pointers)
    nodes_with_outgoing = set()
    nodes_with_incoming = set()
    for node_id, node in nodes.items():
        if node.get("pointers"):
            nodes_with_outgoing.add(node_id)
        for ptr in node.get("pointers", []):
            nodes_with_incoming.add(ptr.get("to", ""))

    orphans = [
        nid for nid in nodes
        if nid not in nodes_with_outgoing and nid not in nodes_with_incoming
    ]

    # Community structure (nodes grouped by domain and top tag clusters)
    tag_clusters = network.get("tag_clusters", {})

    # Weight statistics
    avg_weight = sum(weight_values) / len(weight_values) if weight_values else 0
    max_weight = max(weight_values) if weight_values else 0
    min_weight = min(weight_values) if weight_values else 0

    return {
        "node_count": len(nodes),
        "edge_count": edge_count,
        "unique_edges": stats.get("unique_edges", edge_count),
        "classified_count": classified_count,
        "unclassified_count": strength_dist.get("unclassified", 0),
        "classification_ratio": round(classified_count / edge_count, 4) if edge_count else 0,
        "orphan_count": len(orphans),
        "orphan_ids": orphans,
        "strength_distribution": dict(strength_dist.most_common()),
        "domain_distribution": dict(domain_dist.most_common()),
        "weight_stats": {
            "avg": round(avg_weight, 2),
            "min": min_weight,
            "max": max_weight,
        },
        "tag_cluster_count": len(tag_clusters),
        "edge_type_counts": stats.get("edge_type_counts", {}),
    }


def take_snapshot(network=None, label=None):
    """Take a versioned snapshot of the current network state."""
    if network is None:
        network = load_network()

    now = datetime.now(timezone.utc)
    snapshot_id = now.strftime("snap-%Y%m%d-%H%M%S")
    if label:
        snapshot_id += f"-{label}"

    metrics = compute_metrics(network)
    structure_hash = compute_structure_hash(network)

    # Extract node list and edge list for diff capability
    node_ids = sorted(network.get("nodes", {}).keys())
    edges = []
    for node_id in node_ids:
        node = network["nodes"][node_id]
        for ptr in node.get("pointers", []):
            edges.append({
                "from": node_id,
                "to": ptr.get("to", ""),
                "weight": ptr.get("weight", 0),
                "strength": ptr.get("strength", "unclassified"),
            })

    snapshot = {
        "snapshot_id": snapshot_id,
        "timestamp": now.isoformat(),
        "schema_version": network.get("schema_version", "unknown"),
        "structure_hash": structure_hash,
        "metrics": metrics,
        "node_ids": node_ids,
        "edge_summary": {
            "total": len(edges),
            "by_strength": dict(Counter(e["strength"] for e in edges)),
        },
        "edges": edges,
    }

    # Save snapshot
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOTS_DIR / f"{snapshot_id}.json"
    with open(snapshot_path, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"Snapshot saved: {snapshot_path}")
    print(f"  ID:              {snapshot_id}")
    print(f"  Schema:          {snapshot['schema_version']}")
    print(f"  Structure hash:  {structure_hash[:16]}...")
    print(f"  Nodes:           {metrics['node_count']}")
    print(f"  Edges:           {metrics['edge_count']}")
    print(f"  Classified:      {metrics['classified_count']} ({metrics['classification_ratio']*100:.1f}%)")
    print(f"  Orphans:         {metrics['orphan_count']}")

    return snapshot


def load_snapshot(snapshot_id_or_path):
    """Load a snapshot by ID or file path."""
    p = Path(snapshot_id_or_path)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    # Try as snapshot ID
    p = SNAPSHOTS_DIR / f"{snapshot_id_or_path}.json"
    if p.exists():
        with open(p) as f:
            return json.load(f)
    raise FileNotFoundError(f"Snapshot not found: {snapshot_id_or_path}")


def list_snapshots():
    """List all available snapshots sorted by timestamp."""
    if not SNAPSHOTS_DIR.exists():
        return []
    snaps = []
    for f in sorted(SNAPSHOTS_DIR.glob("snap-*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            snaps.append({
                "id": data.get("snapshot_id", f.stem),
                "timestamp": data.get("timestamp", "unknown"),
                "schema_version": data.get("schema_version", "unknown"),
                "node_count": data.get("metrics", {}).get("node_count", 0),
                "edge_count": data.get("metrics", {}).get("edge_count", 0),
                "hash": data.get("structure_hash", "")[:16],
                "path": str(f),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return snaps


def diff_snapshots(snap_a, snap_b):
    """Compare two snapshots and produce a structured diff.

    Returns a diff dict with:
      - nodes_added, nodes_removed
      - edges_added, edges_removed, edges_reweighted
      - strength_changes
      - metric_deltas
      - connectivity_changes
    """
    # Nodes
    nodes_a = set(snap_a.get("node_ids", []))
    nodes_b = set(snap_b.get("node_ids", []))
    nodes_added = sorted(nodes_b - nodes_a)
    nodes_removed = sorted(nodes_a - nodes_b)

    # Edges — index by (from, to)
    def edge_index(snap):
        idx = {}
        for e in snap.get("edges", []):
            key = (e["from"], e["to"])
            idx[key] = e
        return idx

    edges_a = edge_index(snap_a)
    edges_b = edge_index(snap_b)
    keys_a = set(edges_a.keys())
    keys_b = set(edges_b.keys())

    edges_added = [
        {"from": k[0], "to": k[1], "weight": edges_b[k]["weight"], "strength": edges_b[k]["strength"]}
        for k in sorted(keys_b - keys_a)
    ]
    edges_removed = [
        {"from": k[0], "to": k[1], "weight": edges_a[k]["weight"], "strength": edges_a[k]["strength"]}
        for k in sorted(keys_a - keys_b)
    ]

    # Reweighted edges
    edges_reweighted = []
    strength_changes = []
    for k in sorted(keys_a & keys_b):
        ea, eb = edges_a[k], edges_b[k]
        if ea["weight"] != eb["weight"]:
            edges_reweighted.append({
                "from": k[0], "to": k[1],
                "weight_before": ea["weight"], "weight_after": eb["weight"],
                "delta": eb["weight"] - ea["weight"],
            })
        if ea.get("strength") != eb.get("strength"):
            strength_changes.append({
                "from": k[0], "to": k[1],
                "strength_before": ea.get("strength", "unclassified"),
                "strength_after": eb.get("strength", "unclassified"),
            })

    # Metric deltas
    ma = snap_a.get("metrics", {})
    mb = snap_b.get("metrics", {})
    metric_deltas = {}
    for key in ["node_count", "edge_count", "classified_count", "unclassified_count",
                 "orphan_count", "classification_ratio", "tag_cluster_count"]:
        va = ma.get(key, 0)
        vb = mb.get(key, 0)
        if va != vb:
            metric_deltas[key] = {"before": va, "after": vb, "delta": vb - va}

    # Connectivity changes
    connectivity = {
        "nodes_added_count": len(nodes_added),
        "nodes_removed_count": len(nodes_removed),
        "edges_added_count": len(edges_added),
        "edges_removed_count": len(edges_removed),
        "edges_reweighted_count": len(edges_reweighted),
        "strength_reclassified_count": len(strength_changes),
        "structure_hash_changed": snap_a.get("structure_hash") != snap_b.get("structure_hash"),
        "schema_version_changed": snap_a.get("schema_version") != snap_b.get("schema_version"),
    }

    return {
        "diff_from": snap_a.get("snapshot_id", "unknown"),
        "diff_to": snap_b.get("snapshot_id", "unknown"),
        "timestamp_from": snap_a.get("timestamp", "unknown"),
        "timestamp_to": snap_b.get("timestamp", "unknown"),
        "nodes_added": nodes_added,
        "nodes_removed": nodes_removed,
        "edges_added": edges_added[:50],  # Cap for readability
        "edges_removed": edges_removed[:50],
        "edges_reweighted": edges_reweighted[:50],
        "strength_changes": strength_changes[:50],
        "metric_deltas": metric_deltas,
        "connectivity": connectivity,
        "total_edges_added": len(edges_added),
        "total_edges_removed": len(edges_removed),
        "total_edges_reweighted": len(edges_reweighted),
        "total_strength_changes": len(strength_changes),
    }


def generate_changelog():
    """Generate a changelog from all sequential snapshots."""
    snaps = list_snapshots()
    if len(snaps) < 2:
        print("Need at least 2 snapshots to generate a changelog.")
        if snaps:
            print(f"Only 1 snapshot found: {snaps[0]['id']}")
        return None

    changelog = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total_snapshots": len(snaps),
        "entries": [],
    }

    for i in range(1, len(snaps)):
        snap_a = load_snapshot(snaps[i - 1]["path"])
        snap_b = load_snapshot(snaps[i]["path"])
        diff = diff_snapshots(snap_a, snap_b)

        entry = {
            "from_snapshot": diff["diff_from"],
            "to_snapshot": diff["diff_to"],
            "timestamp": diff["timestamp_to"],
            "summary": [],
        }

        conn = diff["connectivity"]
        if conn["nodes_added_count"]:
            entry["summary"].append(f"+{conn['nodes_added_count']} nodes")
        if conn["nodes_removed_count"]:
            entry["summary"].append(f"-{conn['nodes_removed_count']} nodes")
        if conn["edges_added_count"]:
            entry["summary"].append(f"+{conn['edges_added_count']} edges")
        if conn["edges_removed_count"]:
            entry["summary"].append(f"-{conn['edges_removed_count']} edges")
        if conn["edges_reweighted_count"]:
            entry["summary"].append(f"~{conn['edges_reweighted_count']} reweighted")
        if conn["strength_reclassified_count"]:
            entry["summary"].append(f"~{conn['strength_reclassified_count']} reclassified")
        if conn["schema_version_changed"]:
            entry["summary"].append("SCHEMA CHANGED")

        entry["summary_text"] = ", ".join(entry["summary"]) if entry["summary"] else "No changes"
        entry["metric_deltas"] = diff["metric_deltas"]
        changelog["entries"].append(entry)

    # Save changelog
    changelog_path = SNAPSHOTS_DIR / "changelog.json"
    with open(changelog_path, "w") as f:
        json.dump(changelog, f, indent=2)

    print(f"Changelog generated: {changelog_path}")
    print(f"  Snapshots: {len(snaps)}")
    print(f"  Entries:   {len(changelog['entries'])}")
    for entry in changelog["entries"]:
        print(f"  [{entry['timestamp'][:19]}] {entry['from_snapshot']} -> {entry['to_snapshot']}: {entry['summary_text']}")

    return changelog


def check_schema():
    """Validate the current network schema and check migration needs."""
    network = load_network()
    version = network.get("schema_version", "unknown")

    print(f"Current schema version: {version}")

    if version in SCHEMA_DEFINITIONS:
        defn = SCHEMA_DEFINITIONS[version]
        top_keys = set(network.keys())
        missing_required = set(defn["required"]) - top_keys
        has_optional = set(defn.get("optional", [])) & top_keys
        unknown_keys = top_keys - set(defn["required"]) - set(defn.get("optional", []))

        if missing_required:
            print(f"  WARNING: Missing required keys: {missing_required}")
        else:
            print(f"  All required keys present: {defn['required']}")
        print(f"  Optional keys present: {sorted(has_optional)}")
        if unknown_keys:
            print(f"  Unknown keys (may need schema update): {sorted(unknown_keys)}")
    else:
        print(f"  WARNING: Unknown schema version '{version}'")
        print(f"  Known versions: {list(SCHEMA_DEFINITIONS.keys())}")

    # Check compatibility with existing snapshots
    snaps = list_snapshots()
    if snaps:
        snap_versions = set(s["schema_version"] for s in snaps)
        print(f"\n  Snapshot schema versions: {snap_versions}")
        compat = COMPATIBLE_SCHEMAS.get(version, [])
        for sv in snap_versions:
            if sv in compat:
                print(f"    {version} <-> {sv}: Compatible (can diff)")
            else:
                print(f"    {version} <-> {sv}: INCOMPATIBLE (migration needed)")

    return version


def show_latest():
    """Show the latest snapshot summary."""
    snaps = list_snapshots()
    if not snaps:
        print("No snapshots found. Run 'snapshot' first.")
        return

    latest = snaps[-1]
    snap = load_snapshot(latest["path"])
    metrics = snap.get("metrics", {})

    print(f"Latest snapshot: {latest['id']}")
    print(f"  Timestamp:     {latest['timestamp']}")
    print(f"  Schema:        {latest['schema_version']}")
    print(f"  Hash:          {snap.get('structure_hash', '')[:16]}...")
    print(f"  Nodes:         {metrics.get('node_count', 0)}")
    print(f"  Edges:         {metrics.get('edge_count', 0)}")
    print(f"  Classified:    {metrics.get('classified_count', 0)} ({metrics.get('classification_ratio', 0)*100:.1f}%)")
    print(f"  Orphans:       {metrics.get('orphan_count', 0)}")
    print(f"  Strength dist: {json.dumps(metrics.get('strength_distribution', {}))}")

    # If there are at least 2 snapshots, show diff from previous
    if len(snaps) >= 2:
        prev = load_snapshot(snaps[-2]["path"])
        diff = diff_snapshots(prev, snap)
        conn = diff["connectivity"]
        changes = []
        if conn["nodes_added_count"]:
            changes.append(f"+{conn['nodes_added_count']} nodes")
        if conn["nodes_removed_count"]:
            changes.append(f"-{conn['nodes_removed_count']} nodes")
        if conn["edges_added_count"]:
            changes.append(f"+{conn['edges_added_count']} edges")
        if conn["edges_removed_count"]:
            changes.append(f"-{conn['edges_removed_count']} edges")
        if conn["edges_reweighted_count"]:
            changes.append(f"~{conn['edges_reweighted_count']} reweighted")
        if changes:
            print(f"\n  Changes from previous: {', '.join(changes)}")
        else:
            print(f"\n  No structural changes from previous snapshot.")


def cmd_diff(args):
    """Handle diff subcommand."""
    if len(args) < 2:
        print("Usage: network_version.py diff <snapshot_a> <snapshot_b>")
        sys.exit(1)

    snap_a = load_snapshot(args[0])
    snap_b = load_snapshot(args[1])
    diff = diff_snapshots(snap_a, snap_b)

    # Save diff
    diff_filename = f"diff-{diff['diff_from']}-vs-{diff['diff_to']}.json"
    diff_path = SNAPSHOTS_DIR / diff_filename
    with open(diff_path, "w") as f:
        json.dump(diff, f, indent=2)

    print(f"Diff saved: {diff_path}")
    conn = diff["connectivity"]
    print(f"\n  From:  {diff['diff_from']} ({diff['timestamp_from'][:19]})")
    print(f"  To:    {diff['diff_to']} ({diff['timestamp_to'][:19]})")
    print(f"  Hash changed:   {conn['structure_hash_changed']}")
    print(f"  Schema changed: {conn['schema_version_changed']}")
    print(f"  Nodes:  +{conn['nodes_added_count']} / -{conn['nodes_removed_count']}")
    print(f"  Edges:  +{conn['edges_added_count']} / -{conn['edges_removed_count']} / ~{conn['edges_reweighted_count']} reweighted")
    print(f"  Strength reclassifications: {conn['strength_reclassified_count']}")

    if diff["metric_deltas"]:
        print(f"\n  Metric deltas:")
        for k, v in diff["metric_deltas"].items():
            sign = "+" if v["delta"] > 0 else ""
            print(f"    {k}: {v['before']} -> {v['after']} ({sign}{v['delta']})")

    return diff


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "snapshot":
        label = None
        if len(sys.argv) > 2:
            label = sys.argv[2]
        take_snapshot(label=label)

    elif cmd == "diff":
        cmd_diff(sys.argv[2:])

    elif cmd == "changelog":
        generate_changelog()

    elif cmd == "latest":
        show_latest()

    elif cmd == "check-schema":
        check_schema()

    elif cmd == "list":
        snaps = list_snapshots()
        if not snaps:
            print("No snapshots found.")
        else:
            print(f"{'ID':<40} {'Timestamp':<25} {'Schema':<8} {'Nodes':>6} {'Edges':>6} {'Hash'}")
            print("-" * 110)
            for s in snaps:
                print(f"{s['id']:<40} {s['timestamp'][:19]:<25} {s['schema_version']:<8} {s['node_count']:>6} {s['edge_count']:>6} {s['hash']}")

    else:
        print(f"Unknown command: {cmd}")
        print("Commands: snapshot, diff, changelog, latest, check-schema, list")
        sys.exit(1)


if __name__ == "__main__":
    main()
