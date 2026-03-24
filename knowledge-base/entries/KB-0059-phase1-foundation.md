---
id: KB-0059
date: 2026-03-23
team: solo-research
role: architect
category: methodology
tags: [nemo-maxxing, dgx-spark, neo4j, lancedb, llama-cpp, mcp, setup-guide, nemotron, thermal-monitoring]
status: validated
confidence: high
builds_on: [KB-0058]
---
# Phase 1: Foundation — DGX Spark, Neo4j, LanceDB, Controller & MCP Server Setup

**Target hardware:** NVIDIA DGX Spark (128GB unified, 4TB NVMe, Blackwell GB10)
**Time estimate:** Weeks 1-3 (~40-60 hours)
**Prerequisites:** DGX Spark powered on with Ubuntu 22.04+, network access, sudo

---

## Architecture (Phase 1 Scope)

```
┌────────────────────────────────────────────────┐
│              MCP Server (FastMCP)                │
│   /retrieve  /ingest  /status                    │
├────────────────────────────────────────────────┤
│  ┌──────────────┐     ┌──────────────┐          │
│  │  Controller   │     │  Embedding   │          │
│  │  Nano 4B      │     │  embed-1b-v2 │          │
│  │  llama.cpp    │     │  llama.cpp   │          │
│  │  port 8081    │     │  port 8082   │          │
│  └──────┬───────┘     └──────┬───────┘          │
│         └──────────┬─────────┘                   │
│  ┌─────────────────▼──────────────────────────┐ │
│  │  Neo4j (Bolt :7687)  │  LanceDB on NVMe   │ │
│  │  6-8GB heap          │  /mnt/nvme/lancedb  │ │
│  │  14GB page cache     │  mmap + huge pages  │ │
│  └──────────────────────┴─────────────────────┘ │
├────────────────────────────────────────────────┤
│  Thermal Monitor Daemon (systemd)                │
│  GREEN < 75°C │ YELLOW < 90°C │ RED ≥ 90°C      │
└────────────────────────────────────────────────┘
```

---

## Step 1.1: Hardware Verification

### 1.1.1 Verify GPU and Memory

```bash
# Confirm Blackwell GB10 GPU
nvidia-smi

# Expected output should show:
#   GPU Name: NVIDIA GB10
#   Memory: 128GB (unified with CPU)

# Check CUDA version
nvcc --version
# Expected: CUDA 12.x+

# Verify NVMe storage
lsblk | grep nvme
sudo smartctl -a /dev/nvme0n1 | head -20
df -h /mnt/nvme  # or wherever mounted
```

### 1.1.2 Set Up NVMe Mount (if not already mounted)

```bash
# Format and mount NVMe for data storage
sudo mkdir -p /mnt/nvme
# If the NVMe is a raw device:
sudo mkfs.ext4 /dev/nvme0n1p1
sudo mount /dev/nvme0n1p1 /mnt/nvme
echo '/dev/nvme0n1p1 /mnt/nvme ext4 defaults,noatime 0 2' | sudo tee -a /etc/fstab

# Create directory structure
sudo mkdir -p /mnt/nvme/{lancedb,models,backups,logs}
sudo chown -R $USER:$USER /mnt/nvme
```

### 1.1.3 Enable Huge Pages (for LanceDB mmap performance)

```bash
# Check current huge page settings
cat /proc/meminfo | grep Huge

# Allocate 4096 x 2MB huge pages = 8GB for LanceDB mmap
echo 4096 | sudo tee /proc/sys/vm/nr_hugepages

# Make persistent across reboots
echo 'vm.nr_hugepages=4096' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### 1.1.4 Unified Memory Verification

```bash
# Verify unified memory (CPU+GPU shared)
python3 -c "
import subprocess
result = subprocess.run(['nvidia-smi', '--query-gpu=memory.total', '--format=csv,noheader'], capture_output=True, text=True)
print(f'GPU reports: {result.stdout.strip()}')
print('Note: On DGX Spark, this is shared with CPU (unified memory)')
"
```

---

## Step 1.2: Thermal Monitoring Daemon

### 1.2.1 Create the Monitor Script

```bash
mkdir -p /opt/nemo/thermal
cat > /opt/nemo/thermal/thermal_monitor.py << 'PYEOF'
#!/usr/bin/env python3
"""Thermal monitoring daemon for DGX Spark.

Zones:
  GREEN  < 75°C  — normal operation
  YELLOW < 90°C  — reduce workload, log warning
  RED    >= 90°C — graceful shutdown of inference, alert
"""
import subprocess
import time
import json
import logging
from pathlib import Path

