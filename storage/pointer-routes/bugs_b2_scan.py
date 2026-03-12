#!/usr/bin/env python3
"""Bug Hunter B2: Data integrity validation for pointer-network.json"""

import json
import sys
from collections import defaultdict

with open("/home/user/Claude/storage/pointer-network.json", "r") as f:
    data = json.load(f)

bugs = []
bug_id = 0

def add_bug(category, severity, description, details=None):
    global bug_id
    bug_id += 1
    bug = {
        "id": f"B2-{bug_id:04d}",
        "category": category,
        "severity": severity,
        "description": description,
    }
    if details:
        bug["details"] = details
    bugs.append(bug)

nodes = data.get("nodes", {})
stats = data.get("stats", {})
node_ids = set(nodes.keys())

# ── 1. Schema Validation ──
REQUIRED_NODE_FIELDS = {"domain", "pointers"}
REQUIRED_POINTER_FIELDS = {"to", "weight", "reasons"}
VALID_STRENGTHS = {"primary", "supporting", "related", "tangential"}

for nid, node in nodes.items():
    # Check node required fields
    missing = REQUIRED_NODE_FIELDS - set(node.keys())
    if missing:
        add_bug("schema_validation", "high",
                f"Node {nid} missing required fields: {sorted(missing)}")

    if not isinstance(node.get("pointers"), list):
        add_bug("schema_validation", "high",
                f"Node {nid} 'pointers' is not a list or missing")
        continue

    for i, ptr in enumerate(node["pointers"]):
        if not isinstance(ptr, dict):
            add_bug("schema_validation", "high",
                    f"Node {nid} pointer[{i}] is not a dict")
            continue
        missing_ptr = REQUIRED_POINTER_FIELDS - set(ptr.keys())
        if missing_ptr:
            add_bug("schema_validation", "high",
                    f"Node {nid} pointer[{i}] missing required fields: {sorted(missing_ptr)}",
                    {"pointer": ptr})

        # Weight should be numeric 1-10
        w = ptr.get("weight")
        if w is not None:
            if not isinstance(w, (int, float)):
                add_bug("schema_validation", "medium",
                        f"Node {nid} pointer[{i}] weight is not numeric: {w}")
            elif w < 1 or w > 10:
                add_bug("schema_validation", "medium",
                        f"Node {nid} pointer[{i}] weight out of range: {w}")

        # Reasons should be a list of strings
        reasons = ptr.get("reasons")
        if reasons is not None:
            if not isinstance(reasons, list):
                add_bug("schema_validation", "medium",
                        f"Node {nid} pointer[{i}] reasons is not a list: {type(reasons).__name__}")
            else:
                for j, r in enumerate(reasons):
                    if not isinstance(r, str):
                        add_bug("schema_validation", "medium",
                                f"Node {nid} pointer[{i}] reasons[{j}] is not a string: {type(r).__name__}")

        # Strength validation
        strength = ptr.get("strength")
        if strength is not None and strength not in VALID_STRENGTHS:
            add_bug("schema_validation", "medium",
                    f"Node {nid} pointer[{i}] invalid strength: '{strength}'",
                    {"valid_values": sorted(VALID_STRENGTHS)})

# ── 2. Referential Integrity ──
dangling = []
for nid, node in nodes.items():
    for i, ptr in enumerate(node.get("pointers", [])):
        target = ptr.get("to")
        if target and target not in node_ids:
            dangling.append({"source": nid, "target": target, "pointer_index": i})

if dangling:
    add_bug("referential_integrity", "critical",
            f"Found {len(dangling)} dangling references (target node does not exist)",
            {"dangling_references": dangling})

# ── 3. Weight Consistency (asymmetric edges) ──
# Build edge map: (source, target) -> weight
edge_weights = {}
for nid, node in nodes.items():
    for ptr in node.get("pointers", []):
        target = ptr.get("to")
        weight = ptr.get("weight")
        if target and weight is not None:
            edge_weights[(nid, target)] = weight

