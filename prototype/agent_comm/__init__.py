"""agent_comm — Multi-agent communication infrastructure.

Quick start::

    from agent_comm import setup_comm_environment

    comm, identity, config = setup_comm_environment(
        agent_id="team-0018-worker-a",
        team="TEAM-0018",
        role="worker",
    )

Public API
----------
CommDir               Resolves and validates the shared communication directory.
AgentIdentity         Dataclass representing a running agent.
CommConfig            Dataclass holding tunable communication parameters.
setup_comm_environment  One-call bootstrap returning (CommDir, AgentIdentity, CommConfig).
CommDirError          Raised when directory validation fails.
"""

from agent_comm.core import (
    AgentIdentity,
    CommConfig,
    CommDir,
    CommDirError,
    setup_comm_environment,
)
from agent_comm.bus import BusReader, BusWriter, Message
from agent_comm.rate_limiter import RateLimiter
from agent_comm.compactor import BusCompactor, OffsetStore
from agent_comm.ack_protocol import AckProtocol

__all__ = [
    "CommDir",
    "CommDirError",
    "AgentIdentity",
    "CommConfig",
    "setup_comm_environment",
    "BusReader",
    "BusWriter",
    "Message",
    "RateLimiter",
    "BusCompactor",
    "OffsetStore",
    "AckProtocol",
]
