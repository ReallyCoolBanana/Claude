#!/usr/bin/env python3
"""
Auto-classifier for pointer-network edge strengths.

Reads pointer-network.json, applies classification rules to all unclassified
edges based on weight, reason types, and domain pair heuristics.

Usage:
    python3 auto_classify.py                  # Dry-run: report what would change
    python3 auto_classify.py --apply          # Apply changes to pointer-network.json
    python3 auto_classify.py --stats          # Show current classification stats only
    python3 auto_classify.py --apply --output FILE  # Write to a different file

Classification Rules (derived from 889 classified edges):
    weight >= 7  -> "primary"      (strong explicit references, multiple shared tags)
    weight == 6  -> "supporting"   (explicit_reference only, or strong shared_tags)
    weight == 5  -> "supporting"   (tag_bridge, moderate shared_tags)
    weight == 4  -> "related"      (tag_bridge) or "supporting" (category_affinity+shared_tags)
    weight == 3  -> "related"      (category_affinity, shared_tags, backlink)
    weight == 2  -> "related"      (shared_tags, category_affinity)
    weight == 1  -> "tangential"   (single shared tag)

Domain-pair adjustments:
    - KB<->KB with explicit_reference: upgrade one tier (builds_on = strong link)
    - KB<->SCR with explicit_reference: upgrade one tier (script implements KB concept)
    - SCR<->SRC with explicit_reference: upgrade one tier (uses_sources reference)
    - TEAM<->SOP with explicit_reference: upgrade one tier (SOP governs team work)
    - wiki<->*: no adjustment (category/tag matching is weaker signal)
"""

import json
import sys
import os
import argparse
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
POINTER_NETWORK = REPO_ROOT / "storage" / "pointer-network.json"

# Strength tiers in order (for upgrade/downgrade)
TIERS = ["tangential", "related", "supporting", "primary"]
TIER_INDEX = {t: i for i, t in enumerate(TIERS)}

# Domain pairs that get an upgrade when explicit_reference is present
UPGRADE_PAIRS = {
    frozenset(["KB", "KB"]),
    frozenset(["KB", "SCR"]),
    frozenset(["SCR", "SRC"]),
    frozenset(["TEAM", "SOP"]),
    frozenset(["SOP", "TEAM"]),
}


def get_domain(node_id: str) -> str:
    """Extract domain prefix from node ID (e.g., KB-0001 -> KB)."""
    return node_id.split("-")[0]


def get_reason_types(reasons: list) -> set:
    """Extract reason type names from reason strings."""
    return set(r.split(":")[0] for r in reasons)


def upgrade_tier(strength: str, steps: int = 1) -> str:
    """Move a strength up by N tiers (capped at primary)."""
    idx = TIER_INDEX.get(strength, 0)
    new_idx = min(idx + steps, len(TIERS) - 1)
    return TIERS[new_idx]


def classify_edge(weight: int, reason_types: set, src_domain: str, tgt_domain: str) -> str:
    """
    Classify an edge based on weight, reason types, and domain pair.

    Returns one of: "primary", "supporting", "related", "tangential"
    """
    has_explicit = "explicit_reference" in reason_types
    has_backlink = "backlink" in reason_types
    has_category = "category_affinity" in reason_types
    has_shared_tags = "shared_tags" in reason_types
    domain_pair = frozenset([src_domain, tgt_domain])

    # Base classification by weight (learned from 889 classified edges)
    if weight >= 7:
        base = "primary"
    elif weight == 6:
        base = "supporting"  # weight 6 is consistently supporting
    elif weight == 5:
        base = "supporting"
    elif weight == 4:
        if has_category and has_shared_tags:
            base = "supporting"
        elif has_explicit:
            base = "supporting"  # explicit ref at weight 4 = supporting
        else:
            base = "related"
    elif weight == 3:
        base = "related"
    elif weight == 2:
        base = "related"
    elif weight == 1:
        # Weight 1 defaults to tangential, but with overrides:
        if "tag_bridge" in reason_types:
            base = "related"  # SRC<->SCR tag_bridge pattern (from classified data)
        elif has_explicit:
            base = "related"  # explicit reference overrides low weight
        elif domain_pair == frozenset(["SOP", "SCR"]):
            base = "related"  # SOP<->SCR: SOPs referencing scripts (from classified data)
        else:
            base = "tangential"
    else:
        base = "tangential"

    # Domain-pair upgrade: if explicit_reference present in an upgrade-eligible pair
    if domain_pair in UPGRADE_PAIRS and has_explicit and base not in ("primary", "related"):
        # Only upgrade if not already upgraded by weight-1 overrides above
        base = upgrade_tier(base, 1)

    # Backlink with high weight gets primary
    if has_backlink and weight >= 7:
        base = "primary"

    return base


