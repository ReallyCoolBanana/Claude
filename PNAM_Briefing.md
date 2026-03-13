# PNAM (Pointer Network Agent Model) — Complete Briefing

**Date**: 2026-03-13
**Target Hardware**: RTX 4070 Super (12GB VRAM, 7168 CUDA cores, 504 GB/s bandwidth)
**Source**: KB-0055 through KB-0064

---

## What We're Building

A custom multi-task AI model called **PNAM** that lives on your GPU and manages/maintains a massive knowledge graph (the "pointer network"). It's not a chatbot — it's a specialized graph intelligence engine that handles navigation, link discovery, classification, and search across the network.

Three versions are designed, each building on the last:

| Version | Scope | Key Feature |
|---------|-------|-------------|
| **PNAM v1** (KB-0056) | 310 nodes, 4,809 edges | Core architecture proof |
| **PNAM v2** (KB-0057) | 37K → 1M edges | Mini-batch scaling, 256-dim projection |
| **PNAM v3** (KB-0059) | Hierarchical two-layer network | Subnets, exponential depth penalty, HeteroSAGE |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────┐
│              PNAM (~150M parameters)                   │
├──────────────────────────────────────────────────────┤
│                                                        │
│  ┌──────────────────┐   ┌────────────────────────┐   │
│  │   Text Encoder    │   │   Graph Encoder          │   │
│  │ nomic-embed-text  │   │   4-layer HeteroSAGE     │   │
│  │ → 256-dim project │   │   w/ NeighborLoader      │   │
│  │   ~140M (frozen)  │   │   ~12M (trainable)       │   │
│  └────────┬─────────┘   └───────────┬────────────┘   │
│           │   256-dim                │   256-dim       │
│           └──────────┬───────────────┘                 │
│                      │                                  │
│              ┌───────▼────────┐                         │
│              │  Gated Fusion   │                         │
│              │  w/ residuals   │                         │
│              │    ~6M params   │                         │
│              └───────┬────────┘                         │
│                      │  256-dim                         │
│    ┌─────────────────┼─────────────────┐                │
│    │                 │                 │                │
│    ▼                 ▼                 ▼                │
│ ┌────────┐   ┌────────────┐   ┌────────────┐          │
│ │ Head 1  │   │   Head 2   │   │   Head 3   │          │
│ │ Link    │   │   Path     │   │   Node     │          │
│ │ Predict │   │  Finder    │   │  Classify  │          │
│ │  ~3M    │   │   ~6M      │   │   ~3M      │          │
│ └────────┘   └────────────┘   └────────────┘          │
│                                                        │
│  Trainable: ~30M  |  Total w/ frozen: ~150M            │
│  Training VRAM: ~6 GB  |  Inference VRAM: ~2.85 GB     │
└──────────────────────────────────────────────────────┘
```

---

## The Three Components

### 1. Text Encoder (~140M params, mostly frozen)

**Base model**: `nomic-embed-text-v1.5` (137M params, 768-dim native output)

**What it does**: Converts any text (article titles, node content, search queries) into a 256-dim vector that captures semantic meaning.

**Why 256-dim and not 768**: Full Wikipedia (6M articles) at 768-dim = 18GB — doesn't fit in 12GB VRAM. At 256-dim = 6.0GB, leaving room for everything else. Retains ~95% retrieval quality after fine-tuning.

**Training approach**:
- Freeze the backbone weights (saves VRAM and prevents overfitting)
- Unfreeze only the last 2 transformer layers for domain adaptation
- Add a learned projection head: 768 → 512 → GELU → 256 → LayerNorm
- Fine-tune on contrastive pairs from the pointer network's edges:
  - **Positive pairs**: Nodes connected by primary/supporting edges
  - **Hard negatives**: Nodes in the same domain but not connected
  - **Cross-domain positives**: wiki→KB edges that exist in the network

```python
class TextEncoder(nn.Module):
    def __init__(self):
        self.backbone = AutoModel.from_pretrained("nomic-ai/nomic-embed-text-v1.5")
        self.backbone.requires_grad_(False)  # freeze
        self.proj = nn.Sequential(
            nn.Linear(768, 512),
            nn.GELU(),
            nn.Linear(512, 256),
            nn.LayerNorm(256),
        )
        # Unfreeze last 2 transformer layers
        for layer in self.backbone.encoder.layer[-2:]:
            layer.requires_grad_(True)

    def forward(self, input_ids, attention_mask):
        with torch.cuda.amp.autocast():
            hidden = self.backbone(input_ids, attention_mask).last_hidden_state
            pooled = hidden[:, 0]  # CLS token
            return self.proj(pooled)  # 256-dim output
