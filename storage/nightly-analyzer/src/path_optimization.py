#!/usr/bin/env python3
"""
Step 3.6: Path Pointer Optimization
=====================================
Re-computes path pointers (lancedb_ids arrays) on edges based on:
    1. Access patterns from the scoring accumulator
    2. Edge weight changes from decay + LLM analysis
    3. LanceDB vector proximity to both endpoints

Path pointers allow the two-phase retrieval pipeline to jump from
a graph traversal result directly to relevant vectors in LanceDB,
avoiding a full vector search for each graph hit.

Edge schema reminder:
    -[r:TYPE {lancedb_ids: ["vec_001", "vec_042", ...]}]->
    These IDs point into the LanceDB 'memories' table.

Algorithm:
    For each high-traffic edge (identified by access patterns):
        1. Get the embedding vectors for both endpoint nodes
        2. Query LanceDB for the K nearest vectors to each endpoint
        3. Take the intersection + union, ranked by combined distance
        4. Store the top N vector IDs as path pointers on the edge
        5. Cap at max_pointers_per_edge to bound memory usage

This step is primarily mechanical (no LLM needed) but uses the
embedding model which is always resident.

Processing targets:
    - ~3000 paths per night
    - ~20 vector candidates per endpoint
    - Expected duration: 30-45 minutes

Prerequisites:
    - LanceDB on NVMe with mmap optimizations (Phase 1)
    - Embedding model loaded (always resident)
    - Neo4j edges have lancedb_ids property
    - Scoring accumulator has access frequency data
"""

import json
import logging
import time
from datetime import datetime
from typing import Optional

import lancedb
import numpy as np

logger = logging.getLogger("nightly.path_optimization")


