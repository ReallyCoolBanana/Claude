#!/usr/bin/env python3
"""
Step 3.1: Nightly Model Manager
================================
Handles loading/unloading the Nemotron-3-Nano-30B-A3B model via vLLM.

Download command (run once):
    huggingface-cli download nvidia/Nemotron-3-Nano-30B-A3B-Instruct-NVFP4 \
        --local-dir /nvme/models/nemotron-30b-a3b-nvfp4 \
        --local-dir-use-symlinks False

Expected download size: ~18GB
Expected NVMe load time: 30-45 seconds
Expected GPU memory usage: ~20GB
Expected inference speed: ~70 tok/s at batch=1

Verification after download:
    ls -la /nvme/models/nemotron-30b-a3b-nvfp4/
    # Should see: config.json, tokenizer.json, model*.safetensors, etc.
    du -sh /nvme/models/nemotron-30b-a3b-nvfp4/
    # Should show ~18GB
"""

import subprocess
import time
import json
import logging
import signal
import os
import sys
import requests
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("nightly.model_manager")


@dataclass
class ThermalStatus:
    gpu_temp_c: float
    gpu_util_pct: float
    mem_used_gb: float
    mem_total_gb: float
    timestamp: float = field(default_factory=time.time)


def get_gpu_status() -> ThermalStatus:
    """
    Query nvidia-smi for current GPU status.

    Expected output format from nvidia-smi:
        82, 45, 20480, 131072

    Returns ThermalStatus with current readings.

    Common errors:
        - nvidia-smi not found: NVIDIA drivers not installed
        - "No devices were found": GPU not detected, check PCIe
        - Permission denied: user not in 'video' group
    """
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"nvidia-smi failed: {result.stderr}")

        parts = result.stdout.strip().split(",")
        return ThermalStatus(
            gpu_temp_c=float(parts[0].strip()),
            gpu_util_pct=float(parts[1].strip()),
            mem_used_gb=float(parts[2].strip()) / 1024,
            mem_total_gb=float(parts[3].strip()) / 1024,
        )
    except FileNotFoundError:
        raise RuntimeError("nvidia-smi not found. Ensure NVIDIA drivers are installed.")
    except (subprocess.TimeoutExpired, IndexError, ValueError) as e:
        raise RuntimeError(f"Failed to parse GPU status: {e}")


class ThermalMonitor:
    """
    Monitors GPU temperature and pauses work if thresholds are exceeded.

    Usage:
        monitor = ThermalMonitor(max_temp=82, critical_temp=90, pause_duration=60)
        monitor.check_and_pause()  # Call periodically during heavy work

    Behavior:
        - Below max_temp: returns immediately
        - Between max_temp and critical_temp: sleeps for pause_duration, then rechecks
        - Above critical_temp: raises ThermalShutdownError

    Time estimate: ~0 seconds normally, up to pause_duration when throttling
    """

    def __init__(
        self,
        max_temp_c: float = 82,
        critical_temp_c: float = 90,
        pause_duration_s: int = 60,
    ):
        self.max_temp_c = max_temp_c
        self.critical_temp_c = critical_temp_c
        self.pause_duration_s = pause_duration_s
        self.pause_count = 0
        self.total_pause_time_s = 0

    def check_and_pause(self) -> ThermalStatus:
        """
        Check GPU temperature and pause if needed. Returns current status.

        Raises ThermalShutdownError if temperature exceeds critical threshold.
        """
        status = get_gpu_status()

        if status.gpu_temp_c >= self.critical_temp_c:
            logger.critical(
                "GPU temperature CRITICAL: %.1f C (threshold: %.1f C). "
                "Initiating emergency shutdown.",
                status.gpu_temp_c,
                self.critical_temp_c,
            )
            raise ThermalShutdownError(
                f"GPU at {status.gpu_temp_c}C exceeds critical threshold "
                f"{self.critical_temp_c}C"
            )

        while status.gpu_temp_c >= self.max_temp_c:
            self.pause_count += 1
            logger.warning(
                "GPU temperature %.1f C exceeds threshold %.1f C. "
                "Pausing for %d seconds (pause #%d).",
                status.gpu_temp_c,
                self.max_temp_c,
                self.pause_duration_s,
                self.pause_count,
            )
            time.sleep(self.pause_duration_s)
            self.total_pause_time_s += self.pause_duration_s
            status = get_gpu_status()

        return status

    def get_stats(self) -> dict:
        return {
            "thermal_pauses": self.pause_count,
            "total_pause_time_s": self.total_pause_time_s,
        }


class ThermalShutdownError(Exception):
    """Raised when GPU temperature exceeds critical threshold."""
    pass