```

---

### 2. Graph Encoder (~12M params, trained from scratch)

**Architecture**: 4-layer **HeteroSAGE** (heterogeneous GraphSAGE)

**What it does**: Learns structural patterns from the graph itself — which nodes are hubs, which clusters are tightly connected, how different edge types carry different information. Produces a 256-dim structural embedding per node.

**Why heterogeneous**: The network has 12+ edge types (shared_tags, explicit_reference, citation, dive, surface, cross_subnet, etc.). A homogeneous GNN treats all edges the same. HeteroSAGE learns *separate weight matrices* for each edge type, so it understands that a citation link and a tag overlap encode fundamentally different relationships.

**Mini-batch training (critical for 1M+ edges)**: At scale, the full graph cannot fit in GPU memory during backprop. The NeighborLoader samples a subgraph per batch:
- Layer 1: 25 neighbors sampled
- Layer 2: 15 neighbors sampled
- Layer 3: 10 neighbors sampled
- Layer 4: 5 neighbors sampled
- Per-batch (1024 targets): ~24K unique nodes loaded → ~28 MB. Very manageable.

```python
class HierarchicalGraphEncoder(nn.Module):
    def __init__(self, in_dim=529, hidden=256, out_dim=256):
        # Separate convolutions per edge type
        self.conv1 = HeteroConv({
            ('main', 'shared_tags', 'main'): SAGEConv(in_dim, hidden),
            ('main', 'explicit_ref', 'main'): SAGEConv(in_dim, hidden),
            ('main', 'semantic', 'main'): SAGEConv(in_dim, hidden),
            ('main', 'dive', 'subnet'): SAGEConv(in_dim, hidden),
            ('subnet', 'surface', 'main'): SAGEConv(in_dim, hidden),
            ('subnet', 'citation', 'subnet'): SAGEConv(in_dim, hidden),
            ('subnet', 'section_link', 'subnet'): SAGEConv(in_dim, hidden),
            ('subnet', 'cross_subnet', 'subnet'): SAGEConv(in_dim, hidden),
        }, aggr='mean')
        # Layers 2-4 follow same pattern with different weights
```

**Node feature vector (~529-dim)**:
- Text embedding (256-dim from text encoder)
- Domain one-hot (7-dim)
- Tag hash (256-dim, feature hashing for open vocabulary)
- Layer indicator (2-dim: is_main, is_subnet)
- Depth (1-dim: normalized depth in subnet)
- Subnet metadata (3-dim: subnet size, edge count, entry points)
- Structural features (4-dim: degree, centrality, clustering coefficient, PageRank)

---

### 3. Gated Fusion Layer (~6M params)

**What it does**: Combines the text embedding and graph embedding into a single 256-dim representation. The "gated" part is critical — it learns *how much to trust each signal*.

**Why gated**: When a new Wikipedia article is added (no graph edges yet), the graph embedding is a zero vector. The gate learns to rely entirely on the text embedding in this case, and gradually shifts toward graph structure as the node accumulates edges.

```python
class GatedFusion(nn.Module):
    def __init__(self, dim=256):
        self.gate = nn.Sequential(nn.Linear(dim * 2, dim), nn.Sigmoid())
        self.transform = nn.Sequential(
            nn.Linear(dim * 2, dim * 2), nn.GELU(), nn.Dropout(0.1),
            nn.Linear(dim * 2, dim), nn.LayerNorm(dim),
        )

    def forward(self, text_emb, graph_emb):
        combined = torch.cat([text_emb, graph_emb], dim=-1)
        g = self.gate(combined)  # learn how much to trust each signal
        fused = self.transform(combined)
        return g * text_emb + (1 - g) * graph_emb + fused  # residual
