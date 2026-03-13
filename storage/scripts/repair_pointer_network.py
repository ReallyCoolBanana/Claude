#!/usr/bin/env python3
"""Repair Team 2: Fix orphan nodes, missing backlinks, and unreachable scripts."""

import json
import sys

NETWORK_PATH = "/home/user/Claude/storage/pointer-network.json"

def load_network():
    with open(NETWORK_PATH) as f:
        return json.load(f)

def save_network(data):
    with open(NETWORK_PATH, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

def has_pointer(data, from_id, to_id):
    """Check if from_id already has a pointer to to_id."""
    node = data["nodes"].get(from_id, {})
    return any(p["to"] == to_id for p in node.get("pointers", []))

def add_pointer(data, from_id, to_id, weight, reasons, strength=None):
    """Add a pointer if it doesn't already exist."""
    if has_pointer(data, from_id, to_id):
        return False
    if from_id not in data["nodes"]:
        return False
    pointer = {"to": to_id, "weight": weight, "reasons": reasons}
    if strength:
        pointer["strength"] = strength
    data["nodes"][from_id]["pointers"].append(pointer)
    return True

def get_incoming(data, target_id):
    """Get all nodes that point to target_id."""
    incoming = []
    for nid, node in data["nodes"].items():
        for p in node.get("pointers", []):
            if p["to"] == target_id:
                incoming.append((nid, p["weight"]))
    return incoming

def main():
    data = load_network()
    changes = []

    # =========================================================================
    # TASK 1: Fix orphan scripts (SCR-0046, SCR-0050, SCR-0051)
    # =========================================================================

    # SCR-0046: data-gathering, search, public-api, python
    # Best SOPs: SOP-023 (data collection/search), SOP-017 (search-related)
    orphan_sop_links = {
        "SCR-0046": [
            ("SOP-023", 4, ["shared_tags:search,python", "category_affinity:data-collection"]),
            ("SOP-017", 3, ["shared_tags:search,python", "category_affinity:data-collection"]),
        ],
        "SCR-0050": [
            ("SOP-023", 4, ["shared_tags:search,indexing,python", "category_affinity:search"]),
            ("SOP-028", 3, ["shared_tags:indexing,python", "category_affinity:indexing"]),
        ],
        "SCR-0051": [
            ("SOP-028", 4, ["shared_tags:indexing,automation,python", "category_affinity:indexing"]),
            ("SOP-023", 3, ["shared_tags:indexing,python", "category_affinity:indexing"]),
        ],
    }

    for scr_id, sop_links in orphan_sop_links.items():
        for sop_id, weight, reasons in sop_links:
            # SOP -> SCR (make script discoverable)
            if add_pointer(data, sop_id, scr_id, weight, reasons):
                changes.append(f"Added {sop_id} -> {scr_id} (weight {weight})")
            # SCR -> SOP (backlink)
            back_weight = max(3, weight - 1)
            if add_pointer(data, scr_id, sop_id, back_weight, reasons + ["backlink:reciprocal"]):
                changes.append(f"Added {scr_id} -> {sop_id} (weight {back_weight}, backlink)")

    # =========================================================================
    # TASK 2: Fix SOP-029 orphan
    # =========================================================================

    # SOP-028 -> SOP-029 (pointer network ops -> route tracking)
    if add_pointer(data, "SOP-028", "SOP-029", 9,
                   ["explicit_reference:related_to", "shared_tags:coordination,indexing,sop",
                    "category_affinity:infrastructure"],
                   strength="primary"):
        changes.append("Added SOP-028 -> SOP-029 (weight 9, primary)")

    # SOP-012 -> SOP-029 (monitoring -> route tracking)
    if add_pointer(data, "SOP-012", "SOP-029", 6,
                   ["explicit_reference:related_to", "shared_tags:coordination,monitoring,sop",
                    "category_affinity:infrastructure"]):
        changes.append("Added SOP-012 -> SOP-029 (weight 6)")

    # =========================================================================
    # TASK 3: Add missing backlinks for all edges with weight >= 5
    # =========================================================================

    missing_backlinks = []
    for nid, node in data["nodes"].items():
        for p in node.get("pointers", []):
            if p["weight"] >= 5:
                target = p["to"]
                if not has_pointer(data, target, nid):
                    missing_backlinks.append({
                        "from": nid,
                        "to": target,
                        "weight": p["weight"],
                        "strength": p.get("strength", ""),
                        "reasons": p.get("reasons", []),
                    })

    for mb in missing_backlinks:
        back_weight = max(3, mb["weight"] - 2)
        strength = mb["strength"] if mb["strength"] else None
        if add_pointer(data, mb["to"], mb["from"], back_weight,
                       ["backlink:reciprocal"], strength=strength):
            changes.append(
                f"Added backlink {mb['to']} -> {mb['from']} "
                f"(weight {back_weight}, reverse of weight-{mb['weight']} edge)"
            )

    # =========================================================================
    # TASK 4: Fix unreachable scripts (SCR-0045 through SCR-0051)
    # Ensure each has at least one incoming pointer from an SOP
    # =========================================================================

    # Check which still need SOP incoming pointers after previous fixes
    sop_links_for_unreachable = {
        "SCR-0045": ("SOP-023", 3, ["shared_tags:data-gathering,python", "category_affinity:data-collection"]),
        "SCR-0047": ("SOP-023", 4, ["shared_tags:search,indexing,python,automation", "category_affinity:automation"]),
        "SCR-0048": ("SOP-023", 3, ["shared_tags:search,indexing,python", "category_affinity:search"]),
        "SCR-0049": ("SOP-028", 4, ["shared_tags:indexing,python", "category_affinity:indexing"]),
    }

    for scr_id, (sop_id, weight, reasons) in sop_links_for_unreachable.items():
        # Check if any SOP already points to this script
        incoming = get_incoming(data, scr_id)
        has_sop_incoming = any(src.startswith("SOP-") for src, _ in incoming)
        if not has_sop_incoming:
            if add_pointer(data, sop_id, scr_id, weight, reasons):
                changes.append(f"Added {sop_id} -> {scr_id} (weight {weight}, reachability fix)")
            # Also add backlink
            back_weight = max(3, weight - 1)
            if add_pointer(data, scr_id, sop_id, back_weight, reasons + ["backlink:reciprocal"]):
                changes.append(f"Added {scr_id} -> {sop_id} (weight {back_weight}, backlink)")

    # =========================================================================
    # Update stats
    # =========================================================================
    total_edges = sum(len(n.get("pointers", [])) for n in data["nodes"].values())
    unique_edges = set()
    edge_type_counts = {}
    for nid, node in data["nodes"].items():
        for p in node.get("pointers", []):
            unique_edges.add((nid, p["to"]))
            for r in p.get("reasons", []):
                etype = r.split(":")[0]
                edge_type_counts[etype] = edge_type_counts.get(etype, 0) + 1

    data["stats"]["total_edges"] = total_edges
    data["stats"]["unique_edges"] = len(unique_edges)
    data["stats"]["edge_type_counts"] = edge_type_counts

    save_network(data)

    # Print summary
    print(f"\n{'='*60}")
    print(f"REPAIR TEAM 2 SUMMARY")
    print(f"{'='*60}")
    print(f"Total changes made: {len(changes)}")
    print()
    for i, c in enumerate(changes, 1):
        print(f"  {i:2d}. {c}")
    print()

    # Verify fixes
    print("VERIFICATION:")
    for nid in ["SCR-0046", "SCR-0050", "SCR-0051", "SOP-029"]:
        incoming = get_incoming(data, nid)
        print(f"  {nid} incoming pointers: {len(incoming)} (was 0)")

    print()
    for scr_id in [f"SCR-{i:04d}" for i in range(45, 52)]:
        incoming = get_incoming(data, scr_id)
        sop_incoming = [(s, w) for s, w in incoming if s.startswith("SOP-")]
        print(f"  {scr_id} SOP incoming: {sop_incoming}")

    # Check remaining missing backlinks
    remaining = 0
    for nid, node in data["nodes"].items():
        for p in node.get("pointers", []):
            if p["weight"] >= 5 and not has_pointer(data, p["to"], nid):
                remaining += 1
    print(f"\n  Remaining missing backlinks (weight>=5): {remaining}")

    print(f"\n  Updated stats: {json.dumps(data['stats'], indent=4)}")

if __name__ == "__main__":
    main()