class NightlyModelManager:
    """
    Manages the lifecycle of the nightly 30B model via vLLM.

    Lifecycle:
        1. Check GPU memory availability (~20GB needed, ~100GB available)
        2. Start vLLM server as subprocess
        3. Wait for health check endpoint
        4. Serve inference requests via OpenAI-compatible API
        5. Shutdown server and free memory

    The always-resident models (Nemotron Nano 4B + embedding) use ~4.5GB.
    The 30B model needs ~20GB. Total: ~24.5GB of 128GB.

    Usage:
        mgr = NightlyModelManager(config)
        mgr.load()           # Start vLLM, wait for ready
        mgr.health_check()   # Verify responding
        # ... do work via http://localhost:8001/v1/completions ...
        mgr.unload()         # Stop vLLM, free memory
    """

    def __init__(self, config: dict):
        self.model_path = config["model"]["local_path"]
        self.port = config["model"]["vllm_port"]
        self.host = config["model"]["vllm_host"]
        self.gpu_memory_utilization = config["model"]["gpu_memory_utilization"]
        self.tensor_parallel_size = config["model"]["tensor_parallel_size"]
        self.max_model_len = config["model"]["max_model_len"]
        self.max_load_time_s = config["model"]["max_load_time_s"]
        self.base_url = f"http://{self.host}:{self.port}"
        self._process: Optional[subprocess.Popen] = None
        self._load_time_s: float = 0
        self._thermal = ThermalMonitor(
            max_temp_c=config["thermal"]["max_gpu_temp_c"],
            critical_temp_c=config["thermal"]["critical_temp_c"],
            pause_duration_s=config["thermal"]["pause_duration_s"],
        )

    def pre_load_checks(self) -> dict:
        """
        Verify system is ready for model loading.

        Checks:
            1. Model files exist on NVMe
            2. GPU has sufficient free memory (>= 25GB recommended)
            3. GPU temperature is safe
            4. Port is not already in use

        Returns dict with check results.

        Expected output:
            {
                "model_exists": true,
                "model_size_gb": 18.2,
                "gpu_mem_free_gb": 103.5,
                "gpu_temp_c": 42,
                "port_available": true,
                "all_passed": true
            }

        Common errors:
            - Model directory missing: run the download command above
            - Insufficient GPU memory: another process using GPU, check nvidia-smi
            - Port in use: another vLLM instance running, kill it first
        """
        results = {}

        # Check model exists
        model_dir = Path(self.model_path)
        results["model_exists"] = model_dir.exists() and any(model_dir.iterdir())
        if results["model_exists"]:
            total_size = sum(f.stat().st_size for f in model_dir.rglob("*") if f.is_file())
            results["model_size_gb"] = round(total_size / (1024**3), 1)

        # Check GPU status
        status = get_gpu_status()
        results["gpu_mem_free_gb"] = round(status.mem_total_gb - status.mem_used_gb, 1)
        results["gpu_temp_c"] = status.gpu_temp_c

        # Check port availability
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(1)
            result = sock.connect_ex((self.host, self.port))
            results["port_available"] = result != 0
        finally:
            sock.close()

        results["all_passed"] = (
            results["model_exists"]
            and results["gpu_mem_free_gb"] >= 25
            and results["gpu_temp_c"] < self._thermal.max_temp_c
            and results["port_available"]
        )

        logger.info("Pre-load checks: %s", json.dumps(results))
        return results

    def load(self) -> dict:
        """
        Start the vLLM server with the nightly model.

        Process:
            1. Run pre-load checks
            2. Launch vLLM as subprocess
            3. Poll /health endpoint until ready (up to max_load_time_s)
            4. Run a test inference to verify

        Expected timeline:
            - vLLM process start: ~2 seconds
            - Model loading from NVMe: ~30-45 seconds
            - First inference (warmup): ~3 seconds
            - Total: ~35-50 seconds

        Returns:
            {
                "load_time_s": 42.3,
                "model": "nemotron-30b-a3b-nvfp4",
                "status": "ready",
                "gpu_mem_used_gb": 24.1
            }

        Common errors:
            - "CUDA out of memory": reduce gpu_memory_utilization or unload other models
            - "Address already in use": kill existing process on port 8001
            - Timeout: model files corrupted, re-download
        """
        checks = self.pre_load_checks()
        if not checks["all_passed"]:
            raise RuntimeError(f"Pre-load checks failed: {json.dumps(checks)}")

        logger.info("Starting vLLM server for %s on port %d", self.model_path, self.port)
        start_time = time.time()

        # Build vLLM command
        # Note: For NVFP4 quantized models, vLLM handles the quantization natively.
        # If using llama.cpp instead, see the alternative command below.
        cmd = [
            sys.executable, "-m", "vllm.entrypoints.openai.api_server",
            "--model", self.model_path,
            "--port", str(self.port),
            "--host", self.host,
            "--gpu-memory-utilization", str(self.gpu_memory_utilization),
            "--tensor-parallel-size", str(self.tensor_parallel_size),
            "--max-model-len", str(self.max_model_len),
            "--dtype", "auto",
            "--trust-remote-code",
            # Disable logging to stdout to avoid noise
            "--disable-log-requests",
        ]

        # Start vLLM as a subprocess
        log_path = Path("/nvme/logs/vllm-nightly.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w")

        self._process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,  # New process group for clean shutdown
        )

        logger.info("vLLM process started (PID %d). Waiting for ready...", self._process.pid)

        # Poll health endpoint
        health_url = f"{self.base_url}/health"
        deadline = start_time + self.max_load_time_s
        ready = False

        while time.time() < deadline:
            # Check process hasn't crashed
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"vLLM process exited with code {self._process.returncode}. "
                    f"Check {log_path} for details."
                )
            try:
                resp = requests.get(health_url, timeout=2)
                if resp.status_code == 200:
                    ready = True
                    break
            except requests.ConnectionError:
                pass
            time.sleep(2)

        if not ready:
            self.unload()
            raise RuntimeError(
                f"vLLM failed to start within {self.max_load_time_s}s. "
                f"Check {log_path} for details."
            )

        self._load_time_s = time.time() - start_time
        logger.info("vLLM ready in %.1f seconds", self._load_time_s)

        # Verify with a test inference
        self._warmup_inference()

        status = get_gpu_status()
        result = {
            "load_time_s": round(self._load_time_s, 1),
            "model": Path(self.model_path).name,
            "status": "ready",
            "gpu_mem_used_gb": round(status.mem_used_gb, 1),
        }
        logger.info("Model loaded: %s", json.dumps(result))
        return result

    def _warmup_inference(self):
        """
        Run a single test inference to warm up the model.

        Expected response time: 2-4 seconds for warmup, <1s subsequent.
        """
        try:
            resp = requests.post(
                f"{self.base_url}/v1/completions",
                json={
                    "model": self.model_path,
                    "prompt": "Hello, this is a warmup test.",
                    "max_tokens": 10,
                    "temperature": 0,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            logger.info(
                "Warmup inference OK. Tokens generated: %d",
                data["usage"]["completion_tokens"],
            )
        except Exception as e:
            logger.warning("Warmup inference failed (non-fatal): %s", e)

    def health_check(self) -> bool:
        """
        Check if the vLLM server is responding.

        Returns True if healthy, False otherwise.
        """
        if self._process is None or self._process.poll() is not None:
            return False
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=5)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    def generate(
        self,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.3,
        stop: Optional[list] = None,
    ) -> str:
        """
        Generate text using the nightly model.

        This wraps the vLLM OpenAI-compatible API.

        Args:
            prompt: The input prompt
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0 = greedy)
            stop: Optional stop sequences

        Returns:
            Generated text string

        Expected performance: ~70 tok/s at batch=1, so 512 tokens ~ 7.3 seconds

        Common errors:
            - ConnectionError: vLLM crashed, check health_check()
            - Timeout: prompt too long, reduce or increase timeout
        """
        # Thermal check before inference
        self._thermal.check_and_pause()

        payload = {
            "model": self.model_path,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if stop:
            payload["stop"] = stop

        resp = requests.post(
            f"{self.base_url}/v1/completions",
            json=payload,
            timeout=120,  # 512 tokens at 70 tok/s = ~7s, generous timeout
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["text"]

    def generate_chat(
        self,
        messages: list,
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        """
        Chat-style generation using the nightly model.

        Args:
            messages: List of {"role": "...", "content": "..."} dicts
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Assistant's response text
        """
        self._thermal.check_and_pause()

        resp = requests.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model_path,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def unload(self):
        """
        Stop the vLLM server and free GPU memory.

        Process:
            1. Send SIGTERM to vLLM process group
            2. Wait up to 15 seconds for graceful shutdown
            3. Send SIGKILL if still running
            4. Verify GPU memory is freed

        Expected timeline: 2-5 seconds for graceful shutdown

        After unloading, GPU memory should return to ~4.5GB
        (Nemotron Nano 4B + embedding model).
        """
        if self._process is None:
            logger.info("No vLLM process to unload.")
            return

        pid = self._process.pid
        logger.info("Stopping vLLM server (PID %d)...", pid)

        try:
            # Send SIGTERM to process group
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            logger.info("vLLM process already exited.")
            self._process = None
            return

        # Wait for graceful shutdown
        try:
            self._process.wait(timeout=15)
            logger.info("vLLM server stopped gracefully.")
        except subprocess.TimeoutExpired:
            logger.warning("vLLM did not stop gracefully. Sending SIGKILL.")
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
                self._process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass

        self._process = None

        # Verify memory freed
        time.sleep(2)  # Brief pause for GPU memory reclamation
        status = get_gpu_status()
        logger.info(
            "vLLM unloaded. GPU memory now: %.1f / %.1f GB",
            status.mem_used_gb,
            status.mem_total_gb,
        )

    def get_stats(self) -> dict:
        """Return model manager statistics."""
        return {
            "load_time_s": self._load_time_s,
            "is_loaded": self.health_check(),
            "thermal": self._thermal.get_stats(),
        }

    def benchmark(self) -> dict:
        """
        Run a quick benchmark of the loaded model.

        Tests:
            1. Short generation (32 tokens) - latency test
            2. Medium generation (256 tokens) - throughput test
            3. Long generation (512 tokens) - sustained throughput

        Expected results on DGX Spark with 30B-A3B:
            {
                "short_32tok_s": 1.2,
                "medium_256tok_s": 4.1,
                "long_512tok_s": 7.8,
                "tokens_per_second": 68.5,
                "gpu_temp_c": 65
            }

        Time estimate: ~15 seconds total
        """
        results = {}
        prompt = (
            "Explain the relationship between memory consolidation "
            "during sleep and long-term knowledge retention."
        )

        for label, n_tokens in [("short_32tok", 32), ("medium_256tok", 256), ("long_512tok", 512)]:
            start = time.time()
            resp = requests.post(
                f"{self.base_url}/v1/completions",
                json={
                    "model": self.model_path,
                    "prompt": prompt,
                    "max_tokens": n_tokens,
                    "temperature": 0,
                },
                timeout=60,
            )
            elapsed = time.time() - start
            data = resp.json()
            actual_tokens = data["usage"]["completion_tokens"]
            results[f"{label}_s"] = round(elapsed, 1)
            if label == "long_512tok":
                results["tokens_per_second"] = round(actual_tokens / elapsed, 1)

        status = get_gpu_status()
        results["gpu_temp_c"] = status.gpu_temp_c
        results["gpu_mem_used_gb"] = round(status.mem_used_gb, 1)

        logger.info("Benchmark results: %s", json.dumps(results))
        return results


# ---------------------------------------------------------------------------
# Alternative: llama.cpp server (if vLLM is not available)
# ---------------------------------------------------------------------------
# If you prefer llama.cpp over vLLM, use this command instead:
#
#   ./llama-server \
#       --model /nvme/models/nemotron-30b-a3b-nvfp4/model.gguf \
#       --port 8001 \
#       --host 127.0.0.1 \
#       --n-gpu-layers 99 \
#       --ctx-size 8192 \
#       --threads 8 \
#       --batch-size 512
#
# The API is OpenAI-compatible, so the NightlyModelManager works with
# either backend. Just change the launch command in load().
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    """
    Standalone test script.

    Usage:
        python model_manager.py download   # Download model from HuggingFace
        python model_manager.py load       # Load model and run benchmark
        python model_manager.py check      # Check GPU status only

    Expected output for 'check':
        GPU Status:
          Temperature: 42.0 C
          Utilization: 5%
          Memory: 4.5 / 128.0 GB
          Memory free: 123.5 GB
    """
    import yaml

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config_path = Path(__file__).parent.parent / "config" / "nightly_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    if len(sys.argv) < 2:
        print("Usage: python model_manager.py [download|load|check]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "download":
        repo_id = config["model"]["repo_id"]
        local_path = config["model"]["local_path"]
        print(f"Downloading {repo_id} to {local_path}...")
        print("Run this command:")
        print(f"  huggingface-cli download {repo_id} "
              f"--local-dir {local_path} "
              f"--local-dir-use-symlinks False")

    elif cmd == "check":
        status = get_gpu_status()
        print(f"GPU Status:")
        print(f"  Temperature: {status.gpu_temp_c} C")
        print(f"  Utilization: {status.gpu_util_pct}%")
        print(f"  Memory: {status.mem_used_gb:.1f} / {status.mem_total_gb:.1f} GB")
        print(f"  Memory free: {status.mem_total_gb - status.mem_used_gb:.1f} GB")

    elif cmd == "load":
        mgr = NightlyModelManager(config)
        try:
            result = mgr.load()
            print(f"Model loaded: {json.dumps(result, indent=2)}")
            print("\nRunning benchmark...")
            bench = mgr.benchmark()
            print(f"Benchmark: {json.dumps(bench, indent=2)}")
            input("Press Enter to unload model...")
        finally:
            mgr.unload()
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