```

---

## The Three Task Heads

### Head 1: Link Prediction (~3M params)

**What it does**: Given two nodes, predicts:
1. Should an edge exist between them? (yes/no probability)
2. What weight should it have? (1-10)
3. What strength class? (primary / supporting / related / tangential)

**Why it matters**: Automatically discovers missing connections in the network. The current network has 92 dead-end Wikipedia nodes with zero inbound edges — this head finds their connections.

**Training data**:
- Positive: existing edges with known weight/strength
- Negative: structure-aware sampling (50% random, 30% same-domain, 20% 2-hop)
- At 1M edges: 1M positive + 3M negative = 4M training examples per epoch

```python
class LinkPredHead(nn.Module):
    def __init__(self, dim=256):
        self.edge_mlp = nn.Sequential(
            nn.Linear(dim * 2 + 5, 512), nn.ReLU(), nn.Dropout(0.15),
            nn.Linear(512, 256), nn.ReLU(), nn.Linear(256, 1),
        )
        self.weight_head = nn.Linear(256, 10)   # weight 1-10
        self.strength_head = nn.Linear(256, 4)   # 4 strength classes
```

### Head 2: Pathfinder (~6M params)

**What it does**: The core "agent assist" capability. Given a source node, target node, and strategy hint, predicts:
1. **Layer action**: stay on current layer / dive into subnet / surface back to main (three-way decision)
2. **Next hop**: which neighbor to move to (two-phase: fast prune → detailed score)
3. **Path quality**: estimated hops, cost, and success probability

**Two-phase scoring**: At scale, some hub nodes have 200+ edges. Scoring all neighbors is expensive. The pruner (tiny 2-layer MLP) cuts to 32 candidates, then the full scorer evaluates those. Reduces compute ~6x for high-degree nodes.

**Hierarchical navigation**: The pathfinder learns when diving into a subnet is worthwhile vs. staying on the main layer. Training signal:
- Routes where diving found a cross-subnet shortcut → positive (dive was worth it)
- Routes where diving led to a dead end → negative (dive was wasteful)
- The exponential cost model provides automatic supervision

```python
class HierarchicalPathfinderHead(nn.Module):
    def __init__(self, dim=256, num_strategies=8):
        self.strategy_embed = nn.Embedding(num_strategies, 64)
        # Layer decision: dive / surface / stay
        self.layer_decision = nn.Sequential(
            nn.Linear(dim * 2 + 64 + 4, 256), nn.ReLU(),
            nn.Dropout(0.1), nn.Linear(256, 3),
        )
        # Next-hop scorer (within chosen layer)
        self.hop_scorer = nn.Sequential(
            nn.Linear(dim * 3 + 64, 512), nn.ReLU(),
            nn.Dropout(0.1), nn.Linear(512, 256), nn.ReLU(),
            nn.Linear(256, 1),
        )
        # Cost estimator
        self.cost_estimator = nn.Sequential(
            nn.Linear(dim * 2 + 64 + 4, 256), nn.ReLU(),
            nn.Linear(256, 4),  # [main_hops, subnet_hops, total_cost, success_prob]
        )
```

### Head 3: Node Classifier (~3M params)

**What it does**: Predicts metadata for any node:
- Domain (7 classes: kb, scr, sop, team, src, tool, wiki)
- Category (6 classes)
- Tags (multi-label, hashed into 256-dim space for open vocabulary)
- Community membership (hierarchical: 16 super-communities × 32 sub-communities)
- Importance scores (hub, bridge, authority)

**Why it matters**: When new Wikipedia articles or technical docs join the network, this head instantly classifies them without manual tagging.

```python
class ScaledNodeClassifyHead(nn.Module):
    def __init__(self, dim=256, num_domains=7, num_categories=6,
                 tag_hash_dim=256, max_communities=64):
        self.domain_cls = nn.Linear(dim, num_domains)
        self.category_cls = nn.Linear(dim, num_categories)
        self.tag_predictor = nn.Sequential(
            nn.Linear(dim, 512), nn.ReLU(), nn.Linear(512, tag_hash_dim),
        )
        self.community_cls = nn.Linear(dim, max_communities)
        self.importance = nn.Sequential(
            nn.Linear(dim, 128), nn.ReLU(),
            nn.Linear(128, 3),  # [hub_score, bridge_score, authority_score]
        )
