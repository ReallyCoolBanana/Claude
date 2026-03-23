#!/usr/bin/env python3
"""
Step 3.4: LLM-Driven Edge Weight Analysis
===========================================
Uses the 30B nightly model to evaluate edge relationship strength.

For each batch of edges:
    1. Fetch the two connected nodes' summaries from Neo4j
    2. Construct a prompt with node summaries + edge type
    3. Ask the LLM to rate connection strength [0, 1]
    4. Parse response and update edge weight

Processing targets:
    - ~2000 edges per night
    - Batch size: 5 edges per LLM call
    - ~400 LLM calls total
    - At ~70 tok/s with 512 max tokens: ~7.3s per call
    - Total LLM time: ~49 minutes
    - With overhead: ~60-90 minutes

Edge selection priority:
    1. Edges that haven't been LLM-analyzed recently
    2. Edges with weights near decision boundaries (0.3-0.7)
    3. Edges connected to frequently-accessed nodes
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("nightly.llm_edge_analysis")


# ---------------------------------------------------------------------------
# Prompt Templates (one per edge type)
# ---------------------------------------------------------------------------

PROMPT_TEMPLATES = {
    "STRUCTURAL": """You are a memory relationship analyst. Evaluate the structural relationship between two memories.

Memory A: {node_a_summary}
Memory A type: {node_a_type}

Memory B: {node_b_summary}
Memory B type: {node_b_type}

Relationship type: STRUCTURAL (part-of, contains, belongs-to, component-of)
Current weight: {current_weight}
Days since last access: {days_since_access}

