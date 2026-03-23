#!/usr/bin/env python3
"""
Step 3.7: Condensation Pipeline
=================================
Identifies clusters of similar/redundant memories and merges them
into condensed summaries.

The goal: reduce graph size while preserving knowledge. When multiple
memories say similar things, create one FactMemory node that captures
the essence, link the originals to it, and mark originals for eventual
cold-tier migration (to slower storage).

Clustering algorithm (graph-based, NOT vector-based):
    1. For each node, compute a "neighborhood signature" based on its
       immediate neighbors and edge types
    2. Nodes with highly similar neighborhood signatures are clustered
    3. Within each cluster, use the LLM to generate a condensed summary
    4. Create the condensed node and re-link

Why graph-based instead of vector-based?
    Vector similarity catches semantic overlap, but misses structural
    relationships. Two memories can be semantically different but
    structurally redundant (e.g., both describe the same event from
    different angles). The graph structure captures this.

Processing targets:
    - ~800 node clusters per night
    - Cluster sizes: 3-15 nodes
    - LLM calls: ~800 (one per cluster)
    - At ~70 tok/s with 1024 max tokens: ~14.6s per call
    - Total LLM time: ~3.2 hours (time-boxed to 1 hour)

Prerequisites:
    - 5 memory types: EpisodicMemory, SemanticMemory, FactMemory,
      ProceduralMemory, MetaMemory
    - 4 edge types: STRUCTURAL, SEMANTIC, CAUSAL, TEMPORAL
"""

import json
import logging
import time
from collections import defaultdict
from datetime import datetime
from typing import Optional

logger = logging.getLogger("nightly.condensation")


# ---------------------------------------------------------------------------
# LLM Prompt Template
# ---------------------------------------------------------------------------

CONDENSATION_PROMPT = """You are a memory condensation expert. Given a cluster of related memories, create a single condensed summary that preserves all essential information.

Cluster of {cluster_size} memories:

{memory_list}

Shared structural patterns:
{structural_patterns}

Task: Create a condensed summary that:
1. Captures ALL key facts from the cluster
2. Preserves causal relationships between memories
3. Removes redundant information
4. Maintains temporal ordering where relevant
5. Is concise but complete

Respond in this exact format:
SUMMARY: <condensed summary, 2-4 sentences>
KEY_FACTS: <comma-separated list of preserved facts>
CONFIDENCE: <float 0.0-1.0, how confident you are that no information was lost>"""


def parse_condensation_response(response: str) -> Optional[dict]:
    """
    Parse the condensation LLM response.

    Expected format:
        SUMMARY: The user investigated API latency issues on March 15...
        KEY_FACTS: API latency spike, monitoring alert, root cause was DB query
        CONFIDENCE: 0.85

    Returns:
        {
            "summary": "The user investigated...",
            "key_facts": ["API latency spike", "monitoring alert", ...],
            "confidence": 0.85
        }
        or None if parsing fails
    """
    import re

    summary_match = re.search(r'SUMMARY:\s*(.+?)(?=\nKEY_FACTS:|\n\n|$)', response, re.DOTALL)
    if not summary_match:
        return None

    summary = summary_match.group(1).strip()

    facts_match = re.search(r'KEY_FACTS:\s*(.+?)(?=\nCONFIDENCE:|\n\n|$)', response, re.DOTALL)
    key_facts = []
    if facts_match:
        facts_text = facts_match.group(1).strip()
        key_facts = [f.strip() for f in facts_text.split(",") if f.strip()]

    conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', response)
    confidence = 0.5
    if conf_match:
        confidence = float(conf_match.group(1))
        if confidence > 1.0:
            confidence = confidence / 100.0
        confidence = max(0.0, min(1.0, confidence))

    return {
        "summary": summary[:1000],
        "key_facts": key_facts[:20],
        "confidence": round(confidence, 3),
    }


