"""Prototype B: mmap + named-pipe based multi-agent communication.

Alternative to Prototype A (JSONL + SQLite). Prioritizes speed and
simplicity using mmap for shared state and named pipes (FIFOs) for
messaging.  Deliberately makes different tradeoffs — simpler but
potentially more prone to races.
"""

from .core import CommDir, AgentIdentity, CommConfig, setup_comm_environment
from .bus import Message, PipeBusWriter, PipeBusReader
from .state import SharedStateMap

__all__ = [
    "CommDir",
    "AgentIdentity",
    "CommConfig",
    "setup_comm_environment",
    "Message",
    "PipeBusWriter",
    "PipeBusReader",
    "SharedStateMap",
]