def load_network(path: Path) -> dict:
    """Load the pointer network JSON."""
    with open(path) as f:
        return json.load(f)


def analyze_and_classify(data: dict) -> dict:
    """
    Analyze all edges and classify unclassified ones.

    Returns a dict with:
        - classifications: list of (src_id, edge_index, new_strength, edge_info)
        - stats: classification statistics
    """
    classifications = []
    stats = {
        "already_classified": 0,
        "newly_classified": 0,
        "by_strength": defaultdict(int),
        "by_domain_pair": defaultdict(lambda: defaultdict(int)),
        "by_weight": defaultdict(lambda: defaultdict(int)),
    }

    for src_id, node in data["nodes"].items():
        src_domain = get_domain(src_id)
        for i, ptr in enumerate(node.get("pointers", [])):
            if "strength" in ptr:
                stats["already_classified"] += 1
                continue

            tgt_id = ptr["to"]
            tgt_domain = get_domain(tgt_id)
            weight = ptr.get("weight", 0)
            reasons = ptr.get("reasons", [])
            reason_types = get_reason_types(reasons)

            strength = classify_edge(weight, reason_types, src_domain, tgt_domain)

            classifications.append({
                "src": src_id,
                "edge_index": i,
                "target": tgt_id,
                "weight": weight,
                "reasons": reasons,
                "new_strength": strength,
            })

            stats["newly_classified"] += 1
            stats["by_strength"][strength] += 1
            pair_key = "-".join(sorted([src_domain, tgt_domain]))
            stats["by_domain_pair"][pair_key][strength] += 1
            stats["by_weight"][weight][strength] += 1

    return {"classifications": classifications, "stats": stats}


def apply_classifications(data: dict, classifications: list) -> dict:
    """Apply classifications to the pointer network data (in-place)."""
    for c in classifications:
        src = c["src"]
        idx = c["edge_index"]
        data["nodes"][src]["pointers"][idx]["strength"] = c["new_strength"]

    # Update stats
    total_classified = sum(
        1 for node in data["nodes"].values()
        for ptr in node.get("pointers", [])
        if "strength" in ptr
    )
    data["stats"]["classified_edges"] = total_classified
    data["stats"]["classification_coverage"] = round(
        total_classified / data["stats"]["total_edges"] * 100, 1
    )
    return data


def print_report(result: dict) -> None:
    """Print a human-readable classification report."""
    stats = result["stats"]
    total = stats["already_classified"] + stats["newly_classified"]

    print("=" * 60)
    print("POINTER NETWORK AUTO-CLASSIFICATION REPORT")
    print("=" * 60)
    print(f"\nEdges already classified:  {stats['already_classified']}")
    print(f"Edges newly classified:    {stats['newly_classified']}")
    print(f"Total after classification: {total}")
    print(f"Coverage: {total}/{total} (100.0%)")

    print(f"\n--- New classifications by strength ---")
    for s in TIERS:
        count = stats["by_strength"].get(s, 0)
        pct = count / stats["newly_classified"] * 100 if stats["newly_classified"] else 0
        bar = "#" * int(pct / 2)
        print(f"  {s:12s}: {count:5d} ({pct:5.1f}%) {bar}")

    print(f"\n--- New classifications by domain pair ---")
    for pair in sorted(stats["by_domain_pair"].keys()):
        pair_dist = stats["by_domain_pair"][pair]
        pair_total = sum(pair_dist.values())
        dist_str = ", ".join(f"{s}={pair_dist.get(s, 0)}" for s in TIERS if pair_dist.get(s, 0))
        print(f"  {pair:12s}: {pair_total:4d}  [{dist_str}]")

    print(f"\n--- New classifications by weight ---")
    for w in sorted(stats["by_weight"].keys()):
        w_dist = stats["by_weight"][w]
        w_total = sum(w_dist.values())
        dist_str = ", ".join(f"{s}={w_dist.get(s, 0)}" for s in TIERS if w_dist.get(s, 0))
        print(f"  weight={w}: {w_total:4d}  [{dist_str}]")


