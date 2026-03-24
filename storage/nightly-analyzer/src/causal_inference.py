#!/usr/bin/env python3
"""
Step 3.5: Causal Inference Pipeline
=====================================
Discovers and validates causal relationships using the 30B nightly model.

The pipeline has two phases:
    A. Discovery: Find node pairs that LACK causal edges but have correlated
       temporal patterns (e.g., Event A consistently precedes Event B).
    B. Validation: Re-evaluate existing CAUSAL edges to strengthen or weaken them.

Candidate selection criteria:
    - Nodes connected by TEMPORAL edges but not CAUSAL edges
    - Nodes in the same neighborhood with correlated access patterns
    - Nodes that share many SEMANTIC neighbors (conceptual overlap)

Processing targets:
    - ~500 new causal candidates discovered per night
    - ~300 existing causal edges validated per night
    - ~800 total causal chains processed
    - At ~70 tok/s with 768 max tokens: ~11s per call
    - Total LLM time: ~2.4 hours, but time-boxed to 1 hour

Prerequisites:
    - Neo4j schema with TEMPORAL and CAUSAL edge types
    - Nodes have 'timestamp' or 'created_at' properties
    - The 30B nightly model is loaded and serving
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("nightly.causal_inference")


# ---------------------------------------------------------------------------
# Prompt Templates
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """You are a causal reasoning expert. Analyze whether a causal relationship exists between two memories.

Memory A (potential cause):
  Type: {node_a_type}
  Content: {node_a_summary}
  Timestamp: {node_a_time}

Memory B (potential effect):
  Type: {node_b_type}
  Content: {node_b_summary}
  Timestamp: {node_b_time}

Context: These two memories have a temporal relationship (A precedes B) and share semantic neighbors, but no explicit causal link has been established.

Shared semantic neighbors: {shared_neighbors}

Questions to consider:
1. Could A have directly or indirectly caused B?
2. Is there a plausible mechanism connecting A to B?
3. Could this be mere correlation (co-occurrence without causation)?
4. Is the temporal ordering consistent with causation (cause must precede effect)?

Respond in this exact format:
CAUSAL: <YES or NO>
CONFIDENCE: <float between 0.0 and 1.0>
DIRECTION: <A_CAUSES_B or B_CAUSES_A or BIDIRECTIONAL or NONE>
MECHANISM: <one sentence describing the causal mechanism, or "N/A">"""

VALIDATION_PROMPT = """You are a causal reasoning expert. Re-evaluate an existing causal relationship.

Cause Memory:
  Type: {cause_type}
  Content: {cause_summary}
  Timestamp: {cause_time}

Effect Memory:
  Type: {effect_type}
  Content: {effect_summary}
  Timestamp: {effect_time}

Current causal edge:
  Weight (confidence): {current_weight}
  Previously identified mechanism: {mechanism}
  Edge age: {edge_age_days} days

New evidence since last evaluation:
{new_evidence}

Task: Re-evaluate this causal relationship. Has the evidence strengthened or weakened it?