Task: Rate the strength of this structural connection on a scale of 0.0 to 1.0.
- 1.0 = essential structural relationship (A is a core component of B, or vice versa)
- 0.7 = strong structural relationship (A is meaningfully part of B's structure)
- 0.4 = moderate structural relationship (A has some structural overlap with B)
- 0.1 = weak structural relationship (A and B are barely structurally related)
- 0.0 = no structural relationship (these should not be connected)

Respond in this exact format:
SCORE: <float between 0.0 and 1.0>
REASONING: <one sentence explaining why>""",

    "SEMANTIC": """You are a memory relationship analyst. Evaluate the semantic relationship between two memories.

Memory A: {node_a_summary}
Memory A type: {node_a_type}

Memory B: {node_b_summary}
Memory B type: {node_b_type}

Relationship type: SEMANTIC (similar meaning, related concepts, shared topics)
Current weight: {current_weight}
Days since last access: {days_since_access}

Task: Rate the strength of this semantic connection on a scale of 0.0 to 1.0.
- 1.0 = nearly identical or deeply intertwined concepts
- 0.7 = strongly related concepts that frequently co-occur
- 0.4 = moderately related with shared themes
- 0.1 = tangentially related
- 0.0 = unrelated (these should not be connected)

Respond in this exact format:
SCORE: <float between 0.0 and 1.0>
REASONING: <one sentence explaining why>""",

    "CAUSAL": """You are a memory relationship analyst. Evaluate the causal relationship between two memories.

Memory A (potential cause): {node_a_summary}
Memory A type: {node_a_type}

Memory B (potential effect): {node_b_summary}
Memory B type: {node_b_type}

Relationship type: CAUSAL (A causes/enables/leads-to B)
Current weight: {current_weight}
Days since last access: {days_since_access}

Task: Rate the strength of this causal connection on a scale of 0.0 to 1.0.
- 1.0 = definite causal relationship (A directly causes B)
- 0.7 = strong causal link (A significantly increases likelihood of B)
- 0.4 = moderate causal link (A contributes to B among other factors)
- 0.1 = weak possible causation (A might indirectly influence B)
- 0.0 = no causal relationship

Respond in this exact format:
SCORE: <float between 0.0 and 1.0>
REASONING: <one sentence explaining why>""",

    "TEMPORAL": """You are a memory relationship analyst. Evaluate the temporal relationship between two memories.

Memory A (earlier event): {node_a_summary}
Memory A type: {node_a_type}
Memory A time: {node_a_time}

Memory B (later event): {node_b_summary}
Memory B type: {node_b_type}
Memory B time: {node_b_time}

Relationship type: TEMPORAL (A happened before B, sequential ordering)
Current weight: {current_weight}
Days since last access: {days_since_access}

Task: Rate the importance of this temporal ordering on a scale of 0.0 to 1.0.
- 1.0 = the temporal order is critical to understanding both memories
- 0.7 = the sequence is important context
- 0.4 = the ordering is somewhat relevant
- 0.1 = the temporal connection is incidental
- 0.0 = the temporal ordering is meaningless

Respond in this exact format:
SCORE: <float between 0.0 and 1.0>
REASONING: <one sentence explaining why>""",
}


def parse_llm_score(response: str) -> Optional[tuple]:
    """
    Parse the LLM response to extract score and reasoning.

    Expected format:
        SCORE: 0.75
        REASONING: These concepts share deep structural overlap because...

    Returns:
        (score: float, reasoning: str) or None if parsing fails

    Handles common malformed responses:
        - Score outside [0, 1] -> clamped
        - Missing REASONING line -> uses "No reasoning provided"
        - Extra text before/after -> regex extraction
        - Score as percentage -> divided by 100
    """
    # Try to extract SCORE line
    score_match = re.search(r'SCORE:\s*([\d.]+)', response)
    if not score_match:
        # Try alternative formats
        score_match = re.search(r'(\d+\.?\d*)\s*/\s*1\.0', response)
        if not score_match:
            score_match = re.search(r'(?:score|rating|strength)[:\s]+(\d+\.?\d*)', response, re.IGNORECASE)

    if not score_match:
        return None

    score = float(score_match.group(1))

    # Handle percentage format
    if score > 1.0 and score <= 100:
        score = score / 100.0

    # Clamp to [0, 1]
    score = max(0.0, min(1.0, score))

    # Extract reasoning
    reasoning_match = re.search(r'REASONING:\s*(.+)', response, re.DOTALL)
    reasoning = reasoning_match.group(1).strip() if reasoning_match else "No reasoning provided"
    # Take only first sentence
    reasoning = reasoning.split('\n')[0].strip()

    return (round(score, 3), reasoning)


class LLMEdgeAnalyzer:
    """
    Analyzes edge weights using the nightly 30B model.

    Selection strategy for which edges to analyze:
        1. Edges never analyzed by LLM (llm_last_analyzed IS NULL)
        2. Edges with ambiguous weights (0.3 <= weight <= 0.7)
        3. Edges on frequently-accessed nodes
        4. Oldest-analyzed edges (for periodic re-evaluation)

    Usage:
        analyzer = LLMEdgeAnalyzer(driver, db, config, model_mgr, wal, shutdown)
        metrics = analyzer.execute(soft_deadline, hard_deadline, dry_run=False)
    """

    def __init__(self, driver, database: str, config: dict, model_mgr, wal, shutdown):
        self.driver = driver
        self.database = database
        self.config = config
        self.llm_config = config["llm_edge_analysis"]
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
        Run LLM edge analysis.

        Process:
            1. Select candidate edges (priority-ordered)
            2. Fetch node summaries for each edge
            3. Build prompts and call LLM in batches
            4. Parse responses and update weights
            5. Log all changes to WAL

        Returns:
            {
                "edges_analyzed": 1850,
                "edges_updated": 1720,
                "parse_failures": 45,
                "avg_weight_change": 0.08,
                "llm_calls": 370,
                "duration_s": 4200
            }

        Time estimate: 60-90 minutes for 2000 edges
        """
        start = time.time()
        target = self.llm_config["target_edges_per_night"]
        batch_size = self.llm_config["batch_size"]

        metrics = {
            "edges_analyzed": 0,
            "edges_updated": 0,
            "parse_failures": 0,
            "avg_weight_change": 0.0,
            "llm_calls": 0,
            "retries": 0,
        }

        weight_changes = []

        # Step 1: Select candidate edges
        candidates = self._select_candidates(target)
        logger.info("Selected %d candidate edges for LLM analysis", len(candidates))

        # Step 2: Process in batches
        for i in range(0, len(candidates), batch_size):
            if self.shutdown.requested:
                logger.warning("Shutdown requested during LLM edge analysis.")
                break
            if datetime.now() >= hard_deadline:
                logger.warning("Hard deadline reached during LLM edge analysis.")
                break

            batch = candidates[i:i + batch_size]

            for edge_data in batch:
                if self.shutdown.requested or datetime.now() >= hard_deadline:
                    break

                result = self._analyze_single_edge(edge_data, dry_run)
                metrics["edges_analyzed"] += 1
                metrics["llm_calls"] += 1

                if result is None:
                    metrics["parse_failures"] += 1
                    continue

                new_score, reasoning = result
                old_weight = edge_data["weight"]
                weight_changes.append(abs(new_score - old_weight))
                metrics["edges_updated"] += 1

                if not dry_run:
                    self._update_edge_weight(
                        edge_data["edge_id"],
                        new_score,
                        reasoning,
                    )

            # Progress logging every 10 batches
            if (i // batch_size) % 10 == 0 and i > 0:
                logger.info(
                    "LLM edge analysis progress: %d/%d edges (%.0f%%)",
                    metrics["edges_analyzed"],
                    len(candidates),
                    100 * metrics["edges_analyzed"] / len(candidates),
                )

        if weight_changes:
            metrics["avg_weight_change"] = round(
                sum(weight_changes) / len(weight_changes), 4
            )

        metrics["duration_s"] = round(time.time() - start, 1)
        return metrics

    def _select_candidates(self, target: int) -> list:
        """
        Select edges for LLM analysis, prioritized by need.

        Priority ordering:
            1. Never analyzed (llm_last_analyzed IS NULL) - up to 40% of target
            2. Ambiguous weights (0.3-0.7) - up to 30% of target
            3. High-traffic edges (connected to frequently accessed nodes) - up to 20%
            4. Stale analysis (oldest llm_last_analyzed) - remaining 10%

        Returns list of dicts:
            [
                {
                    "edge_id": "4:abc:123",
                    "edge_type": "SEMANTIC",
                    "weight": 0.65,
                    "node_a_id": "4:abc:1",
                    "node_a_summary": "...",
                    "node_a_type": "EpisodicMemory",
                    "node_b_id": "4:abc:2",
                    "node_b_summary": "...",
                    "node_b_type": "FactMemory",
                    "days_since_access": 5,
                },
                ...
            ]

        Time estimate: 2-5 seconds for the queries
        """
        candidates = []

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Never-analyzed edges (priority 1)
        # ---------------------------------------------------------------
        never_analyzed_query = """
        MATCH (a)-[r]->(b)
        WHERE r.weight IS NOT NULL
          AND r.llm_last_analyzed IS NULL
        WITH a, r, b,
             duration.between(
                 coalesce(r.last_accessed, r.created_at, datetime('2026-01-01')),
                 datetime()
             ).days AS days_since
        RETURN elementId(r) AS edge_id,
               type(r) AS edge_type,
               r.weight AS weight,
               elementId(a) AS node_a_id,
               coalesce(a.summary, a.content, '') AS node_a_summary,
               labels(a)[0] AS node_a_type,
               elementId(b) AS node_b_id,
               coalesce(b.summary, b.content, '') AS node_b_summary,
               labels(b)[0] AS node_b_type,
               days_since AS days_since_access,
               coalesce(a.timestamp, '') AS node_a_time,
               coalesce(b.timestamp, '') AS node_b_time
        ORDER BY r.weight DESC
        LIMIT $limit
        """

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Ambiguous-weight edges (priority 2)
        # ---------------------------------------------------------------
        ambiguous_query = """
        MATCH (a)-[r]->(b)
        WHERE r.weight IS NOT NULL
          AND r.weight >= 0.3 AND r.weight <= 0.7
          AND (r.llm_last_analyzed IS NULL
               OR r.llm_last_analyzed < datetime() - duration('P7D'))
        WITH a, r, b,
             duration.between(
                 coalesce(r.last_accessed, r.created_at, datetime('2026-01-01')),
                 datetime()
             ).days AS days_since
        RETURN elementId(r) AS edge_id,
               type(r) AS edge_type,
               r.weight AS weight,
               elementId(a) AS node_a_id,
               coalesce(a.summary, a.content, '') AS node_a_summary,
               labels(a)[0] AS node_a_type,
               elementId(b) AS node_b_id,
               coalesce(b.summary, b.content, '') AS node_b_summary,
               labels(b)[0] AS node_b_type,
               days_since AS days_since_access,
               coalesce(a.timestamp, '') AS node_a_time,
               coalesce(b.timestamp, '') AS node_b_time
        ORDER BY abs(r.weight - 0.5) ASC
        LIMIT $limit
        """

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Stale-analysis edges (priority 4)
        # ---------------------------------------------------------------
        stale_query = """
        MATCH (a)-[r]->(b)
        WHERE r.weight IS NOT NULL
          AND r.llm_last_analyzed IS NOT NULL
        WITH a, r, b,
             duration.between(
                 coalesce(r.last_accessed, r.created_at, datetime('2026-01-01')),
                 datetime()
             ).days AS days_since
        RETURN elementId(r) AS edge_id,
               type(r) AS edge_type,
               r.weight AS weight,
               elementId(a) AS node_a_id,
               coalesce(a.summary, a.content, '') AS node_a_summary,
               labels(a)[0] AS node_a_type,
               elementId(b) AS node_b_id,
               coalesce(b.summary, b.content, '') AS node_b_summary,
               labels(b)[0] AS node_b_type,
               days_since AS days_since_access,
               coalesce(a.timestamp, '') AS node_a_time,
               coalesce(b.timestamp, '') AS node_b_time
        ORDER BY r.llm_last_analyzed ASC
        LIMIT $limit
        """

        seen_ids = set()

        with self.driver.session(database=self.database) as session:
            # Priority 1: Never analyzed (40%)
            for record in session.run(never_analyzed_query, limit=int(target * 0.4)):
                edge = dict(record)
                if edge["edge_id"] not in seen_ids:
                    candidates.append(edge)
                    seen_ids.add(edge["edge_id"])

            # Priority 2: Ambiguous weights (30%)
            for record in session.run(ambiguous_query, limit=int(target * 0.3)):
                edge = dict(record)
                if edge["edge_id"] not in seen_ids:
                    candidates.append(edge)
                    seen_ids.add(edge["edge_id"])

            # Priority 4: Stale (fill remaining)
            remaining = target - len(candidates)
            if remaining > 0:
                for record in session.run(stale_query, limit=remaining):
                    edge = dict(record)
                    if edge["edge_id"] not in seen_ids:
                        candidates.append(edge)
                        seen_ids.add(edge["edge_id"])

        return candidates[:target]

    def _analyze_single_edge(self, edge_data: dict, dry_run: bool) -> Optional[tuple]:
        """
        Analyze a single edge using the LLM.

        Returns (score, reasoning) or None if parsing fails after retries.

        Retry logic:
            - Up to max_retries attempts (default 3)
            - On parse failure, slightly rephrase the prompt
            - On connection error, wait 5 seconds and retry
        """
        edge_type = edge_data["edge_type"]
        template = PROMPT_TEMPLATES.get(edge_type)
        if not template:
            logger.warning("No template for edge type: %s", edge_type)
            return None

        prompt = template.format(
            node_a_summary=edge_data["node_a_summary"][:500],  # Truncate long summaries
            node_a_type=edge_data["node_a_type"],
            node_b_summary=edge_data["node_b_summary"][:500],
            node_b_type=edge_data["node_b_type"],
            current_weight=round(edge_data["weight"], 3),
            days_since_access=edge_data.get("days_since_access", "unknown"),
            node_a_time=edge_data.get("node_a_time", "N/A"),
            node_b_time=edge_data.get("node_b_time", "N/A"),
        )

        max_retries = self.llm_config["max_retries"]
        for attempt in range(max_retries):
            try:
                if dry_run:
                    # In dry-run, return current weight unchanged
                    return (edge_data["weight"], "dry-run: no change")

                response = self.model_mgr.generate(
                    prompt=prompt,
                    max_tokens=self.llm_config["max_tokens"],
                    temperature=self.llm_config["temperature"],
                )

                result = parse_llm_score(response)
                if result is not None:
                    return result

                logger.warning(
                    "Failed to parse LLM response for edge %s (attempt %d/%d): %s",
                    edge_data["edge_id"],
                    attempt + 1,
                    max_retries,
                    response[:200],
                )

                # On retry, add explicit instruction
                if attempt < max_retries - 1:
                    prompt += "\n\nIMPORTANT: You MUST respond with exactly 'SCORE: X.XX' on the first line."

            except Exception as e:
                logger.warning(
                    "LLM call failed for edge %s (attempt %d/%d): %s",
                    edge_data["edge_id"],
                    attempt + 1,
                    max_retries,
                    e,
                )
                if attempt < max_retries - 1:
                    time.sleep(5)

        return None

    def _update_edge_weight(self, edge_id: str, new_weight: float, reasoning: str):
        """
        Update a single edge's weight from LLM analysis.

        Sets:
            - r.weight = new_weight
            - r.llm_last_analyzed = now
            - r.llm_reasoning = reasoning (for debugging/audit)
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Update single edge weight
        # ---------------------------------------------------------------
        update_query = """
        MATCH ()-[r]->()
        WHERE elementId(r) = $edge_id
        SET r.weight = $new_weight,
            r.llm_last_analyzed = datetime(),
            r.llm_reasoning = $reasoning
        RETURN r.weight AS updated_weight
        """

        with self.driver.session(database=self.database) as session:
            session.run(
                update_query,
                edge_id=edge_id,
                new_weight=new_weight,
                reasoning=reasoning[:200],  # Truncate reasoning
            )

        self.wal.write({
            "op": "llm_edge_update",
            "edge_id": edge_id,
            "new_weight": new_weight,
            "reasoning": reasoning[:200],
        })
