"""Vector Clocks for causal message ordering.

Lamport-style vector clocks for establishing happened-before relationships
between messages in the agent communication system. Each agent maintains
a vector of counters, one per known agent.

Protocol:
    - On send: increment own counter, attach vector to message.
    - On receive: merge vectors (element-wise max), increment own counter.

Lightweight implementation: vector stored as JSON dict in message body.
Integrates with bus.py's Message dataclass via optional vector_clock field.
"""

from __future__ import annotations

import copy
import json
from typing import Optional


class VectorClock:
    """Lamport-style vector clock for a single agent.

    Each agent maintains a vector of counters, one per known agent.
    The vector is represented as a dict mapping agent_id to counter value.

    Parameters
    ----------
    agent_id:
        The identifier for this agent (used as the key in the vector).
    initial_vector:
        Optional initial vector state (e.g., restored from persistence).
    """

    def __init__(self, agent_id: str, initial_vector: Optional[dict[str, int]] = None) -> None:
        self.agent_id = agent_id
        self._vector: dict[str, int] = dict(initial_vector) if initial_vector else {}
        # Ensure our own counter exists
        if self.agent_id not in self._vector:
            self._vector[self.agent_id] = 0

    @property
    def vector(self) -> dict[str, int]:
        """Return a copy of the current vector clock state."""
        return dict(self._vector)

    def tick(self) -> dict[str, int]:
        """Increment local counter and return current vector.

        Called before sending a message. The returned vector should be
        attached to the outgoing message.

        Returns
        -------
        Copy of the current vector clock (after incrementing).
        """
        self._vector[self.agent_id] = self._vector.get(self.agent_id, 0) + 1
        return dict(self._vector)

    def merge(self, other_vector: dict[str, int]) -> None:
        """Merge received vector clock with local (element-wise max).

        Called when receiving a message. After merging, also increments
        the local counter.

        Parameters
        ----------
        other_vector:
            The vector clock from the received message.
        """
        for agent_id, counter in other_vector.items():
            if counter > self._vector.get(agent_id, 0):
                self._vector[agent_id] = counter
        # Increment own counter after merge
        self._vector[self.agent_id] = self._vector.get(self.agent_id, 0) + 1

    @staticmethod
    def is_before(v1: dict[str, int], v2: dict[str, int]) -> bool:
        """Check if v1 happened-before v2 (causal ordering).

        v1 < v2 iff:
            - For all agents a: v1[a] <= v2[a]
            - There exists at least one agent a where v1[a] < v2[a]

        Parameters
        ----------
        v1:
            First vector clock.
        v2:
            Second vector clock.

        Returns
        -------
        True if v1 causally precedes v2.
        """
        all_agents = set(v1.keys()) | set(v2.keys())
        at_least_one_less = False
        for agent in all_agents:
            c1 = v1.get(agent, 0)
            c2 = v2.get(agent, 0)
            if c1 > c2:
                return False
            if c1 < c2:
                at_least_one_less = True
        return at_least_one_less

    @staticmethod
    def is_concurrent(v1: dict[str, int], v2: dict[str, int]) -> bool:
        """Check if v1 and v2 are concurrent (no causal relationship).

        Two events are concurrent iff neither v1 < v2 nor v2 < v1.

        Parameters
        ----------
        v1:
            First vector clock.
        v2:
            Second vector clock.

        Returns
        -------
        True if the events are concurrent (causally unordered).
        """
        return (
            not VectorClock.is_before(v1, v2)
            and not VectorClock.is_before(v2, v1)
            and v1 != v2
        )

    @staticmethod
    def is_equal(v1: dict[str, int], v2: dict[str, int]) -> bool:
        """Check if two vector clocks are identical.

        Treats missing keys as 0.
        """
        all_agents = set(v1.keys()) | set(v2.keys())
        return all(v1.get(a, 0) == v2.get(a, 0) for a in all_agents)

    @staticmethod
    def detect_out_of_order(messages: list[dict]) -> list[dict]:
        """Given a list of messages with vector clocks, find any delivered out of causal order.

        Checks sequential pairs: if message[i] has a vector clock that
        causally follows message[i+1], then message[i+1] was delivered
        out of order.

        Parameters
        ----------
        messages:
            List of message dicts, each with a 'vector_clock' key containing
            the vector clock dict. Messages without vector_clock are skipped.

        Returns
        -------
        List of dicts describing out-of-order deliveries:
            {index, message_id, expected_before_id, reason}
        """
        violations = []
        clocked = [
            (i, m) for i, m in enumerate(messages)
            if m.get("vector_clock") is not None
        ]

        for pos in range(len(clocked) - 1):
            idx_a, msg_a = clocked[pos]
            idx_b, msg_b = clocked[pos + 1]
            vc_a = msg_a["vector_clock"]
            vc_b = msg_b["vector_clock"]

            # If b happened-before a, then they are delivered in wrong order
            if VectorClock.is_before(vc_b, vc_a):
                violations.append({
                    "index": idx_b,
                    "message_id": msg_b.get("id", f"msg@{idx_b}"),
                    "expected_before_id": msg_a.get("id", f"msg@{idx_a}"),
                    "reason": (
                        f"Message at index {idx_b} (vc={vc_b}) causally precedes "
                        f"message at index {idx_a} (vc={vc_a}) but was delivered after it"
                    ),
                })

        return violations

    @staticmethod
    def causal_sort(messages: list[dict]) -> list[dict]:
        """Sort messages by causal order using their vector clocks.

        Uses topological sort based on happened-before relationships.
        Concurrent messages maintain their original relative order (stable sort).

        Parameters
        ----------
        messages:
            List of message dicts with 'vector_clock' keys.

        Returns
        -------
        New list of messages sorted in causal order.
        """
        # Separate messages with and without vector clocks
        clocked = [(i, m) for i, m in enumerate(messages) if m.get("vector_clock")]
        unclocked = [m for m in messages if not m.get("vector_clock")]

        if not clocked:
            return list(messages)

        # Sort by sum of vector components (a rough topological order),
        # using original index as tiebreaker for concurrent events
        def sort_key(item):
            idx, msg = item
            vc = msg["vector_clock"]
            return (sum(vc.values()), idx)

        clocked.sort(key=sort_key)

        # Unclocked messages go first (they have no ordering constraints)
        return unclocked + [m for _, m in clocked]

    def to_json(self) -> str:
        """Serialize vector clock state to JSON string."""
        return json.dumps(self._vector, separators=(",", ":"))

    @classmethod
    def from_json(cls, agent_id: str, json_str: str) -> "VectorClock":
        """Restore a VectorClock from a JSON string.

        Parameters
        ----------
        agent_id:
            The agent this clock belongs to.
        json_str:
            JSON-encoded vector dict.
        """
        vector = json.loads(json_str)
        return cls(agent_id, initial_vector=vector)
