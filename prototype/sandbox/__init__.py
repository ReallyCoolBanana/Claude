"""Multi-process sandbox for prototyping and testing coordination features.

Addresses the critical gap identified by TEAM-0020, TEAM-0021, and TEAM-0022:
all prior testing used threads, but real deployments use separate OS processes.

This sandbox spawns real child processes that communicate through Proto A's
JSONL bus and SQLite WAL shared state, providing true multi-process validation.

Usage:
    from prototype.sandbox import Sandbox, AgentSpec

    with Sandbox() as sb:
        sb.add_agent(AgentSpec("coord", "team-1", "coordinator"))
        sb.add_agent(AgentSpec("worker-1", "team-1", "worker"))
        sb.start_all()
        sb.wait_until_phase("research", timeout=10)
        results = sb.collect_results()
"""

from prototype.sandbox.sandbox import (
    AgentSpec,
    Sandbox,
    SandboxResult,
)

__all__ = ["AgentSpec", "Sandbox", "SandboxResult"]