```

---

## The Hierarchical Network (Two-Layer Architecture)

### Structure

```
┌──────────────────────────────────────────────────────────────┐
│                        MAIN LAYER                              │
│   ┌─────┐     ┌─────┐     ┌─────┐     ┌─────┐     ┌─────┐   │
│   │Wiki │────▶│Wiki │────▶│ RFC │────▶│Paper│────▶│ KB  │   │
│   │Art.A│     │Art.B│     │ 793 │     │ X   │     │Entry│   │
│   └──┬──┘     └──┬──┘     └──┬──┘     └──┬──┘     └─────┘   │
│      │            │            │            │                   │
│ ─────┼────────────┼────────────┼────────────┼──── boundary ──  │
│      ▼            ▼            ▼            ▼                   │
│   ┌──────┐    ┌──────┐    ┌──────┐    ┌──────┐                │
│   │Subnet│    │Subnet│    │Subnet│    │Subnet│                │
│   │  A   │    │  B   │    │  C   │    │  D   │                │
│   │[c1]──┼───▶│[c4]  │    │[§3]──┼───▶│[ref2]│ cross-subnet  │
│   │[c2]  │    │[c5]──┼───▶│[§4]  │    │[ref3]│ links         │
│   │[c3]──┼────┼──────┼───▶│[§5]  │    │[ref4]│                │
│   └──────┘    └──────┘    └──────┘    └──────┘                │
│   c=citation   §=section   ref=reference                       │
└──────────────────────────────────────────────────────────────┘
```

### Movement Rules

| Transition | Cost | Description |
|-----------|------|-------------|
| Main → Main | Normal (weight-based) | Standard network traversal |
| Main → Subnet ("dive") | Free | Entering a node's details |
| Within Subnet | **Exponential depth penalty** | Each hop costs base^depth more |
| Subnet → Subnet (cross-link) | Jump cost × exponential penalty | Crossing between different nodes' subnets |
| Subnet → Main ("surface") | Free | Returning to the main layer |

### The Exponential Depth Penalty

```python
class HierarchicalCostModel:
    def __init__(self, base=2.0, dive_cost=0.0, surface_cost=0.0,
                 cross_subnet_base_cost=3.0):
        self.base = base  # exponential base

    def edge_cost(self, edge, current_depth=0):
        if edge['type'] == 'main_to_main':
            return 10 - edge['weight']  # high weight = low cost
        elif edge['type'] == 'dive':
            return 0.0  # free
        elif edge['type'] == 'surface':
            return 0.0  # free
        elif edge['type'] == 'internal':
            new_depth = current_depth + 1
            base_cost = 10 - edge.get('weight', 5)
            penalty = self.base ** new_depth  # EXPONENTIAL
            return base_cost * penalty
        elif edge['type'] == 'cross_subnet':
            new_depth = current_depth + 1
            penalty = self.base ** new_depth
            return self.cross_subnet_base_cost * penalty
```

**Why exponential**: Citation chains have diminishing relevance. If A cites B, which cites C, which cites D — the relevance of D to A drops off exponentially. Base=2 means:
- 1-2 subnet hops: usually worth it (2-4x cost)
- 3 hops: sometimes worth it (8x cost)
- 4+ hops: almost never worth it (16x+) — surface and travel main layer instead

### What Goes into Subnets

| Document Type | Subnet Contents | Nodes Per Parent |
|--------------|-----------------|-----------------|
| Wikipedia article | Citations, sections, infobox entities, "See also" | 30-200 |
| RFC / Technical spec | Sections, normative references, defined terms | 20-100 |
| Academic paper | Citations, figures, theorems, datasets | 30-80 |
| KB entry | References, scripts cited, sources | 5-20 |
| Technical manual | Chapters, API endpoints, examples, glossary | 50-300 |

### Subnet Storage (Sharded)

Subnets do NOT live in pointer-network.json. One file per parent node:

```
storage/
├── pointer-network.json           # Main layer only (5K-50K nodes)
├── subnets/
│   ├── index.json                 # Metadata + entry points
│   ├── wikipedia/
│   │   ├── transformer.subnet.json
│   │   ├── attention.subnet.json
│   │   └── ...
│   ├── rfcs/
│   │   ├── rfc793.subnet.json
│   │   └── ...
│   └── papers/
│       └── ...
├── cross-subnet-links.jsonl       # All cross-subnet edges
└── embeddings/
    ├── main_256d.index            # FAISS for main layer
    └── subnet_256d.index          # FAISS for subnet nodes
