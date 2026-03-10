"""
Sonnet Research API - Queue Manager

Thread-safe priority queue for dispatching research requests to the
SonnetResearchClient with configurable concurrency and rate limiting.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from client import QueryError, QueryResult, SonnetResearchClient
from config import MAX_QUEUE_SIZE, RATE_LIMIT_REQUESTS_PER_MINUTE


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class RequestStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(order=True)
class _PrioritizedRequest:
    """Wrapper that makes requests sortable by priority (higher = first)."""
    priority: int  # negated so higher values sort first in a min-heap
    seq: int  # tie-breaker to preserve insertion order
    request: Any = field(compare=False)


@dataclass
class ResearchRequest:
    request_id: str
    prompt: str
    system_prompt: str | None = None
    priority: int = 0
    callback: Callable[[str, QueryResult | QueryError], None] | None = None


@dataclass
class RequestRecord:
    """Internal bookkeeping for a single request."""
    request: ResearchRequest
    status: RequestStatus = RequestStatus.PENDING
    result: QueryResult | QueryError | None = None


# ---------------------------------------------------------------------------
# Queue Manager
# ---------------------------------------------------------------------------

class RequestQueue:
    """Thread-safe priority queue with rate-limited, concurrent processing."""

    def __init__(
        self,
        max_queue_size: int = MAX_QUEUE_SIZE,
        requests_per_minute: int = RATE_LIMIT_REQUESTS_PER_MINUTE,
    ) -> None:
        self._queue: queue.PriorityQueue[_PrioritizedRequest] = queue.PriorityQueue(
            maxsize=max_queue_size,
        )
        self._records: dict[str, RequestRecord] = {}
        self._lock = threading.Lock()
        self._seq = 0  # monotonic counter for tie-breaking

        # Rate limiting
        self._requests_per_minute = requests_per_minute
        self._min_interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0
        self._last_request_time = 0.0
        self._rate_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        request_id: str,
        prompt: str,
        system_prompt: str | None = None,
        priority: int = 0,
        callback: Callable[[str, QueryResult | QueryError], None] | None = None,
    ) -> bool:
        """Add a research request to the queue.

        Returns ``True`` if successfully enqueued, ``False`` if the queue is
        full or the *request_id* is a duplicate.
        """
        with self._lock:
            if request_id in self._records:
                return False

            req = ResearchRequest(
                request_id=request_id,
                prompt=prompt,
                system_prompt=system_prompt,
                priority=priority,
                callback=callback,
            )
            record = RequestRecord(request=req)

            try:
                self._queue.put_nowait(
                    _PrioritizedRequest(
                        priority=-priority,  # negate so higher priority pops first
                        seq=self._seq,
                        request=req,
                    )
                )
            except queue.Full:
                return False

            self._records[request_id] = record
            self._seq += 1
            return True

    def process_queue(
        self,
        client: SonnetResearchClient,
        max_concurrent: int = 3,
    ) -> None:
        """Process all queued requests using *max_concurrent* worker threads.

        Blocks until every request currently in the queue has been processed.
        """
        workers: list[threading.Thread] = []
        stop_event = threading.Event()

        for _ in range(max_concurrent):
            t = threading.Thread(
                target=self._worker,
                args=(client, stop_event),
                daemon=True,
            )
            t.start()
            workers.append(t)

        # Wait for the queue to drain.
        self._queue.join()
        stop_event.set()

        for t in workers:
            t.join(timeout=5)

    def get_status(self, request_id: str) -> dict[str, Any] | None:
        """Return the current status (and result, if complete) of a request.

        Returns ``None`` if *request_id* is unknown.
        """
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return None
            info: dict[str, Any] = {"status": record.status.value}
            if record.result is not None:
                info["result"] = record.result
            return info

    def get_all_results(self) -> dict[str, QueryResult | QueryError]:
        """Return all completed/failed results keyed by request_id."""
        with self._lock:
            return {
                rid: rec.result
                for rid, rec in self._records.items()
                if rec.result is not None
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _wait_for_rate_limit(self) -> None:
        """Block the calling thread until the rate-limit window allows a new request."""
        with self._rate_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    def _worker(
        self,
        client: SonnetResearchClient,
        stop_event: threading.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                item: _PrioritizedRequest = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            req: ResearchRequest = item.request

            # Mark as processing.
            with self._lock:
                record = self._records.get(req.request_id)
                if record:
                    record.status = RequestStatus.PROCESSING

            # Rate limit, then execute.
            self._wait_for_rate_limit()
            result = client.query(
                prompt=req.prompt,
                system_prompt=req.system_prompt,
            )

            # Store result.
            with self._lock:
                if record:
                    record.result = result
                    record.status = (
                        RequestStatus.COMPLETED
                        if isinstance(result, QueryResult)
                        else RequestStatus.FAILED
                    )

            # Fire callback if provided.
            if req.callback is not None:
                try:
                    req.callback(req.request_id, result)
                except Exception:  # noqa: BLE001
                    pass  # don't let a bad callback kill the worker

            self._queue.task_done()