LOG_DIR = Path("/mnt/nvme/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "thermal.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

POLL_INTERVAL = 10  # seconds
YELLOW_THRESHOLD = 75
RED_THRESHOLD = 90
SHUTDOWN_FILE = Path("/tmp/nemo_thermal_shutdown")

def get_gpu_temp():
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
        capture_output=True, text=True
    )
    return int(result.stdout.strip())

def get_zone(temp):
    if temp >= RED_THRESHOLD:
        return "RED"
    elif temp >= YELLOW_THRESHOLD:
        return "YELLOW"
    return "GREEN"

def main():
    logging.info("Thermal monitor started")
    last_zone = None
    while True:
        try:
            temp = get_gpu_temp()
            zone = get_zone(temp)

            if zone != last_zone:
                logging.warning(f"Zone change: {last_zone} -> {zone} (temp={temp}°C)")
                last_zone = zone

            if zone == "RED":
                logging.critical(f"RED zone at {temp}°C — writing shutdown signal")
                SHUTDOWN_FILE.touch()
            elif SHUTDOWN_FILE.exists():
                SHUTDOWN_FILE.unlink()

            # Write current state for other services to read
            state = {"temp": temp, "zone": zone, "timestamp": time.time()}
            Path("/tmp/nemo_thermal_state.json").write_text(json.dumps(state))

        except Exception as e:
            logging.error(f"Monitor error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
PYEOF
chmod +x /opt/nemo/thermal/thermal_monitor.py
```

### 1.2.2 Create systemd Service

```bash
sudo cat > /etc/systemd/system/nemo-thermal.service << 'EOF'
[Unit]
Description=Nemo-Maxxing Thermal Monitor
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/nemo/thermal/thermal_monitor.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nemo-thermal
sudo systemctl start nemo-thermal

# Verify it's running
sudo systemctl status nemo-thermal
cat /tmp/nemo_thermal_state.json
```

---

## Step 1.3: Neo4j Installation & Configuration

### 1.3.1 Install Neo4j Community Edition 5.x

```bash
# Add Neo4j repository
wget -O - https://debian.neo4j.com/neotechnology.gpg.key | sudo gpg --dearmor -o /usr/share/keyrings/neo4j.gpg
echo 'deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable latest' | sudo tee /etc/apt/sources.list.d/neo4j.list

sudo apt update
sudo apt install -y neo4j

# Verify installation
neo4j --version
```

### 1.3.2 Configure Memory Settings

```bash
# Edit Neo4j configuration
sudo tee /etc/neo4j/neo4j.conf.d/memory.conf << 'EOF'
# JVM Heap: 6-8GB for 5M nodes / 20M relationships
server.memory.heap.initial_size=6g
server.memory.heap.max_size=8g

# Page cache: 14GB — keeps full graph in RAM
server.memory.pagecache.size=14g

# Transaction memory limit
db.memory.transaction.total.max=2g

# Bolt connector (localhost only for security)
server.bolt.listen_address=127.0.0.1:7687
server.http.listen_address=127.0.0.1:7474

# Disable browser (not needed for API-only access)
browser.post_connect_cmd=

# Data directory on NVMe for persistence
server.directories.data=/mnt/nvme/neo4j/data
server.directories.logs=/mnt/nvme/neo4j/logs
EOF

# Create data directories
sudo mkdir -p /mnt/nvme/neo4j/{data,logs}
sudo chown -R neo4j:neo4j /mnt/nvme/neo4j

# Start Neo4j
sudo systemctl enable neo4j
sudo systemctl start neo4j

# Wait for startup then verify
sleep 10
cypher-shell -u neo4j -p neo4j "RETURN 1 AS test"
# Change default password on first login
cypher-shell -u neo4j -p neo4j "ALTER CURRENT USER SET PASSWORD FROM 'neo4j' TO 'your-secure-password'"
```

### 1.3.3 Verify Neo4j Memory Allocation

```bash
# Check JVM is using expected heap
ps aux | grep neo4j | grep -oP 'Xmx\S+'
# Expected: Xmx8g

# Check page cache allocation
cypher-shell -u neo4j -p 'your-secure-password' \
  "CALL dbms.listConfig() YIELD name, value WHERE name CONTAINS 'pagecache' RETURN name, value"
```

---

## Step 1.4: LanceDB on NVMe

### 1.4.1 Install LanceDB

```bash
# Create Python virtual environment
python3 -m venv /opt/nemo/venv
source /opt/nemo/venv/bin/activate

pip install lancedb pyarrow numpy
```

### 1.4.2 Initialize LanceDB with NVMe Storage

```python
# /opt/nemo/scripts/init_lancedb.py
import lancedb
import numpy as np
import pyarrow as pa
import time

DB_PATH = "/mnt/nvme/lancedb/nemo_memory"

# Connect — LanceDB creates the directory if it doesn't exist
db = lancedb.connect(DB_PATH)

# Create a test table with 1024-dim vectors (matches embed-1b-v2 output)
EMBED_DIM = 1024
N_TEST = 10000

print(f"Creating test table with {N_TEST} vectors of dim {EMBED_DIM}...")
data = pa.table({
    "id": list(range(N_TEST)),
    "vector": [np.random.randn(EMBED_DIM).astype(np.float32).tolist() for _ in range(N_TEST)],
    "content": [f"Test memory content {i}" for i in range(N_TEST)],
    "memory_type": [["temporary", "operational", "facts", "episodic", "meta"][i % 5] for i in range(N_TEST)],
})

table = db.create_table("memories", data, mode="overwrite")

# Benchmark random vector query
query_vec = np.random.randn(EMBED_DIM).astype(np.float32).tolist()

times = []
for _ in range(100):
    start = time.perf_counter()
    results = table.search(query_vec).limit(10).to_list()
    elapsed = (time.perf_counter() - start) * 1000
    times.append(elapsed)

avg = sum(times) / len(times)
p99 = sorted(times)[98]
print(f"Query latency: avg={avg:.2f}ms, p99={p99:.2f}ms")
print(f"Target: <5ms avg on NVMe — {'PASS' if avg < 5 else 'FAIL'}")
```

```bash
python /opt/nemo/scripts/init_lancedb.py
```

---

## Step 1.5: Controller Model (Nemotron Nano 4B)

### 1.5.1 Build llama.cpp with CUDA Support

```bash
cd /opt/nemo
git clone https://github.com/ggerganov/llama.cpp.git
cd llama.cpp
mkdir build && cd build
cmake .. -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release -j$(nproc)

# Verify CUDA acceleration
./bin/llama-cli --version
```

### 1.5.2 Download Nemotron Nano 4B Q4_K_M

```bash
pip install huggingface-hub

# Download the quantized model
huggingface-cli download \
  nvidia/Nemotron-3-Nano-4B-Instruct-GGUF \
  nemotron-3-nano-4b-instruct.Q4_K_M.gguf \
  --local-dir /mnt/nvme/models/nano-4b/
```

### 1.5.3 Start Controller Model Server

```bash
# Run llama.cpp server for the controller
/opt/nemo/llama.cpp/build/bin/llama-server \
  --model /mnt/nvme/models/nano-4b/nemotron-3-nano-4b-instruct.Q4_K_M.gguf \
  --host 127.0.0.1 \
  --port 8081 \
  --n-gpu-layers 99 \
  --ctx-size 4096 \
  --parallel 4 \
  --threads $(nproc) &

# Wait for server to start, then test
sleep 5
curl -s http://127.0.0.1:8081/health
# Expected: {"status":"ok"}

curl -s http://127.0.0.1:8081/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nano-4b",
    "messages": [{"role": "user", "content": "Classify this query intent: What happened yesterday?"}],
    "max_tokens": 50
  }'
