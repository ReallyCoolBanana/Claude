# Phase 3: Nightly Analyzer — Complete Implementation Guide

**Target hardware:** NVIDIA DGX Spark (128GB unified, 4TB NVMe, Blackwell GB10)
**Time estimate:** Weeks 7-9
**Prerequisites:** Phase 1 (Neo4j + LanceDB) and Phase 2 (MCP + scoring) complete

---

## Architecture Overview

```
                    systemd timer (1 AM daily)
                            |
                    nightly_orchestrator.py
                            |
                   +--------+--------+
                   |                 |
              model_manager.py   consistency_manager.py
              (load/unload 30B)  (WAL + invariant checks)
                   |
        +----------+----------+----------+----------+----------+
        |          |          |          |          |          |
   edge_decay  llm_edge  causal    path_opt  condensation  emergence
   (no LLM)   analysis  inference  (no LLM)  (LLM)        (LLM)
```

**Nightly window:** 1:00 AM - 9:00 AM (8 hours)
**Always-resident models:** Nemotron Nano 4B (~3GB) + embedding model (~1.5GB)
**Nightly model:** Nemotron-3-Nano-30B-A3B (~20GB), loaded only during the window

---

## Step 3.1: Nightly Model Setup

### 3.1.1 Download the Model

```bash
# Install huggingface-cli if not already installed
pip install huggingface-hub

# Download the model to NVMe for fast loading
# Expected download: ~18GB, ~10-30 minutes depending on connection
huggingface-cli download nvidia/Nemotron-3-Nano-30B-A3B-Instruct-NVFP4 \
    --local-dir /nvme/models/nemotron-30b-a3b-nvfp4 \
    --local-dir-use-symlinks False

# Verify download
du -sh /nvme/models/nemotron-30b-a3b-nvfp4/
# Expected: ~18GB

ls /nvme/models/nemotron-30b-a3b-nvfp4/
# Expected files: config.json, tokenizer.json, tokenizer_config.json,
#                 model*.safetensors, special_tokens_map.json, etc.
```

**Common errors:**
- `huggingface-cli: command not found` -- install with `pip install huggingface-hub`
- `403 Forbidden` -- model may require accepting license terms on HuggingFace website
- `No space left on device` -- verify 4TB NVMe has sufficient free space

### 3.1.2 Install vLLM

```bash
# vLLM is the recommended serving framework for NVIDIA GPUs
pip install vllm>=0.6.0

# Verify installation
python -c "import vllm; print(vllm.__version__)"
# Expected: 0.6.x or higher

# Verify CUDA availability
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# Expected: True NVIDIA GB10
```

### 3.1.3 Model Manager Implementation

The model manager is at `src/model_manager.py`. Key features:
- Pre-load GPU memory and temperature checks
- vLLM subprocess management with process group isolation
- Health check polling with configurable timeout
- Warmup inference to prime the model
- Clean shutdown with SIGTERM -> SIGKILL escalation
- Thermal monitoring with configurable thresholds

**Test the model manager:**

```bash
cd /home/user/Claude/storage/nightly-analyzer

# Check GPU status (no model load)
python src/model_manager.py check
# Expected output:
#   GPU Status:
#     Temperature: 42.0 C
#     Utilization: 5%
#     Memory: 4.5 / 128.0 GB
#     Memory free: 123.5 GB

# Load model, run benchmark, then unload
python src/model_manager.py load
# Expected output:
#   Model loaded: {"load_time_s": 42.3, "model": "nemotron-30b-a3b-nvfp4", "status": "ready", "gpu_mem_used_gb": 24.1}
#   Benchmark: {"short_32tok_s": 1.2, "medium_256tok_s": 4.1, "long_512tok_s": 7.8, "tokens_per_second": 68.5, ...}
```

**Benchmark expectations on DGX Spark:**

