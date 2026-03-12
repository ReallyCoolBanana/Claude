"""Capability Discovery Protocol for intelligent work matching.

Auto-registers agent capabilities on startup and provides scored matching
to assign work to the best-qualified available team. Replaces random
assignment in help_protocol.py's auto_assign_idle_teams().

SQLite-backed with WAL mode, following Proto A patterns. Thread-safe
with retry-on-busy semantics.
"""

import functools
import json
import logging
import os
import sqlite3
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_RETRY_BACKOFF = 0.1

_CAPABILITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_capabilities (
    agent_id TEXT NOT NULL,
    team TEXT NOT NULL,
    capability TEXT NOT NULL,
    proficiency INTEGER DEFAULT 3 CHECK(proficiency BETWEEN 1 AND 5),
    version TEXT DEFAULT '1.0',
    registered_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (agent_id, capability)
);

CREATE INDEX IF NOT EXISTS idx_agent_caps_team
    ON agent_capabilities(team);
CREATE INDEX IF NOT EXISTS idx_agent_caps_capability
    ON agent_capabilities(capability);
CREATE INDEX IF NOT EXISTS idx_agent_caps_proficiency
    ON agent_capabilities(capability, proficiency DESC);
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


class CapabilityRegistry:
    """Auto-register and discover agent capabilities for intelligent work matching.

    Capabilities are skills like: 'coding', 'testing', 'research', 'data-gathering',
    'market-analysis', 'debugging', 'documentation', 'api-integration'.

    Each capability has a proficiency score from 1 (novice) to 5 (expert).

    Scoring algorithm for find_best_match:
      - For each candidate team, sum proficiency scores for matching capabilities
      - Penalize missing required capabilities: subtract 3 per missing cap
      - Teams with negative scores are excluded
      - Returns candidates sorted by score descending

    Parameters
    ----------
    db_path:
        Path to the SQLite database file.
    """

    # Penalty applied per missing required capability
    MISSING_CAP_PENALTY = 3

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
        self._conn.executescript(_CAPABILITY_SCHEMA)
        self._conn.commit()
        self._closed = False

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
            raise RuntimeError("CapabilityRegistry instance is closed")

    @_retry_on_busy
    def register_agent(self, agent_id: str, team: str, capabilities: list[dict]) -> None:
        """Register agent with capabilities.

        Each capability dict: {name: str, proficiency: int (1-5), version: str}.

        Parameters
        ----------
        agent_id:
            Unique agent identifier.
        team:
            Team this agent belongs to.
        capabilities:
            List of capability dicts. Each must have 'name' and optionally
            'proficiency' (default 3) and 'version' (default '1.0').
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            for cap in capabilities:
                name = cap["name"]
                proficiency = cap.get("proficiency", 3)
                version = cap.get("version", "1.0")
                if not (1 <= proficiency <= 5):
                    raise ValueError(
                        f"Proficiency must be 1-5, got {proficiency} for capability '{name}'"
                    )
                self._conn.execute(
                    """
                    INSERT INTO agent_capabilities
                        (agent_id, team, capability, proficiency, version, registered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id, capability) DO UPDATE SET
                        team = excluded.team,
                        proficiency = excluded.proficiency,
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (agent_id, team, name, proficiency, version, now, now),
                )
            self._conn.commit()
        logger.info(
            "Registered %d capabilities for agent %s (team %s)",
            len(capabilities), agent_id, team,
        )

    @_retry_on_busy
    def update_capabilities(self, agent_id: str, capabilities: list[dict]) -> None:
        """Update an agent's capabilities (e.g., learned new skill during session).

        Parameters
        ----------
        agent_id:
            Agent whose capabilities to update.
        capabilities:
            List of capability dicts to upsert. Same format as register_agent.
        """
        self._check_closed()
        now = time.time()
        with self._lock:
            # Look up the agent's team from existing registrations
            row = self._conn.execute(
                "SELECT team FROM agent_capabilities WHERE agent_id = ? LIMIT 1",
                (agent_id,),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"Agent '{agent_id}' has no existing registrations. "
                    f"Use register_agent() first."
                )
            team = row["team"]

            for cap in capabilities:
                name = cap["name"]
                proficiency = cap.get("proficiency", 3)
                version = cap.get("version", "1.0")
                if not (1 <= proficiency <= 5):
                    raise ValueError(
                        f"Proficiency must be 1-5, got {proficiency} for capability '{name}'"
                    )
                self._conn.execute(
                    """
                    INSERT INTO agent_capabilities
                        (agent_id, team, capability, proficiency, version, registered_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(agent_id, capability) DO UPDATE SET
                        proficiency = excluded.proficiency,
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (agent_id, team, name, proficiency, version, now, now),
                )
            self._conn.commit()

    @_retry_on_busy
    def find_best_match(
        self,
        required_capabilities: list[str],
        exclude_teams: Optional[list[str]] = None,
    ) -> list[dict]:
        """Find agents/teams best matching required capabilities. Returns scored list.

        Scoring:
        - Sum proficiency scores for each matching capability (best agent per team)
        - Subtract MISSING_CAP_PENALTY per missing required capability
        - Exclude teams with score <= 0
        - Sort descending by score

        Parameters
        ----------
        required_capabilities:
            List of capability names needed.
        exclude_teams:
            Teams to exclude from matching (e.g., the requesting team).

        Returns
        -------
        List of dicts: {team, score, matched_caps, missing_caps, agents}
        """
        self._check_closed()
        if not required_capabilities:
            return []

        exclude = set(exclude_teams or [])
        required_set = set(required_capabilities)

        with self._lock:
            # Get all agents with any of the required capabilities
            placeholders = ",".join("?" for _ in required_capabilities)
            rows = self._conn.execute(
                f"""
                SELECT agent_id, team, capability, proficiency
                FROM agent_capabilities
                WHERE capability IN ({placeholders})
                ORDER BY team, capability, proficiency DESC
                """,
                list(required_capabilities),
            ).fetchall()

        # Aggregate by team: for each capability, take the best proficiency
        team_caps: dict[str, dict[str, int]] = {}  # team -> {cap: best_proficiency}
        team_agents: dict[str, set] = {}  # team -> set of agent_ids
        for row in rows:
            team = row["team"]
            if team in exclude:
                continue
            cap = row["capability"]
            prof = row["proficiency"]
            if team not in team_caps:
                team_caps[team] = {}
                team_agents[team] = set()
            team_agents[team].add(row["agent_id"])
            # Keep best proficiency per capability per team
            if cap not in team_caps[team] or prof > team_caps[team][cap]:
                team_caps[team][cap] = prof

        results = []
        for team, caps in team_caps.items():
            matched = set(caps.keys())
            missing = required_set - matched
            score = sum(caps.values()) - len(missing) * self.MISSING_CAP_PENALTY
            if score > 0:
                results.append({
                    "team": team,
                    "score": score,
                    "matched_caps": sorted(matched),
                    "missing_caps": sorted(missing),
                    "agents": sorted(team_agents[team]),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    @_retry_on_busy
    def get_team_capabilities(self, team: str) -> dict:
        """Get aggregate capabilities for a team (union of all agents' skills).

        Returns dict: {capability_name: {max_proficiency, agent_count, agents}}
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT capability, proficiency, agent_id
                FROM agent_capabilities
                WHERE team = ?
                ORDER BY capability, proficiency DESC
                """,
                (team,),
            ).fetchall()

        caps: dict[str, dict] = {}
        for row in rows:
            cap = row["capability"]
            if cap not in caps:
                caps[cap] = {
                    "max_proficiency": row["proficiency"],
                    "agent_count": 0,
                    "agents": [],
                }
            caps[cap]["agent_count"] += 1
            caps[cap]["agents"].append(row["agent_id"])
            if row["proficiency"] > caps[cap]["max_proficiency"]:
                caps[cap]["max_proficiency"] = row["proficiency"]

        return caps

    @_retry_on_busy
    def get_capability_matrix(self) -> dict:
        """Get full matrix: which teams can do what, with proficiency scores.

        Returns dict: {team: {capability: max_proficiency, ...}, ...}
        """
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT team, capability, MAX(proficiency) as max_prof
                FROM agent_capabilities
                GROUP BY team, capability
                ORDER BY team, capability
                """,
            ).fetchall()

        matrix: dict[str, dict[str, int]] = {}
        for row in rows:
            team = row["team"]
            if team not in matrix:
                matrix[team] = {}
            matrix[team][row["capability"]] = row["max_prof"]

        return matrix

    @_retry_on_busy
    def match_work_to_team(
        self,
        work_item: dict,
        exclude_teams: Optional[list[str]] = None,
        idle_teams: Optional[list[str]] = None,
    ) -> Optional[str]:
        """Given a work item with required_capabilities, return best team assignment.

        Parameters
        ----------
        work_item:
            Dict with at least 'required_capabilities' (list of str or JSON string).
        exclude_teams:
            Teams to exclude from consideration.
        idle_teams:
            If provided, only consider these teams. Useful for constraining
            to teams that are actually idle/available.

        Returns
        -------
        Best matching team name, or None if no capable team found.
        """
        required = work_item.get("required_capabilities", [])
        if isinstance(required, str):
            required = json.loads(required) if required else []
        if not required:
            # No specific capabilities required -- return first idle team if available
            if idle_teams:
                exclude = set(exclude_teams or [])
                for t in idle_teams:
                    if t not in exclude:
                        return t
            return None

        matches = self.find_best_match(required, exclude_teams=exclude_teams)

        # Filter to idle teams if constraint provided
        if idle_teams is not None:
            idle_set = set(idle_teams)
            matches = [m for m in matches if m["team"] in idle_set]

        if matches:
            return matches[0]["team"]
        return None

    @_retry_on_busy
    def deregister_agent(self, agent_id: str) -> int:
        """Remove all capabilities for an agent (e.g., on shutdown).

        Returns the number of capability rows removed.
        """
        self._check_closed()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM agent_capabilities WHERE agent_id = ?",
                (agent_id,),
            )
            count = cur.rowcount
            self._conn.commit()
        return count

    @_retry_on_busy
    def get_all_agents(self) -> list[dict]:
        """List all registered agents with their teams and capability counts."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT agent_id, team, COUNT(*) as cap_count,
                       GROUP_CONCAT(capability) as capabilities,
                       MAX(updated_at) as last_updated
                FROM agent_capabilities
                GROUP BY agent_id
                ORDER BY team, agent_id
                """,
            ).fetchall()

        return [
            {
                "agent_id": r["agent_id"],
                "team": r["team"],
                "cap_count": r["cap_count"],
                "capabilities": r["capabilities"].split(",") if r["capabilities"] else [],
                "last_updated": r["last_updated"],
            }
            for r in rows
        ]

    @_retry_on_busy
    def get_all_capabilities(self) -> list[str]:
        """Return a sorted list of all distinct capability names in the registry."""
        self._check_closed()
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT capability FROM agent_capabilities ORDER BY capability",
            ).fetchall()
        return [r["capability"] for r in rows]
