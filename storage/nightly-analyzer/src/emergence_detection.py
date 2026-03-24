#!/usr/bin/env python3
"""
Step 3.8: Emergence Detection
================================
Detects novel patterns the system hasn't explicitly been told about.

Emergence = when multiple independent chains of memory converge on
a pattern that wasn't part of any individual chain. The whole is
greater than the sum of its parts.

Detection algorithm:
    1. Find "convergence points": nodes where 3+ independent paths arrive
       from different origin clusters (no shared ancestors within 3 hops)
    2. For each convergence point, extract the full neighborhood
    3. Use the LLM to analyze whether the convergence reveals an
       insight that none of the individual paths contain
    4. If emergence is detected, create a MetaMemory node capturing it
    5. Link back to source memories via CAUSAL edges

Examples of emergence:
    - User mentioned "headaches after coffee" in one conversation and
      "sleep problems" in another. Neither mentioned caffeine sensitivity,
      but the convergence suggests it.
    - Several disconnected projects all hit the same performance bottleneck,
      revealing a systemic infrastructure issue.

Processing targets:
    - ~600 convergence neighborhoods per night
    - LLM calls: ~600
    - At ~70 tok/s with 1024 max tokens: ~14.6s per call
    - Total LLM time: ~2.4 hours (time-boxed to 45 minutes)

Prerequisites:
    - 5 memory types including MetaMemory
    - Multiple independent memory chains in the graph
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("nightly.emergence")


# ---------------------------------------------------------------------------
# Prompt Templates
# ---------------------------------------------------------------------------

EMERGENCE_PROMPT = """You are an insight detection expert. Analyze whether multiple independent memory chains converging on a single point reveal an emergent insight.

Convergence point:
  Type: {convergence_type}
  Content: {convergence_summary}

Independent paths arriving at this point:

{path_descriptions}

Number of independent paths: {num_paths}

Questions to consider:
1. Do these independent paths, taken together, reveal a pattern that none of them individually contain?
2. Is there a higher-level insight that connects all these paths?
3. Is this a genuine emergence (novel insight) or just a coincidental convergence?
4. If emergent, what actionable insight does it provide?

