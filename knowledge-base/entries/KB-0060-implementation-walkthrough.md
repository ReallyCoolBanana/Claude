---
id: KB-0060
date: 2026-03-23
team: solo-research
role: architect
category: methodology
tags: [nemo-maxxing, implementation-guide, walkthrough, dgx-spark, neo4j, lancedb, mcp, nemotron]
status: validated
confidence: high
builds_on: [KB-0055, KB-0056, KB-0057, KB-0058]
---
# Nemo-Maxxing Memory Architecture v3: Complete Human Implementation Walkthrough

## What This Is

A step-by-step guide for a human developer to build the entire Nemo-Maxxing Memory Architecture v3 on a single NVIDIA DGX Spark (128GB unified memory, 4TB NVMe, Blackwell GB10 GPU). Every step includes exact shell commands, full configuration files, complete Python code, verification commands, and expected outputs.

**Total estimated time:** 16 weeks (4 months) at ~20 hours/week
**Difficulty:** Advanced — requires familiarity with Linux, Python, graph databases, vector search, and LLM serving

## Target Hardware

| Spec | Value |
|------|-------|
| Device | NVIDIA DGX Spark |
| Memory | 128 GB LPDDR5x unified (CPU+GPU shared) |
| Storage | 4 TB NVMe PCIe Gen5 (12.1 GiB/s seq read) |
| GPU | Blackwell GB10, 48 SMs, 6144 CUDA cores |
| Bandwidth | 273 GB/s shared |
| OS | Ubuntu 22.04+ (pre-installed) |

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      MCP API Layer (FastMCP)                     │
│   /retrieve  /ingest  /score  /query_meta  /status              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Controller   │  │  Embedding   │  │     Reranker         │  │
│  │  Nano 4B      │  │  embed-1b-v2 │  │  rerank-1b-v2        │  │
│  │  ~2.5GB       │  │  ~1GB        │  │  ~2GB                │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                  │                      │              │
│  ┌──────▼──────────────────▼──────────────────────▼───────────┐ │
│  │              Two-Phase Retrieval Pipeline                   │ │
│  │  Phase 1: Neo4j graph traversal (1-20ms)                   │ │
│  │  Phase 2: LanceDB vector fetch (3-25ms warm)               │ │
│  │  Rerank: RRF + cross-encoder (5-15ms)                      │ │
│  └──────┬─────────────────────────────────┬───────────────────┘ │
│         │                                  │                     │
│  ┌──────▼───────────┐           ┌─────────▼──────────────────┐ │
│  │  Neo4j Graph      │           │  LanceDB on NVMe           │ │
│  │  20-28GB in RAM   │           │  Disk-first, mmap          │ │
│  │  5M nodes/20M rels│           │  4TB capacity               │ │
│  │  4 typed edges    │           │  cuVS via NeMo Retriever   │ │
│  └──────────────────┘           └────────────────────────────┘ │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Nightly Analyzer (1 AM - 9 AM)                          │   │
│  │  30B-A3B model (~20GB) for: edge decay, causal inference,│   │
│  │  condensation, emergence detection, LoRA training         │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## Phase Index

The walkthrough is split into 5 phases. Each phase has its own detailed guide document. Follow them in order.

### Phase 1: Foundation (Weeks 1-3)
**File:** `knowledge-base/entries/KB-0059-phase1-foundation.md`
**What you build:**
- DGX Spark hardware verification and thermal monitoring daemon
- Neo4j Community Edition with optimized memory settings (6-8GB heap, 14GB page cache)
- LanceDB on NVMe with mmap optimizations and huge pages
- Controller model: Nemotron Nano 4B Q4_K_M via llama.cpp (~2.5GB)
- Embedding model: llama-nemotron-embed-1b-v2 via llama.cpp (~1GB)
- Basic MCP server with /ingest, /retrieve, /status endpoints

**Key deliverables:**
- [ ] `nvidia-smi` shows GB10 GPU, 128GB unified memory
- [ ] Neo4j responds to Bolt queries on localhost:7687
- [ ] LanceDB benchmark: <5ms random vector query on NVMe
- [ ] Controller model responding on port 8081
- [ ] Embedding model responding on port 8082
- [ ] MCP server can ingest and retrieve test memories

**Estimated time:** 40-60 hours

---