```

---

## Step 1.6: Embedding Model (llama-nemotron-embed-1b-v2)

### 1.6.1 Download Embedding Model

```bash
huggingface-cli download \
  nvidia/llama-nemotron-embed-1b-v2 \
  --local-dir /mnt/nvme/models/embed-1b-v2/
```

### 1.6.2 Start Embedding Server

```bash
# llama.cpp supports embedding mode
/opt/nemo/llama.cpp/build/bin/llama-server \
  --model /mnt/nvme/models/embed-1b-v2/llama-nemotron-embed-1b-v2.gguf \
  --host 127.0.0.1 \
  --port 8082 \
  --embedding \
  --n-gpu-layers 99 \
  --ctx-size 8192 \
  --threads $(nproc) &

# Test embedding generation
sleep 3
curl -s http://127.0.0.1:8082/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "embed-1b-v2",
    "input": "What happened at the meeting yesterday?"
  }' | python3 -c "
import sys, json
resp = json.load(sys.stdin)
vec = resp['data'][0]['embedding']
print(f'Embedding dim: {len(vec)}')
print(f'First 5 values: {vec[:5]}')
"
```

---

## Step 1.7: Basic MCP Server

### 1.7.1 Install FastMCP

```bash
source /opt/nemo/venv/bin/activate
pip install fastmcp httpx neo4j
```

### 1.7.2 Create MCP Server

```python
# /opt/nemo/mcp_server.py
"""Nemo-Maxxing MCP Server — Phase 1 (Foundation)

Endpoints:
  memory_retrieve — Two-phase graph+vector search (basic version)
  memory_ingest   — Add new memories with embeddings
  memory_status   — System health check
"""
import asyncio
import json
import time
from pathlib import Path

