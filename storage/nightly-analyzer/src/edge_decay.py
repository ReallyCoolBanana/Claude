#!/usr/bin/env python3
"""
Step 3.3: Edge Weight Decay (Mechanical, No LLM)
==================================================
Applies time-based exponential decay to all edge weights in Neo4j.

Formula:
    new_weight = old_weight * decay_factor^(days_since_last_access)

Decay rates (per edge type, applied per day):
    STRUCTURAL: 0.995  (~50% strength after 138 days)
    SEMANTIC:   0.990  (~50% strength after 69 days)
    CAUSAL:     0.992  (~50% strength after 86 days)
    TEMPORAL:   0.980  (~50% strength after 34 days)

The rationale: structural relationships (e.g., "X is part of Y") are
the most durable. Temporal relationships ("X happened before Y") lose
relevance fastest as new events occur.

After decay, edges below min_weight_threshold (0.05) are flagged for
pruning (not deleted -- the condensation step may reference them).

Score boost: accumulated /score feedback from the MCP server increases
weights. A memory that users frequently access or rate highly resists
decay.

Processing:
    - Batched Cypher queries (1500 relationships per transaction)
    - Total edges per night: all edges (typically 40,000-80,000)
    - Expected duration: 15-30 minutes (no LLM, pure graph operations)

Prerequisites:
    - Neo4j running with schema from Phase 1
    - Edges have properties: weight (float), last_accessed (datetime),
      score_accumulator (float, from Phase 2 /score endpoint)
"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("nightly.edge_decay")


class EdgeDecayProcessor:
    """
    Processes exponential decay on all edge weights.

    Edge schema (from Phase 1):
        -[r:STRUCTURAL|SEMANTIC|CAUSAL|TEMPORAL]-
        r.weight: float [0, 1]
        r.last_accessed: datetime
        r.score_accumulator: float (sum of /score boosts since last decay)
        r.created_at: datetime
        r.lancedb_ids: list<string>  (path pointers)

    Usage:
        processor = EdgeDecayProcessor(driver, db, config, wal, shutdown)
        metrics = processor.execute(
            soft_deadline=datetime(...),
            hard_deadline=datetime(...),
            dry_run=False,
        )
    """

    def __init__(self, driver, database: str, config: dict, wal, shutdown):
        self.driver = driver
        self.database = database
        self.config = config
        self.decay_config = config["decay"]
        self.wal = wal
        self.shutdown = shutdown

    def execute(
        self,
        soft_deadline: datetime,
        hard_deadline: datetime,
        dry_run: bool = False,
    ) -> dict:
        """
        Run edge decay across all edge types.

        Steps:
            1. Get weight distribution before decay (for verification)
            2. Apply decay per edge type in batches
            3. Apply score boosts
            4. Flag low-weight edges for pruning
            5. Get weight distribution after decay
            6. Reset score accumulators

        Returns metrics dict:
            {
                "edges_processed": 45000,
                "edges_by_type": {"STRUCTURAL": 12000, ...},
                "edges_flagged_for_pruning": 230,
                "score_boosts_applied": 1500,
                "weight_distribution_before": {"mean": 0.72, "median": 0.78, ...},
                "weight_distribution_after": {"mean": 0.68, "median": 0.74, ...},
                "duration_s": 420
            }

        Time estimate: 15-30 minutes for 40,000-80,000 edges
        """
        start = time.time()
        metrics = {
            "edges_processed": 0,
            "edges_by_type": {},
            "edges_flagged_for_pruning": 0,
            "score_boosts_applied": 0,
        }

        # Step 1: Get weight distribution before decay
        metrics["weight_distribution_before"] = self._get_weight_distribution()
        logger.info(
            "Weight distribution before decay: %s",
            json.dumps(metrics["weight_distribution_before"]),
        )

        # Step 2: Apply decay per edge type
        edge_types = ["STRUCTURAL", "SEMANTIC", "CAUSAL", "TEMPORAL"]
        for edge_type in edge_types:
            if self.shutdown.requested or datetime.now() >= hard_deadline:
                logger.warning("Deadline or shutdown reached during decay.")
                break

            decay_rate = self.decay_config["rates"][edge_type]
            count = self._apply_decay_for_type(edge_type, decay_rate, dry_run)
            metrics["edges_by_type"][edge_type] = count
            metrics["edges_processed"] += count

            logger.info(
                "Decayed %d %s edges (rate=%.4f)",
                count, edge_type, decay_rate,
            )

        # Step 3: Apply score boosts
        if not self.shutdown.requested and datetime.now() < hard_deadline:
            metrics["score_boosts_applied"] = self._apply_score_boosts(dry_run)
            logger.info("Applied %d score boosts", metrics["score_boosts_applied"])

        # Step 4: Flag low-weight edges
        if not self.shutdown.requested and datetime.now() < hard_deadline:
            metrics["edges_flagged_for_pruning"] = self._flag_low_weight_edges(dry_run)
            logger.info(
                "Flagged %d edges for pruning (weight < %.3f)",
                metrics["edges_flagged_for_pruning"],
                self.decay_config["min_weight_threshold"],
            )

        # Step 5: Get weight distribution after decay
        metrics["weight_distribution_after"] = self._get_weight_distribution()
        logger.info(
            "Weight distribution after decay: %s",
            json.dumps(metrics["weight_distribution_after"]),
        )

        # Step 6: Reset score accumulators
        if not dry_run and not self.shutdown.requested:
            self._reset_score_accumulators()

        metrics["duration_s"] = round(time.time() - start, 1)
        return metrics

    def _get_weight_distribution(self) -> dict:
        """
        Query weight statistics across all edges.

        Cypher query returns: mean, median (p50), p25, p75, min, max, count

        Expected output:
            {"mean": 0.72, "p25": 0.55, "p50": 0.78, "p75": 0.91, "min": 0.02, "max": 1.0, "count": 45000}

        Time estimate: < 2 seconds
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY
        # ---------------------------------------------------------------
        query = """
        MATCH ()-[r]->()
        WHERE r.weight IS NOT NULL
        WITH r.weight AS w
        ORDER BY w
        WITH collect(w) AS weights
        RETURN
            round(reduce(s = 0.0, x IN weights | s + x) / size(weights), 4) AS mean,
            weights[toInteger(size(weights) * 0.25)] AS p25,
            weights[toInteger(size(weights) * 0.50)] AS p50,
            weights[toInteger(size(weights) * 0.75)] AS p75,
            weights[0] AS min,
            weights[size(weights) - 1] AS max,
            size(weights) AS count
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(query).single()
            if result is None:
                return {"mean": 0, "p25": 0, "p50": 0, "p75": 0, "min": 0, "max": 0, "count": 0}
            return {
                "mean": result["mean"],
                "p25": result["p25"],
                "p50": result["p50"],
                "p75": result["p75"],
                "min": result["min"],
                "max": result["max"],
                "count": result["count"],
            }

    def _apply_decay_for_type(self, edge_type: str, decay_rate: float, dry_run: bool) -> int:
        """
        Apply exponential decay to all edges of a given type.

        Algorithm:
            1. Fetch edges in batches of 1500
            2. For each edge: new_weight = old_weight * decay_rate^days_since_access
            3. Clamp to [0, 1]
            4. Update in a single transaction per batch

        The 'days_since_last_access' is computed from the edge's last_accessed
        property vs. now. If last_accessed is NULL, we use the created_at date.

        Time estimate: ~0.5 seconds per batch of 1500 edges
        """
        batch_size = self.decay_config["batch_size"]
        total_processed = 0

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Count edges of this type
        # ---------------------------------------------------------------
        count_query = f"""
        MATCH ()-[r:{edge_type}]->()
        WHERE r.weight IS NOT NULL
        RETURN count(r) AS cnt
        """

        with self.driver.session(database=self.database) as session:
            total_count = session.run(count_query).single()["cnt"]

        logger.info("Processing %d %s edges in batches of %d", total_count, edge_type, batch_size)

        # Process in batches using SKIP/LIMIT
        offset = 0
        while offset < total_count:
            if self.shutdown.requested:
                break

            # ---------------------------------------------------------------
            # EXACT CYPHER QUERY: Batch decay update
            # ---------------------------------------------------------------
            # This query:
            #   1. Matches edges of the type, ordered by internal ID for stable paging
            #   2. Computes days since last access (or creation if never accessed)
            #   3. Applies exponential decay: weight * rate^days
            #   4. Clamps result to [0, 1]
            #   5. Updates the weight and records the decay timestamp
            #
            # Using elementId() for ordering ensures stable pagination.
            # The CASE handles NULL last_accessed gracefully.
            # ---------------------------------------------------------------
            if dry_run:
                # In dry-run mode, just count without mutating
                dry_query = f"""
                MATCH ()-[r:{edge_type}]->()
                WHERE r.weight IS NOT NULL
                WITH r ORDER BY elementId(r) SKIP $offset LIMIT $batch_size
                WITH r,
                     duration.between(
                         coalesce(r.last_accessed, r.created_at, datetime('2026-01-01')),
                         datetime()
                     ).days AS days_elapsed
                RETURN count(r) AS processed,
                       avg(r.weight * ($decay_rate ^ days_elapsed)) AS avg_new_weight
                """
                with self.driver.session(database=self.database) as session:
                    result = session.run(
                        dry_query,
                        offset=offset,
                        batch_size=batch_size,
                        decay_rate=decay_rate,
                    ).single()
                    batch_count = result["processed"]
            else:
                decay_query = f"""
                MATCH ()-[r:{edge_type}]->()
                WHERE r.weight IS NOT NULL
                WITH r ORDER BY elementId(r) SKIP $offset LIMIT $batch_size
                WITH r,
                     duration.between(
                         coalesce(r.last_accessed, r.created_at, datetime('2026-01-01')),
                         datetime()
                     ).days AS days_elapsed
                SET r.weight = CASE
                        WHEN r.weight * ($decay_rate ^ days_elapsed) < 0.0 THEN 0.0
                        WHEN r.weight * ($decay_rate ^ days_elapsed) > 1.0 THEN 1.0
                        ELSE r.weight * ($decay_rate ^ days_elapsed)
                    END,
                    r.last_decay_at = datetime()
                RETURN count(r) AS processed
                """

                with self.driver.session(database=self.database) as session:
                    result = session.run(
                        decay_query,
                        offset=offset,
                        batch_size=batch_size,
                        decay_rate=decay_rate,
                    ).single()
                    batch_count = result["processed"]

                # WAL entry
                self.wal.write({
                    "op": "edge_decay",
                    "edge_type": edge_type,
                    "batch_offset": offset,
                    "batch_size": batch_count,
                    "decay_rate": decay_rate,
                })

            total_processed += batch_count
            offset += batch_size

            if batch_count > 0:
                logger.debug(
                    "Decayed batch: %s offset=%d count=%d",
                    edge_type, offset, batch_count,
                )

        return total_processed

    def _apply_score_boosts(self, dry_run: bool) -> int:
        """
        Boost edge weights based on accumulated /score feedback.

        The /score endpoint from Phase 2 accumulates a score on edges that
        were part of successful retrievals. This score is added to the weight
        (scaled by score_boost_multiplier) to counteract decay for
        frequently-accessed memories.

        Formula:
            new_weight = min(1.0, decayed_weight + score_accumulator * boost_multiplier)

        After boosting, the score_accumulator is reset to 0.

        Time estimate: < 5 seconds
        """
        boost_mult = self.decay_config["score_boost_multiplier"]

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Apply score boosts
        # ---------------------------------------------------------------
        # Finds all edges where score_accumulator > 0, boosts their weight,
        # then resets the accumulator.
        # ---------------------------------------------------------------
        if dry_run:
            count_query = """
            MATCH ()-[r]->()
            WHERE r.score_accumulator IS NOT NULL AND r.score_accumulator > 0
            RETURN count(r) AS cnt
            """
            with self.driver.session(database=self.database) as session:
                return session.run(count_query).single()["cnt"]

        boost_query = """
        MATCH ()-[r]->()
        WHERE r.score_accumulator IS NOT NULL AND r.score_accumulator > 0
        WITH r, r.score_accumulator * $boost_mult AS boost
        SET r.weight = CASE
                WHEN r.weight + boost > 1.0 THEN 1.0
                ELSE r.weight + boost
            END,
            r.score_accumulator = 0.0,
            r.last_boosted_at = datetime()
        RETURN count(r) AS boosted
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(boost_query, boost_mult=boost_mult).single()
            count = result["boosted"]

        self.wal.write({
            "op": "score_boost",
            "count": count,
            "boost_multiplier": boost_mult,
        })

        return count

    def _flag_low_weight_edges(self, dry_run: bool) -> int:
        """
        Flag edges below minimum weight threshold for potential pruning.

        Does NOT delete edges. Sets a 'prune_candidate' flag so the
        condensation step can decide whether to merge or remove them.

        Time estimate: < 3 seconds
        """
        threshold = self.decay_config["min_weight_threshold"]

        if dry_run:
            query = """
            MATCH ()-[r]->()
            WHERE r.weight IS NOT NULL AND r.weight < $threshold
            RETURN count(r) AS cnt
            """
            with self.driver.session(database=self.database) as session:
                return session.run(query, threshold=threshold).single()["cnt"]

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Flag prune candidates
        # ---------------------------------------------------------------
        flag_query = """
        MATCH ()-[r]->()
        WHERE r.weight IS NOT NULL AND r.weight < $threshold
          AND (r.prune_candidate IS NULL OR r.prune_candidate = false)
        SET r.prune_candidate = true,
            r.prune_flagged_at = datetime()
        RETURN count(r) AS flagged
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(flag_query, threshold=threshold).single()
            count = result["flagged"]

        self.wal.write({
            "op": "flag_prune_candidates",
            "count": count,
            "threshold": threshold,
        })

        return count

    def _reset_score_accumulators(self):
        """
        Reset all score accumulators to 0 after processing.

        This is done separately from the boost step to ensure all
        accumulators are cleared even if boost was partially applied.

        Time estimate: < 2 seconds
        """
        query = """
        MATCH ()-[r]->()
        WHERE r.score_accumulator IS NOT NULL AND r.score_accumulator > 0
        SET r.score_accumulator = 0.0
        RETURN count(r) AS reset
        """
        with self.driver.session(database=self.database) as session:
            result = session.run(query).single()
            logger.debug("Reset %d score accumulators", result["reset"])


# ---------------------------------------------------------------------------
# Verification script
# ---------------------------------------------------------------------------
# After running edge decay, verify with:
#
#   MATCH ()-[r]->()
#   WHERE r.weight IS NOT NULL
#   RETURN type(r) AS edge_type,
#          count(r) AS count,
#          avg(r.weight) AS avg_weight,
#          min(r.weight) AS min_weight,
#          max(r.weight) AS max_weight,
#          sum(CASE WHEN r.prune_candidate = true THEN 1 ELSE 0 END) AS prune_candidates
#   ORDER BY edge_type
#
# Expected output:
#   edge_type    | count | avg_weight | min_weight | max_weight | prune_candidates
#   STRUCTURAL   | 12000 | 0.82       | 0.03       | 1.0        | 15
#   SEMANTIC     | 15000 | 0.71       | 0.02       | 1.0        | 45
#   CAUSAL       |  8000 | 0.75       | 0.04       | 1.0        | 30
#   TEMPORAL     | 10000 | 0.58       | 0.01       | 1.0        | 140
# ---------------------------------------------------------------------------