### Phase 2: Graph + Vector Integration (Weeks 4-6)
**Guide:** Inline below (from research agent output)
**What you build:**
- Neo4j schema: 5 memory types as labeled nodes, 4 typed edge relationships
- Path pointer storage: LanceDB vector IDs stored on Neo4j edge properties
- Two-phase retrieval pipeline: graph traversal → vector fetch → rerank
- Reranker (llama-nemotron-rerank-1b-v2) with Reciprocal Rank Fusion
- /score MCP endpoint with SQLite scoring accumulator
- End-to-end integration test

**Key deliverables:**
- [ ] 6 Neo4j constraints + 9 indexes created
- [ ] 100+ seed memories with typed relationships
- [ ] Two-phase pipeline: Phase 1 ≤20ms, Phase 2 ≤25ms average
- [ ] RRF combining 5 signals (structural, semantic, temporal, causal, entity)
- [ ] Cross-encoder reranker scoring in 5-15ms
- [ ] Scoring accumulator with weighted aggregation and outlier detection
- [ ] E2E integration test passing all 7 steps

**Key files to create:**
```
schema/
  neo4j_schema.cypher
  seed_data.py
memory/
  graph/path_pointers.py
  vector/lancedb_client.py
  controller/intent_classifier.py
  controller/embedding_client.py
  retrieval/two_phase_pipeline.py
  retrieval/reranker.py
  scoring/accumulator.py
tests/
  test_path_pointers.py
  benchmark_retrieval.py
  test_reranker.py
  test_scoring.py
  integration_e2e.py
```

**Estimated time:** 50-70 hours

---

### Phase 3: Nightly Analyzer (Weeks 7-9)
**File:** `storage/nightly-analyzer/phase3_guide.md`
**Implementation:** `storage/nightly-analyzer/src/`
**What you build:**
- Nightly model management: load/unload Nemotron 30B-A3B on schedule
- Orchestrator with systemd timer (1 AM daily, 8-hour window)
- Edge weight decay: exponential decay per edge type, no LLM required
- LLM-driven edge weight analysis: 30B evaluates ~2000 edges/night
- Causal inference: find temporal correlations, generate/validate causal hypotheses
- Path pointer optimization: re-compute based on access patterns
- Condensation: cluster similar memories, generate summaries
- Emergence detection: find convergence patterns, create MetaMemory nodes
- Consistency manager: WAL, snapshots, invariant checking, rollback

**Key deliverables:**
- [ ] 30B model loads in <90s from NVMe, uses ~20GB
- [ ] Orchestrator completes 7 tasks within 8-hour window
- [ ] Edge decay processes all edges in <30 minutes
- [ ] ~5000+ analysis units processed per night
- [ ] Consistency checks pass after every run
- [ ] Rollback tested and working

**Implementation files (already created):**
```
storage/nightly-analyzer/
  config/nightly_config.yaml
  src/
    nightly_orchestrator.py
    model_manager.py
    edge_decay.py
    llm_edge_analysis.py
    causal_inference.py
    path_optimization.py
    condensation.py
    emergence_detection.py
    consistency_manager.py
  systemd/
    nightly-analyzer.service
    nightly-analyzer.timer
  rollback.sh
  tests/test_parsers.py
```

**Estimated time:** 50-70 hours

---

### Phase 4: LoRA Training + Cross-Model Consensus (Weeks 10-12)
**What you build:**
- Unsloth QLoRA pipeline for Nemotron Nano 4B controller fine-tuning
- Training data generator from scoring accumulator (Alpaca-style JSON Lines)
- Cross-model score aggregation with Bayesian weighting and outlier detection
- Nightly LoRA training integrated into orchestrator (6:00-7:30 AM slot)
- A/B testing framework: LoRA-adapted vs base controller
- Automatic adapter promotion/rollback based on statistical significance

**Key deliverables:**
- [ ] Unsloth QLoRA runs on DGX Spark (~8-12GB during training)
- [ ] Training data pipeline produces valid Alpaca-format examples
- [ ] Model accuracy tracking per external model (weighted scoring)
- [ ] Nightly LoRA training completes in <90 minutes
- [ ] A/B test framework routes queries and tracks metrics
- [ ] Auto-promotion after p < 0.05 with Mann-Whitney U test

**Estimated time:** 50-70 hours

---