import httpx
import lancedb
import numpy as np
from fastmcp import FastMCP
from neo4j import AsyncGraphDatabase

# Configuration
NEO4J_URI = "bolt://127.0.0.1:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "your-secure-password"
LANCEDB_PATH = "/mnt/nvme/lancedb/nemo_memory"
EMBED_URL = "http://127.0.0.1:8082/v1/embeddings"
CONTROLLER_URL = "http://127.0.0.1:8081/v1/chat/completions"

mcp = FastMCP("nemo-memory")

# Lazy-initialized connections
_neo4j_driver = None
_lancedb = None
_http_client = None


async def get_neo4j():
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = AsyncGraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _neo4j_driver


async def get_lancedb():
    global _lancedb
    if _lancedb is None:
        _lancedb = lancedb.connect(LANCEDB_PATH)
    return _lancedb


async def get_http():
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


async def embed_text(text: str) -> list[float]:
    """Get embedding vector for text."""
    client = await get_http()
    resp = await client.post(EMBED_URL, json={
        "model": "embed-1b-v2",
        "input": text
    })
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


@mcp.tool()
async def memory_retrieve(
    query: str,
    memory_types: list[str] | None = None,
    max_results: int = 10,
    min_relevance: float = 0.5,
    max_hops: int = 3
) -> dict:
    """Retrieve relevant memories using two-phase graph+vector search.

    Phase 1: Find candidate nodes via Neo4j graph traversal.
    Phase 2: Fetch content and vectors from LanceDB, rerank by similarity.
    """
    start = time.perf_counter()

    # Get query embedding
    query_vec = await embed_text(query)

    # Phase 2 (basic — direct vector search in Phase 1, graph traversal added in Phase 2)
    db = await get_lancedb()
    table = db.open_table("memories")

    results = table.search(query_vec).limit(max_results).to_list()

    # Filter by memory_type if specified
    if memory_types:
        results = [r for r in results if r.get("memory_type") in memory_types]

    # Filter by relevance (distance → similarity)
    filtered = []
    for r in results:
        # LanceDB returns _distance (L2); convert to similarity score
        similarity = 1.0 / (1.0 + r.get("_distance", 0))
        if similarity >= min_relevance:
            filtered.append({
                "memory_id": str(r.get("id", "")),
                "content": r.get("content", ""),
                "memory_type": r.get("memory_type", ""),
                "relevance": round(similarity, 4),
            })

    elapsed_ms = (time.perf_counter() - start) * 1000

    return {
        "retrieval_id": f"ret-{int(time.time() * 1000)}",
        "query": query,
        "results": filtered[:max_results],
        "timing_ms": round(elapsed_ms, 2),
        "phase": "vector-only (Phase 1 foundation)"
    }


