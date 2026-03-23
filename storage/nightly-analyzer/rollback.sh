#!/bin/bash
# =============================================================================
# Step 3.9: Neo4j Rollback Script
# =============================================================================
# Usage:
#   ./rollback.sh /nvme/neo4j-snapshots/neo4j-2026-03-23T01-00-00.dump
#
# This script restores Neo4j from a nightly snapshot.
# WARNING: All changes since the snapshot will be lost.
#
# Prerequisites:
#   - neo4j-admin in PATH
#   - sudo access for systemctl
#   - NEO4J_PASSWORD environment variable set
#
# Expected runtime: 30-60 seconds
#
# Common errors:
#   - "database is running": script stops it, but check if stop failed
#   - "permission denied": run with sudo
#   - "dump file not found": verify the path
#   - "corrupted dump": try an older snapshot from /nvme/neo4j-snapshots/
# =============================================================================

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <snapshot-path>"
    echo ""
    echo "Available snapshots:"
    ls -lt /nvme/neo4j-snapshots/neo4j-*.dump 2>/dev/null || echo "  No snapshots found."
    exit 1
fi

SNAPSHOT_PATH="$1"
SNAPSHOT_DIR=$(dirname "$SNAPSHOT_PATH")
NEO4J_DB="neo4j"

if [ ! -f "$SNAPSHOT_PATH" ] && [ ! -d "$SNAPSHOT_DIR" ]; then
    echo "ERROR: Snapshot not found: $SNAPSHOT_PATH"
    exit 1
fi

echo "========================================"
echo "Neo4j Rollback"
echo "========================================"
echo "Snapshot: $SNAPSHOT_PATH"
echo "Database: $NEO4J_DB"
echo ""
echo "WARNING: This will DESTROY all changes made since this snapshot."
echo ""
read -p "Type 'yes' to continue: " confirm
if [ "$confirm" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "[1/4] Stopping Neo4j..."
sudo systemctl stop neo4j || true
sleep 3

# Verify it's stopped
if systemctl is-active --quiet neo4j; then
    echo "ERROR: Neo4j did not stop. Force killing..."
    sudo systemctl kill neo4j
    sleep 3
fi
echo "  Neo4j stopped."

echo ""
echo "[2/4] Restoring from snapshot..."
sudo neo4j-admin database load "$NEO4J_DB" \
    --from-path="$SNAPSHOT_DIR" \
    --overwrite-destination=true

if [ $? -ne 0 ]; then
    echo "ERROR: Restore failed. Attempting with legacy command..."
    sudo neo4j-admin load \
        --database="$NEO4J_DB" \
        --from="$SNAPSHOT_PATH" \
        --force
fi
echo "  Restore complete."

echo ""
echo "[3/4] Starting Neo4j..."
sudo systemctl start neo4j

echo ""
echo "[4/4] Waiting for Neo4j to be ready..."
for i in $(seq 1 30); do
    if cypher-shell -u neo4j -p "${NEO4J_PASSWORD:-password}" "RETURN 1" 2>/dev/null; then
        echo ""
        echo "========================================"
        echo "Rollback COMPLETE. Neo4j is ready."
        echo "========================================"
        echo ""
        echo "Verify with:"
        echo "  cypher-shell -u neo4j -p \$NEO4J_PASSWORD 'MATCH (n) RETURN count(n)'"
        exit 0
    fi
    printf "."
    sleep 2
done

echo ""
echo "ERROR: Neo4j did not start within 60 seconds."
echo "Check: journalctl -u neo4j -n 50"
exit 1