```

Lazy-loaded with LRU cache (500 subnets cached). Each file is 5-50KB, loads in <1ms.

### Cross-Subnet Links (The Most Valuable Edges)

When the same paper is cited by two different Wikipedia articles, that's a **shared citation** — a strong implicit connection the main layer might miss.

Example: wiki:Transformer cites "Attention Is All You Need" and wiki:BERT also cites it.
Path: Transformer → (dive) → cite:vaswani → (cross) → cite:vaswani@BERT → (surface) → BERT
Cost: dive(0) + internal(2) + cross(6) + surface(0) = 8
vs. main-layer path that might cost 12+ hops at weight 1-2 each.

---

## Training Strategy

### Overview

```
Day 1-2: Foundation training at 37K edges
    ↓ validation gates pass?
Day 3-4: Expand to 100K edges (incremental, EWC + replay)
    ↓ validation gates pass?
Day 5-6: Stress test at 250K edges
    ↓ validation gates pass?
Day 7-10: Scale to 1M edges
    ↓
Ongoing: Continuous learning (auto-retrain every 5K new edges)
    ↓ hit 10x original size?
One-time: Full retrain, restart the cycle
```

### Phase Details

| Phase | Edges | Time | LR | EWC Lambda | Key Action |
|-------|-------|------|-----|-----------|------------|
| 0: Foundation | 37K | 6 hrs | 5e-4 | — | Full train from scratch |
| 1: Wiki Expansion | 100K | 8 hrs | 2e-4 | 500 | Incremental, sqrt domain sampling |
| 2: Stress Test | 250K | 3 hrs | 1e-4 | 1000 | Incremental, enable hub caching |
| 3: Full Scale | 1M | 5 hrs | 5e-5 | 2000 | Incremental, large batches |
| 4: Continuous | 1M+ | 2 hrs/batch | 1e-5 | 2000 | Auto-retrain every 5K new edges |

### Validation Gates (Must Pass Before Advancing)

- Link prediction AUC > 0.80
- Pathfinder next-hop accuracy > 60%
- Node classification domain accuracy > 90%
- Core network pathfinding degradation < 5% vs previous phase

### Four Risks and Mitigations

**Risk 1: Distribution Shift** — Wiki goes from 37% to 95% of nodes.
*Fix*: Square-root domain sampling. Not proportional (would drown out rare domains), not uniform (would over-represent them).

**Risk 2: Catastrophic Forgetting** — New wiki training erases KB/SCR patterns.
*Fix*: EWC (penalizes changing important weights) + Replay buffer (30% foundation, 40% recent, 30% uniform).

**Risk 3: Embedding Space Drift** — Fine-tuning the encoder makes old FAISS vectors stale.
*Fix*: Versioned embeddings + lazy background reindex. Core network reindexed in seconds; Wikipedia reindexes over hours in background.

**Risk 4: Architectural Bottlenecks at Scale** — Hub nodes, tag vocabulary growth, community count explosion.
*Fixes*: Hub embedding cache (top 5% by degree), feature hashing for tags (256-dim, handles 500+ tags), hierarchical community prediction (16 super × 32 sub).

### The 10x Rule

When the network has 10x more edges than what you originally trained on, do a full retrain:
- Train at 37K → full retrain at 370K
- Train at 370K → full retrain at 3.7M
Between those milestones, incremental training with EWC + replay works well.

---

## VRAM Budget

### Training Mode (~6 GB used)

| Component | VRAM |
|-----------|------|
| Text encoder (nomic, frozen INT8) | 1.5 GB |
| HeteroSAGE (4 layers, 12 edge types) | 0.4 GB |
| Fusion + 3 task heads | 0.4 GB |
| Optimizer (AdamW, 35M params) | 0.7 GB |
| Mini-batch data (main + subnet subgraphs) | 0.8 GB |
| Activations + gradients | 1.5 GB |
| EWC Fisher matrices + replay buffer | 0.3 GB |
| FAISS PQ (for validation) | 0.5 GB |
| **Total** | **~6.1 GB** |
| **Headroom** | **~5.9 GB** |

### Inference Mode (~2.85 GB used)

| Component | VRAM |
|-----------|------|
| Full model (INT8 quantized) | 0.8 GB |
| FAISS PQ index (6M wiki) | 1.5 GB |
| Graph structure (1M edges) | 0.1 GB |
| Subnet cache (500 subnets) | 0.2 GB |
| Batch inference buffer | 0.3 GB |
| **Total** | **~2.85 GB** |
| **Headroom** | **~9.15 GB** |

### Full GPU Load (PNAM + FAISS + LLM simultaneously)

| Component | VRAM |
|-----------|------|
| PNAM inference | 1.3 GB |
| FAISS PQ index | 1.5 GB |
| nomic-embed-text (always loaded) | 0.8 GB |
| 7B LLM at Q4_K_M (on-demand) | 4.1 GB |
| KV cache (32K context) | 1.0 GB |
| **Total** | **~8.7 GB** |
| **Headroom** | **~3.3 GB** |

Managed via NVIDIA MPS (Multi-Process Service) for concurrent GPU sharing.

---

## How Claude Agents Use PNAM

### Bridge 1: Baked Predictions (No Model Loading Needed)

PNAM runs batch inference and writes results directly into pointer-network.json:
- Predicted edges with confidence scores
- Node classifications and importance scores
- Pre-computed path quality estimates

Claude agents read the JSON files they already use — no model integration needed.

### Bridge 2: MCP Server (Direct GPU Access)

A FastAPI-based MCP server exposes PNAM as tools Claude agents can call:

```python
@mcp_tool("search_network")
async def search_network(query: str, k: int = 10):
    embedding = pnam.encode(query)
    results = faiss_index.search(embedding, k)
    return format_results(results)

