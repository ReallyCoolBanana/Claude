# DATA-GATHER-ALGO: Algorithmic Methods Findings
**Date:** 2026-03-11
**Team:** DATA-GATHER-ALGO (3 subagents)
**Focus:** Algorithmic methods, data structures, optimization techniques

---

## 1. MODERN SORTING ALGORITHMS

### 1.1 PDQSort (Pattern-Defeating Quicksort)
- **Category:** Sorting / Hybrid
- **Time:** O(n log n) average, O(n) best case for patterned input, O(n log n) worst case
- **Space:** O(log n) stack
- **Key Insight:** Combines randomized quicksort average case with heapsort worst case. Uses BlockQuicksort technique to eliminate branch mispredictions on large arrays. Detects patterns (ascending, descending, all-equal) for linear-time fast paths.
- **Adopted By:** Rust std, Boost, GAP, Zig
- **Reference:** https://github.com/orlp/pdqsort
- **Practical Applications:** General-purpose unstable sorting, standard library implementations

### 1.2 Glidesort
- **Category:** Sorting / Stable Hybrid
- **Time:** O(n log n) worst case, adaptive to presorted runs and duplicates
- **Space:** O(n) auxiliary
- **Key Insight:** First practical stable sort fully adaptive to BOTH presorted runs (like Timsort) AND low-cardinality inputs (like pdqsort). Merges multiple sorted runs simultaneously, interleaving merge loops for better ILP and memory-level parallelism. 3x faster than Rust's std Timsort on random 32-bit integers.
- **Reference:** https://github.com/orlp/glidesort
- **Practical Applications:** Stable sorting where data may have runs or duplicates

### 1.3 Driftsort
- **Category:** Sorting / Stable (simplified Glidesort)
- **Key Insight:** Simplified Glidesort variant that removes complications irrelevant to compound data types. Better suited for general-purpose stable sorting of structs/objects.

### 1.4 SIMD Sorting Networks (2026)
- **Category:** Sorting / Constant-time / Cryptographic
- **Time:** O(n log^2 n) — slightly worse than comparison sorts
- **Key Insight:** Fixed, data-independent comparison schedule using SIMD vectorization. 2x-5x faster than stdlib pdqsort on Apple Silicon. Essential for post-quantum cryptography (Classic McEliece, NTRU Prime) where timing leaks compromise security.
- **Reference:** https://00f.net/2026/02/17/sorting-without-leaking-secrets/
- **Practical Applications:** Cryptographic implementations, constant-time sorting, SIMD-heavy workloads

### TOOL REQUEST: Sorting benchmark harness
- Build a benchmark tool that compares pdqsort, glidesort, driftsort, and SIMD sorting networks across different input distributions (random, nearly-sorted, few-unique, reversed).

---

## 2. DATA STRUCTURE INNOVATIONS

### 2.1 Skip Hash (ACM PPoPP 2025)
- **Category:** Concurrent Ordered Map
- **Time:** O(log n) ordered operations, O(1) amortized hash lookups
- **Space:** O(n)
- **Key Insight:** Composes a hash map with a doubly-linked skip list under a single STM abstraction. Includes a Range Query Coordinator (RQC) with version numbers for concurrent range queries without locking. Outperforms state-of-art lock-free structures in almost all cases, often by large margin. Challenges conventional wisdom that lock-free is always superior to STM.
- **Reference:** https://arxiv.org/html/2410.07466v1
- **Practical Applications:** Concurrent databases, ordered indexes, range query systems

### 2.2 HAMT (Hash Array Mapped Trie)
- **Category:** Persistent / Immutable Map
- **Time:** O(log32 n) ≈ O(1) practical for lookup/insert/delete
- **Space:** O(n) with structural sharing
- **Key Insight:** Uses 5-bit chunks of 32-bit hash to navigate trie levels. Branching factor of 32 means <7 levels for billions of entries. Bitmap compression reduces memory overhead. Foundation for Clojure, Scala, Haskell persistent maps.
- **Reference:** https://github.com/mkirchner/hamt (C implementation)
- **Practical Applications:** Functional programming, undo/redo, concurrent read-heavy workloads