### Phase 5: Optimization + Hardening (Weeks 13-16)
**What you build:**
- Memgraph benchmark: install, migrate from Neo4j, compare latency (8-25x faster potential)
- NeMo Retriever deployment: cuVS-accelerated search via Docker NIMs
- Dynamic Neo4j page cache: systemd timers for day/night transitions + cache warming
- Thermal monitoring daemon: zones (GREEN/YELLOW/RED), burst scheduling (45min on/15min cool)
- Advanced causal inference: Granger causality, counterfactual reasoning
- Path pointer optimization: access pattern prediction, LRU cache (615x speedup)
- Production monitoring: Prometheus + Grafana + custom Nemo metrics exporter
- Backup & disaster recovery: daily automated backups, tested restore procedure
- Security hardening: API key auth, rate limiting, input validation, log sanitization

**Key deliverables:**
- [ ] Memgraph vs Neo4j benchmark report with decision framework
- [ ] NeMo Retriever NIMs healthy on ports 9080/9081
- [ ] Thermal daemon running with zone-based workload management
- [ ] Grafana dashboard with 15+ panels covering all components
- [ ] Daily backup timer active, restore procedure tested
- [ ] API keys generated, Neo4j localhost-only, permissions locked down

**Estimated time:** 60-80 hours

---

## Quick Start Checklist

Before you begin, ensure you have:

1. **DGX Spark powered on** with Ubuntu 22.04+
2. **Network access** for package downloads (apt, pip, Docker Hub, HuggingFace)
3. **Root/sudo access** on the DGX Spark
4. **HuggingFace account** with accepted model licenses for Nemotron models
5. **~50GB free disk** for model downloads before NVMe setup

## Dependency Summary

```bash
# System packages
sudo apt update && sudo apt install -y \
  python3-pip python3-venv git curl wget \
  openjdk-17-jre-headless \
  smartmontools \
  build-essential cmake

# Python packages (install in a venv)
python3 -m venv /opt/nemo/venv
source /opt/nemo/venv/bin/activate
pip install \
  neo4j lancedb pyarrow \
  fastmcp httpx requests \
  torch transformers accelerate \
  unsloth scipy \
  prometheus_client

# Neo4j Community Edition 5.x
# (see Phase 1 for full apt install instructions)

# llama.cpp (build from source with CUDA)
# (see Phase 1 for cmake build instructions)

# Docker (for Phase 5: NeMo Retriever, Memgraph, monitoring)
# (see Phase 5 for installation)
```

## Memory Budget Reference

### Daytime (9 AM - 1 AM)
| Component | RAM (GB) |
|-----------|----------|
| OS + services | 5-8 |
| Neo4j (heap + page cache) | 20-24 |
| Controller (Nano 4B Q4_K_M) | 2-3 |
| Embedding (embed-1b-v2) | 1-2 |
| Reranker (rerank-1b-v2) | 1-2 |
| MCP server + scoring | 0.5-1 |
| **Free for OS page cache (LanceDB warm tier)** | **88-98** |

### Nighttime (1 AM - 9 AM)
| Component | RAM (GB) |
|-----------|----------|
| OS + services | 5-8 |
| Neo4j (heap + page cache) | 18-24 |
| Controller (stays loaded) | 2-3 |
| Nemotron 30B-A3B analyzer | ~20 |
| **Free headroom** | **73-83** |

## Critical Warnings

1. **The 120B model needs 77-87GB, not 64-72GB.** Use the 30B-A3B as default nightly analyzer. Only load 120B (GGUF Q4_K_M ~64GB) for specific causal inference tasks.

2. **The embedding model "llama-embed-nemotron-reasoning-3b" doesn't exist publicly.** Use `llama-nemotron-embed-1b-v2` (compact, 1B params) or `omni-embed-nemotron-3b` (multimodal, 3B params).

3. **There is no direct LanceDB + cuVS integration.** You must deploy through NeMo Retriever (Phase 5) for GPU-accelerated search.

4. **DGX Spark has thermal throttling issues.** Set up the thermal monitoring daemon in Phase 1 BEFORE running sustained workloads. Use external cooling if possible.

5. **The reranker cannot natively fuse 5 signals.** Start with Reciprocal Rank Fusion (Phase 2), graduate to custom fine-tuning later.

## Related Knowledge Base Entries

| Entry | Topic |
|-------|-------|
| [KB-0055](KB-0055.md) | Neo4j graph memory deep dive |
| [KB-0056](KB-0056.md) | LanceDB + cuVS + Nemotron vector search |
| [KB-0057](KB-0057.md) | DGX Spark hardware feasibility |
| [KB-0058](KB-0058.md) | Master architecture analysis & implementation plan |
| KB-0059 | Phase 1 Foundation detailed guide |
| KB-0060 | This document (walkthrough index) |