Respond in this exact format:
STILL_CAUSAL: <YES or NO>
NEW_CONFIDENCE: <float between 0.0 and 1.0>
REASONING: <one sentence explaining the change or confirmation>"""


def parse_discovery_response(response: str) -> Optional[dict]:
    """
    Parse the causal discovery LLM response.

    Expected format:
        CAUSAL: YES
        CONFIDENCE: 0.72
        DIRECTION: A_CAUSES_B
        MECHANISM: Increased API latency caused the monitoring alert to trigger.

    Returns:
        {
            "is_causal": True,
            "confidence": 0.72,
            "direction": "A_CAUSES_B",
            "mechanism": "Increased API latency caused..."
        }
        or None if parsing fails

    Common malformed responses handled:
        - "CAUSAL: yes" (case insensitive)
        - "CONFIDENCE: 72%" (percentage format)
        - Missing MECHANISM line
    """
    result = {}

    # Parse CAUSAL
    causal_match = re.search(r'CAUSAL:\s*(YES|NO|yes|no|Yes|No)', response)
    if not causal_match:
        return None
    result["is_causal"] = causal_match.group(1).upper() == "YES"

    # Parse CONFIDENCE
    conf_match = re.search(r'CONFIDENCE:\s*([\d.]+)', response)
    if not conf_match:
        return None
    confidence = float(conf_match.group(1))
    if confidence > 1.0 and confidence <= 100:
        confidence /= 100.0
    result["confidence"] = max(0.0, min(1.0, round(confidence, 3)))

    # Parse DIRECTION
    dir_match = re.search(
        r'DIRECTION:\s*(A_CAUSES_B|B_CAUSES_A|BIDIRECTIONAL|NONE)',
        response,
        re.IGNORECASE,
    )
    result["direction"] = dir_match.group(1).upper() if dir_match else "NONE"

    # Parse MECHANISM
    mech_match = re.search(r'MECHANISM:\s*(.+)', response)
    result["mechanism"] = mech_match.group(1).strip()[:300] if mech_match else "N/A"

    return result


def parse_validation_response(response: str) -> Optional[dict]:
    """
    Parse the causal validation LLM response.

    Returns:
        {"still_causal": True, "new_confidence": 0.85, "reasoning": "..."}
        or None if parsing fails
    """
    result = {}

    causal_match = re.search(r'STILL_CAUSAL:\s*(YES|NO|yes|no)', response)
    if not causal_match:
        return None
    result["still_causal"] = causal_match.group(1).upper() == "YES"

    conf_match = re.search(r'NEW_CONFIDENCE:\s*([\d.]+)', response)
    if not conf_match:
        return None
    confidence = float(conf_match.group(1))
    if confidence > 1.0 and confidence <= 100:
        confidence /= 100.0
    result["new_confidence"] = max(0.0, min(1.0, round(confidence, 3)))

    reason_match = re.search(r'REASONING:\s*(.+)', response, re.DOTALL)
    result["reasoning"] = reason_match.group(1).strip().split('\n')[0][:300] if reason_match else ""

    return result


class CausalInferenceProcessor:
    """
    Discovers new and validates existing causal relationships.

    Graph patterns used for discovery:
        1. Temporal chains: (A)-[:TEMPORAL]->(B) where no (A)-[:CAUSAL]->(B) exists
        2. Semantic bridges: (A)-[:SEMANTIC]->(X)<-[:SEMANTIC]-(B) where
           A and B have correlated timestamps
        3. Converging temporal: multiple independent temporal paths lead to the
           same node, suggesting a common cause

    Usage:
        processor = CausalInferenceProcessor(driver, db, config, model_mgr, wal, shutdown)
        metrics = processor.execute(soft_deadline, hard_deadline)
    """

    def __init__(self, driver, database: str, config: dict, model_mgr, wal, shutdown):
        self.driver = driver
        self.database = database
        self.config = config
        self.causal_config = config["causal_inference"]
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
        Run the full causal inference pipeline.

        Steps:
            1. Discover candidate causal pairs (~30 minutes)
            2. Validate existing causal edges (~20 minutes)
            3. Create new CAUSAL edges for confirmed discoveries
            4. Update weights on validated edges

        Returns:
            {
                "candidates_found": 520,
                "candidates_confirmed_causal": 185,
                "new_causal_edges_created": 185,
                "existing_edges_validated": 280,
                "existing_edges_strengthened": 210,
                "existing_edges_weakened": 55,
                "existing_edges_removed": 15,
                "llm_calls": 800,
                "duration_s": 3600
            }

        Time estimate: 45-75 minutes
        """
        start = time.time()
        target = self.causal_config["target_chains_per_night"]

        metrics = {
            "candidates_found": 0,
            "candidates_confirmed_causal": 0,
            "new_causal_edges_created": 0,
            "existing_edges_validated": 0,
            "existing_edges_strengthened": 0,
            "existing_edges_weakened": 0,
            "existing_edges_removed": 0,
            "llm_calls": 0,
        }

        # Phase A: Discovery (60% of time budget)
        discovery_deadline = datetime.now() + (hard_deadline - datetime.now()) * 0.6
        candidates = self._find_causal_candidates(int(target * 0.625))
        metrics["candidates_found"] = len(candidates)
        logger.info("Found %d causal candidates", len(candidates))

        for candidate in candidates:
            if self.shutdown.requested or datetime.now() >= discovery_deadline:
                break

            result = self._evaluate_candidate(candidate, dry_run)
            metrics["llm_calls"] += 1

            if result and result["is_causal"] and result["confidence"] >= self.causal_config["min_confidence"]:
                metrics["candidates_confirmed_causal"] += 1
                if not dry_run:
                    self._create_causal_edge(candidate, result)
                    metrics["new_causal_edges_created"] += 1

        # Phase B: Validation (40% of time budget)
        if not self.shutdown.requested and datetime.now() < hard_deadline:
            existing = self._get_existing_causal_edges(int(target * 0.375))
            logger.info("Validating %d existing causal edges", len(existing))

            for edge_data in existing:
                if self.shutdown.requested or datetime.now() >= hard_deadline:
                    break

                result = self._validate_causal_edge(edge_data, dry_run)
                metrics["llm_calls"] += 1
                metrics["existing_edges_validated"] += 1

                if result is None:
                    continue

                if not dry_run:
                    old_weight = edge_data["weight"]
                    new_weight = result["new_confidence"]

                    if not result["still_causal"] or new_weight < self.causal_config["min_confidence"]:
                        self._remove_causal_edge(edge_data["edge_id"])
                        metrics["existing_edges_removed"] += 1
                    elif new_weight > old_weight:
                        self._update_causal_edge(edge_data["edge_id"], new_weight, result["reasoning"])
                        metrics["existing_edges_strengthened"] += 1
                    else:
                        self._update_causal_edge(edge_data["edge_id"], new_weight, result["reasoning"])
                        metrics["existing_edges_weakened"] += 1

        metrics["duration_s"] = round(time.time() - start, 1)
        return metrics

    def _find_causal_candidates(self, limit: int) -> list:
        """
        Find node pairs that are candidates for new CAUSAL edges.

        Strategy 1: Temporal neighbors without causal links
            Find (A)-[:TEMPORAL]->(B) where NOT (A)-[:CAUSAL]->(B)

        Strategy 2: Semantic bridge pairs with temporal correlation
            Find (A)-[:SEMANTIC]->(X)<-[:SEMANTIC]-(B) where
            A.timestamp < B.timestamp and no CAUSAL link exists

        Returns list of candidate dicts.

        Time estimate: 3-8 seconds for the queries
        """
        candidates = []
        seen = set()

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Temporal neighbors without causal links
        # ---------------------------------------------------------------
        temporal_query = """
        MATCH (a)-[:TEMPORAL]->(b)
        WHERE NOT (a)-[:CAUSAL]->(b)
          AND NOT (b)-[:CAUSAL]->(a)
          AND a.summary IS NOT NULL
          AND b.summary IS NOT NULL
        WITH a, b,
             duration.between(
                 coalesce(a.timestamp, a.created_at, datetime('2026-01-01')),
                 coalesce(b.timestamp, b.created_at, datetime('2026-01-01'))
             ).days AS time_gap_days
        WHERE time_gap_days >= 0
        RETURN elementId(a) AS node_a_id,
               coalesce(a.summary, a.content, '') AS node_a_summary,
               labels(a)[0] AS node_a_type,
               coalesce(a.timestamp, '') AS node_a_time,
               elementId(b) AS node_b_id,
               coalesce(b.summary, b.content, '') AS node_b_summary,
               labels(b)[0] AS node_b_type,
               coalesce(b.timestamp, '') AS node_b_time,
               time_gap_days
        ORDER BY time_gap_days ASC
        LIMIT $limit
        """

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Semantic bridge pairs
        # Find pairs sharing >= 2 semantic neighbors (strong conceptual overlap)
        # ---------------------------------------------------------------
        semantic_bridge_query = """
        MATCH (a)-[:SEMANTIC]->(x)<-[:SEMANTIC]-(b)
        WHERE a <> b
          AND NOT (a)-[:CAUSAL]->(b)
          AND NOT (b)-[:CAUSAL]->(a)
          AND a.summary IS NOT NULL
          AND b.summary IS NOT NULL
          AND a.timestamp IS NOT NULL
          AND b.timestamp IS NOT NULL
          AND a.timestamp < b.timestamp
        WITH a, b, collect(DISTINCT coalesce(x.summary, x.content, '')) AS shared_neighbors
        WHERE size(shared_neighbors) >= 2
        RETURN elementId(a) AS node_a_id,
               coalesce(a.summary, a.content, '') AS node_a_summary,
               labels(a)[0] AS node_a_type,
               coalesce(a.timestamp, '') AS node_a_time,
               elementId(b) AS node_b_id,
               coalesce(b.summary, b.content, '') AS node_b_summary,
               labels(b)[0] AS node_b_type,
               coalesce(b.timestamp, '') AS node_b_time,
               shared_neighbors[0..5] AS shared_neighbors,
               0 AS time_gap_days
        ORDER BY size(shared_neighbors) DESC
        LIMIT $limit
        """

        with self.driver.session(database=self.database) as session:
            # Strategy 1: Temporal candidates
            for record in session.run(temporal_query, limit=int(limit * 0.6)):
                data = dict(record)
                key = (data["node_a_id"], data["node_b_id"])
                if key not in seen:
                    data["shared_neighbors"] = "N/A (temporal pattern)"
                    candidates.append(data)
                    seen.add(key)

            # Strategy 2: Semantic bridge candidates
            for record in session.run(semantic_bridge_query, limit=int(limit * 0.4)):
                data = dict(record)
                key = (data["node_a_id"], data["node_b_id"])
                if key not in seen:
                    neighbors = data.get("shared_neighbors", [])
                    data["shared_neighbors"] = "; ".join(str(n)[:100] for n in neighbors[:5])
                    candidates.append(data)
                    seen.add(key)

        return candidates[:limit]

    def _evaluate_candidate(self, candidate: dict, dry_run: bool) -> Optional[dict]:
        """
        Use the LLM to evaluate whether a candidate pair has a causal relationship.

        Returns parsed result dict or None on failure.
        """
        if dry_run:
            return None

        prompt = DISCOVERY_PROMPT.format(
            node_a_type=candidate["node_a_type"],
            node_a_summary=candidate["node_a_summary"][:500],
            node_a_time=candidate.get("node_a_time", "N/A"),
            node_b_type=candidate["node_b_type"],
            node_b_summary=candidate["node_b_summary"][:500],
            node_b_time=candidate.get("node_b_time", "N/A"),
            shared_neighbors=candidate.get("shared_neighbors", "None identified"),
        )

        for attempt in range(self.causal_config.get("max_retries", 3)):
            try:
                response = self.model_mgr.generate(
                    prompt=prompt,
                    max_tokens=self.causal_config["max_tokens"],
                    temperature=self.causal_config["temperature"],
                )
                result = parse_discovery_response(response)
                if result is not None:
                    return result

                logger.warning("Failed to parse causal discovery response (attempt %d)", attempt + 1)
            except Exception as e:
                logger.warning("LLM call failed in causal discovery: %s", e)
                if attempt < 2:
                    time.sleep(5)

        return None

    def _create_causal_edge(self, candidate: dict, result: dict):
        """
        Create a new CAUSAL edge between two nodes.

        The edge direction depends on the LLM's DIRECTION assessment:
            - A_CAUSES_B: (a)-[:CAUSAL]->(b)
            - B_CAUSES_A: (b)-[:CAUSAL]->(a)
            - BIDIRECTIONAL: (a)-[:CAUSAL]->(b) AND (b)-[:CAUSAL]->(a)
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Create causal edge
        # ---------------------------------------------------------------
        create_query = """
        MATCH (a), (b)
        WHERE elementId(a) = $from_id AND elementId(b) = $to_id
        CREATE (a)-[r:CAUSAL {
            weight: $confidence,
            mechanism: $mechanism,
            created_at: datetime(),
            last_accessed: datetime(),
            discovered_by: 'nightly_causal_inference',
            llm_last_analyzed: datetime(),
            score_accumulator: 0.0
        }]->(b)
        RETURN elementId(r) AS edge_id
        """

        direction = result["direction"]

        with self.driver.session(database=self.database) as session:
            if direction in ("A_CAUSES_B", "BIDIRECTIONAL"):
                r = session.run(
                    create_query,
                    from_id=candidate["node_a_id"],
                    to_id=candidate["node_b_id"],
                    confidence=result["confidence"],
                    mechanism=result["mechanism"],
                ).single()

                self.wal.write({
                    "op": "create_causal_edge",
                    "edge_id": r["edge_id"] if r else None,
                    "from_id": candidate["node_a_id"],
                    "to_id": candidate["node_b_id"],
                    "confidence": result["confidence"],
                    "mechanism": result["mechanism"],
                })

            if direction in ("B_CAUSES_A", "BIDIRECTIONAL"):
                r = session.run(
                    create_query,
                    from_id=candidate["node_b_id"],
                    to_id=candidate["node_a_id"],
                    confidence=result["confidence"],
                    mechanism=result["mechanism"],
                ).single()

                self.wal.write({
                    "op": "create_causal_edge",
                    "edge_id": r["edge_id"] if r else None,
                    "from_id": candidate["node_b_id"],
                    "to_id": candidate["node_a_id"],
                    "confidence": result["confidence"],
                    "mechanism": result["mechanism"],
                })

    def _get_existing_causal_edges(self, limit: int) -> list:
        """
        Fetch existing CAUSAL edges for validation.

        Priority: oldest-validated first.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Existing causal edges for validation
        # ---------------------------------------------------------------
        query = """
        MATCH (a)-[r:CAUSAL]->(b)
        WHERE r.weight IS NOT NULL
        RETURN elementId(r) AS edge_id,
               r.weight AS weight,
               coalesce(r.mechanism, '') AS mechanism,
               duration.between(r.created_at, datetime()).days AS edge_age_days,
               elementId(a) AS cause_id,
               coalesce(a.summary, a.content, '') AS cause_summary,
               labels(a)[0] AS cause_type,
               coalesce(a.timestamp, '') AS cause_time,
               elementId(b) AS effect_id,
               coalesce(b.summary, b.content, '') AS effect_summary,
               labels(b)[0] AS effect_type,
               coalesce(b.timestamp, '') AS effect_time
        ORDER BY coalesce(r.llm_last_analyzed, datetime('2020-01-01')) ASC
        LIMIT $limit
        """

        with self.driver.session(database=self.database) as session:
            return [dict(record) for record in session.run(query, limit=limit)]

    def _validate_causal_edge(self, edge_data: dict, dry_run: bool) -> Optional[dict]:
        """
        Use the LLM to re-validate an existing causal edge.
        """
        if dry_run:
            return None

        # Gather new evidence: recent nodes connected to cause or effect
        new_evidence = self._gather_new_evidence(edge_data["cause_id"], edge_data["effect_id"])

        prompt = VALIDATION_PROMPT.format(
            cause_type=edge_data["cause_type"],
            cause_summary=edge_data["cause_summary"][:500],
            cause_time=edge_data.get("cause_time", "N/A"),
            effect_type=edge_data["effect_type"],
            effect_summary=edge_data["effect_summary"][:500],
            effect_time=edge_data.get("effect_time", "N/A"),
            current_weight=round(edge_data["weight"], 3),
            mechanism=edge_data.get("mechanism", "Not recorded"),
            edge_age_days=edge_data.get("edge_age_days", "unknown"),
            new_evidence=new_evidence if new_evidence else "No new evidence available.",
        )

        try:
            response = self.model_mgr.generate(
                prompt=prompt,
                max_tokens=self.causal_config["max_tokens"],
                temperature=self.causal_config["temperature"],
            )
            return parse_validation_response(response)
        except Exception as e:
            logger.warning("Validation LLM call failed: %s", e)
            return None

    def _gather_new_evidence(self, cause_id: str, effect_id: str) -> str:
        """
        Find recent memories connected to the cause or effect nodes
        that could serve as new evidence for or against causation.

        Returns a text summary of new evidence (up to 500 chars).
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Recent evidence near cause/effect
        # ---------------------------------------------------------------
        query = """
        MATCH (target)-[r]->(neighbor)
        WHERE elementId(target) IN [$cause_id, $effect_id]
          AND r.created_at > datetime() - duration('P30D')
        RETURN coalesce(neighbor.summary, neighbor.content, '') AS summary,
               type(r) AS rel_type,
               labels(neighbor)[0] AS node_type
        ORDER BY r.created_at DESC
        LIMIT 5
        """

        with self.driver.session(database=self.database) as session:
            records = list(session.run(query, cause_id=cause_id, effect_id=effect_id))

        if not records:
            return ""

        evidence_lines = []
        for r in records:
            evidence_lines.append(
                f"- [{r['rel_type']}] {r['node_type']}: {r['summary'][:100]}"
            )
        return "\n".join(evidence_lines)

    def _update_causal_edge(self, edge_id: str, new_weight: float, reasoning: str):
        """Update an existing CAUSAL edge's weight and reasoning."""
        query = """
        MATCH ()-[r:CAUSAL]->()
        WHERE elementId(r) = $edge_id
        SET r.weight = $new_weight,
            r.llm_last_analyzed = datetime(),
            r.llm_reasoning = $reasoning
        """
        with self.driver.session(database=self.database) as session:
            session.run(query, edge_id=edge_id, new_weight=new_weight, reasoning=reasoning[:200])

        self.wal.write({
            "op": "update_causal_edge",
            "edge_id": edge_id,
            "new_weight": new_weight,
        })

    def _remove_causal_edge(self, edge_id: str):
        """
        Remove a CAUSAL edge that is no longer valid.

        Note: We don't actually DELETE the edge; we set weight to 0 and
        mark it as invalidated. The consistency manager handles actual cleanup.
        """
        query = """
        MATCH ()-[r:CAUSAL]->()
        WHERE elementId(r) = $edge_id
        SET r.weight = 0.0,
            r.invalidated = true,
            r.invalidated_at = datetime(),
            r.prune_candidate = true
        """
        with self.driver.session(database=self.database) as session:
            session.run(query, edge_id=edge_id)

        self.wal.write({"op": "invalidate_causal_edge", "edge_id": edge_id})
