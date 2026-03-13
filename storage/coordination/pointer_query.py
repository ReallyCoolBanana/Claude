#!/usr/bin/env python3
"""
PointerNetworkQuery — Query language and API for the pointer network.

Provides graph traversal, search, impact analysis, and edge suggestion
capabilities over the pointer-network.json graph. Designed for agent use
both programmatically and via a mini query language.

Query Language (PNQ - Pointer Network Query):
  FIND nodes WHERE domain = "scripts" AND weight >= 5
  PATH FROM "SOP-012" TO "SRC-0001" STRATEGY weight
  NEIGHBORS OF "SCR-0043" DEPTH 2 STRENGTH primary
  IMPACT OF "SCR-0043"
  SUGGEST EDGES FOR "SOP-015"
  SHARED BETWEEN "SOP-012" AND "SOP-015"

Usage:
  # Python API
  pq = PointerNetworkQuery()
  pq.neighbors("SCR-0043", depth=2, strength="primary")
  pq.find_path("SOP-012", "SRC-0001", strategy="weight")
  pq.search(domain="scripts", min_weight=5)
  pq.impact("SCR-0043")
  pq.suggest_edges("SOP-015")

  # CLI
  python3 pointer_query.py query 'FIND nodes WHERE domain = "scripts" AND weight >= 5'
  python3 pointer_query.py neighbors SCR-0043 --depth 2 --strength primary
  python3 pointer_query.py path SOP-012 SRC-0001 --strategy weight
  python3 pointer_query.py impact SCR-0043
  python3 pointer_query.py suggest SOP-015
"""

import json
import heapq
import re
import sys
from pathlib import Path
from collections import defaultdict, deque
from typing import Optional

POINTER_NETWORK = Path(__file__).parent.parent / "pointer-network.json"