inconsistent_weights = []
seen_pairs = set()
for (a, b), w_ab in edge_weights.items():
    pair = tuple(sorted([a, b]))
    if pair in seen_pairs:
        continue
    if (b, a) in edge_weights:
        w_ba = edge_weights[(b, a)]
        diff = abs(w_ab - w_ba)
        if diff > 5:
            inconsistent_weights.append({
                "nodeA": a, "nodeB": b,
                "weight_A_to_B": w_ab, "weight_B_to_A": w_ba,
                "difference": diff
            })
            seen_pairs.add(pair)

if inconsistent_weights:
    add_bug("weight_consistency", "high",
            f"Found {len(inconsistent_weights)} edge pairs with weight difference > 5",
            {"inconsistent_pairs": inconsistent_weights})

# ── 4. Strength/Weight Mismatches ──
# primary=7+, supporting=4-6, related=2-3, tangential=1
def expected_strength(weight):
    if weight >= 7: return "primary"
    if weight >= 4: return "supporting"
    if weight >= 2: return "related"
    return "tangential"

strength_mismatches = []
for nid, node in nodes.items():
    for i, ptr in enumerate(node.get("pointers", [])):
        w = ptr.get("weight")
        s = ptr.get("strength")
        if w is not None and s is not None:
            exp = expected_strength(w)
            if s != exp:
                strength_mismatches.append({
                    "node": nid, "pointer_index": i,
                    "to": ptr.get("to"),
                    "weight": w, "strength": s,
                    "expected_strength": exp
                })

# Also flag pointers missing strength field
missing_strength = []
for nid, node in nodes.items():
    for i, ptr in enumerate(node.get("pointers", [])):
        if "strength" not in ptr and ptr.get("weight") is not None:
            missing_strength.append({
                "node": nid, "pointer_index": i,
                "to": ptr.get("to"),
                "weight": ptr.get("weight"),
                "expected_strength": expected_strength(ptr["weight"])
            })

if strength_mismatches:
    add_bug("strength_weight_mismatch", "medium",
            f"Found {len(strength_mismatches)} edges where strength doesn't match weight rules",
            {"mismatches": strength_mismatches[:50],
             "total": len(strength_mismatches),
             "note": "Showing first 50" if len(strength_mismatches) > 50 else "Showing all"})

if missing_strength:
    add_bug("missing_strength", "low",
            f"Found {len(missing_strength)} edges missing 'strength' field",
            {"sample": missing_strength[:20],
             "total": len(missing_strength),
             "note": "Showing first 20" if len(missing_strength) > 20 else "Showing all"})

# ── 5. Duplicate Detection ──
# Exact duplicates: same source, target, weight
edge_list = []  # (source, target, weight)
for nid, node in nodes.items():
    for ptr in node.get("pointers", []):
        edge_list.append((nid, ptr.get("to"), ptr.get("weight")))

exact_dupes = defaultdict(int)
for e in edge_list:
    exact_dupes[e] += 1
exact_dupes = {k: v for k, v in exact_dupes.items() if v > 1}

if exact_dupes:
    dupe_details = [{"source": k[0], "target": k[1], "weight": k[2], "count": v}
                    for k, v in exact_dupes.items()]
    add_bug("duplicate_edges", "high",
            f"Found {len(exact_dupes)} exact duplicate edges (same source, target, weight)",
            {"duplicates": dupe_details})

# Near-duplicates: same source+target, different weights
edge_by_pair = defaultdict(list)
for nid, node in nodes.items():
    for ptr in node.get("pointers", []):
        edge_by_pair[(nid, ptr.get("to"))].append(ptr.get("weight"))

near_dupes = []
for (src, tgt), weights in edge_by_pair.items():
    if len(weights) > 1 and len(set(weights)) > 1:
        near_dupes.append({"source": src, "target": tgt, "weights": weights})