### 2.3 CHAMP (Compressed Hash Array Mapped Prefix-tree)
- **Category:** Persistent Map (improved HAMT)
- **Key Insight:** Improves cache locality and reduces memory overhead vs classic HAMT. 10-100% performance gains in equality checking and iteration. Maintains compact canonical form on deletion (unlike Clojure's HAMT).
- **Practical Applications:** Drop-in HAMT replacement with better cache behavior

### 2.4 RRB Trees (Relaxed Radix Balanced)
- **Category:** Persistent Vector with efficient concatenation
- **Time:** O(log n) concatenation, split, insert-at-index
- **Key Insight:** Extends HAMT-style vectors to support O(log n) concatenation without compromising per-element O(log32 n) access. Critical missing operation in standard persistent vectors.
- **Practical Applications:** Rope-like text editors, parallel merge operations

### 2.5 Cache-Oblivious B-Trees (van Emde Boas Layout)
- **Category:** Cache-efficient search tree
- **Time:** O(log_B N) memory transfers for search (B = cache line size)
- **Space:** O(N)
- **Key Insight:** Recursively splits tree at middle level and lays out pieces in van Emde Boas order. Optimal for ANY cache hierarchy without knowing cache parameters. Combined with Packed Memory Array for O(1 + (log^2 N)/B) amortized insertions.
- **References:**
  - Bender, Demaine, Farach-Colton: https://erikdemaine.org/papers/CacheObliviousBTrees_SICOMP/paper.pdf
  - CORoBTS (2024): https://arxiv.org/abs/2209.09166
- **Practical Applications:** Database indexes, external memory algorithms, systems where cache hierarchy is unknown

### 2.6 Lock-Free Concurrent Hash Tries (Ctries)
- **Category:** Concurrent Map
- **Key Insight:** Non-blocking operations using single-word CAS. Supports lock-free snapshots without eager copying. Efficient compression on removal for space efficiency.
- **Practical Applications:** High-concurrency key-value stores, concurrent caching

---

## 3. PROBABILISTIC DATA STRUCTURES

### 3.1 Binary Fuse Filters
- **Category:** Approximate Membership Query (AMQ)
- **Space:** Within 13% of information-theoretic lower bound
- **Key Insight:** Construction 2x+ faster than xor filters. Near-optimal space. With slight query speed tradeoff, can reach within 8% of lower bound.
- **Practical Applications:** Database query acceleration, network packet filtering

### 3.2 Vacuum Filters
- **Category:** AMQ (Bloom/Cuckoo filter replacement)
- **Space:** 25% less than Cuckoo filters, 15% less than Bloom filters at same FPR
- **Key Insight:** Smallest space among all known AMQ structures while maintaining high throughput. >10x throughput vs Bloom filters.
- **Practical Applications:** Memory-constrained membership testing

### 3.3 Stable Cuckoo Filters (SCF)
- **Category:** AMQ for streaming data
- **Key Insight:** Fine-grained eviction of stale elements for data streams with frequent member updates. Maintains stable performance over time unlike standard Bloom/Cuckoo which become unusable with updates.
- **Practical Applications:** Network flow monitoring, stream processing

### 3.4 Stacked Filters (Workload-Aware)
- **Category:** Learned AMQ
- **Key Insight:** Incorporates workload knowledge about frequently queried non-existing values using hashing and sequenced filter layers. Indexes both data and frequent negatives.
- **Practical Applications:** Database indexes where query distribution is known

### 3.5 Adaptive Quotient Filters (2025)
- **Category:** AMQ with quotient-based hashing
- **Key Insight:** Adapts filter parameters based on workload. Published by Conway, Farach-Colton, Johnson, Pandey at 2025 conference.

### TOOL REQUEST: Probabilistic filter benchmark
- Build a comparison tool for Bloom, Cuckoo, Binary Fuse, and Vacuum filters across FPR targets, space usage, and throughput.

---

## 4. OPTIMIZATION METHODS

### 4.1 Knuth's Optimization (Knuth-Yao Speedup)
- **Category:** DP Optimization
- **Time:** Reduces O(n^3) range DP to O(n^2)
- **Key Insight:** For recurrences dp(i,j) = min_k[dp(i,k) + dp(k+1,j) + C(i,j)], if cost C satisfies monotonicity and quadrangle inequality, then opt(i,j-1) <= opt(i,j) <= opt(i+1,j), limiting the search range for each transition.
- **Reference:** https://cp-algorithms.com/dynamic_programming/knuth-optimization.html
- **Practical Applications:** Optimal BST, matrix chain multiplication, merging stones

### 4.2 Divide and Conquer DP Optimization
- **Category:** DP Optimization
- **Time:** Reduces O(n^2) partition DP to O(n log n)
- **Key Insight:** For dp[i][j] = min_{k<j}{dp[i-1][k] + C[k][j]}, if opt[i][j] is monotonically non-decreasing, can recursively solve middle element then recurse on left/right halves.
- **Reference:** https://jeffreyxiao.me/blog/divide-and-conquer-optimization/
- **Practical Applications:** Partition problems, scheduling, resource allocation

### 4.3 Convex Hull Trick (CHT)
- **Category:** DP Optimization
- **Time:** O(n) or O(n log n) depending on ordering
- **Key Insight:** When DP transitions can be expressed as evaluating linear functions, maintaining a convex hull of lines allows O(1) amortized or O(log n) query per transition.
- **Practical Applications:** Competitive programming, cost optimization with linear cost functions

### 4.4 k-Submodular Maximization via Multilinear Extension (ICLR 2025 Spotlight)
- **Category:** Submodular Optimization
- **Key Insight:** Novel multilinear extension framework for k-submodular functions with unified Frank-Wolfe-type algorithms. Handles monotone/non-monotone functions under matroid, knapsack, and combined constraints. Improved or optimal approximation ratios.
- **Reference:** https://openreview.net/forum?id=EPHsIa0Ytg
- **Practical Applications:** Feature selection, sensor placement, influence maximization

---

## 5. CONCURRENCY ALGORITHMS

### 5.1 Work-Stealing Schedulers

#### 5.1.1 Cilk (Foundational)
- **Time:** Expected T1/P + O(T_inf) on P processors
- **Key Insight:** Continuation stealing — steal the continuation, execute the child. Provably optimal space and time bounds. Foundation for all modern work-stealing schedulers.
- **Reference:** https://dl.acm.org/doi/pdf/10.1145/324133.324234

#### 5.1.2 Tokio Work-Stealing (Rust Async)
- **Key Insight:** Batch stealing (takes multiple tasks at once), lock-free work queues, adapted Go's fixed-size SPMC queue. Achieved 10x speedup over previous scheduler design.
- **Reference:** https://tokio.rs/blog/2019-10-scheduler

#### 5.1.3 BWoS (Block-based Workstealing) — Proposed for Tokio
- **Key Insight:** Block-based design allows thieves to steal from the middle of the queue, preventing stealing from slowing down the owner. Block-level synchronization ensures owner always operates on different block than thieves. Significant improvement in HTTP server benchmarks.
- **Reference:** https://github.com/tokio-rs/tokio/issues/5240

#### 5.1.4 Rayon-LH (Latency-Hiding Work Stealing)
- **Key Insight:** Extension of Rayon for programs with latency-incurring operations (I/O), not just compute-bound work. Uses Rust futures integration to handle blocking operations within work-stealing framework.

#### 5.1.5 Wasp (SC'25)
- **Category:** Graph algorithm via work stealing
- **Key Insight:** Efficient async single-source shortest path on multicore via work stealing. Published at SC'25 (supercomputing conference).

### 5.2 Consensus Protocols

#### 5.2.1 Fast Raft
- **Key Insight:** Reduces commit path from 3 rounds to 2 via "fast track" direct broadcast to designated quorum. Falls back to standard Raft on conflicts.

#### 5.2.2 C-Raft (Hierarchical)
- **Key Insight:** Batches local consensus then orders results in global log. Up to 5x throughput improvement in geo-distributed deployments.

#### 5.2.3 RaftOptima (Proxy Leaders)
- **Key Insight:** Distributes command distribution and response gathering to proxy leaders. 60% latency reduction in configurations up to 25 servers. TLA+ verified.

#### 5.2.4 Dynatune (Dynamic Election Tuning)
- **Key Insight:** Real-time RTT and packet loss based tuning of election timeouts and heartbeat intervals. 80% reduction in leader failure detection time. 45% reduction in out-of-service window.

#### 5.2.5 KRaft (Kafka Raft)
- **Key Insight:** Kafka's internal Raft implementation replacing ZooKeeper. Simplified architecture, reduced operational overhead, improved leader transitions.

### TOOL REQUEST: Raft protocol simulator
- Build a Raft simulator that can demonstrate leader election, log replication, and measure latency under different network conditions. Could help test Fast Raft and C-Raft variants.

---

## 6. GRAPH ALGORITHMS

### 6.1 Parallel Cluster-BFS (C-BFS)
- **Category:** Parallel Graph Traversal
- **Time:** O(dm(k/w+1)) for cluster of k vertices within distance d
- **Key Insight:** Combines bit-parallelism (processing w BFS sources simultaneously in a single word) with thread-level parallelism. Open research question solved for combining both parallelism levels.
- **Reference:** https://arxiv.org/abs/2410.17226
- **Practical Applications:** Social network analysis, web graph traversal, multi-source shortest paths

### 6.2 Hyperbolic Graph Embeddings (Rigel)
- **Category:** Approximate Distance Queries
- **Time:** O(1) query time after O(n) embedding
- **Key Insight:** Embeds graph into hyperbolic coordinate space. Approximates node distances in constant time. Low distortion error for social/web graphs.
- **Reference:** https://sandlab.cs.uchicago.edu/rigel/documents/rigel.pdf
- **Practical Applications:** Social network distance queries, geographic routing

### 6.3 Dynamic Graph Processing
- **Key Insight:** Real-world graphs change constantly. Monotonic algorithms (shortest path, BFS, reachability) can use pruning for incremental updates. Gem engine handles out-of-core execution for graphs larger than memory.
- **Practical Applications:** Real-time social networks, evolving road networks

---

## 7. STRING MATCHING ALGORITHMS

### 7.1 StringZilla (SIMD String Search)
- **Category:** SIMD-accelerated substring search
- **Throughput:** 16 GB/s, 5-10x faster than standard libraries
- **Key Insight:** If first 4 characters match, rest likely matches too. Implemented for SSE, AVX, AVX-512, and ARM Neon.
- **Reference:** https://ashvardanian.com/posts/stringzilla/
- **Practical Applications:** Text processing, log analysis, general substring search

### 7.2 Teddy Algorithm (from Hyperscan)
- **Category:** Multi-pattern SIMD matching
- **Key Insight:** Uses PSHUFB instruction for vectorized multi-pattern matching. Completely outperforms competition for short substrings. Core algorithm behind Intel's Hyperscan regex engine.
- **Reference:** https://github.com/jneem/teddy
- **Practical Applications:** Regex engines, network intrusion detection, content filtering

### 7.3 rfgrep (2025 — Rust SIMD grep)
- **Category:** SIMD file search tool
- **Key Insight:** Leverages Rust memchr crate for SIMD-optimized matching. Compares 16-64 bytes per instruction depending on ISA.
- **Reference:** https://dev.to/kherld/how-rfgrep-achieves-hardware-acceleration-simd-optimized-string-matching-1cdd

### TOOL REQUEST: SIMD string search library
- Build or wrap StringZilla/Teddy for a reusable fast string search module.

---

## 8. AI/ML ALGORITHMS

### 8.1 Flash Attention (v1/v2/v3)
- **Category:** Efficient Attention Computation
- **Time:** O(n^2) work but IO-optimal — O(n^2 d / M) HBM accesses (M = SRAM size)
- **Space:** O(n) instead of O(n^2)
- **Key Insight:** Tiling breaks attention matrix into blocks fitting in SRAM, reducing HBM accesses. FlashAttention-3 on Hopper GPUs adds async + low-precision for additional 1.5-2x boost.
- **Variants (2025):**
  - **Flash Sparse Attention:** 3.5x lower latency vs NSA kernels by changing loop order
  - **Flash Window Attention:** 3x speedup for vision transformers via feature-dimension tiling
  - **FLASH-D:** ASIC-optimized variant, 22.8% area reduction, 20.3% power reduction
  - **INT-FlashAttention:** Full INT8 with 72% faster inference, 82% less quantization error
- **Practical Applications:** All transformer inference and training

### 8.2 Multi-Head Latent Attention (MLA) — DeepSeek
- **Category:** KV-Cache Optimization
- **Key Insight:** Shared latent matrix among attention heads, projected back individually. Better performance than MQA/GQA with similar cache savings.
- **Practical Applications:** Long-context LLM inference, memory-efficient serving

### 8.3 Linear Attention & State Space Models
- **Category:** Sub-quadratic sequence modeling
- **Time:** O(n) instead of O(n^2)
- **Key Insight:** Replace softmax attention with linear kernels or SSM mechanisms. flash-linear-attention library provides efficient implementations. GDN integrated into Qwen3-Next. RWKV-7 uses generalized delta rule with vector-valued gating.
- **Reference:** https://github.com/fla-org/flash-linear-attention
- **Practical Applications:** Long-sequence modeling, real-time inference, edge deployment

### 8.4 Hybrid Transformer-SSM Architectures
- **Key Insight:** Combine attention layers with SSM layers for best of both worlds. Examples: Qwen3-Next, IBM Granite 4.0, NVIDIA Nemotron Nano 2. Non-transformer architectures have reached parity on key benchmarks.
- **Practical Applications:** Production LLM deployment, efficiency-focused inference

### 8.5 LLM Quantization Methods

#### GPTQ (GPU-focused)
- **Compression:** 4-8x size reduction
- **Speed:** 5x faster than GGUF on GPU with Marlin kernels
- **Key Insight:** One-shot weight quantization using approximate second-order information. Requires calibration dataset (2048+ samples, 2-4 hours for 7B model).

#### AWQ (Activation-Aware)
- **Compression:** 4x typical
- **Speed:** Fastest with Marlin-AWQ kernel (741 tok/s vs 461 baseline)
- **Key Insight:** Preserves small percentage of activation-important weights. No backpropagation needed. Faster quantization (10-30 min for 7B). Better generalization than GPTQ.

#### GGUF (CPU+GPU hybrid)
- **Compression:** Variable (Q4_K_M to Q8)
- **Key Insight:** Not a quantization algorithm but a format supporting mixed-precision storage. Best for CPU+GPU inference on consumer hardware. Fastest quantization (5-15 min for 7B).
- **Key 2026 Benchmark Finding:** Kernels matter more than algorithms — Marlin kernels give 2.6x speedup for GPTQ and 10.9x for AWQ.

### 8.6 RAG (Retrieval-Augmented Generation) Techniques

#### Chunking Strategies
- **Semantic chunking:** +9% recall over fixed-size
- **Proposition-based:** LLM extracts atomic claims, grouped into ~500-word chunks
- **Best practice:** Recursive/semantic chunkers with 10-20% overlap

#### Embedding Models (2025 Benchmarks)
- **Voyage-3-large:** Best MTEB scores, 9.74% better than OpenAI, 2.2x cheaper
- **OpenAI text-embedding-3-large:** Most battle-tested, configurable 256-3072 dims

#### Reranking
- **Impact:** +10-30% precision with 50-100ms latency cost
- **Best approach:** Hybrid BM25 + dense retrieval, then cross-encoder reranker
- **Top rerankers:** Cohere ReRank, ColBERT late interaction

#### Key 2025 Shift: Context Engineering
- RAG evolving from single retrieval algorithms to systematic "retrieval-context assembly-model reasoning" pipelines

### TOOL REQUEST: RAG pipeline benchmark
- Build evaluation harness for chunking strategies, embedding models, and rerankers across different document types and query patterns.

---

## SUMMARY OF TOOL REQUESTS FOR PROGRAMMING TEAMS

| Priority | Tool | Description |
|----------|------|-------------|
| HIGH | Sorting Benchmark | Compare pdqsort/glidesort/driftsort/SIMD across distributions |
| HIGH | Probabilistic Filter Library | Bloom/Cuckoo/Binary Fuse/Vacuum filter comparison |
| HIGH | RAG Pipeline Evaluator | Chunk/embed/rerank benchmark harness |
| MEDIUM | Raft Simulator | Leader election + log replication under network conditions |
| MEDIUM | SIMD String Search Wrapper | Wrap StringZilla/Teddy for reusable fast search |
| MEDIUM | Work-Stealing Scheduler Demo | Demonstrate Cilk/Tokio/Rayon patterns |
| LOW | Cache-Oblivious B-Tree | Reference implementation with vEB layout |
| LOW | HAMT/CHAMP Library | Persistent data structure with benchmark suite |

---

## KEY SOURCES

### Sorting
- [pdqsort](https://github.com/orlp/pdqsort)
- [Glidesort](https://github.com/orlp/glidesort)
- [SIMD Branchless Sorting (2026)](https://00f.net/2026/02/17/sorting-without-leaking-secrets/)
- [Glidesort Performance Analysis](https://github.com/Voultapher/sort-research-rs/blob/main/writeup/glidesort_perf_analysis/text.md)

### Data Structures
- [Skip Hash (PPoPP 2025)](https://arxiv.org/html/2410.07466v1)
- [HAMT in C](https://github.com/mkirchner/hamt)
- [Cache-Oblivious B-Trees](https://erikdemaine.org/papers/CacheObliviousBTrees_SICOMP/paper.pdf)
- [Lock-free concurrent data structures](https://github.com/jfuentes/concurrent-data-structures)

### Probabilistic Structures
- [Beyond Bloom (SIGMOD 2024 Tutorial)](https://people.iiis.tsinghua.edu.cn/~huanchen/publications/filter-tutorial-sigmod24.pdf)
- [Bloom Filters at Fifty (2025 Review)](https://www.mdpi.com/1999-4893/18/12/767)

### Concurrency
- [Tokio Scheduler](https://tokio.rs/blog/2019-10-scheduler)
- [BWoS for Tokio](https://github.com/tokio-rs/tokio/issues/5240)
- [Raft Protocol](https://raft.github.io/)
- [RaftOptima](https://www.emergentmind.com/topics/raft-consensus-algorithm)

### AI/ML
- [Flash Linear Attention](https://github.com/fla-org/flash-linear-attention)
- [vLLM Quantization Benchmarks](https://docs.jarvislabs.ai/blog/vllm-quantization-complete-guide-benchmarks)
- [RAG 2025 Year-End Review](https://ragflow.io/blog/rag-review-2025-from-rag-to-context)
- [Efficient Transformers Survey](https://arxiv.org/html/2510.05364v1)

### DP Optimization
- [Knuth's Optimization](https://cp-algorithms.com/dynamic_programming/knuth-optimization.html)
- [Divide and Conquer DP](https://jeffreyxiao.me/blog/divide-and-conquer-optimization/)

### String Matching
- [StringZilla](https://ashvardanian.com/posts/stringzilla/)
- [Teddy Algorithm](https://github.com/jneem/teddy)

### Graph Algorithms
- [Parallel Cluster-BFS](https://arxiv.org/abs/2410.17226)
- [Rigel Hyperbolic Embedding](https://sandlab.cs.uchicago.edu/rigel/documents/rigel.pdf)