class PointerNetworkQuery:
    """Query engine for the pointer network graph."""

    def __init__(self, network_path: str = None):
        path = Path(network_path) if network_path else POINTER_NETWORK
        with open(path) as f:
            self._data = json.load(f)
        self._nodes = self._data.get("nodes", {})
        self._tag_clusters = self._data.get("tag_clusters", {})
        self._strength_levels = self._data.get("strength_levels", {})
        # Build reverse adjacency for backlink traversal
        self._reverse_adj = defaultdict(list)
        for node_id, node_data in self._nodes.items():
            for ptr in node_data.get("pointers", []):
                self._reverse_adj[ptr["to"]].append({
                    "from": node_id,
                    "weight": ptr.get("weight", 0),
                    "strength": ptr.get("strength"),
                    "reasons": ptr.get("reasons", []),
                })

    # ── Core Properties ──────────────────────────────────────────────

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(n.get("pointers", [])) for n in self._nodes.values())

    @property
    def domains(self) -> list:
        return sorted(set(n.get("domain", "") for n in self._nodes.values()))

    def node(self, node_id: str) -> Optional[dict]:
        """Get full node data by ID."""
        data = self._nodes.get(node_id)
        if data is None:
            return None
        return {"id": node_id, **data}

    def node_ids(self, domain: str = None) -> list:
        """List all node IDs, optionally filtered by domain."""
        if domain:
            return [nid for nid, nd in self._nodes.items()
                    if nd.get("domain") == domain]
        return list(self._nodes.keys())

    # ── neighbors() ──────────────────────────────────────────────────

    def neighbors(self, node_id: str, depth: int = 1,
                  min_weight: int = 0, strength: str = None,
                  direction: str = "outgoing") -> list:
        """
        Find nodes within `depth` hops of `node_id`.

        Args:
            node_id: Starting node.
            depth: Max hop distance (1 = direct neighbors).
            min_weight: Minimum pointer weight to follow.
            strength: Filter to this strength level (primary/supporting/related/tangential).
            direction: "outgoing" (default), "incoming", or "both".

        Returns:
            List of dicts: {id, distance, weight, strength, path}
        """
        if node_id not in self._nodes:
            return []

        visited = {node_id: 0}
        result = []
        queue = deque([(node_id, 0, [node_id])])

        while queue:
            current, dist, path = queue.popleft()
            if dist >= depth:
                continue

            edges = []
            if direction in ("outgoing", "both"):
                for ptr in self._nodes.get(current, {}).get("pointers", []):
                    edges.append((ptr["to"], ptr))
            if direction in ("incoming", "both"):
                for rev in self._reverse_adj.get(current, []):
                    edges.append((rev["from"], rev))

            for target, ptr_data in edges:
                w = ptr_data.get("weight", 0)
                s = ptr_data.get("strength")

                if w < min_weight:
                    continue
                if strength and s != strength:
                    continue

                new_dist = dist + 1
                if target not in visited or visited[target] > new_dist:
                    visited[target] = new_dist
                    new_path = path + [target]
                    result.append({
                        "id": target,
                        "distance": new_dist,
                        "weight": w,
                        "strength": s,
                        "path": new_path,
                        "domain": self._nodes.get(target, {}).get("domain"),
                    })
                    queue.append((target, new_dist, new_path))

        # Sort by distance, then descending weight
        result.sort(key=lambda x: (x["distance"], -x["weight"]))
        return result

    # ── find_path() ──────────────────────────────────────────────────

    def find_path(self, from_node: str, to_node: str,
                  strategy: str = "weight") -> Optional[dict]:
        """
        Find a path between two nodes.

        Strategies:
            "weight"  — maximize total weight (Dijkstra on negative weights)
            "hops"    — minimize hop count (BFS)
            "strength" — prefer primary > supporting > related edges
            "hybrid"  — score = weight * strength_bonus, maximize

        Returns:
            {path: [...], total_weight: int, hops: int, edges: [...]} or None
        """
        if from_node not in self._nodes or to_node not in self._nodes:
            return None

        if strategy == "hops":
            return self._bfs_path(from_node, to_node)
        elif strategy == "weight":
            return self._dijkstra_path(from_node, to_node, mode="weight")
        elif strategy == "strength":
            return self._dijkstra_path(from_node, to_node, mode="strength")
        elif strategy == "hybrid":
            return self._dijkstra_path(from_node, to_node, mode="hybrid")
        else:
            raise ValueError(f"Unknown strategy: {strategy}")

    def _strength_score(self, strength: str) -> float:
        """Convert strength label to numeric score."""
        return {"primary": 4, "supporting": 3, "related": 2,
                "tangential": 1}.get(strength or "", 0.5)

    def _bfs_path(self, start: str, end: str) -> Optional[dict]:
        """Shortest path by hop count (BFS)."""
        visited = {start: None}
        queue = deque([start])

        while queue:
            current = queue.popleft()
            if current == end:
                # Reconstruct
                path = []
                node = end
                while node is not None:
                    path.append(node)
                    node = visited[node]
                path.reverse()
                return self._build_path_result(path)

            for ptr in self._nodes.get(current, {}).get("pointers", []):
                target = ptr["to"]
                if target not in visited:
                    visited[target] = current
                    queue.append(target)

        return None

    def _dijkstra_path(self, start: str, end: str,
                       mode: str = "weight") -> Optional[dict]:
        """Best path by weight/strength/hybrid (Dijkstra, maximizing score)."""
        # Use negative costs so heapq finds max-weight path
        INF = float("inf")
        dist = defaultdict(lambda: INF)
        dist[start] = 0
        prev = {}
        heap = [(0, start)]

        while heap:
            cost, current = heapq.heappop(heap)
            if current == end:
                path = []
                node = end
                while node in prev:
                    path.append(node)
                    node = prev[node]
                path.append(start)
                path.reverse()
                return self._build_path_result(path)

            if cost > dist[current]:
                continue

            for ptr in self._nodes.get(current, {}).get("pointers", []):
                target = ptr["to"]
                w = ptr.get("weight", 0)
                s = self._strength_score(ptr.get("strength"))

                if mode == "weight":
                    edge_cost = -w  # negate to maximize
                elif mode == "strength":
                    edge_cost = -s
                else:  # hybrid
                    edge_cost = -(w * s)

                new_cost = cost + edge_cost
                if new_cost < dist[target]:
                    dist[target] = new_cost
                    prev[target] = current
                    heapq.heappush(heap, (new_cost, target))

        return None

    def _build_path_result(self, path: list) -> dict:
        """Construct a path result dict from a list of node IDs."""
        edges = []
        total_weight = 0
        for i in range(len(path) - 1):
            src, dst = path[i], path[i + 1]
            ptr = self._find_edge(src, dst)
            edge_info = {
                "from": src,
                "to": dst,
                "weight": ptr.get("weight", 0) if ptr else 0,
                "strength": ptr.get("strength") if ptr else None,
                "reasons": ptr.get("reasons", []) if ptr else [],
            }
            edges.append(edge_info)
            total_weight += edge_info["weight"]

        return {
            "path": path,
            "hops": len(path) - 1,
            "total_weight": total_weight,
            "edges": edges,
        }

    def _find_edge(self, src: str, dst: str) -> Optional[dict]:
        """Find a pointer from src to dst."""
        for ptr in self._nodes.get(src, {}).get("pointers", []):
            if ptr["to"] == dst:
                return ptr
        return None

    # ── search() ─────────────────────────────────────────────────────

    def search(self, tags: list = None, domain: str = None,
               min_weight: int = 0, strength: str = None,
               reasons_contain: str = None,
               limit: int = 50) -> list:
        """
        Search for nodes matching criteria.

        Args:
            tags: Filter to nodes connected via edges with matching reason tags.
            domain: Filter to this domain (scripts, sops, knowledge-base, etc.).
            min_weight: Only include edges with weight >= this value.
            strength: Only include edges with this strength level.
            reasons_contain: Only include edges whose reasons contain this substring.
            limit: Max results.

        Returns:
            List of matching node dicts with their qualifying edges.
        """
        results = []

        for node_id, node_data in self._nodes.items():
            if domain and node_data.get("domain") != domain:
                continue

            # Check if this node has qualifying edges (as source)
            qualifying_edges = []
            for ptr in node_data.get("pointers", []):
                w = ptr.get("weight", 0)
                s = ptr.get("strength")

                if w < min_weight:
                    continue
                if strength and s != strength:
                    continue

                reasons = ptr.get("reasons", [])
                if tags:
                    # Check if any reason contains any of the requested tags
                    reason_text = " ".join(reasons).lower()
                    if not any(t.lower() in reason_text for t in tags):
                        continue
                if reasons_contain:
                    reason_text = " ".join(reasons).lower()
                    if reasons_contain.lower() not in reason_text:
                        continue

                qualifying_edges.append({
                    "to": ptr["to"],
                    "weight": w,
                    "strength": s,
                    "reasons": reasons,
                })

            if qualifying_edges:
                results.append({
                    "id": node_id,
                    "domain": node_data.get("domain"),
                    "edge_count": len(qualifying_edges),
                    "max_weight": max(e["weight"] for e in qualifying_edges),
                    "edges": qualifying_edges,
                })

            if len(results) >= limit:
                break

        # Sort by max qualifying weight descending
        results.sort(key=lambda x: -x["max_weight"])
        return results

    # ── impact() ─────────────────────────────────────────────────────

    def impact(self, node_id: str, max_depth: int = 3) -> dict:
        """
        Analyze the impact if a node changes.

        Returns which nodes are affected (depend on this node via incoming edges),
        grouped by distance and strength.

        Args:
            node_id: The node that might change.
            max_depth: How many hops of reverse dependencies to trace.

        Returns:
            {node_id, direct_dependents, transitive_dependents,
             by_strength, by_domain, critical_paths}
        """
        if node_id not in self._nodes:
            return {"error": f"Node {node_id} not found"}

        # Trace reverse edges (who points TO this node)
        all_affected = {}
        queue = deque([(node_id, 0)])
        visited = {node_id}

        while queue:
            current, dist = queue.popleft()
            if dist >= max_depth:
                continue

            for rev in self._reverse_adj.get(current, []):
                src = rev["from"]
                if src not in visited:
                    visited.add(src)
                    all_affected[src] = {
                        "distance": dist + 1,
                        "weight": rev["weight"],
                        "strength": rev.get("strength"),
                        "domain": self._nodes.get(src, {}).get("domain"),
                    }
                    queue.append((src, dist + 1))

        # Group results
        by_strength = defaultdict(list)
        by_domain = defaultdict(list)
        by_distance = defaultdict(list)

        for nid, info in all_affected.items():
            s = info["strength"] or "unclassified"
            by_strength[s].append(nid)
            by_domain[info["domain"] or "unknown"].append(nid)
            by_distance[info["distance"]].append(nid)

        # Critical paths: high-weight direct dependents
        critical = [
            nid for nid, info in all_affected.items()
            if info["distance"] == 1 and info["weight"] >= 7
        ]

        return {
            "node_id": node_id,
            "total_affected": len(all_affected),
            "direct_dependents": len(by_distance.get(1, [])),
            "transitive_dependents": len(all_affected) - len(by_distance.get(1, [])),
            "by_distance": {str(k): v for k, v in sorted(by_distance.items())},
            "by_strength": dict(by_strength),
            "by_domain": dict(by_domain),
            "critical_dependents": critical,
            "all_affected": all_affected,
        }

    # ── suggest_edges() ──────────────────────────────────────────────

    def suggest_edges(self, node_id: str, top_n: int = 10) -> list:
        """
        Suggest edges that should be added to a node.

        Uses three heuristics:
        1. Tag affinity: Nodes that share many tags via common neighbors.
        2. Structural holes: Nodes reachable in 2 hops but not 1.
        3. Domain bridges: High-value nodes in underrepresented domains.

        Returns:
            List of {target, score, reason, via} dicts.
        """
        if node_id not in self._nodes:
            return []

        node_data = self._nodes[node_id]
        current_targets = {p["to"] for p in node_data.get("pointers", [])}
        current_domain = node_data.get("domain")

        suggestions = {}

        # 1. Structural holes: reachable in 2 hops but not 1
        for ptr in node_data.get("pointers", []):
            neighbor_id = ptr["to"]
            neighbor_data = self._nodes.get(neighbor_id, {})
            for ptr2 in neighbor_data.get("pointers", []):
                target = ptr2["to"]
                if target == node_id or target in current_targets:
                    continue
                # Score by average weight of the two-hop path
                score = (ptr.get("weight", 0) + ptr2.get("weight", 0)) / 2
                key = target
                if key not in suggestions or suggestions[key]["score"] < score:
                    suggestions[key] = {
                        "target": target,
                        "score": round(score, 1),
                        "reason": "structural_hole",
                        "via": neighbor_id,
                        "domain": self._nodes.get(target, {}).get("domain"),
                    }

        # 2. Tag affinity from tag_clusters
        node_tags = set()
        for ptr in node_data.get("pointers", []):
            for reason in ptr.get("reasons", []):
                if reason.startswith("shared_tags:"):
                    node_tags.update(reason.split(":")[1].split(","))

        for tag in node_tags:
            cluster = self._tag_clusters.get(tag, {})
            for cluster_node in cluster.get("nodes", []):
                if cluster_node == node_id or cluster_node in current_targets:
                    continue
                key = cluster_node
                tag_score = 2.0  # base score for tag affinity
                if key in suggestions:
                    suggestions[key]["score"] += tag_score
                    suggestions[key]["reason"] = "tag_affinity+structural_hole"
                else:
                    suggestions[key] = {
                        "target": cluster_node,
                        "score": tag_score,
                        "reason": "tag_affinity",
                        "via": f"tag:{tag}",
                        "domain": self._nodes.get(cluster_node, {}).get("domain"),
                    }

        # 3. Domain bridges: find high-weight nodes in domains we have few edges to
        domain_edge_counts = defaultdict(int)
        for ptr in node_data.get("pointers", []):
            t_domain = self._nodes.get(ptr["to"], {}).get("domain")
            if t_domain:
                domain_edge_counts[t_domain] += 1

        underrepresented = [
            d for d in self.domains
            if d != current_domain and domain_edge_counts.get(d, 0) < 2
        ]

        for d in underrepresented:
            # Find highest-connected node in this domain
            best = None
            best_edges = 0
            for nid in self.node_ids(domain=d):
                edge_c = len(self._nodes[nid].get("pointers", []))
                if edge_c > best_edges and nid not in current_targets:
                    best = nid
                    best_edges = edge_c
            if best:
                key = best
                bridge_score = 1.5
                if key in suggestions:
                    suggestions[key]["score"] += bridge_score
                else:
                    suggestions[key] = {
                        "target": best,
                        "score": bridge_score,
                        "reason": "domain_bridge",
                        "via": f"domain:{d}",
                        "domain": d,
                    }

        # Sort by score descending, return top N
        ranked = sorted(suggestions.values(), key=lambda x: -x["score"])
        return ranked[:top_n]

    # ── shared_neighbors() ───────────────────────────────────────────

    def shared_neighbors(self, node_a: str, node_b: str) -> dict:
        """
        Find nodes that both node_a and node_b point to.

        Useful for: "Which SOPs share the most common scripts?"
        """
        targets_a = {p["to"] for p in self._nodes.get(node_a, {}).get("pointers", [])}
        targets_b = {p["to"] for p in self._nodes.get(node_b, {}).get("pointers", [])}
        shared = targets_a & targets_b

        shared_details = []
        for nid in sorted(shared):
            edge_a = self._find_edge(node_a, nid)
            edge_b = self._find_edge(node_b, nid)
            shared_details.append({
                "id": nid,
                "domain": self._nodes.get(nid, {}).get("domain"),
                "weight_from_a": edge_a.get("weight", 0) if edge_a else 0,
                "weight_from_b": edge_b.get("weight", 0) if edge_b else 0,
                "combined_weight": (
                    (edge_a.get("weight", 0) if edge_a else 0) +
                    (edge_b.get("weight", 0) if edge_b else 0)
                ),
            })

        shared_details.sort(key=lambda x: -x["combined_weight"])

        return {
            "node_a": node_a,
            "node_b": node_b,
            "shared_count": len(shared),
            "only_a": len(targets_a - targets_b),
            "only_b": len(targets_b - targets_a),
            "shared_nodes": shared_details,
        }

    def most_shared_pairs(self, domain: str = "sops", top_n: int = 10) -> list:
        """
        Find pairs of nodes (in a domain) that share the most common neighbors.

        Answers: "Which SOPs share the most common scripts?"
        """
        ids = self.node_ids(domain=domain)
        pairs = []

        for i in range(len(ids)):
            targets_i = {p["to"] for p in self._nodes.get(ids[i], {}).get("pointers", [])}
            for j in range(i + 1, len(ids)):
                targets_j = {p["to"] for p in self._nodes.get(ids[j], {}).get("pointers", [])}
                shared = targets_i & targets_j
                if shared:
                    pairs.append({
                        "node_a": ids[i],
                        "node_b": ids[j],
                        "shared_count": len(shared),
                        "shared_nodes": sorted(shared),
                    })

        pairs.sort(key=lambda x: -x["shared_count"])
        return pairs[:top_n]

    # ── stats() ──────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Return summary statistics about the pointer network."""
        weight_dist = defaultdict(int)
        strength_dist = defaultdict(int)
        domain_dist = defaultdict(int)
        reason_dist = defaultdict(int)

        for node_data in self._nodes.values():
            domain_dist[node_data.get("domain", "unknown")] += 1
            for ptr in node_data.get("pointers", []):
                weight_dist[ptr.get("weight", 0)] += 1
                s = ptr.get("strength", "unclassified")
                strength_dist[s] += 1
                for reason in ptr.get("reasons", []):
                    rtype = reason.split(":")[0] if ":" in reason else reason
                    reason_dist[rtype] += 1

        return {
            "nodes": self.node_count,
            "edges": self.edge_count,
            "domains": dict(domain_dist),
            "weight_distribution": dict(sorted(weight_dist.items())),
            "strength_distribution": dict(strength_dist),
            "reason_types": dict(reason_dist),
        }

    # ── Query Language Parser ────────────────────────────────────────

    def execute(self, query: str) -> dict:
        """
        Execute a PNQ (Pointer Network Query) string.

        Syntax examples:
          FIND nodes WHERE domain = "scripts" AND weight >= 5
          PATH FROM "SOP-012" TO "SRC-0001" STRATEGY weight
          NEIGHBORS OF "SCR-0043" DEPTH 2 STRENGTH primary
          IMPACT OF "SCR-0043"
          SUGGEST EDGES FOR "SOP-015"
          SHARED BETWEEN "SOP-012" AND "SOP-015"
          MOST SHARED IN "sops"
          STATS

        Returns:
            Query result dict.
        """
        q = query.strip()
        q_upper = q.upper()

        # STATS
        if q_upper == "STATS":
            return {"query": q, "result": self.stats()}

        # FIND nodes WHERE ...
        if q_upper.startswith("FIND"):
            return self._parse_find(q)

        # PATH FROM ... TO ... [STRATEGY ...]
        if q_upper.startswith("PATH"):
            return self._parse_path(q)

        # NEIGHBORS OF ... [DEPTH n] [STRENGTH s] [MIN_WEIGHT w]
        if q_upper.startswith("NEIGHBORS"):
            return self._parse_neighbors(q)

        # IMPACT OF ...
        if q_upper.startswith("IMPACT"):
            return self._parse_impact(q)

        # SUGGEST EDGES FOR ...
        if q_upper.startswith("SUGGEST"):
            return self._parse_suggest(q)

        # SHARED BETWEEN ... AND ...
        if q_upper.startswith("SHARED"):
            return self._parse_shared(q)

        # MOST SHARED IN ...
        if q_upper.startswith("MOST"):
            return self._parse_most_shared(q)

        return {"error": f"Unrecognized query: {q}",
                "hint": "Use FIND, PATH, NEIGHBORS, IMPACT, SUGGEST, SHARED, MOST SHARED, or STATS"}

    def _extract_quoted(self, text: str) -> list:
        """Extract all quoted strings from text."""
        return re.findall(r'"([^"]*)"', text)

    def _parse_find(self, q: str) -> dict:
        """Parse: FIND nodes WHERE domain = "scripts" AND weight >= 5"""
        kwargs = {}
        quoted = self._extract_quoted(q)

        # Domain
        m = re.search(r'domain\s*=\s*"([^"]*)"', q, re.I)
        if m:
            kwargs["domain"] = m.group(1)

        # weight >= N
        m = re.search(r'weight\s*>=\s*(\d+)', q, re.I)
        if m:
            kwargs["min_weight"] = int(m.group(1))

        # strength = "..."
        m = re.search(r'strength\s*=\s*"([^"]*)"', q, re.I)
        if m:
            kwargs["strength"] = m.group(1)

        # tags
        m = re.search(r'tags?\s*=\s*"([^"]*)"', q, re.I)
        if m:
            kwargs["tags"] = [t.strip() for t in m.group(1).split(",")]

        # reasons
        m = re.search(r'reasons?\s+contain\s+"([^"]*)"', q, re.I)
        if m:
            kwargs["reasons_contain"] = m.group(1)

        # limit
        m = re.search(r'limit\s+(\d+)', q, re.I)
        if m:
            kwargs["limit"] = int(m.group(1))

        results = self.search(**kwargs)
        return {
            "query": q,
            "params": kwargs,
            "count": len(results),
            "results": results,
        }

    def _parse_path(self, q: str) -> dict:
        """Parse: PATH FROM "SOP-012" TO "SRC-0001" STRATEGY weight"""
        quoted = self._extract_quoted(q)
        if len(quoted) < 2:
            return {"error": "PATH requires FROM and TO node IDs in quotes"}

        from_node, to_node = quoted[0], quoted[1]
        strategy = "weight"
        m = re.search(r'STRATEGY\s+(\w+)', q, re.I)
        if m:
            strategy = m.group(1).lower()

        result = self.find_path(from_node, to_node, strategy=strategy)
        return {
            "query": q,
            "from": from_node,
            "to": to_node,
            "strategy": strategy,
            "result": result if result else {"message": "No path found"},
        }

    def _parse_neighbors(self, q: str) -> dict:
        """Parse: NEIGHBORS OF "SCR-0043" DEPTH 2 STRENGTH primary"""
        quoted = self._extract_quoted(q)
        if not quoted:
            return {"error": "NEIGHBORS requires a node ID in quotes"}

        node_id = quoted[0]
        depth = 1
        strength = None
        min_weight = 0

        m = re.search(r'DEPTH\s+(\d+)', q, re.I)
        if m:
            depth = int(m.group(1))

        m = re.search(r'STRENGTH\s+(\w+)', q, re.I)
        if m:
            strength = m.group(1).lower()

        m = re.search(r'MIN_WEIGHT\s+(\d+)', q, re.I)
        if m:
            min_weight = int(m.group(1))

        results = self.neighbors(node_id, depth=depth, min_weight=min_weight,
                                 strength=strength)
        return {
            "query": q,
            "node": node_id,
            "depth": depth,
            "strength": strength,
            "min_weight": min_weight,
            "count": len(results),
            "results": results,
        }

    def _parse_impact(self, q: str) -> dict:
        """Parse: IMPACT OF "SCR-0043" """
        quoted = self._extract_quoted(q)
        if not quoted:
            return {"error": "IMPACT requires a node ID in quotes"}
        result = self.impact(quoted[0])
        return {"query": q, "result": result}

    def _parse_suggest(self, q: str) -> dict:
        """Parse: SUGGEST EDGES FOR "SOP-015" """
        quoted = self._extract_quoted(q)
        if not quoted:
            return {"error": "SUGGEST requires a node ID in quotes"}
        results = self.suggest_edges(quoted[0])
        return {"query": q, "node": quoted[0], "count": len(results), "suggestions": results}

    def _parse_shared(self, q: str) -> dict:
        """Parse: SHARED BETWEEN "SOP-012" AND "SOP-015" """
        quoted = self._extract_quoted(q)
        if len(quoted) < 2:
            return {"error": "SHARED requires two node IDs in quotes"}
        result = self.shared_neighbors(quoted[0], quoted[1])
        return {"query": q, "result": result}

    def _parse_most_shared(self, q: str) -> dict:
        """Parse: MOST SHARED IN "sops" """
        quoted = self._extract_quoted(q)
        domain = quoted[0] if quoted else "sops"
        m = re.search(r'TOP\s+(\d+)', q, re.I)
        top_n = int(m.group(1)) if m else 10
        results = self.most_shared_pairs(domain=domain, top_n=top_n)
        return {"query": q, "domain": domain, "count": len(results), "results": results}


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Pointer Network Query Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  query    Execute a PNQ query string
  neighbors  Find neighbors of a node
  path     Find path between two nodes
  impact   Show impact of a node changing
  suggest  Suggest new edges for a node
  shared   Find shared neighbors between two nodes
  stats    Show network statistics