class PathOptimizer:
    """
    Optimizes path pointers on edges for faster two-phase retrieval.

    The path pointers are stored as lancedb_ids arrays on Neo4j edges.
    They act as pre-computed shortcuts: when the graph traversal finds
    an edge, the retrieval pipeline can immediately fetch the associated
    vectors from LanceDB without doing a new similarity search.

    Usage:
        optimizer = PathOptimizer(driver, db, config, wal, shutdown)
        metrics = optimizer.execute(soft_deadline, hard_deadline)
    """

    def __init__(self, driver, database: str, config: dict, wal, shutdown):
        self.driver = driver
        self.database = database
        self.config = config
        self.path_config = config["path_optimization"]
        self.wal = wal
        self.shutdown = shutdown

        # Connect to LanceDB
        self.lance_db = lancedb.connect(config["lancedb"]["path"])
        self.lance_table = self.lance_db.open_table(config["lancedb"]["table_name"])

    def execute(
        self,
        soft_deadline: datetime,
        hard_deadline: datetime,
        dry_run: bool = False,
    ) -> dict:
        """
        Run path pointer optimization.

        Steps:
            1. Identify high-traffic edges (most accessed, recently updated)
            2. For each edge, compute optimal path pointers
            3. Update edges in batched transactions

        Returns:
            {
                "edges_optimized": 2800,
                "total_pointers_set": 22400,
                "avg_pointers_per_edge": 8.0,
                "pointers_added": 5600,
                "pointers_removed": 3200,
                "duration_s": 1800
            }

        Time estimate: 30-45 minutes for 3000 edges
        """
        start = time.time()
        target = self.path_config["target_paths_per_night"]

        metrics = {
            "edges_optimized": 0,
            "total_pointers_set": 0,
            "pointers_added": 0,
            "pointers_removed": 0,
        }

        # Step 1: Select high-traffic edges
        edges = self._select_high_traffic_edges(target)
        logger.info("Selected %d edges for path optimization", len(edges))

        # Step 2: Process each edge
        batch = []
        batch_size = 100  # Update in batches of 100 edges

        for edge_data in edges:
            if self.shutdown.requested or datetime.now() >= hard_deadline:
                break

            new_pointers = self._compute_optimal_pointers(edge_data)
            if new_pointers is not None:
                old_pointers = set(edge_data.get("current_lancedb_ids", []) or [])
                new_pointer_set = set(new_pointers)

                batch.append({
                    "edge_id": edge_data["edge_id"],
                    "lancedb_ids": new_pointers,
                })

                metrics["edges_optimized"] += 1
                metrics["total_pointers_set"] += len(new_pointers)
                metrics["pointers_added"] += len(new_pointer_set - old_pointers)
                metrics["pointers_removed"] += len(old_pointers - new_pointer_set)

            # Flush batch
            if len(batch) >= batch_size:
                if not dry_run:
                    self._update_edge_pointers_batch(batch)
                batch = []

        # Flush remaining
        if batch and not dry_run:
            self._update_edge_pointers_batch(batch)

        if metrics["edges_optimized"] > 0:
            metrics["avg_pointers_per_edge"] = round(
                metrics["total_pointers_set"] / metrics["edges_optimized"], 1
            )

        metrics["duration_s"] = round(time.time() - start, 1)
        return metrics

    def _select_high_traffic_edges(self, limit: int) -> list:
        """
        Select edges that would benefit most from pointer optimization.

        Priority:
            1. Edges with recent access (last_accessed within 7 days)
            2. Edges whose weights changed significantly (from decay/LLM steps)
            3. Edges with stale or empty path pointers

        Returns list of edge data dicts including both endpoint node IDs
        and their embedding vectors.

        Time estimate: 3-5 seconds
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Select high-traffic edges for optimization
        # ---------------------------------------------------------------
        # This query scores edges by a combination of:
        #   - recency of access (higher = better)
        #   - weight change magnitude (higher = needs update)
        #   - staleness of path pointers (older = needs update)
        # ---------------------------------------------------------------
        query = """
        MATCH (a)-[r]->(b)
        WHERE r.weight IS NOT NULL AND r.weight > 0.05
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
        WITH a, r, b,
             // Recency score: 1.0 for today, decaying
             CASE WHEN r.last_accessed IS NOT NULL
                  THEN 1.0 / (1.0 + duration.between(r.last_accessed, datetime()).days)
                  ELSE 0.1
             END AS recency_score,
             // Pointer staleness: boost edges without recent optimization
             CASE WHEN r.pointers_optimized_at IS NULL THEN 1.0
                  WHEN duration.between(r.pointers_optimized_at, datetime()).days > 7 THEN 0.8
                  ELSE 0.2
             END AS staleness_score
        WITH a, r, b,
             (recency_score * 0.5 + staleness_score * 0.3 + r.weight * 0.2) AS priority_score
        ORDER BY priority_score DESC
        LIMIT $limit
        RETURN elementId(r) AS edge_id,
               type(r) AS edge_type,
               r.weight AS weight,
               coalesce(r.lancedb_ids, []) AS current_lancedb_ids,
               elementId(a) AS node_a_id,
               a.embedding AS node_a_embedding,
               coalesce(a.lancedb_id, '') AS node_a_lancedb_id,
               elementId(b) AS node_b_id,
               b.embedding AS node_b_embedding,
               coalesce(b.lancedb_id, '') AS node_b_lancedb_id
        """

        with self.driver.session(database=self.database) as session:
            return [dict(record) for record in session.run(query, limit=limit)]

    def _compute_optimal_pointers(self, edge_data: dict) -> Optional[list]:
        """
        Compute optimal path pointers for a single edge.

        Algorithm:
            1. Get K nearest vectors to node A's embedding in LanceDB
            2. Get K nearest vectors to node B's embedding in LanceDB
            3. Score each candidate by combined proximity:
               score = (1 / (1 + dist_to_A)) * (1 / (1 + dist_to_B))
               This favors vectors that are close to BOTH endpoints.
            4. Take the top N by combined score
            5. Return their LanceDB IDs

        Returns list of LanceDB ID strings, or None if embeddings are missing.

        Time estimate: ~5-10ms per edge (LanceDB vector search is fast with mmap)
        """
        k = self.path_config["vector_candidates"]
        max_pointers = self.path_config["max_pointers_per_edge"]

        emb_a = edge_data.get("node_a_embedding")
        emb_b = edge_data.get("node_b_embedding")

        if emb_a is None or emb_b is None:
            return None

        try:
            # Convert embeddings to numpy arrays
            vec_a = np.array(emb_a, dtype=np.float32)
            vec_b = np.array(emb_b, dtype=np.float32)

            # Query LanceDB for nearest vectors to each endpoint
            # LanceDB's search returns results with _distance column
            results_a = (
                self.lance_table.search(vec_a)
                .limit(k)
                .to_pandas()
            )
            results_b = (
                self.lance_table.search(vec_b)
                .limit(k)
                .to_pandas()
            )

            # Build candidate pool with distances to both endpoints
            candidates = {}

            for _, row in results_a.iterrows():
                vec_id = str(row.get("id", row.name))
                candidates[vec_id] = {
                    "dist_a": float(row["_distance"]),
                    "dist_b": float("inf"),
                }

            for _, row in results_b.iterrows():
                vec_id = str(row.get("id", row.name))
                if vec_id in candidates:
                    candidates[vec_id]["dist_b"] = float(row["_distance"])
                else:
                    candidates[vec_id] = {
                        "dist_a": float("inf"),
                        "dist_b": float(row["_distance"]),
                    }

            # Score by combined proximity (closer to both = better)
            scored = []
            for vec_id, dists in candidates.items():
                combined_score = (1.0 / (1.0 + dists["dist_a"])) * (1.0 / (1.0 + dists["dist_b"]))
                scored.append((vec_id, combined_score))

            # Sort by score descending, take top N
            scored.sort(key=lambda x: x[1], reverse=True)
            optimal_ids = [vec_id for vec_id, _ in scored[:max_pointers]]

            return optimal_ids

        except Exception as e:
            logger.warning("Failed to compute pointers for edge %s: %s", edge_data["edge_id"], e)
            return None

    def _update_edge_pointers_batch(self, batch: list):
        """
        Update path pointers for a batch of edges in a single transaction.

        Time estimate: ~50ms per batch of 100 edges
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Batch update path pointers
        # ---------------------------------------------------------------
        # UNWIND processes each item in the batch within a single transaction.
        # ---------------------------------------------------------------
        query = """
        UNWIND $updates AS update
        MATCH ()-[r]->()
        WHERE elementId(r) = update.edge_id
        SET r.lancedb_ids = update.lancedb_ids,
            r.pointers_optimized_at = datetime()
        RETURN count(r) AS updated
        """

        with self.driver.session(database=self.database) as session:
            result = session.run(query, updates=batch).single()
            updated = result["updated"]

        self.wal.write({
            "op": "batch_pointer_update",
            "count": updated,
        })

        logger.debug("Updated path pointers for %d edges", updated)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
# After running, verify path pointers with:
#
#   MATCH ()-[r]->()
#   WHERE r.lancedb_ids IS NOT NULL AND size(r.lancedb_ids) > 0
#   RETURN type(r) AS edge_type,
#          count(r) AS edges_with_pointers,
#          avg(size(r.lancedb_ids)) AS avg_pointers,
#          min(size(r.lancedb_ids)) AS min_pointers,
#          max(size(r.lancedb_ids)) AS max_pointers
#   ORDER BY edge_type
#
# Expected output:
#   edge_type    | edges_with_pointers | avg_pointers | min | max
#   CAUSAL       | 6500                | 7.2          | 1   | 10
#   SEMANTIC     | 12000               | 8.5          | 2   | 10
#   STRUCTURAL   | 9800                | 6.8          | 1   | 10
#   TEMPORAL     | 8200                | 5.4          | 1   | 10
#
# Also verify pointers are valid by sampling:
#
#   import lancedb
#   db = lancedb.connect("/nvme/lancedb")
#   table = db.open_table("memories")
#   # Pick a random lancedb_id from an edge and confirm it exists
#   result = table.search().where(f"id = 'vec_001'").limit(1).to_pandas()
#   assert len(result) == 1
# ---------------------------------------------------------------------------