@mcp_tool("find_path")
async def find_path(source: str, target: str, strategy: str = "hybrid"):
    path = pnam.pathfind(source, target, strategy)
    return path

@mcp_tool("predict_edges")
async def predict_edges(node_id: str, top_k: int = 20):
    predictions = pnam.predict_links(node_id, top_k)
    return predictions

@mcp_tool("summarize")
async def summarize(node_id: str):
    content = load_node_content(node_id)
    summary = llm.generate(f"Summarize: {content}")
    return summary
```

Latency: <10ms for PNAM/FAISS, ~500ms for LLM summarization.

---

## Supporting Models (Running Alongside PNAM)

| Task | Model | VRAM (Q4) | Tokens/s | Notes |
|------|-------|-----------|----------|-------|
| Code generation | Qwen2.5-Coder-7B | 4.5 GB | 45-60 | Best code model under 12GB |
| General reasoning | Llama 3.1 8B Instruct | 4.5 GB | 50-70 | Strong all-around |
| Fast classification | Phi-3-mini (3.8B) | 2.5 GB | 80-100 | Tagging, sorting |
| Summarization | Mistral 7B Instruct v0.3 | 4.5 GB | 50-65 | Article summaries |
| Entity extraction | Qwen2.5-7B | 4.5 GB | 45-60 | Structured output |
| Embedding | nomic-embed-text v1.5 | 0.8 GB | 200+ docs/s | Always loaded |

Only one LLM loaded at a time (on-demand via Ollama). PNAM + FAISS + embedding model are always loaded (~3.6 GB).

---

## GPU Scheduling Strategy

```
Priority 1: PNAM inference    (always loaded, ~1.3GB, microsecond responses)
Priority 2: FAISS index       (always loaded, ~1.5GB, millisecond queries)
Priority 3: Embedding model   (always loaded, ~0.8GB, fast encoding)
Priority 4: LLM inference     (on-demand, ~4GB, swapped in/out)
Priority 5: PNAM training     (background, paused during LLM requests)
```

Use CUDA streams to overlap PNAM inference with FAISS queries. LLM runs in a separate CUDA context via MPS. PNAM training uses remaining VRAM with gradient checkpointing.

---

## Continuous Learning System

```python
class PNAMContinuousLearner:
    def __init__(self, model):
        self.retrain_threshold_edges = 5000   # retrain after 5K new edges
        self.retrain_threshold_routes = 200   # or 200 new routes
        self.replay_buffer = ReplayBuffer(max_size=50000)

    def on_new_edge(self, edge):
        self.new_edges_buffer.append(edge)
        self.replay_buffer.add(edge)
        if len(self.new_edges_buffer) >= self.retrain_threshold_edges:
            self.incremental_train()

    def incremental_train(self):
        # Mix new data with replay buffer
        train_data = self.new_edges_buffer + self.replay_buffer.sample(10000)
        train_pnam(self.model, train_data, epochs=5, lr=1e-5)
        self.update_faiss_index(affected_nodes)
        save_checkpoint(self.model, version=self.version + 1)