Examples:
  python3 pointer_query.py query 'FIND nodes WHERE domain = "scripts" AND weight >= 5'
  python3 pointer_query.py neighbors SCR-0043 --depth 2 --strength primary
  python3 pointer_query.py path SOP-012 SRC-0001 --strategy weight
  python3 pointer_query.py impact SCR-0043
  python3 pointer_query.py suggest SOP-015
  python3 pointer_query.py shared SOP-012 SOP-015
  python3 pointer_query.py stats
""")
    sub = parser.add_subparsers(dest="command")

    # query
    q_parser = sub.add_parser("query", help="Execute a PNQ query string")
    q_parser.add_argument("pnq", help="PNQ query string")

    # neighbors
    n_parser = sub.add_parser("neighbors", help="Find neighbors")
    n_parser.add_argument("node", help="Node ID")
    n_parser.add_argument("--depth", type=int, default=1)
    n_parser.add_argument("--strength", default=None)
    n_parser.add_argument("--min-weight", type=int, default=0)
    n_parser.add_argument("--direction", default="outgoing",
                          choices=["outgoing", "incoming", "both"])

    # path
    p_parser = sub.add_parser("path", help="Find path between nodes")
    p_parser.add_argument("from_node", help="Source node ID")
    p_parser.add_argument("to_node", help="Target node ID")
    p_parser.add_argument("--strategy", default="weight",
                          choices=["weight", "hops", "strength", "hybrid"])

    # impact
    i_parser = sub.add_parser("impact", help="Show impact analysis")
    i_parser.add_argument("node", help="Node ID")
    i_parser.add_argument("--depth", type=int, default=3)

    # suggest
    s_parser = sub.add_parser("suggest", help="Suggest edges")
    s_parser.add_argument("node", help="Node ID")
    s_parser.add_argument("--top", type=int, default=10)

    # shared
    sh_parser = sub.add_parser("shared", help="Find shared neighbors")
    sh_parser.add_argument("node_a", help="First node ID")
    sh_parser.add_argument("node_b", help="Second node ID")

    # stats
    sub.add_parser("stats", help="Network statistics")

    args = parser.parse_args()
    pq = PointerNetworkQuery()

    if args.command == "query":
        result = pq.execute(args.pnq)
    elif args.command == "neighbors":
        result = pq.neighbors(args.node, depth=args.depth,
                               min_weight=args.min_weight,
                               strength=args.strength,
                               direction=args.direction)
    elif args.command == "path":
        result = pq.find_path(args.from_node, args.to_node,
                               strategy=args.strategy)
    elif args.command == "impact":
        result = pq.impact(args.node, max_depth=args.depth)
    elif args.command == "suggest":
        result = pq.suggest_edges(args.node, top_n=args.top)
    elif args.command == "shared":
        result = pq.shared_neighbors(args.node_a, args.node_b)
    elif args.command == "stats":
        result = pq.stats()
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