@mcp.tool()
async def memory_ingest(
    content: str,
    memory_type: str,
    source: str = "unknown",
    confidence: float = 0.8,
    ttl_hours: int | None = None
) -> dict:
    """Add new information to the memory system.

    Generates embedding, stores in LanceDB, creates Neo4j node.
    """
    start = time.perf_counter()

    # Generate embedding
    vector = await embed_text(content)

    # Store in LanceDB
    db = await get_lancedb()
    table = db.open_table("memories")

    memory_id = f"mem-{int(time.time() * 1000)}"
    table.add([{
        "id": memory_id,
        "vector": vector,
        "content": content,
        "memory_type": memory_type,
    }])

    # Create Neo4j node
    driver = await get_neo4j()
    async with driver.session() as session:
        await session.run(
            """
            CREATE (m:Memory {
                id: $id,
                memory_type: $memory_type,
                content_preview: $preview,
                source: $source,
                confidence: $confidence,
                created_at: datetime(),
                ttl_hours: $ttl_hours
            })
            """,
            id=memory_id,
            memory_type=memory_type,
            preview=content[:200],
            source=source,
            confidence=confidence,
            ttl_hours=ttl_hours,
        )

    elapsed_ms = (time.perf_counter() - start) * 1000

    return {
        "memory_id": memory_id,
        "memory_type": memory_type,
        "embedding_dim": len(vector),
        "timing_ms": round(elapsed_ms, 2),
    }


@mcp.tool()
async def memory_status() -> dict:
    """Return system health and operational state."""
    status = {
        "timestamp": time.time(),
        "components": {}
    }

    # Check Neo4j
    try:
        driver = await get_neo4j()
        async with driver.session() as session:
            result = await session.run("RETURN 1 AS test")
            await result.single()
        status["components"]["neo4j"] = {"status": "healthy", "uri": NEO4J_URI}
    except Exception as e:
        status["components"]["neo4j"] = {"status": "error", "error": str(e)}

    # Check LanceDB
    try:
        db = await get_lancedb()
        table = db.open_table("memories")
        count = table.count_rows()
        status["components"]["lancedb"] = {"status": "healthy", "row_count": count, "path": LANCEDB_PATH}
    except Exception as e:
        status["components"]["lancedb"] = {"status": "error", "error": str(e)}

    # Check embedding model
    try:
        client = await get_http()
        resp = await client.get("http://127.0.0.1:8082/health")
        status["components"]["embedding"] = {"status": "healthy", "port": 8082}
    except Exception as e:
        status["components"]["embedding"] = {"status": "error", "error": str(e)}

    # Check controller model
    try:
        client = await get_http()
        resp = await client.get("http://127.0.0.1:8081/health")
        status["components"]["controller"] = {"status": "healthy", "port": 8081}
    except Exception as e:
        status["components"]["controller"] = {"status": "error", "error": str(e)}

    # Check thermal state
    thermal_path = Path("/tmp/nemo_thermal_state.json")
    if thermal_path.exists():
        thermal = json.loads(thermal_path.read_text())
        status["components"]["thermal"] = thermal
    else:
        status["components"]["thermal"] = {"status": "unknown", "note": "thermal monitor not running"}

    # Overall status
    all_healthy = all(
        c.get("status") == "healthy"
        for k, c in status["components"].items()
        if k != "thermal"
    )
    status["overall"] = "healthy" if all_healthy else "degraded"

    return status


if __name__ == "__main__":
    mcp.run()
```

### 1.7.3 Create systemd Service for MCP Server

```bash
sudo cat > /etc/systemd/system/nemo-mcp.service << 'EOF'
[Unit]
Description=Nemo-Maxxing MCP Server
After=neo4j.service nemo-thermal.service
Requires=neo4j.service

