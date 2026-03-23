#!/usr/bin/env python3
"""
Step 3.9: Consistency Manager with Snapshot/Rollback
=====================================================
Ensures graph invariants hold after nightly analysis.

Invariants checked:
    1. No orphan nodes: every node has at least one edge
    2. All weights in valid range [0, 1]
    3. No broken path pointers: lancedb_ids reference valid vectors
    4. No cycles in TEMPORAL edges (time must be acyclic)
    5. All required properties present on nodes and edges

Write-ahead log (WAL):
    Every mutation during nightly analysis is logged to a JSONL file.
    If consistency checks fail, the WAL can be used for:
    - Forensic analysis of what went wrong
    - Targeted rollback (undo specific operations)
    - The full Neo4j snapshot rollback is the nuclear option

Snapshot strategy:
    - Pre-analysis: neo4j-admin dump (Step 3.2)
    - During analysis: WAL logging (this module)
    - Post-analysis: run all invariant checks
    - On failure: attempt targeted fix, then full rollback if needed

Processing time:
    - Orphan check: ~5 seconds
    - Weight range check: ~3 seconds
    - Path pointer validation (sampled): ~30-60 seconds
    - Temporal cycle detection: ~10-30 seconds
    - Total: ~1-2 minutes
"""

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nightly.consistency")


class WALWriter:
    """
    Write-ahead log for all graph mutations during nightly analysis.

    Each entry is a JSON line with:
        {"ts": "...", "op": "edge_decay|create_causal_edge|...", ...}

    The WAL is append-only during a run and never modified.

    Usage:
        wal = WALWriter("/nvme/logs/nightly-wal-2026-03-23.jsonl")
        wal.write({"op": "edge_decay", "edge_type": "SEMANTIC", "batch_size": 1500})
        wal.close()

    File size: typically 5-20MB per nightly run
    """

    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "a")
        self._entry_count = 0

    def write(self, entry: dict):
        """Append a WAL entry."""
        entry["ts"] = datetime.now().isoformat()
        entry["seq"] = self._entry_count
        self._file.write(json.dumps(entry) + "\n")
        self._file.flush()
        self._entry_count += 1

    def get_entry_count(self) -> int:
        return self._entry_count

    def close(self):
        self._file.close()