def print_stats_only(data: dict) -> None:
    """Print current classification stats."""
    classified = 0
    unclassified = 0
    strength_counts = defaultdict(int)

    for node in data["nodes"].values():
        for ptr in node.get("pointers", []):
            if "strength" in ptr:
                classified += 1
                strength_counts[ptr["strength"]] += 1
            else:
                unclassified += 1

    total = classified + unclassified
    print(f"Total edges: {total}")
    print(f"Classified: {classified} ({classified/total*100:.1f}%)")
    print(f"Unclassified: {unclassified} ({unclassified/total*100:.1f}%)")
    print(f"\nStrength distribution:")
    for s in TIERS:
        print(f"  {s}: {strength_counts.get(s, 0)}")


def generate_report_json(result: dict, data: dict) -> dict:
    """Generate a JSON report of the classification results."""
    stats = result["stats"]
    total = stats["already_classified"] + stats["newly_classified"]

    # Compute projected full distribution
    existing_dist = defaultdict(int)
    for node in data["nodes"].values():
        for ptr in node.get("pointers", []):
            if "strength" in ptr:
                existing_dist[ptr["strength"]] += 1

    projected_dist = dict(existing_dist)
    for s, count in stats["by_strength"].items():
        projected_dist[s] = projected_dist.get(s, 0) + count

    return {
        "agent": "analysis-7",
        "task": "auto-classify-design",
        "timestamp": "2026-03-12",
        "summary": {
            "before": {
                "total_edges": data["stats"]["total_edges"],
                "classified": stats["already_classified"],
                "unclassified": stats["newly_classified"],
                "coverage_pct": round(stats["already_classified"] / data["stats"]["total_edges"] * 100, 1),
            },
            "after": {
                "total_edges": data["stats"]["total_edges"],
                "classified": total,
                "unclassified": 0,
                "coverage_pct": 100.0,
            },
            "newly_classified": stats["newly_classified"],
        },
        "classification_rules": {
            "weight_based": {
                "weight>=7": "primary",
                "weight==6": "supporting",
                "weight==5": "supporting",
                "weight==4_with_category+tags": "supporting",
                "weight==4_other": "related",
                "weight==3": "related",
                "weight==2": "related",
                "weight==1": "tangential",
            },
            "domain_pair_upgrades": [
                "KB<->KB with explicit_reference: +1 tier",
                "KB<->SCR with explicit_reference: +1 tier",
                "SCR<->SRC with explicit_reference: +1 tier",
                "TEAM<->SOP with explicit_reference: +1 tier",
            ],
            "weight_1_overrides": [
                "weight 1 + tag_bridge reason: related (SRC<->SCR pattern)",
                "weight 1 + explicit_reference: related (strong signal overrides low weight)",
                "weight 1 + SOP<->SCR domain pair: related (SOPs referencing scripts)",
            ],
            "special_cases": [
                "backlink with weight>=7: primary",
            ],
        },
        "projected_distribution": {
            s: projected_dist.get(s, 0) for s in TIERS
        },
        "by_domain_pair": {
            pair: dict(dist) for pair, dist in sorted(stats["by_domain_pair"].items())
        },
        "by_weight": {
            str(w): dict(dist) for w, dist in sorted(stats["by_weight"].items())
        },
    }


def main():
    parser = argparse.ArgumentParser(
        description="Auto-classify pointer-network edge strengths"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply classifications (default: dry-run)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output file path (default: overwrite pointer-network.json)"
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show current classification stats and exit"
    )
    parser.add_argument(
        "--report", type=str, default=None,
        help="Write JSON report to specified file"
    )
    args = parser.parse_args()

    # Load network
    data = load_network(POINTER_NETWORK)

    if args.stats:
        print_stats_only(data)
        return

    # Analyze and classify
    result = analyze_and_classify(data)
    print_report(result)

    # Generate report JSON
    report = generate_report_json(result, data)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nReport written to: {report_path}")

    if args.apply:
        data = apply_classifications(data, result["classifications"])
        out_path = Path(args.output) if args.output else POINTER_NETWORK
        with open(out_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"\nClassifications applied. Output written to: {out_path}")
    else:
        print(f"\n[DRY RUN] No changes applied. Use --apply to write changes.")


if __name__ == "__main__":
    main()