| Metric | Expected | Acceptable Range |
|--------|----------|-----------------|
| Load time from NVMe | 30-45s | < 120s |
| GPU memory usage | ~20GB | 18-22GB |
| Inference speed (batch=1) | ~70 tok/s | 50-90 tok/s |
| GPU temperature during inference | 60-75 C | < 82 C |

---

## Step 3.2: Nightly Orchestrator

### 3.2.1 systemd Timer and Service

Install the unit files:

```bash
# Copy unit files
sudo cp systemd/nightly-analyzer.timer /etc/systemd/system/
sudo cp systemd/nightly-analyzer.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable the timer (starts automatically on boot)
sudo systemctl enable nightly-analyzer.timer

# Start the timer
sudo systemctl start nightly-analyzer.timer

# Verify timer is active
systemctl list-timers | grep nightly
# Expected output:
#   NEXT                         LEFT     LAST  PASSED  UNIT                    ACTIVATES
#   Tue 2026-03-24 01:00:00 UTC  23h left  -     -      nightly-analyzer.timer  nightly-analyzer.service
```

**Important:** Edit the service file to set the correct `NEO4J_PASSWORD`:
```bash
sudo systemctl edit nightly-analyzer.service
# Add:
#   [Service]
#   Environment=NEO4J_PASSWORD=your_actual_password
```

### 3.2.2 Orchestrator Implementation

The orchestrator is at `src/nightly_orchestrator.py`. Key features:
- Time-boxed task execution with soft and hard deadlines
- Graceful SIGTERM handling for clean systemd shutdown
- Neo4j snapshot before mutations (for rollback)
- Structured JSONL logging + JSON summary report
- Selective task execution via `--tasks` flag
- Dry-run mode for safe testing

**Manual test run:**

```bash
# Dry run -- no mutations, no model load
python src/nightly_orchestrator.py --dry-run --skip-model --skip-snapshot

# Run only edge decay (no LLM needed)
python src/nightly_orchestrator.py --tasks edge_decay --skip-model

# Full run (all tasks)
python src/nightly_orchestrator.py
```

**View logs:**

```bash
# Structured log
tail -f /nvme/logs/nightly-analyzer.jsonl | python -m json.tool

# systemd journal
journalctl -u nightly-analyzer.service -f

# Summary report
cat /nvme/logs/nightly-report-2026-03-23.json | python -m json.tool
```

### 3.2.3 Rollback Script

The rollback script is at `rollback.sh`:

```bash
# List available snapshots
ls -lt /nvme/neo4j-snapshots/

# Rollback to a specific snapshot
./rollback.sh /nvme/neo4j-snapshots/neo4j-2026-03-23T01-00-00.dump
# Follow the interactive prompts
```

---

## Step 3.3: Edge Weight Decay

**Implementation:** `src/edge_decay.py`
**Duration:** 15-30 minutes
**LLM required:** No

### Algorithm

Exponential decay applied per edge type:

```
new_weight = old_weight * decay_factor ^ days_since_last_access
```

| Edge Type | Decay Factor | Half-life | Rationale |
|-----------|-------------|-----------|-----------|
| STRUCTURAL | 0.995 | 138 days | Part-of relationships are durable |
| SEMANTIC | 0.990 | 69 days | Conceptual links evolve moderately |
| CAUSAL | 0.992 | 86 days | Cause-effect is relatively stable |
| TEMPORAL | 0.980 | 34 days | Event ordering loses relevance fastest |

### Score Boost

Edges accessed via the `/score` endpoint accumulate a boost counter.
During decay, this boost counteracts decay:

```
final_weight = min(1.0, decayed_weight + score_accumulator * 0.1)
```

### Verification

After decay, run this Cypher query to check the weight distribution:

```cypher
MATCH ()-[r]->()
WHERE r.weight IS NOT NULL
RETURN type(r) AS edge_type,
       count(r) AS count,
       round(avg(r.weight), 3) AS avg_weight,
       round(min(r.weight), 3) AS min_weight,
       round(max(r.weight), 3) AS max_weight,
       sum(CASE WHEN r.prune_candidate = true THEN 1 ELSE 0 END) AS prune_candidates
ORDER BY edge_type
```