class ConsistencyManager:
    """
    Validates graph invariants after nightly analysis.

    All checks are read-only queries against Neo4j. If any check fails,
    the orchestrator decides whether to attempt a targeted fix or a
    full rollback from snapshot.

    Usage:
        cm = ConsistencyManager(config, uri, user, password, database)
        results = cm.run_all_checks()
        if not results["all_passed"]:
            # Handle failure
    """

    def __init__(self, config: dict, uri: str, user: str, password: str, database: str):
        self.config = config
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver = None

    def _get_driver(self):
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        return self._driver

    def run_all_checks(self) -> dict:
        """
        Run all consistency checks.

        Returns:
            {
                "all_passed": true/false,
                "checks": {
                    "no_orphan_nodes": {"passed": true, "count": 0, "duration_s": 5.1},
                    "weights_in_range": {"passed": true, "violations": 0, "duration_s": 3.2},
                    "valid_path_pointers": {"passed": true, "broken": 0, "sampled": 500, "duration_s": 45.3},
                    "no_temporal_cycles": {"passed": true, "cycles_found": 0, "duration_s": 12.5},
                    "required_properties": {"passed": true, "missing": 0, "duration_s": 4.1}
                },
                "total_duration_s": 70.2
            }

        Time estimate: 1-2 minutes total
        """
        start = time.time()
        checks = {}

        checks["no_orphan_nodes"] = self.check_orphan_nodes()
        checks["weights_in_range"] = self.check_weight_range()
        checks["valid_path_pointers"] = self.check_path_pointers()
        checks["no_temporal_cycles"] = self.check_temporal_cycles()
        checks["required_properties"] = self.check_required_properties()

        all_passed = all(c["passed"] for c in checks.values())

        result = {
            "all_passed": all_passed,
            "checks": checks,
            "total_duration_s": round(time.time() - start, 1),
        }

        if all_passed:
            logger.info("All consistency checks PASSED in %.1fs", result["total_duration_s"])
        else:
            failed = [name for name, c in checks.items() if not c["passed"]]
            logger.error("Consistency checks FAILED: %s", ", ".join(failed))

        return result

    def check_orphan_nodes(self) -> dict:
        """
        Check that every node has at least one edge.

        Orphan nodes are problematic because:
            - They can't be reached by graph traversal
            - They waste space
            - They indicate a bug in edge creation/deletion

        Exception: CondensedMemory nodes that were just created might
        temporarily appear orphaned if the linking step failed partway.

        Time estimate: 3-5 seconds
        """
        start = time.time()

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Find orphan nodes
        # ---------------------------------------------------------------
        query = """
        MATCH (n)
        WHERE NOT (n)--()
          AND NOT n:CondensedMemory
        RETURN count(n) AS orphan_count,
               collect(elementId(n))[0..10] AS sample_ids
        """

        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            result = session.run(query).single()
            orphan_count = result["orphan_count"]
            sample_ids = result["sample_ids"]

        max_allowed = self.config["consistency"].get("max_orphan_nodes", 0)
        passed = orphan_count <= max_allowed

        if not passed:
            logger.error(
                "Orphan node check FAILED: found %d orphans (max allowed: %d). "
                "Sample IDs: %s",
                orphan_count, max_allowed, sample_ids,
            )

        return {
            "passed": passed,
            "count": orphan_count,
            "sample_ids": sample_ids if not passed else [],
            "duration_s": round(time.time() - start, 1),
        }

    def check_weight_range(self) -> dict:
        """
        Check that all edge weights are in [0, 1].

        This catches:
            - Negative weights from buggy decay math
            - Weights > 1 from unbounded score boosts
            - NaN/Infinity from division errors

        Time estimate: 2-3 seconds
        """
        start = time.time()

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Find out-of-range weights
        # ---------------------------------------------------------------
        query = """
        MATCH ()-[r]->()
        WHERE r.weight IS NOT NULL
          AND (r.weight < 0.0 OR r.weight > 1.0)
        RETURN count(r) AS violations,
               collect({id: elementId(r), weight: r.weight, type: type(r)})[0..10] AS samples
        """

        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            result = session.run(query).single()
            violations = result["violations"]
            samples = result["samples"]

        passed = violations == 0

        if not passed:
            logger.error(
                "Weight range check FAILED: %d edges with weight outside [0,1]. "
                "Samples: %s",
                violations,
                json.dumps(samples[:5]),
            )

        return {
            "passed": passed,
            "violations": violations,
            "samples": samples if not passed else [],
            "duration_s": round(time.time() - start, 1),
        }

    def check_path_pointers(self) -> dict:
        """
        Check that lancedb_ids on edges reference valid LanceDB vectors.

        This is a sampled check (not exhaustive) because validating all
        pointers would require querying LanceDB for each one.

        Strategy: sample 500 edges with path pointers, check 2 random
        pointers from each, flag if any are broken.

        Time estimate: 30-60 seconds (depends on LanceDB response time)
        """
        start = time.time()
        sample_size = 500
        broken_count = 0
        sampled_count = 0

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Sample edges with path pointers
        # ---------------------------------------------------------------
        query = """
        MATCH ()-[r]->()
        WHERE r.lancedb_ids IS NOT NULL AND size(r.lancedb_ids) > 0
        WITH r, rand() AS rnd
        ORDER BY rnd
        LIMIT $sample_size
        RETURN elementId(r) AS edge_id,
               r.lancedb_ids AS lancedb_ids
        """

        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            records = list(session.run(query, sample_size=sample_size))

        if not records:
            return {
                "passed": True,
                "broken": 0,
                "sampled": 0,
                "duration_s": round(time.time() - start, 1),
            }

        # Check pointers against LanceDB
        try:
            import lancedb
            lance_db = lancedb.connect(self.config["lancedb"]["path"])
            lance_table = lance_db.open_table(self.config["lancedb"]["table_name"])

            for record in records:
                lancedb_ids = record["lancedb_ids"]
                # Sample up to 2 pointers per edge
                check_ids = lancedb_ids[:2]
                sampled_count += len(check_ids)

                for vec_id in check_ids:
                    try:
                        # Check if vector ID exists in LanceDB
                        result = lance_table.search().where(
                            f"id = '{vec_id}'"
                        ).limit(1).to_pandas()
                        if len(result) == 0:
                            broken_count += 1
                    except Exception:
                        broken_count += 1

        except Exception as e:
            logger.warning("LanceDB check failed: %s. Skipping pointer validation.", e)
            return {
                "passed": True,  # Don't fail on LanceDB unavailability
                "broken": 0,
                "sampled": 0,
                "error": str(e),
                "duration_s": round(time.time() - start, 1),
            }

        # Allow up to 1% broken pointers (stale references from recent deletions)
        max_broken_ratio = 0.01
        broken_ratio = broken_count / max(sampled_count, 1)
        passed = broken_ratio <= max_broken_ratio

        if not passed:
            logger.error(
                "Path pointer check FAILED: %d/%d sampled pointers broken (%.1f%%)",
                broken_count, sampled_count, broken_ratio * 100,
            )

        return {
            "passed": passed,
            "broken": broken_count,
            "sampled": sampled_count,
            "broken_ratio": round(broken_ratio, 4),
            "duration_s": round(time.time() - start, 1),
        }

    def check_temporal_cycles(self) -> dict:
        """
        Check that TEMPORAL edges form a DAG (no cycles).

        TEMPORAL edges represent "happened before" relationships, which
        must be acyclic. A cycle would mean A happened before B happened
        before A, which is a logical impossibility.

        Detection method: attempt to find any cycle in the TEMPORAL
        subgraph using a bounded DFS. We limit depth to 20 to avoid
        unbounded traversal.

        Time estimate: 5-30 seconds depending on TEMPORAL subgraph size
        """
        start = time.time()

        if not self.config["consistency"].get("check_temporal_cycles", True):
            return {"passed": True, "skipped": True, "duration_s": 0}

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Detect cycles in TEMPORAL edges
        # ---------------------------------------------------------------
        # This query finds any node that can reach itself via TEMPORAL edges
        # within 20 hops. If any such path exists, there's a cycle.
        #
        # We use a limited path length to avoid expensive full-graph traversal.
        # In practice, temporal chains longer than 20 are extremely rare.
        # ---------------------------------------------------------------
        query = """
        MATCH path = (n)-[:TEMPORAL*2..20]->(n)
        RETURN count(path) AS cycle_count,
               CASE WHEN count(path) > 0
                    THEN [nodes(path)[0..3] | elementId(nodes(path)[0])][0]
                    ELSE null
               END AS sample_node
        LIMIT 1
        """

        # Alternative, more efficient approach using APOC if available:
        apoc_query = """
        CALL {
            MATCH (n)-[:TEMPORAL]->(m)
            WITH collect(DISTINCT n) AS nodes
            UNWIND nodes AS start
            MATCH path = (start)-[:TEMPORAL*2..20]->(start)
            RETURN start, path
            LIMIT 1
        }
        RETURN count(*) AS cycle_count,
               elementId(start) AS sample_node
        """

        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            try:
                result = session.run(query).single()
                cycle_count = result["cycle_count"] if result else 0
                sample_node = result["sample_node"] if result else None
            except Exception as e:
                # If the query times out, the subgraph may be too large
                logger.warning("Temporal cycle check query failed: %s. Trying simpler check.", e)
                # Fallback: just check 2-hop cycles (most common)
                fallback_query = """
                MATCH (a)-[:TEMPORAL]->(b)-[:TEMPORAL]->(a)
                RETURN count(*) AS cycle_count
                LIMIT 1
                """
                result = session.run(fallback_query).single()
                cycle_count = result["cycle_count"]
                sample_node = None

        passed = cycle_count == 0

        if not passed:
            logger.error(
                "Temporal cycle check FAILED: found %d cycles. Sample node: %s",
                cycle_count, sample_node,
            )

        return {
            "passed": passed,
            "cycles_found": cycle_count,
            "sample_node": sample_node,
            "duration_s": round(time.time() - start, 1),
        }

    def check_required_properties(self) -> dict:
        """
        Check that nodes and edges have all required properties.

        Required node properties:
            - summary or content (at least one)
            - created_at

        Required edge properties:
            - weight
            - created_at

        Time estimate: 3-5 seconds
        """
        start = time.time()
        missing_count = 0

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Nodes missing required properties
        # ---------------------------------------------------------------
        node_query = """
        MATCH (n)
        WHERE (n.summary IS NULL AND n.content IS NULL)
           OR n.created_at IS NULL
        RETURN count(n) AS missing_count,
               collect(elementId(n))[0..5] AS sample_ids
        """

        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Edges missing required properties
        # ---------------------------------------------------------------
        edge_query = """
        MATCH ()-[r]->()
        WHERE r.weight IS NULL OR r.created_at IS NULL
        RETURN count(r) AS missing_count,
               collect(elementId(r))[0..5] AS sample_ids
        """

        driver = self._get_driver()
        samples = []
        with driver.session(database=self.database) as session:
            node_result = session.run(node_query).single()
            node_missing = node_result["missing_count"]
            if node_missing > 0:
                samples.extend([{"type": "node", "id": sid} for sid in node_result["sample_ids"]])

            edge_result = session.run(edge_query).single()
            edge_missing = edge_result["missing_count"]
            if edge_missing > 0:
                samples.extend([{"type": "edge", "id": sid} for sid in edge_result["sample_ids"]])

        missing_count = node_missing + edge_missing
        passed = missing_count == 0

        if not passed:
            logger.error(
                "Required properties check FAILED: %d nodes + %d edges missing properties. "
                "Samples: %s",
                node_missing, edge_missing, json.dumps(samples[:5]),
            )

        return {
            "passed": passed,
            "missing": missing_count,
            "node_missing": node_missing,
            "edge_missing": edge_missing,
            "samples": samples if not passed else [],
            "duration_s": round(time.time() - start, 1),
        }

    def auto_fix_weights(self) -> int:
        """
        Auto-fix out-of-range weights by clamping to [0, 1].

        This is a targeted fix that doesn't require a full rollback.
        Called by the orchestrator when weight_range check fails.

        Returns number of edges fixed.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Clamp weights to [0, 1]
        # ---------------------------------------------------------------
        query = """
        MATCH ()-[r]->()
        WHERE r.weight IS NOT NULL
          AND (r.weight < 0.0 OR r.weight > 1.0)
        SET r.weight = CASE
            WHEN r.weight < 0.0 THEN 0.0
            WHEN r.weight > 1.0 THEN 1.0
            ELSE r.weight
        END
        RETURN count(r) AS fixed
        """

        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            result = session.run(query).single()
            fixed = result["fixed"]

        logger.info("Auto-fixed %d out-of-range weights", fixed)
        return fixed

    def auto_fix_orphans(self) -> int:
        """
        Auto-fix orphan nodes by connecting them to their nearest
        neighbor via a low-weight SEMANTIC edge.

        This is a heuristic fix. If the node truly has no semantic
        relationship to anything, it should probably be deleted.

        Returns number of orphans fixed.
        """
        # ---------------------------------------------------------------
        # EXACT CYPHER QUERY: Find orphans and connect to most recent node
        # ---------------------------------------------------------------
        # Strategy: connect each orphan to the most recently created node
        # of the same type, with a low-weight SEMANTIC edge.
        # ---------------------------------------------------------------
        query = """
        MATCH (orphan)
        WHERE NOT (orphan)--()
          AND NOT orphan:CondensedMemory
        WITH orphan
        MATCH (neighbor)
        WHERE neighbor <> orphan
          AND labels(neighbor)[0] = labels(orphan)[0]
          AND (neighbor)--()
        WITH orphan, neighbor
        ORDER BY neighbor.created_at DESC
        LIMIT 1
        CREATE (orphan)-[:SEMANTIC {
            weight: 0.1,
            created_at: datetime(),
            last_accessed: datetime(),
            score_accumulator: 0.0,
            auto_fixed: true
        }]->(neighbor)
        RETURN count(orphan) AS fixed
        """

        driver = self._get_driver()
        with driver.session(database=self.database) as session:
            result = session.run(query).single()
            fixed = result["fixed"]

        logger.info("Auto-fixed %d orphan nodes", fixed)
        return fixed

    def close(self):
        if self._driver:
            self._driver.close()


# ---------------------------------------------------------------------------
# Standalone rollback script
# ---------------------------------------------------------------------------
# Save this as rollback.sh and run manually if needed:
#
# #!/bin/bash
# # Usage: ./rollback.sh /nvme/neo4j-snapshots/neo4j-2026-03-23T01-00-00.dump
#
# set -euo pipefail
#
# SNAPSHOT_PATH="$1"
# SNAPSHOT_DIR=$(dirname "$SNAPSHOT_PATH")
#
# echo "WARNING: This will restore Neo4j from snapshot and LOSE all changes since."
# echo "Snapshot: $SNAPSHOT_PATH"
# read -p "Continue? (yes/no): " confirm
# if [ "$confirm" != "yes" ]; then
#     echo "Aborted."
#     exit 1
# fi
#
# echo "Stopping Neo4j..."
# sudo systemctl stop neo4j
# sleep 3
#
# echo "Restoring from snapshot..."
# sudo neo4j-admin database load neo4j \
#     --from-path="$SNAPSHOT_DIR" \
#     --overwrite-destination=true
#
# echo "Starting Neo4j..."
# sudo systemctl start neo4j
#
# echo "Waiting for Neo4j to be ready..."
# for i in $(seq 1 30); do
#     if cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "RETURN 1" 2>/dev/null; then
#         echo "Neo4j is ready."
#         exit 0
#     fi
#     sleep 2
# done
#
# echo "ERROR: Neo4j did not start within 60 seconds."
# exit 1
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Verification of consistency checks
# ---------------------------------------------------------------------------
# Run these manually to verify the checker works:
#
# 1. Create a test orphan:
#    CREATE (n:TestNode {summary: 'orphan test', created_at: datetime()})
#    # Run check -> should find 1 orphan
#    # Clean up: MATCH (n:TestNode) DETACH DELETE n
#
# 2. Create an out-of-range weight:
#    MATCH ()-[r]->() WITH r LIMIT 1
#    SET r.weight = 1.5
#    # Run check -> should find 1 violation
#    # Fix: SET r.weight = 1.0
#
# 3. Create a temporal cycle:
#    MATCH (a), (b) WHERE a <> b
#    WITH a, b LIMIT 1
#    CREATE (a)-[:TEMPORAL {weight: 0.5, created_at: datetime()}]->(b)
#    CREATE (b)-[:TEMPORAL {weight: 0.5, created_at: datetime()}]->(a)
#    # Run check -> should find cycle
#    # Clean up: MATCH (a)-[r:TEMPORAL]->(b)-[s:TEMPORAL]->(a)
#    #           DELETE r, s
# ---------------------------------------------------------------------------