[Service]
Type=simple
ExecStart=/opt/nemo/venv/bin/python /opt/nemo/mcp_server.py
Restart=always
RestartSec=5
User=nemo
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable nemo-mcp
sudo systemctl start nemo-mcp
```

---

## Step 1.8: Verification Checklist

Run each verification command and confirm the expected output:

### 1. GPU and Memory
```bash
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
# Expected: NVIDIA GB10, 131072 MiB (or similar for 128GB)
```

### 2. Neo4j
```bash
cypher-shell -u neo4j -p 'your-secure-password' "RETURN 'Neo4j OK' AS status"
# Expected: "Neo4j OK"
```

### 3. LanceDB Benchmark
```bash
python /opt/nemo/scripts/init_lancedb.py
# Expected: avg < 5ms
```

### 4. Controller Model
```bash
curl -s http://127.0.0.1:8081/health | python3 -m json.tool
# Expected: {"status": "ok"}
```

### 5. Embedding Model
```bash
curl -s http://127.0.0.1:8082/health | python3 -m json.tool
# Expected: {"status": "ok"}
```

### 6. MCP Server
```bash
# Test status endpoint (via stdio or HTTP depending on FastMCP transport)
curl -s http://127.0.0.1:8080/tools/memory_status | python3 -m json.tool
# Or test via FastMCP CLI tools
```

### 7. Thermal Monitor
```bash
cat /tmp/nemo_thermal_state.json | python3 -m json.tool
# Expected: {"temp": <value>, "zone": "GREEN", "timestamp": <value>}
```

---

## Memory Budget After Phase 1

| Component | RAM (GB) | Status |
|-----------|----------|--------|
| OS + services | 5-8 | Running |
| Neo4j (heap + page cache) | 20-22 | Running |
| Controller (Nano 4B Q4_K_M) | 2-3 | Running on port 8081 |
| Embedding (embed-1b-v2) | 1-2 | Running on port 8082 |
| MCP server | 0.1-0.3 | Running |
| Thermal monitor | <0.1 | Running |
| **Free** | **93-100** | Available for Phase 2+ |

---

## Troubleshooting

### Neo4j won't start
- Check logs: `journalctl -u neo4j -n 50`
- Verify Java 17+: `java -version`
- Ensure NVMe data dir permissions: `ls -la /mnt/nvme/neo4j/`

### llama.cpp CUDA errors
- Verify CUDA toolkit: `nvcc --version`
- Rebuild with correct CUDA path: `cmake .. -DGGML_CUDA=ON -DCMAKE_CUDA_COMPILER=/usr/local/cuda/bin/nvcc`
- Check GPU detection: `./bin/llama-cli --list-devices`

### LanceDB slow queries (>5ms)
- Ensure NVMe is mounted with `noatime`: `mount | grep nvme`
- Verify huge pages: `cat /proc/meminfo | grep HugePages_Total`
- Check disk health: `sudo smartctl -a /dev/nvme0n1`

### Thermal monitor not updating
- Check service: `systemctl status nemo-thermal`
- Run manually to debug: `python3 /opt/nemo/thermal/thermal_monitor.py`
- Verify nvidia-smi access from the service user

---

## Next Steps

After all 7 verification checks pass, proceed to **Phase 2: Graph + Vector Integration** (KB-0060, Phase 2 section). Phase 2 adds:
- Neo4j schema with 5 memory types and 4 typed edge relationships
- Path pointer storage linking Neo4j edges to LanceDB vectors
- Two-phase retrieval pipeline (graph → vector → rerank)
- Reranker with Reciprocal Rank Fusion
- /score MCP endpoint

## Sources

- [KB-0055](KB-0055.md) — Neo4j memory footprint and configuration analysis
- [KB-0056](KB-0056.md) — LanceDB + cuVS benchmarks and integration paths
- [KB-0057](KB-0057.md) — DGX Spark memory budget and thermal constraints
- [KB-0058](KB-0058.md) — Master architecture analysis and implementation roadmap