**Expected output (example):**

```
edge_type    | count | avg_weight | min_weight | max_weight | prune_candidates
STRUCTURAL   | 12000 | 0.82       | 0.03       | 1.0        | 15
SEMANTIC     | 15000 | 0.71       | 0.02       | 1.0        | 45
CAUSAL       |  8000 | 0.75       | 0.04       | 1.0        | 30
TEMPORAL     | 10000 | 0.58       | 0.01       | 1.0        | 140
```

---

## Step 3.4: LLM-Driven Edge Weight Analysis

**Implementation:** `src/llm_edge_analysis.py`
**Duration:** 60-90 minutes
**LLM required:** Yes (30B model)

### Processing Pipeline

1. **Select candidates** (~2000 per night): prioritize never-analyzed, ambiguous-weight, and stale-analysis edges
2. **Build prompts**: one template per edge type, including both endpoint summaries
3. **Call LLM**: via vLLM OpenAI-compatible API at `http://localhost:8001/v1/completions`
4. **Parse response**: extract SCORE and REASONING with retry logic
5. **Update Neo4j**: set new weight, record analysis timestamp

### Prompt Templates

Four templates defined in the source (one per edge type). Each includes:
- Both node summaries (truncated to 500 chars)
- Node types
- Current weight
- Days since last access
- Scoring rubric (what 1.0, 0.7, 0.4, 0.1, 0.0 mean)
- Exact response format required

### Response Parsing

The parser handles:
- Standard format: `SCORE: 0.75`
- Percentage format: `SCORE: 75` (divided by 100)
- Out-of-range values (clamped to [0, 1])
- Missing REASONING (defaults to "No reasoning provided")
- Extra preamble text before SCORE line
- Up to 3 retries with explicit formatting instruction appended

---

## Step 3.5: Causal Inference Pipeline

**Implementation:** `src/causal_inference.py`
**Duration:** 45-75 minutes
**LLM required:** Yes (30B model)

### Discovery Phase (60% of time)

Finds node pairs that should have CAUSAL edges:

**Strategy 1: Temporal neighbors without causal links**
```cypher
MATCH (a)-[:TEMPORAL]->(b)
WHERE NOT (a)-[:CAUSAL]->(b)
  AND NOT (b)-[:CAUSAL]->(a)
```

**Strategy 2: Semantic bridge pairs**
```cypher
MATCH (a)-[:SEMANTIC]->(x)<-[:SEMANTIC]-(b)
WHERE a <> b AND a.timestamp < b.timestamp
WITH a, b, count(DISTINCT x) AS shared
WHERE shared >= 2
```

### Validation Phase (40% of time)

Re-evaluates existing CAUSAL edges:
- Gathers new evidence (memories connected to cause/effect in last 30 days)
- Asks LLM if causation still holds
- Strengthens, weakens, or invalidates edges

### Key Metrics

| Metric | Target per night |
|--------|-----------------|
| Candidates found | ~500 |
| New causal edges created | ~150-200 |
| Existing edges validated | ~300 |
| Edges strengthened | ~210 |
| Edges removed | ~15 |

---

## Step 3.6: Path Pointer Optimization

**Implementation:** `src/path_optimization.py`
**Duration:** 30-45 minutes
**LLM required:** No (uses embedding model, always resident)

### Algorithm

For each high-traffic edge:
1. Get embedding vectors for both endpoint nodes
2. Query LanceDB for 20 nearest vectors to each endpoint
3. Score candidates by combined proximity: `score = 1/(1+dist_A) * 1/(1+dist_B)`
4. Store top 10 vector IDs as `lancedb_ids` on the edge

This enables the two-phase retrieval pipeline to skip a full vector search for known graph paths.

### Verification

