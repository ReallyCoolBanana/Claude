"""Pipeline Quality Gates for validating stage outputs before advancement.

Provides configurable validation gates between pipeline stages per SOP-016v2.
Each stage output is scored 0.0-1.0 against registered validators. Below
threshold = rejected, preventing bad data from propagating downstream.

Integrates with PipelineManager.trigger_downstream() -- call validate_output()
before advancing to ensure quality is maintained.

SQLite-backed with WAL mode for gate definitions and history. Thread-safe
with retry-on-busy semantics. Only uses the Python standard library.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.05

_QUALITY_GATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS quality_gates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    validators TEXT NOT NULL,
    threshold REAL NOT NULL DEFAULT 0.7,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(pipeline_id, stage_name)
);

CREATE TABLE IF NOT EXISTS gate_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pipeline_id INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    score REAL NOT NULL,
    passed INTEGER NOT NULL,
    failures TEXT,
    validator_scores TEXT,
    output_summary TEXT,
    evaluated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gate_history_pipeline
    ON gate_history(pipeline_id, stage_name, evaluated_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_gates_pipeline
    ON quality_gates(pipeline_id, stage_name);
"""


def _retry_on_busy(func):
    """Decorator: retry a method on sqlite3.OperationalError (SQLITE_BUSY)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        delay = _RETRY_BACKOFF
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg or "busy" in msg:
                    last_err = e
                    logger.debug(
                        "SQLITE_BUSY on %s (attempt %d/%d), retrying in %.2fs",
                        func.__name__, attempt + 1, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 0.05)
                else:
                    raise
        raise last_err  # type: ignore[misc]
    return wrapper


class QualityGate:
    """Validation gates between pipeline stages per SOP-016v2.

    Each stage output is scored 0.0-1.0. Below threshold = rejected.

    Built-in validators:
    - has_required_fields: Checks that all required fields exist in the data.
    - min_item_count: Verifies the data contains at least N items.
    - no_duplicates: Ensures no duplicate values for a given key field.
    - schema_compliant: Validates data matches a JSON schema (type checking).

    Custom validators can be registered as callables that accept (data, **params)
    and return a float score between 0.0 and 1.0.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file for gate definitions and history.
    """

    # Registry of built-in validator names -> static methods
    BUILTIN_VALIDATORS: dict[str, Callable] = {}

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(
            db_path, timeout=30, check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_QUALITY_GATE_SCHEMA)
        self._conn.commit()
        self._closed = False
        # Custom validators registered at runtime
        self._custom_validators: dict[str, Callable] = {}

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def _check_closed(self) -> None:
        if self._closed:
            raise RuntimeError("QualityGate instance is closed")

    def register_validator(self, name: str, fn: Callable) -> None:
        """Register a custom validator function.

        Parameters
        ----------
        name:
            Name to reference this validator in gate definitions.
        fn:
            Callable(data, **params) -> float (0.0-1.0).
        """
        self._custom_validators[name] = fn

    def _resolve_validator(self, name: str) -> Optional[Callable]:
        """Resolve a validator name to a callable."""
        # Check custom validators first
        if name in self._custom_validators:
            return self._custom_validators[name]
        # Check built-in validators
        if name in self.BUILTIN_VALIDATORS:
            return self.BUILTIN_VALIDATORS[name]
        # Check if it's a static method on this class
        method = getattr(QualityGate, name, None)
        if method is not None and callable(method):
            return method
        return None

    @_retry_on_busy
    def define_gate(
        self,
        pipeline_id: int,
        stage_name: str,
        validators: list[dict],
        threshold: float = 0.7,
    ) -> int:
        """Define quality requirements for a pipeline stage output.

        Parameters
        ----------
        pipeline_id:
            The pipeline this gate belongs to.
        stage_name:
            The stage whose output this gate validates.
        validators:
            List of validator specifications. Each dict must have:
            - "name": str -- validator function name (built-in or custom)
            - "params": dict -- keyword arguments passed to the validator
            - "weight": float -- relative weight for scoring (default 1.0)
        threshold:
            Minimum weighted score (0.0-1.0) to pass the gate.

        Returns
        -------
        int
            The gate definition ID.
        """
        self._check_closed()
        if not (0.0 <= threshold <= 1.0):
            raise ValueError(f"Threshold must be between 0.0 and 1.0, got {threshold}")
        if not validators:
            raise ValueError("At least one validator is required")

        now = time.time()
        validators_json = json.dumps(validators)

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO quality_gates (pipeline_id, stage_name, validators, threshold,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_id, stage_name) DO UPDATE SET
                    validators = excluded.validators,
                    threshold = excluded.threshold,
                    updated_at = excluded.updated_at
                """,
                (pipeline_id, stage_name, validators_json, threshold, now, now),
            )
            gate_id = self._conn.execute(
                "SELECT id FROM quality_gates WHERE pipeline_id = ? AND stage_name = ?",
                (pipeline_id, stage_name),
            ).fetchone()["id"]
            self._conn.commit()

        logger.info(
            "Defined quality gate for pipeline %d stage '%s': %d validators, threshold=%.2f",
            pipeline_id, stage_name, len(validators), threshold,
        )
        return gate_id

    @_retry_on_busy
    def validate_output(
        self,
        pipeline_id: int,
        stage_name: str,
        output_data: Any,
    ) -> dict:
        """Run all validators on stage output.

        Parameters
        ----------
        pipeline_id:
            The pipeline ID.
        stage_name:
            The stage whose output to validate.
        output_data:
            The output data to validate (typically a dict or list).

        Returns
        -------
        dict
            {
                "score": float,       # Weighted average score 0.0-1.0
                "passed": bool,       # Whether score >= threshold
                "threshold": float,   # The gate's threshold
                "failures": list,     # List of failed validator details
                "validator_scores": dict,  # name -> score for each validator
            }

        If no gate is defined for this stage, returns passed=True with score=1.0.
        """
        self._check_closed()

        # Fetch gate definition
        with self._lock:
            row = self._conn.execute(
                "SELECT validators, threshold FROM quality_gates WHERE pipeline_id = ? AND stage_name = ?",
                (pipeline_id, stage_name),
            ).fetchone()

        if row is None:
            # No gate defined -- pass by default
            return {
                "score": 1.0,
                "passed": True,
                "threshold": 0.0,
                "failures": [],
                "validator_scores": {},
            }

        validators = json.loads(row["validators"])
        threshold = row["threshold"]

        # Run validators
        total_weight = 0.0
        weighted_score = 0.0
        failures = []
        validator_scores = {}

        for spec in validators:
            name = spec["name"]
            params = spec.get("params", {})
            weight = spec.get("weight", 1.0)

            validator_fn = self._resolve_validator(name)
            if validator_fn is None:
                # Unknown validator is an automatic failure
                score = 0.0
                failures.append({
                    "validator": name,
                    "score": 0.0,
                    "reason": f"Unknown validator: {name}",
                })
            else:
                try:
                    score = float(validator_fn(output_data, **params))
                    score = max(0.0, min(1.0, score))  # Clamp to [0, 1]
                except Exception as e:
                    score = 0.0
                    failures.append({
                        "validator": name,
                        "score": 0.0,
                        "reason": f"Validator raised {type(e).__name__}: {e}",
                    })

            validator_scores[name] = score
            total_weight += weight
            weighted_score += score * weight

            if score < threshold:
                if not any(f["validator"] == name for f in failures):
                    failures.append({
                        "validator": name,
                        "score": score,
                        "reason": f"Score {score:.3f} below threshold {threshold:.3f}",
                    })

        final_score = weighted_score / total_weight if total_weight > 0 else 0.0
        passed = final_score >= threshold

        # Record in history
        now = time.time()
        output_summary = json.dumps(output_data)[:500] if output_data else ""
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO gate_history
                    (pipeline_id, stage_name, score, passed, failures,
                     validator_scores, output_summary, evaluated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pipeline_id, stage_name, final_score, int(passed),
                    json.dumps(failures), json.dumps(validator_scores),
                    output_summary, now,
                ),
            )
            self._conn.commit()

        return {
            "score": round(final_score, 4),
            "passed": passed,
            "threshold": threshold,
            "failures": failures,
            "validator_scores": validator_scores,
        }

    @_retry_on_busy
    def get_gate_history(self, pipeline_id: int) -> list[dict]:
        """Get quality scores for all stages in a pipeline.

        Parameters
        ----------
        pipeline_id:
            The pipeline ID to retrieve history for.

        Returns
        -------
        list[dict]
            List of gate evaluation records, newest first.
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT pipeline_id, stage_name, score, passed, failures,
                       validator_scores, output_summary, evaluated_at
                FROM gate_history
                WHERE pipeline_id = ?
                ORDER BY evaluated_at DESC
                """,
                (pipeline_id,),
            ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d["passed"] = bool(d["passed"])
            d["failures"] = json.loads(d["failures"]) if d["failures"] else []
            d["validator_scores"] = json.loads(d["validator_scores"]) if d["validator_scores"] else {}
            result.append(d)
        return result

    @_retry_on_busy
    def get_gate_definition(self, pipeline_id: int, stage_name: str) -> Optional[dict]:
        """Get the gate definition for a specific pipeline stage.

        Returns None if no gate is defined.
        """
        self._check_closed()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM quality_gates WHERE pipeline_id = ? AND stage_name = ?",
                (pipeline_id, stage_name),
            ).fetchone()

        if row is None:
            return None
        d = dict(row)
        d["validators"] = json.loads(d["validators"])
        return d

    @_retry_on_busy
    def get_all_gates(self, pipeline_id: int) -> list[dict]:
        """Get all gate definitions for a pipeline."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM quality_gates WHERE pipeline_id = ? ORDER BY stage_name",
                (pipeline_id,),
            ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            d["validators"] = json.loads(d["validators"])
            result.append(d)
        return result

    @_retry_on_busy
    def remove_gate(self, pipeline_id: int, stage_name: str) -> bool:
        """Remove a gate definition. Returns True if a gate was removed."""
        self._check_closed()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM quality_gates WHERE pipeline_id = ? AND stage_name = ?",
                (pipeline_id, stage_name),
            )
            self._conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Built-in validators
    # ------------------------------------------------------------------

    @staticmethod
    def has_required_fields(data: Any, fields: list[str]) -> float:
        """Validate that all required fields exist in the data.

        Parameters
        ----------
        data:
            Dict to validate.
        fields:
            List of required field names.

        Returns
        -------
        float
            Fraction of required fields present (0.0 to 1.0).
        """
        if not isinstance(data, dict):
            return 0.0
        if not fields:
            return 1.0
        present = sum(1 for f in fields if f in data)
        return present / len(fields)

    @staticmethod
    def min_item_count(data: Any, minimum: int) -> float:
        """Validate that the data contains at least N items.

        Works with lists, dicts (checks number of keys), and other
        objects with __len__.

        Parameters
        ----------
        data:
            Collection to check.
        minimum:
            Minimum required item count.

        Returns
        -------
        float
            1.0 if count >= minimum, otherwise count/minimum (partial credit).
        """
        if minimum <= 0:
            return 1.0
        try:
            count = len(data)
        except TypeError:
            return 0.0
        if count >= minimum:
            return 1.0
        return count / minimum

    @staticmethod
    def no_duplicates(data: Any, key_field: str) -> float:
        """Validate no duplicate values for a given key field in a list of dicts.

        Parameters
        ----------
        data:
            List of dicts to check for duplicates.
        key_field:
            The field name to check for uniqueness.

        Returns
        -------
        float
            1.0 if no duplicates, otherwise (unique_count / total_count).
        """
        if not isinstance(data, list):
            return 0.0
        if not data:
            return 1.0
        values = [item.get(key_field) for item in data if isinstance(item, dict)]
        if not values:
            return 0.0
        unique_count = len(set(values))
        total_count = len(values)
        return unique_count / total_count

    @staticmethod
    def schema_compliant(data: Any, schema: dict) -> float:
        """Validate data matches a simple JSON schema (type checking).

        The schema is a dict mapping field names to expected Python type names:
        {"name": "str", "count": "int", "items": "list", "metadata": "dict"}

        Parameters
        ----------
        data:
            Dict to validate.
        schema:
            Dict mapping field names to type name strings.

        Returns
        -------
        float
            Fraction of fields that have the correct type (0.0 to 1.0).
        """
        if not isinstance(data, dict) or not schema:
            return 0.0

        type_map = {
            "str": str,
            "int": int,
            "float": (int, float),
            "list": list,
            "dict": dict,
            "bool": bool,
        }

        total = len(schema)
        correct = 0
        for field, expected_type_name in schema.items():
            if field not in data:
                continue
            expected_type = type_map.get(expected_type_name)
            if expected_type is None:
                continue
            if isinstance(data[field], expected_type):
                correct += 1

        return correct / total if total > 0 else 1.0


# Register built-in validators in the class registry
QualityGate.BUILTIN_VALIDATORS = {
    "has_required_fields": QualityGate.has_required_fields,
    "min_item_count": QualityGate.min_item_count,
    "no_duplicates": QualityGate.no_duplicates,
    "schema_compliant": QualityGate.schema_compliant,
}