if near_dupes:
    add_bug("near_duplicate_edges", "medium",
            f"Found {len(near_dupes)} near-duplicate edges (same source/target, different weights)",
            {"near_duplicates": near_dupes})

# Also flag same source+target with same weight (but listed multiple times)
multi_same = []
for (src, tgt), weights in edge_by_pair.items():
    if len(weights) > 1 and len(set(weights)) == 1:
        multi_same.append({"source": src, "target": tgt, "weight": weights[0], "count": len(weights)})

if multi_same:
    add_bug("duplicate_edges_same_weight", "medium",
            f"Found {len(multi_same)} source/target pairs with multiple identical-weight edges",
            {"duplicates": multi_same})

# ── 6. Reason Field Validation ──
VALID_REASON_PREFIXES = {"shared_tags", "explicit_reference", "category_affinity", "backlink"}

bad_reasons = []
for nid, node in nodes.items():
    for i, ptr in enumerate(node.get("pointers", [])):
        for j, reason in enumerate(ptr.get("reasons", [])):
            if not isinstance(reason, str):
                continue
            if ":" not in reason:
                bad_reasons.append({
                    "node": nid, "pointer_index": i, "reason_index": j,
                    "reason": reason, "issue": "no colon separator"
                })
            else:
                prefix = reason.split(":")[0]
                if prefix not in VALID_REASON_PREFIXES:
                    bad_reasons.append({
                        "node": nid, "pointer_index": i, "reason_index": j,
                        "reason": reason, "issue": f"unknown reason type '{prefix}'"
                    })

if bad_reasons:
    add_bug("reason_validation", "medium",
            f"Found {len(bad_reasons)} malformed or unknown reason types",
            {"bad_reasons": bad_reasons[:30],
             "total": len(bad_reasons),
             "note": "Showing first 30" if len(bad_reasons) > 30 else "Showing all"})

# ── 7. Tag Consistency ──
# Collect tags referenced in shared_tags reasons and check if target nodes have them
# First build a map of node -> tags (from the node data if available, or from reasons)
node_tags = defaultdict(set)
for nid, node in nodes.items():
    # Check if node has explicit tags
    if "tags" in node:
        for t in node["tags"]:
            node_tags[nid].add(t)

# Collect all shared_tags references
tag_refs = []
for nid, node in nodes.items():
    for i, ptr in enumerate(node.get("pointers", [])):
        target = ptr.get("to")
        for reason in ptr.get("reasons", []):
            if isinstance(reason, str) and reason.startswith("shared_tags:"):
                tags_str = reason.split(":", 1)[1]
                tags = [t.strip() for t in tags_str.split(",")]
                tag_refs.append({"source": nid, "target": target, "tags": tags})

# If nodes have explicit tags, check consistency
if node_tags:
    tag_inconsistencies = []
    for ref in tag_refs:
        src_tags = node_tags.get(ref["source"], set())
        tgt_tags = node_tags.get(ref["target"], set())
        for tag in ref["tags"]:
            if src_tags and tag not in src_tags:
                tag_inconsistencies.append({
                    "source": ref["source"], "target": ref["target"],
                    "tag": tag, "issue": f"tag '{tag}' not found on source node"
                })
            if tgt_tags and tag not in tgt_tags:
                tag_inconsistencies.append({
                    "source": ref["source"], "target": ref["target"],
                    "tag": tag, "issue": f"tag '{tag}' not found on target node"
                })
    if tag_inconsistencies:
        add_bug("tag_consistency", "medium",
                f"Found {len(tag_inconsistencies)} tag references that don't match node tags",
                {"inconsistencies": tag_inconsistencies[:30],
                 "total": len(tag_inconsistencies),
                 "note": "Showing first 30" if len(tag_inconsistencies) > 30 else "Showing all"})