```cypher
MATCH ()-[r]->()
WHERE r.lancedb_ids IS NOT NULL AND size(r.lancedb_ids) > 0
RETURN type(r) AS edge_type,
       count(r) AS edges_with_pointers,
       avg(size(r.lancedb_ids)) AS avg_pointers
ORDER BY edge_type
```

---

## Step 3.7: Condensation Pipeline

**Implementation:** `src/condensation.py`
**Duration:** 45-75 minutes
**LLM required:** Yes (30B model)

### Graph-Based Clustering

Uses **Jaccard similarity on neighborhood signatures** (not vector similarity):

```
signature(node) = set of (neighbor_label, edge_type, direction) tuples
similarity(A, B) = |sig(A) ∩ sig(B)| / |sig(A) ∪ sig(B)|
```

Threshold: 0.75 (configurable). Cluster sizes: 3-15 nodes.

### Condensation Process

1. Compute neighborhood signatures for all uncondensed nodes
2. Greedy agglomerative clustering
3. For each cluster, LLM generates a condensed summary
4. Create `FactMemory:CondensedMemory` node with the summary
5. Link originals to condensed node via `STRUCTURAL` edges
6. Mark originals with `condensed_into` pointer

Originals are NOT deleted. They remain for potential cold-tier migration.

### Verification

```cypher
MATCH (c:CondensedMemory)
RETURN count(c) AS condensed_count,
       avg(c.source_count) AS avg_sources,
       avg(c.condensation_confidence) AS avg_confidence
```

---

## Step 3.8: Emergence Detection

**Implementation:** `src/emergence_detection.py`
**Duration:** 35-50 minutes
**LLM required:** Yes (30B model)

### Convergence Point Detection

A convergence point is a node where 3+ independent paths arrive from different origin clusters:

```cypher
MATCH (target)<-[r]-(source)
WHERE r.weight > 0.1
WITH target, collect(DISTINCT source) AS sources, count(DISTINCT source) AS in_degree
WHERE in_degree >= 3
```

Independence check: source paths don't share nodes within 3 hops.

### Emergence Categories

| Category | Description |
|----------|-------------|
| PATTERN | Recurring theme across unrelated memories |
| CONTRADICTION | Conflicting information revealed |
| SYNTHESIS | Novel combination of separate concepts |
| PREDICTION | Forward-looking inference from converging evidence |
| META_LEARNING | Insight about the learning process itself |

### Output

Creates `MetaMemory` nodes linked to source memories via `CAUSAL` edges with subtype `emerged_from`.

---

## Step 3.9: Consistency Manager

**Implementation:** `src/consistency_manager.py`
**Duration:** 1-2 minutes (post-analysis)

### Invariant Checks

| Check | Description | Auto-fixable? |
|-------|-------------|---------------|
| Orphan nodes | Every node must have ≥1 edge | Yes (connect to nearest) |
| Weight range | All weights in [0, 1] | Yes (clamp) |
| Path pointers | lancedb_ids reference valid vectors | No (requires investigation) |
| Temporal cycles | TEMPORAL edges must be acyclic | No (requires investigation) |
| Required properties | Nodes need summary/content + created_at | No |

### WAL (Write-Ahead Log)

Every mutation is logged to `/nvme/logs/nightly-wal-{date}.jsonl`:

```json
{"ts": "2026-03-23T01:15:00", "seq": 0, "op": "edge_decay", "edge_type": "STRUCTURAL", "batch_size": 1500}
{"ts": "2026-03-23T01:15:01", "seq": 1, "op": "score_boost", "count": 1500, "boost_multiplier": 0.1}
```

### Recovery Hierarchy

1. **Auto-fix**: weight clamping, orphan connection (fastest, safest)
2. **WAL replay**: undo specific operations (targeted, medium effort)
3. **Full rollback**: restore Neo4j snapshot (nuclear option, loses all nightly progress)

---

## Installation Summary