```

Active learning loop: PNAM predicts uncertain edges (confidence 0.4-0.6) → agent validates → confirmed edges become training data. Reduces labeling effort by 3-5x.

---

## Scale Projections

| Scenario | Main Nodes | Subnet Nodes | Total Edges | VRAM |
|----------|-----------|-------------|-------------|------|
| Current (flat) | 5,000 | 0 | 37,000 | 40 MB |
| + Subnets (30 avg) | 5,000 | 150,000 | 447,000 | 352 MB |
| + Tech docs + subnets | 15,000 | 500,000 | 3,162,000 | 1,174 MB |
| Target (1M main edges) | 50,000 | 2,000,000 | 8,000,000+ | ~3,500 MB |

Even the largest scenario fits in 12GB with room for the model.

---

## Disk Storage Budget

| Asset | Size (37K) | Size (1M) | Format |
|-------|-----------|-----------|--------|
| FAISS flat index (6M wiki, 256d) | 6.0 GB | 6.0 GB | .index |
| FAISS PQ index (compressed) | 1.5 GB | 1.5 GB | .index |
| Graph structure | 5 MB | ~150 MB | .json / .pt |
| Node features (precomputed) | 10 MB | ~300 MB | .pt (mmap) |
| Model checkpoint | 600 MB | 600 MB | .pt |
| Subnet files | — | ~500 MB | .json (sharded) |
| Route logs | 1 MB | ~20 MB | .jsonl |
| **Total** | **~8.2 GB** | **~9.1 GB** |

Checkpoints kept per phase (~600MB each) for rollback capability.

---

## Tech Stack

```
# Core
torch >= 2.1 (CUDA 12.x)
torch-geometric >= 2.4 (GNN layers, HeteroConv, NeighborLoader)
sentence-transformers >= 2.3 (text encoder base)
faiss-gpu >= 1.7.4 (embedding index)

# Training
wandb (experiment tracking)
accelerate (mixed precision training)

# Serving
fastapi + uvicorn (API / MCP server)
ollama (LLM management)
onnxruntime-gpu (optional ONNX export for 3-5x inference speedup)

# Graph Analytics
cugraph / RAPIDS (GPU graph algorithms)
networkx or igraph (CPU graph utilities)
```

---

## Summary: What PNAM Does for You

| Capability | Without PNAM | With PNAM |
|-----------|-------------|-----------|
| **Find related content** | Tag string matching (92% synthetic edges) | Semantic similarity + learned structural patterns |
| **Navigate the graph** | CPU pathfinding, 8 hardcoded strategies | GPU pathfinding with learned navigation including subnets |
| **Add new content** | Manual tagging and linking | Auto-classify, auto-link, auto-tag in milliseconds |
| **Discover connections** | Only explicit references | Predict missing links with confidence scores |
| **Handle scale** | Slows linearly with graph size | Mini-batch GNN scales to millions of nodes |
| **Improve over time** | Static rules | Continuous learning from agent behavior |

PNAM is a **130M parameter specialist** that runs at >1,000 inferences/sec on your GPU. It costs nothing per query (local), improves with use (continuous learning), and fits entirely in 12GB VRAM alongside a 7B LLM.