else:
    # Nodes don't have explicit tags - note this
    # Collect all unique tags from shared_tags reasons to report
    all_reason_tags = set()
    for ref in tag_refs:
        all_reason_tags.update(ref["tags"])
    add_bug("tag_consistency", "info",
            f"Nodes lack explicit 'tags' field - cannot cross-check {len(all_reason_tags)} unique tags from shared_tags reasons",
            {"unique_tags_in_reasons": sorted(all_reason_tags),
             "note": "Nodes only have 'domain' and 'pointers' - no tags to validate against"})

# ── 8. Stats Validation ──
actual_node_count = len(nodes)
actual_edge_count = sum(len(node.get("pointers", [])) for node in nodes.values())

reported_nodes = stats.get("total_nodes")
reported_edges = stats.get("total_edges")

if reported_nodes != actual_node_count:
    add_bug("stats_validation", "high",
            f"Stats total_nodes mismatch: reported={reported_nodes}, actual={actual_node_count}")

if reported_edges != actual_edge_count:
    add_bug("stats_validation", "high",
            f"Stats total_edges mismatch: reported={reported_edges}, actual={actual_edge_count}")

# Check unique_edges
reported_unique = stats.get("unique_edges")
unique_edges = len(set((nid, ptr.get("to")) for nid, node in nodes.items()
                       for ptr in node.get("pointers", [])))
if reported_unique != unique_edges:
    add_bug("stats_validation", "medium",
            f"Stats unique_edges mismatch: reported={reported_unique}, actual={unique_edges}")

# Check edge_type_counts
reported_type_counts = stats.get("edge_type_counts", {})
actual_type_counts = defaultdict(int)
for nid, node in nodes.items():
    for ptr in node.get("pointers", []):
        for reason in ptr.get("reasons", []):
            if isinstance(reason, str) and ":" in reason:
                rtype = reason.split(":")[0]
                actual_type_counts[rtype] += 1

for rtype, reported_count in reported_type_counts.items():
    actual = actual_type_counts.get(rtype, 0)
    if reported_count != actual:
        add_bug("stats_validation", "medium",
                f"Stats edge_type_counts['{rtype}'] mismatch: reported={reported_count}, actual={actual}")

for rtype in actual_type_counts:
    if rtype not in reported_type_counts:
        add_bug("stats_validation", "low",
                f"Edge type '{rtype}' exists in data but not in stats.edge_type_counts (count={actual_type_counts[rtype]})")

# ── Summary ──
summary = {
    "scanner": "Bug Hunter B2 - Data Integrity",
    "timestamp": "2026-03-12",
    "file_scanned": "storage/pointer-network.json",
    "actual_stats": {
        "total_nodes": actual_node_count,
        "total_edges": actual_edge_count,
        "unique_edges": unique_edges,
        "edge_type_counts": dict(actual_type_counts)
    },
    "reported_stats": dict(stats),
    "bug_counts": {
        "total": len(bugs),
        "by_category": {},
        "by_severity": {}
    },
    "bugs": bugs
}

for b in bugs:
    cat = b["category"]
    sev = b["severity"]
    summary["bug_counts"]["by_category"][cat] = summary["bug_counts"]["by_category"].get(cat, 0) + 1
    summary["bug_counts"]["by_severity"][sev] = summary["bug_counts"]["by_severity"].get(sev, 0) + 1

output_path = "/home/user/Claude/storage/pointer-routes/bugs_b2_data_integrity.json"
with open(output_path, "w") as f:
    json.dump(summary, f, indent=2)

print(f"Scan complete. Found {len(bugs)} bugs.")
print(f"Results written to: {output_path}")
print(f"\nActual stats: {actual_node_count} nodes, {actual_edge_count} edges, {unique_edges} unique edges")
print(f"Reported stats: {reported_nodes} nodes, {reported_edges} edges, {reported_unique} unique edges")
print(f"\nBugs by category:")
for cat, count in summary["bug_counts"]["by_category"].items():
    print(f"  {cat}: {count}")
print(f"\nBugs by severity:")
for sev, count in summary["bug_counts"]["by_severity"].items():
    print(f"  {sev}: {count}")