```bash
# 1. Install Python dependencies
pip install vllm pyyaml neo4j lancedb numpy pandas requests

# 2. Download the nightly model
huggingface-cli download nvidia/Nemotron-3-Nano-30B-A3B-Instruct-NVFP4 \
    --local-dir /nvme/models/nemotron-30b-a3b-nvfp4 \
    --local-dir-use-symlinks False

# 3. Create log directories
mkdir -p /nvme/logs /nvme/neo4j-snapshots

# 4. Set environment variables
export NEO4J_PASSWORD=your_password

# 5. Run parser tests (no GPU needed)
cd /home/user/Claude/storage/nightly-analyzer
python -m pytest tests/test_parsers.py -v

# 6. Test GPU status check
python src/model_manager.py check

# 7. Test model loading (requires GPU)
python src/model_manager.py load

# 8. Test dry run (requires Neo4j)
python src/nightly_orchestrator.py --dry-run --skip-model --skip-snapshot

# 9. Test edge decay only (requires Neo4j, no GPU)
python src/nightly_orchestrator.py --tasks edge_decay --skip-model --skip-snapshot

# 10. Full test run
python src/nightly_orchestrator.py

# 11. Install systemd timer
sudo cp systemd/nightly-analyzer.timer /etc/systemd/system/
sudo cp systemd/nightly-analyzer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nightly-analyzer.timer
```

---

## File Inventory

```
nightly-analyzer/
├── config/
│   └── nightly_config.yaml          # All configuration in one place
├── src/
│   ├── __init__.py
│   ├── model_manager.py             # Step 3.1: Model load/unload/thermal
│   ├── nightly_orchestrator.py      # Step 3.2: Main orchestrator
│   ├── edge_decay.py                # Step 3.3: Mechanical edge decay
│   ├── llm_edge_analysis.py         # Step 3.4: LLM edge weight analysis
│   ├── causal_inference.py          # Step 3.5: Causal discovery/validation
│   ├── path_optimization.py         # Step 3.6: Path pointer optimization
│   ├── condensation.py              # Step 3.7: Memory condensation
│   ├── emergence_detection.py       # Step 3.8: Emergent pattern detection
│   └── consistency_manager.py       # Step 3.9: WAL + invariant checks
├── systemd/
│   ├── nightly-analyzer.timer       # Step 3.2: systemd timer
│   └── nightly-analyzer.service     # Step 3.2: systemd service
├── tests/
│   └── test_parsers.py              # Unit tests for LLM response parsers
├── rollback.sh                      # Step 3.9: Neo4j rollback script
├── phase3_guide.md                  # This document
└── README.md
```

---

## Troubleshooting

### Model won't load
- Check `nvidia-smi` for other processes using GPU memory
- Verify model files: `ls /nvme/models/nemotron-30b-a3b-nvfp4/`
- Check vLLM log: `cat /nvme/logs/vllm-nightly.log`
- Try reducing `gpu_memory_utilization` in config (0.65 -> 0.55)

### Neo4j snapshot fails
- Ensure `neo4j-admin` is in PATH
- Check disk space: `df -h /nvme`
- Try manual dump: `neo4j-admin database dump neo4j --to-path=/tmp/test/`

### LLM responses are unparseable
- Check `temperature` setting (lower = more deterministic)
- Increase `max_retries` in config
- Check parser tests: `python -m pytest tests/test_parsers.py -v`
- Review raw responses in structured log

### Thermal throttling
- Check `max_gpu_temp_c` threshold (default: 82 C)
- Ensure DGX Spark cooling is adequate
- Reduce batch sizes to lower sustained GPU load
- Check `nvidia-smi dmon -s p` for power/thermal monitoring

### Consistency check failures
- Weight range: auto-fixed by `ConsistencyManager.auto_fix_weights()`
- Orphans: auto-fixed by `ConsistencyManager.auto_fix_orphans()`
- Temporal cycles: manual investigation needed; check WAL for recent TEMPORAL edge creation
- Path pointers: may need LanceDB reindex; non-critical if < 1% broken