class CondensationProcessor:
    """
    Identifies and condenses clusters of similar memories.

    Clustering approach (Jaccard similarity on neighborhoods):
        For each node N:
            signature(N) = set of (neighbor_label, edge_type) pairs
        Similarity(N1, N2) = |sig(N1) & sig(N2)| / |sig(N1) | sig(N2)|

        Nodes with similarity > threshold are grouped into clusters.
        Clusters are processed in order of size (largest first, most redundancy).

    Usage:
        processor = CondensationProcessor(driver, db, config, model_mgr, wal, shutdown)
        metrics = processor.execute(soft_deadline, hard_deadline)
    """

    def __init__(self, driver, database: str, config: dict, model_mgr, wal, shutdown):
        self.driver = driver
        self.database = database
        self.config = config
        self.cond_config = config["condensation"]
        self.model_mgr = model_mgr
        self.wal = wal
        self.shutdown = shutdown

    def execute(
        self,
        soft_deadline: datetime,
        hard_deadline: datetime,
        dry_run: bool = False,
    ) -> dict:
        """
        Run the condensation pipeline.

        Steps:
            1. Compute neighborhood signatures for all nodes (~2 minutes)
            2. Find clusters of similar nodes (~1 minute)
            3. For each cluster, generate condensed summary via LLM
            4. Create condensed FactMemory node
            5. Link originals to condensed node
            6. Mark originals for cold-tier migration

        Returns:
            {
                "clusters_found": 820,
                "clusters_processed": 780,
                "nodes_condensed": 3200,
                "condensed_nodes_created": 780,
                "avg_cluster_size": 4.1,
                "cold_tier_candidates": 3200,
                "duration_s": 3600
            }

        Time estimate: 45-75 minutes
        """
        start = time.time()
        target = self.cond_config["target_clusters_per_night"]

        metrics = {
            "clusters_found": 0,
            "clusters_processed": 0,
            "nodes_condensed": 0,
            "condensed_nodes_created": 0,
            "cold_tier_candidates": 0,
        }

        # Step 1: Compute neighborhood signatures
        logger.info("Computing neighborhood signatures...")
        signatures = self._compute_neighborhood_signatures()
        logger.info("Computed signatures for %d nodes", len(signatures))

        # Step 2: Find clusters
        clusters = self._find_clusters(signatures)
        metrics["clusters_found"] = len(clusters)
        logger.info("Found %d condensation clusters", len(clusters))

        # Step 3-6: Process clusters
        for cluster in clusters[:target]:
            if self.shutdown.requested or datetime.now() >= hard_deadline:
                break

            result = self._process_cluster(cluster, dry_run)
            if result:
                metrics["clusters_processed"] += 1
                metrics["nodes_condensed"] += len(cluster["node_ids"])
                metrics["condensed_nodes_created"] += 1
                metrics["cold_tier_candidates"] += len(cluster["node_ids"])

        if metrics["clusters_processed"] > 0:
            metrics["avg_cluster_size"] = round(
                metrics["nodes_condensed"] / metrics["clusters_processed"], 1
            )

        metrics["duration_s"] = round(time.time() - start, 1)
        return metrics

    def _compute_neighborhood_signatures(self) -> dict:
        """
        Compute a neighborhood signature for each node.

        The signature is a set of (neighbor_label, edge_type, direction) tuples.
        This captures the structural context of each node.

        Returns:
            {
                "node_id_1": {("FactMemory", "SEMANTIC", "out"), ("EpisodicMemory", "TEMPORAL", "in"), ...},
                "node_id_2": {...},
                ...
            }

        Time estimate: 1-3 minutes for 10,000+ nodes
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Compute neighborhood signatures
        # ---------------------------------------------------------------
        # For each node, collect its outgoing and incoming edge patterns.
        # We only consider nodes that haven't been condensed already
        # (no 'condensed_into' property).
        # ---------------------------------------------------------------
        query = """
        MATCH (n)
        WHERE n.summary IS NOT NULL
          AND n.condensed_into IS NULL
          AND NOT n:CondensedMemory
        WITH n
        OPTIONAL MATCH (n)-[r_out]->(neighbor_out)
        WITH n, collect(DISTINCT {label: labels(neighbor_out)[0], type: type(r_out), dir: 'out'}) AS out_patterns
        OPTIONAL MATCH (neighbor_in)-[r_in]->(n)
        WITH n, out_patterns,
             collect(DISTINCT {label: labels(neighbor_in)[0], type: type(r_in), dir: 'in'}) AS in_patterns
        RETURN elementId(n) AS node_id,
               out_patterns + in_patterns AS patterns
        """

        signatures = {}
        with self.driver.session(database=self.database) as session:
            for record in session.run(query):
                node_id = record["node_id"]
                patterns = record["patterns"]
                sig = set()
                for p in patterns:
                    if p["label"] is not None:
                        sig.add((p["label"], p["type"], p["dir"]))
                if sig:  # Only include nodes with at least one neighbor
                    signatures[node_id] = sig

        return signatures

    def _find_clusters(self, signatures: dict) -> list:
        """
        Find clusters of nodes with similar neighborhood signatures.

        Algorithm: greedy agglomerative clustering using Jaccard similarity.
            1. Sort nodes by signature size (descending)
            2. For each unassigned node, find all unassigned nodes with
               Jaccard similarity > threshold
            3. Form a cluster if size >= min_cluster_size
            4. Mark all clustered nodes as assigned

        This is O(n^2) in the worst case but efficient in practice because:
            - We cap at target_clusters_per_night
            - Most nodes have small signatures (< 20 elements)
            - We skip nodes already assigned to clusters

        Returns list of cluster dicts:
            [
                {"node_ids": ["id1", "id2", "id3"], "similarity": 0.85},
                ...
            ]
            Sorted by size descending.

        Time estimate: 10-60 seconds for 10,000 nodes
        """
        threshold = self.cond_config["similarity_threshold"]
        min_size = self.cond_config["min_cluster_size"]
        max_size = self.cond_config["max_cluster_size"]

        assigned = set()
        clusters = []
        node_ids = list(signatures.keys())

        for i, node_a in enumerate(node_ids):
            if node_a in assigned:
                continue

            sig_a = signatures[node_a]
            cluster_members = [node_a]
            cluster_sims = []

            for j in range(i + 1, len(node_ids)):
                if len(cluster_members) >= max_size:
                    break

                node_b = node_ids[j]
                if node_b in assigned:
                    continue

                sig_b = signatures[node_b]

                # Jaccard similarity
                intersection = len(sig_a & sig_b)
                union = len(sig_a | sig_b)
                if union == 0:
                    continue

                similarity = intersection / union
                if similarity >= threshold:
                    cluster_members.append(node_b)
                    cluster_sims.append(similarity)

            if len(cluster_members) >= min_size:
                for member in cluster_members:
                    assigned.add(member)
                avg_sim = sum(cluster_sims) / len(cluster_sims) if cluster_sims else threshold
                clusters.append({
                    "node_ids": cluster_members,
                    "similarity": round(avg_sim, 3),
                })

        # Sort by cluster size descending (condense largest groups first)
        clusters.sort(key=lambda c: len(c["node_ids"]), reverse=True)
        return clusters

    def _process_cluster(self, cluster: dict, dry_run: bool) -> Optional[dict]:
        """
        Process a single cluster: generate condensed summary, create node, re-link.

        Steps:
            1. Fetch full summaries for all nodes in the cluster
            2. Identify structural patterns
            3. Call LLM to generate condensed summary
            4. Create CondensedMemory (FactMemory) node
            5. Link originals to condensed node via STRUCTURAL edges
            6. Mark originals with condensed_into pointer

        Returns result dict or None on failure.
        """
        node_ids = cluster["node_ids"]

        # Step 1: Fetch node summaries
        node_data = self._fetch_cluster_nodes(node_ids)
        if not node_data:
            return None

        # Step 2: Build memory list for prompt
        memory_list_parts = []
        for i, nd in enumerate(node_data, 1):
            memory_list_parts.append(
                f"  [{i}] ({nd['type']}) {nd['summary'][:200]}"
            )
        memory_list = "\n".join(memory_list_parts)

        # Step 3: Structural patterns
        structural_patterns = self._get_structural_patterns(node_ids)

        # Step 4: LLM condensation
        if dry_run:
            return {"condensed": True, "dry_run": True}

        prompt = CONDENSATION_PROMPT.format(
            cluster_size=len(node_data),
            memory_list=memory_list,
            structural_patterns=structural_patterns or "No shared structural patterns detected.",
        )

        try:
            response = self.model_mgr.generate(
                prompt=prompt,
                max_tokens=self.cond_config["max_tokens"],
                temperature=self.cond_config["temperature"],
            )
            parsed = parse_condensation_response(response)
            if parsed is None:
                logger.warning("Failed to parse condensation response for cluster of %d", len(node_ids))
                return None

        except Exception as e:
            logger.warning("LLM call failed during condensation: %s", e)
            return None

        # Step 5: Create condensed node and link originals
        condensed_node_id = self._create_condensed_node(parsed, node_ids)

        # Step 6: Mark originals
        self._mark_originals(node_ids, condensed_node_id)

        self.wal.write({
            "op": "condense_cluster",
            "cluster_size": len(node_ids),
            "condensed_node_id": condensed_node_id,
            "original_node_ids": node_ids,
            "confidence": parsed["confidence"],
        })

        return {
            "condensed_node_id": condensed_node_id,
            "cluster_size": len(node_ids),
            "confidence": parsed["confidence"],
        }

    def _fetch_cluster_nodes(self, node_ids: list) -> list:
        """
        Fetch summary data for all nodes in a cluster.

        Returns list of dicts with id, type, summary, timestamp.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Fetch cluster node data
        # ---------------------------------------------------------------
        query = """
        UNWIND $node_ids AS nid
        MATCH (n)
        WHERE elementId(n) = nid
        RETURN elementId(n) AS id,
               labels(n)[0] AS type,
               coalesce(n.summary, n.content, '') AS summary,
               coalesce(n.timestamp, '') AS timestamp
        """
        with self.driver.session(database=self.database) as session:
            return [dict(r) for r in session.run(query, node_ids=node_ids)]

    def _get_structural_patterns(self, node_ids: list) -> str:
        """
        Identify shared structural patterns within a cluster.

        Returns a text description of shared edge patterns.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Shared neighbors of cluster members
        # ---------------------------------------------------------------
        query = """
        UNWIND $node_ids AS nid
        MATCH (n)-[r]->(shared)
        WHERE elementId(n) = nid
          AND NOT elementId(shared) IN $node_ids
        WITH shared, type(r) AS rel_type, labels(shared)[0] AS shared_type,
             count(DISTINCT nid) AS connected_count
        WHERE connected_count >= 2
        RETURN shared_type, rel_type, connected_count,
               coalesce(shared.summary, shared.content, '')[:100] AS shared_summary
        ORDER BY connected_count DESC
        LIMIT 5
        """
        with self.driver.session(database=self.database) as session:
            records = list(session.run(query, node_ids=node_ids))

        if not records:
            return ""

        lines = []
        for r in records:
            lines.append(
                f"- {r['connected_count']} cluster members share a {r['rel_type']} "
                f"edge to {r['shared_type']}: {r['shared_summary']}"
            )
        return "\n".join(lines)

    def _create_condensed_node(self, parsed: dict, original_ids: list) -> str:
        """
        Create a CondensedMemory node (subtype of FactMemory) and link
        all original nodes to it.

        The condensed node:
            - Has label FactMemory (for compatibility with retrieval pipeline)
            - Has additional label CondensedMemory (for identification)
            - Stores the condensed summary
            - Stores the key facts
            - Stores the source count and confidence

        Original nodes get STRUCTURAL edges to the condensed node,
        representing "is_condensed_into" relationships.

        Returns the elementId of the new condensed node.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Create condensed node and link originals
        # ---------------------------------------------------------------
        create_query = """
        CREATE (c:FactMemory:CondensedMemory {
            summary: $summary,
            key_facts: $key_facts,
            condensation_confidence: $confidence,
            source_count: $source_count,
            created_at: datetime(),
            last_accessed: datetime(),
            condensed_from: $original_ids
        })
        WITH c
        UNWIND $original_ids AS oid
        MATCH (orig)
        WHERE elementId(orig) = oid
        CREATE (orig)-[:STRUCTURAL {
            weight: 1.0,
            relationship_subtype: 'condensed_into',
            created_at: datetime(),
            last_accessed: datetime(),
            score_accumulator: 0.0
        }]->(c)
        RETURN elementId(c) AS condensed_id
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(
                create_query,
                summary=parsed["summary"],
                key_facts=parsed["key_facts"],
                confidence=parsed["confidence"],
                source_count=len(original_ids),
                original_ids=original_ids,
            ).single()
            return result["condensed_id"]

    def _mark_originals(self, node_ids: list, condensed_id: str):
        """
        Mark original nodes as condensed, pointing to the condensed node.

        This doesn't delete the originals -- they remain in the graph
        with a 'condensed_into' pointer. A separate cold-tier migration
        process (not part of this nightly pipeline) moves them to slower
        storage when disk pressure requires it.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Mark originals
        # ---------------------------------------------------------------
        query = """
        UNWIND $node_ids AS nid
        MATCH (n)
        WHERE elementId(n) = nid
        SET n.condensed_into = $condensed_id,
            n.condensed_at = datetime()
        RETURN count(n) AS marked
        """

        with self.driver.session(database=self.database) as session:
            session.run(query, node_ids=node_ids, condensed_id=condensed_id)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
# After running, verify condensation with:
#
#   // Count condensed nodes
#   MATCH (c:CondensedMemory)
#   RETURN count(c) AS condensed_count,
#          avg(c.source_count) AS avg_source_count,
#          avg(c.condensation_confidence) AS avg_confidence
#
# Expected output:
#   condensed_count: 780, avg_source_count: 4.1, avg_confidence: 0.82
#
#   // Count nodes marked for cold tier
#   MATCH (n)
#   WHERE n.condensed_into IS NOT NULL
#   RETURN count(n) AS cold_tier_candidates
#
# Expected output:
#   cold_tier_candidates: 3200
#
#   // Verify condensed nodes have proper links
#   MATCH (orig)-[:STRUCTURAL {relationship_subtype: 'condensed_into'}]->(c:CondensedMemory)
#   RETURN count(orig) AS linked_originals, count(DISTINCT c) AS condensed_nodes
#
# Expected:
#   linked_originals: 3200, condensed_nodes: 780
# ---------------------------------------------------------------------------