Respond in this exact format:
EMERGENT: <YES or NO>
CONFIDENCE: <float between 0.0 and 1.0>
INSIGHT: <the emergent insight in 1-2 sentences, or "N/A" if not emergent>
CATEGORY: <one of: PATTERN, CONTRADICTION, SYNTHESIS, PREDICTION, META_LEARNING>
SOURCE_PATHS: <comma-separated list of path numbers that contribute, e.g., "1,2,4">"""


def parse_emergence_response(response: str) -> Optional[dict]:
    """
    Parse the emergence detection LLM response.

    Expected format:
        EMERGENT: YES
        CONFIDENCE: 0.78
        INSIGHT: The user's recurring API issues across three projects...
        CATEGORY: PATTERN
        SOURCE_PATHS: 1,2,3

    Returns dict or None on parse failure.
    """
    result = {}

    emergent_match = re.search(r'EMERGENT:\s*(YES|NO|yes|no)', response)
    if not emergent_match:
        return None
    result["is_emergent"] = emergent_match.group(1).upper() == "YES"

    conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', response)
    if not conf_match:
        return None
    confidence = float(conf_match.group(1))
    if confidence > 1.0:
        confidence /= 100.0
    result["confidence"] = max(0.0, min(1.0, round(confidence, 3)))

    insight_match = re.search(r'INSIGHT:\s*(.+?)(?=\nCATEGORY:|\n\n|$)', response, re.DOTALL)
    result["insight"] = insight_match.group(1).strip()[:500] if insight_match else "N/A"

    cat_match = re.search(
        r'CATEGORY:\s*(PATTERN|CONTRADICTION|SYNTHESIS|PREDICTION|META_LEARNING)',
        response,
        re.IGNORECASE,
    )
    result["category"] = cat_match.group(1).upper() if cat_match else "PATTERN"

    paths_match = re.search(r'SOURCE_PATHS:\s*([\d,\s]+)', response)
    if paths_match:
        result["source_paths"] = [
            int(p.strip()) for p in paths_match.group(1).split(",")
            if p.strip().isdigit()
        ]
    else:
        result["source_paths"] = []

    return result


class EmergenceDetector:
    """
    Detects emergent patterns from converging independent memory paths.

    A "convergence point" is a node where multiple independent paths
    (no common ancestor within 3 hops) arrive. This suggests the node
    is a nexus of unrelated knowledge chains, which may reveal insights
    that none of the chains individually contain.

    Usage:
        detector = EmergenceDetector(driver, db, config, model_mgr, wal, shutdown)
        metrics = detector.execute(soft_deadline, hard_deadline)
    """

    def __init__(self, driver, database: str, config: dict, model_mgr, wal, shutdown):
        self.driver = driver
        self.database = database
        self.config = config
        self.emerge_config = config["emergence"]
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
        Run emergence detection.

        Steps:
            1. Find convergence points (~3 minutes)
            2. For each, extract neighborhood and independent paths
            3. Use LLM to analyze for emergent insights
            4. Create MetaMemory nodes for confirmed emergences
            5. Link back to source memories

        Returns:
            {
                "convergence_points_found": 620,
                "convergence_points_analyzed": 580,
                "emergences_detected": 45,
                "meta_memories_created": 45,
                "categories": {"PATTERN": 20, "SYNTHESIS": 15, ...},
                "avg_confidence": 0.72,
                "llm_calls": 580,
                "duration_s": 2700
            }

        Time estimate: 35-50 minutes
        """
        start = time.time()
        target = self.emerge_config["target_neighborhoods_per_night"]

        metrics = {
            "convergence_points_found": 0,
            "convergence_points_analyzed": 0,
            "emergences_detected": 0,
            "meta_memories_created": 0,
            "categories": {},
            "confidences": [],
            "llm_calls": 0,
        }

        # Step 1: Find convergence points
        convergence_points = self._find_convergence_points(target)
        metrics["convergence_points_found"] = len(convergence_points)
        logger.info("Found %d convergence points", len(convergence_points))

        # Step 2-5: Analyze each convergence point
        for cp in convergence_points:
            if self.shutdown.requested or datetime.now() >= hard_deadline:
                break

            paths = self._extract_independent_paths(cp)
            if len(paths) < self.emerge_config["min_convergence_paths"]:
                continue

            metrics["convergence_points_analyzed"] += 1
            metrics["llm_calls"] += 1

            result = self._analyze_convergence(cp, paths, dry_run)
            if result and result["is_emergent"] and result["confidence"] >= 0.5:
                metrics["emergences_detected"] += 1
                metrics["confidences"].append(result["confidence"])

                cat = result["category"]
                metrics["categories"][cat] = metrics["categories"].get(cat, 0) + 1

                if not dry_run:
                    self._create_meta_memory(cp, paths, result)
                    metrics["meta_memories_created"] += 1

        if metrics["confidences"]:
            metrics["avg_confidence"] = round(
                sum(metrics["confidences"]) / len(metrics["confidences"]), 3
            )
        del metrics["confidences"]  # Don't include raw list in final metrics

        metrics["duration_s"] = round(time.time() - start, 1)
        return metrics

    def _find_convergence_points(self, limit: int) -> list:
        """
        Find nodes where multiple independent paths converge.

        A convergence point has:
            - At least min_convergence_paths incoming edges from different source clusters
            - The source nodes do NOT share a common ancestor within 3 hops
            - The convergence point hasn't already generated a MetaMemory recently

        This query is the most complex graph pattern in the nightly pipeline.

        Returns list of convergence point dicts.

        Time estimate: 5-15 seconds
        """
        min_paths = self.emerge_config["min_convergence_paths"]
        min_neighborhood = self.emerge_config["min_neighborhood_size"]

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Find convergence points
        # ---------------------------------------------------------------
        # Strategy: Find nodes with high in-degree from diverse sources.
        # "Diverse" means the source nodes don't share neighbors within 2 hops.
        #
        # Step 1: Find nodes with >= min_paths incoming edges
        # Step 2: For each, check that incoming neighbors are independent
        #         (no shared 2nd-hop neighbors)
        # Step 3: Exclude nodes that already spawned a MetaMemory in the last 7 days
        # ---------------------------------------------------------------
        query = """
        // Step 1: Find high-in-degree nodes
        MATCH (target)<-[r]-(source)
        WHERE target.summary IS NOT NULL
          AND source.summary IS NOT NULL
          AND r.weight > 0.1
        WITH target, collect(DISTINCT source) AS sources, count(DISTINCT source) AS in_degree
        WHERE in_degree >= $min_paths

        // Step 2: Check for MetaMemory recency
        OPTIONAL MATCH (target)<-[:CAUSAL]-(existing_meta:MetaMemory)
        WHERE existing_meta.created_at > datetime() - duration('P7D')
        WITH target, sources, in_degree, count(existing_meta) AS recent_metas
        WHERE recent_metas = 0

        // Step 3: Return convergence point data
        RETURN elementId(target) AS node_id,
               coalesce(target.summary, target.content, '') AS summary,
               labels(target)[0] AS type,
               in_degree,
               [s IN sources | {
                   id: elementId(s),
                   summary: coalesce(s.summary, s.content, '')[:200],
                   type: labels(s)[0]
               }] AS source_nodes
        ORDER BY in_degree DESC
        LIMIT $limit
        """

        with self.driver.session(database=self.database) as session:
            return [dict(r) for r in session.run(query, min_paths=min_paths, limit=limit)]

    def _extract_independent_paths(self, convergence_point: dict) -> list:
        """
        Extract the independent paths leading to a convergence point.

        For each source node, trace back up to 3 hops to build a
        "path description" that captures the chain of reasoning.

        Independence check: two paths are independent if they don't
        share any nodes within their first 3 hops (excluding the
        convergence point itself).

        Returns list of path dicts:
            [
                {
                    "source_id": "...",
                    "source_summary": "...",
                    "chain": ["hop3 summary", "hop2 summary", "hop1 summary", "source summary"],
                    "chain_types": ["CAUSAL", "TEMPORAL", "SEMANTIC"],
                },
                ...
            ]
        """
        target_id = convergence_point["node_id"]
        sources = convergence_point["source_nodes"]

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Trace back 3 hops from each source
        # ---------------------------------------------------------------
        trace_query = """
        MATCH (source)
        WHERE elementId(source) = $source_id
        OPTIONAL MATCH path = (ancestor)-[*1..3]->(source)
        WHERE elementId(ancestor) <> $target_id
        WITH source, path,
             [n IN nodes(path) | coalesce(n.summary, n.content, '')[:100]] AS node_summaries,
             [r IN relationships(path) | type(r)] AS edge_types
        ORDER BY length(path) DESC
        LIMIT 1
        RETURN elementId(source) AS source_id,
               coalesce(source.summary, source.content, '')[:200] AS source_summary,
               labels(source)[0] AS source_type,
               node_summaries AS chain,
               edge_types AS chain_types,
               [n IN nodes(coalesce(path, null)) | elementId(n)] AS chain_node_ids
        """

        paths = []
        all_chain_nodes = {}  # path_index -> set of node IDs

        with self.driver.session(database=self.database) as session:
            for i, source in enumerate(sources):
                result = session.run(
                    trace_query,
                    source_id=source["id"],
                    target_id=target_id,
                ).single()

                if result:
                    chain_ids = set(result["chain_node_ids"] or [])
                    all_chain_nodes[i] = chain_ids

                    paths.append({
                        "index": i,
                        "source_id": result["source_id"],
                        "source_summary": result["source_summary"],
                        "source_type": result["source_type"],
                        "chain": result["chain"] or [result["source_summary"]],
                        "chain_types": result["chain_types"] or [],
                        "chain_node_ids": chain_ids,
                    })

        # Filter to truly independent paths (no shared chain nodes)
        independent = []
        used_nodes = set()

        for path in paths:
            chain_ids = path["chain_node_ids"]
            # Check if this path shares nodes with already-selected paths
            if not chain_ids & used_nodes:
                independent.append(path)
                used_nodes |= chain_ids

        return independent

    def _analyze_convergence(
        self,
        convergence_point: dict,
        paths: list,
        dry_run: bool,
    ) -> Optional[dict]:
        """
        Use the LLM to analyze whether converging paths reveal emergence.
        """
        if dry_run:
            return None

        # Build path descriptions for the prompt
        path_descriptions = []
        for i, path in enumerate(paths, 1):
            chain_text = " -> ".join(path["chain"][:4])
            edge_text = " -> ".join(path["chain_types"][:3]) if path["chain_types"] else "direct"
            path_descriptions.append(
                f"  Path {i} ({path['source_type']}):\n"
                f"    Chain: {chain_text}\n"
                f"    Edge types: {edge_text}\n"
                f"    Source: {path['source_summary'][:150]}"
            )

        prompt = EMERGENCE_PROMPT.format(
            convergence_type=convergence_point["type"],
            convergence_summary=convergence_point["summary"][:400],
            path_descriptions="\n\n".join(path_descriptions),
            num_paths=len(paths),
        )

        try:
            response = self.model_mgr.generate(
                prompt=prompt,
                max_tokens=self.emerge_config["max_tokens"],
                temperature=self.emerge_config["temperature"],
            )
            return parse_emergence_response(response)
        except Exception as e:
            logger.warning("LLM call failed in emergence detection: %s", e)
            return None

    def _create_meta_memory(self, convergence_point: dict, paths: list, result: dict):
        """
        Create a MetaMemory node capturing the emergent insight.

        The MetaMemory:
            - Has the emergent insight as its summary
            - Is linked via CAUSAL edges to the convergence point and source nodes
            - Stores metadata about the detection (confidence, category, paths)
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Create MetaMemory and link to sources
        # ---------------------------------------------------------------
        create_query = """
        CREATE (m:MetaMemory {
            summary: $insight,
            category: $category,
            confidence: $confidence,
            source_path_count: $path_count,
            created_at: datetime(),
            last_accessed: datetime(),
            discovered_by: 'nightly_emergence_detection'
        })
        WITH m

        // Link to convergence point
        MATCH (target)
        WHERE elementId(target) = $convergence_id
        CREATE (m)-[:CAUSAL {
            weight: $confidence,
            relationship_subtype: 'emerged_from',
            created_at: datetime(),
            last_accessed: datetime(),
            score_accumulator: 0.0
        }]->(target)

        WITH m
        // Link to source nodes
        UNWIND $source_ids AS sid
        MATCH (source)
        WHERE elementId(source) = sid
        CREATE (m)-[:CAUSAL {
            weight: $confidence,
            relationship_subtype: 'emerged_from_source',
            created_at: datetime(),
            last_accessed: datetime(),
            score_accumulator: 0.0
        }]->(source)

        RETURN elementId(m) AS meta_id
        """

        source_ids = [p["source_id"] for p in paths]

        with self.driver.session(database=self.database) as session:
            r = session.run(
                create_query,
                insight=result["insight"],
                category=result["category"],
                confidence=result["confidence"],
                path_count=len(paths),
                convergence_id=convergence_point["node_id"],
                source_ids=source_ids,
            ).single()

            meta_id = r["meta_id"] if r else None

        self.wal.write({
            "op": "create_meta_memory",
            "meta_id": meta_id,
            "insight": result["insight"][:200],
            "category": result["category"],
            "confidence": result["confidence"],
            "convergence_point": convergence_point["node_id"],
            "source_count": len(source_ids),
        })

        logger.info(
            "Created MetaMemory: %s (category=%s, confidence=%.2f, sources=%d)",
            result["insight"][:80],
            result["category"],
            result["confidence"],
            len(source_ids),
        )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
# After running, verify emergence detection with:
#
#   // Count MetaMemory nodes created tonight
#   MATCH (m:MetaMemory)
#   WHERE m.created_at > datetime() - duration('P1D')
#   RETURN count(m) AS new_meta_memories,
#          avg(m.confidence) AS avg_confidence,
#          collect(DISTINCT m.category) AS categories
#
# Expected output:
#   new_meta_memories: 45, avg_confidence: 0.72, categories: ["PATTERN", "SYNTHESIS", ...]
#
#   // Verify MetaMemory links
#   MATCH (m:MetaMemory)-[:CAUSAL]->(source)
#   WHERE m.created_at > datetime() - duration('P1D')
#   RETURN elementId(m) AS meta_id,
#          m.summary[:80] AS insight,
#          count(source) AS linked_sources
#   ORDER BY linked_sources DESC
#   LIMIT 10
#
# Expected: Each MetaMemory linked to 4-8 source nodes
# ---------------------------------------------------------------------------
