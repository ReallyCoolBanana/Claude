"""SDK configuration system for multi-agent team launches.

Provides YAML/JSON-based configuration for defining teams, roles, and launch
parameters.  Integrates with the existing multi_team_runner.py and
coordinator_hub.py patterns.

Usage:
    from sdk_config import load_config, validate_config

    config = load_config("team_templates.yaml")
    errors = validate_config(config)
    if errors:
        raise ValueError(f"Config errors: {errors}")

    # Use config.teams, config.bus_path, etc.

Stdlib + PyYAML (optional, falls back to JSON).  Python 3.10+.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AgentRole(str, Enum):
    """Roles an agent can hold within a team."""
    LEADER = "leader"
    WORKER = "worker"
    RESEARCHER = "researcher"
    REVIEWER = "reviewer"
    THINK_TANK = "think_tank"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TeamDefinition:
    """Definition of a single team and its agents."""

    team_name: str
    agent_count: int = 2
    role: AgentRole = AgentRole.WORKER
    system_prompt: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    work_directory: str = ""
    environment_vars: dict[str, str] = field(default_factory=dict)

    def agent_ids(self) -> list[str]:
        """Generate agent IDs for this team (matches multi_team_runner convention)."""
        ids: list[str] = []
        # First agent is always the lead
        ids.append(f"{self.team_name}-lead")
        for i in range(1, self.agent_count):
            ids.append(f"{self.team_name}-worker-{i}")
        return ids

    def to_runner_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by MultiTeamRunner."""
        agents = []
        for i, aid in enumerate(self.agent_ids()):
            agents.append({
                "agent_id": aid,
                "role": "coordinator" if i == 0 else self.role.value,
            })
        return {"name": self.team_name, "agents": agents}


@dataclass
class LaunchConfig:
    """Top-level launch configuration for the multi-agent system."""

    teams: list[TeamDefinition] = field(default_factory=list)
    bus_path: str = ""
    db_path: str = ""
    log_dir: str = ""
    max_restarts: int = 3
    health_check_interval: float = 30.0
    heartbeat_interval: float = 30.0
    dead_agent_timeout: float = 120.0
    cleanup_interval: float = 600.0

    def __post_init__(self) -> None:
        # Resolve default paths relative to the coordination directory
        coord_dir = os.path.dirname(os.path.abspath(__file__))
        if not self.bus_path:
            self.bus_path = os.path.join(coord_dir, "bus")
        if not self.db_path:
            self.db_path = os.path.join(coord_dir, "db", "state.db")
        if not self.log_dir:
            self.log_dir = os.path.join(coord_dir, "logs")

    def to_runner_dict(self) -> dict[str, Any]:
        """Convert to the dict format expected by MultiTeamRunner."""
        return {
            "teams": [t.to_runner_dict() for t in self.teams],
            "heartbeat_interval": self.heartbeat_interval,
            "dead_agent_timeout": self.dead_agent_timeout,
            "cleanup_interval": self.cleanup_interval,
        }

    @property
    def total_agents(self) -> int:
        return sum(t.agent_count for t in self.teams)


# ---------------------------------------------------------------------------
# YAML / JSON loading
# ---------------------------------------------------------------------------

def _load_yaml(path: str) -> dict[str, Any]:
    """Load a YAML file, falling back to JSON parsing if PyYAML is unavailable."""
    try:
        import yaml
        with open(path, "r") as f:
            return yaml.safe_load(f)
    except ImportError:
        # Fallback: try JSON (user may have a .json file or a simple subset)
        with open(path, "r") as f:
            return json.load(f)


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def _parse_role(value: str) -> AgentRole:
    """Parse a role string into an AgentRole enum, case-insensitive."""
    try:
        return AgentRole(value.lower())
    except ValueError:
        # Try matching by name
        for member in AgentRole:
            if member.name.lower() == value.lower():
                return member
        raise ValueError(
            f"Unknown role {value!r}. Valid roles: "
            f"{[r.value for r in AgentRole]}"
        )


def _parse_team(data: dict[str, Any]) -> TeamDefinition:
    """Parse a single team definition from raw config data."""
    return TeamDefinition(
        team_name=data["team_name"],
        agent_count=data.get("agent_count", 2),
        role=_parse_role(data.get("role", "worker")),
        system_prompt=data.get("system_prompt", ""),
        allowed_tools=data.get("allowed_tools", []),
        work_directory=data.get("work_directory", ""),
        environment_vars=data.get("environment_vars", {}),
    )


def load_config(path: str) -> LaunchConfig:
    """Load a LaunchConfig from a YAML or JSON file.

    The file should have a top-level structure like:

        teams:
          - team_name: coding
            agent_count: 3
            role: worker
            ...
        bus_path: ./bus
        db_path: ./db/state.db
        ...

    If the file contains a ``configurations`` mapping (as in the template
    file), pass the path and then select a configuration by name using
    ``load_named_config()``.

    Parameters
    ----------
    path:
        Path to the YAML or JSON configuration file.

    Returns
    -------
    LaunchConfig
        Parsed and populated launch configuration.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        raw = _load_yaml(path)
    else:
        raw = _load_json(path)

    # Handle top-level configs that wrap multiple named configurations
    if "configurations" in raw and "teams" not in raw:
        raise ValueError(
            "Config file contains multiple named configurations. "
            "Use load_named_config() to select one, or provide a "
            "config with a top-level 'teams' key."
        )

    return _parse_launch_config(raw)


def load_named_config(path: str, name: str) -> LaunchConfig:
    """Load a specific named configuration from a multi-config YAML file.

    Parameters
    ----------
    path:
        Path to the YAML or JSON file containing a ``configurations`` mapping.
    name:
        The configuration name to load (e.g., ``"standard_dev_team"``).

    Returns
    -------
    LaunchConfig
        The selected configuration.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml"):
        raw = _load_yaml(path)
    else:
        raw = _load_json(path)

    configs = raw.get("configurations", {})
    if name not in configs:
        available = list(configs.keys())
        raise ValueError(
            f"Configuration {name!r} not found. Available: {available}"
        )

    section = configs[name]
    # Inherit top-level defaults if present
    defaults = {k: v for k, v in raw.get("defaults", {}).items()}
    merged = {**defaults, **section}
    return _parse_launch_config(merged)


def _parse_launch_config(raw: dict[str, Any]) -> LaunchConfig:
    """Parse raw dict into a LaunchConfig."""
    teams_raw = raw.get("teams", [])
    teams = [_parse_team(t) for t in teams_raw]

    return LaunchConfig(
        teams=teams,
        bus_path=raw.get("bus_path", ""),
        db_path=raw.get("db_path", ""),
        log_dir=raw.get("log_dir", ""),
        max_restarts=raw.get("max_restarts", 3),
        health_check_interval=raw.get("health_check_interval", 30.0),
        heartbeat_interval=raw.get("heartbeat_interval", 30.0),
        dead_agent_timeout=raw.get("dead_agent_timeout", 120.0),
        cleanup_interval=raw.get("cleanup_interval", 600.0),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(config: LaunchConfig) -> list[str]:
    """Validate a LaunchConfig and return a list of error strings.

    Returns an empty list if the configuration is valid.

    Checks performed:
    - At least one team defined
    - Team names are unique
    - Agent counts are positive
    - No empty team names
    - Work directories (if set) exist
    - Health check interval is positive
    - Max restarts is non-negative
    """
    errors: list[str] = []

    if not config.teams:
        errors.append("No teams defined")
        return errors

    seen_names: set[str] = set()
    for i, team in enumerate(config.teams):
        prefix = f"teams[{i}]"

        if not team.team_name:
            errors.append(f"{prefix}: team_name is empty")
        elif team.team_name in seen_names:
            errors.append(f"{prefix}: duplicate team_name {team.team_name!r}")
        seen_names.add(team.team_name)

        if team.agent_count < 1:
            errors.append(
                f"{prefix} ({team.team_name}): agent_count must be >= 1, "
                f"got {team.agent_count}"
            )

        if team.work_directory and not os.path.isdir(team.work_directory):
            errors.append(
                f"{prefix} ({team.team_name}): work_directory "
                f"{team.work_directory!r} does not exist"
            )

    if config.health_check_interval <= 0:
        errors.append(
            f"health_check_interval must be > 0, got {config.health_check_interval}"
        )

    if config.max_restarts < 0:
        errors.append(
            f"max_restarts must be >= 0, got {config.max_restarts}"
        )

    if config.heartbeat_interval <= 0:
        errors.append(
            f"heartbeat_interval must be > 0, got {config.heartbeat_interval}"
        )

    if config.dead_agent_timeout <= 0:
        errors.append(
            f"dead_agent_timeout must be > 0, got {config.dead_agent_timeout}"
        )

    return errors


# ---------------------------------------------------------------------------
# Default / preset configurations
# ---------------------------------------------------------------------------

def standard_dev_team() -> LaunchConfig:
    """A 3-team development setup: coding, testing, review."""
    return LaunchConfig(
        teams=[
            TeamDefinition(
                team_name="coding",
                agent_count=3,
                role=AgentRole.WORKER,
                system_prompt=(
                    "You are a software development agent. Write clean, "
                    "tested code following project conventions. Coordinate "
                    "with your team lead and report progress regularly."
                ),
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            ),
            TeamDefinition(
                team_name="testing",
                agent_count=2,
                role=AgentRole.WORKER,
                system_prompt=(
                    "You are a testing agent. Write and run tests for code "
                    "produced by the coding team. Report test results, "
                    "coverage gaps, and any bugs found."
                ),
                allowed_tools=["Read", "Bash", "Glob", "Grep"],
            ),
            TeamDefinition(
                team_name="review",
                agent_count=2,
                role=AgentRole.REVIEWER,
                system_prompt=(
                    "You are a code review agent. Review code changes for "
                    "correctness, style, security issues, and adherence to "
                    "project standards. Provide actionable feedback."
                ),
                allowed_tools=["Read", "Glob", "Grep"],
            ),
        ],
        max_restarts=3,
        health_check_interval=30.0,
    )


def research_team() -> LaunchConfig:
    """A research-focused setup with researchers and a think tank."""
    return LaunchConfig(
        teams=[
            TeamDefinition(
                team_name="research",
                agent_count=3,
                role=AgentRole.RESEARCHER,
                system_prompt=(
                    "You are a research agent. Investigate the assigned topic "
                    "thoroughly using available tools. Document findings with "
                    "sources and report to the team lead."
                ),
                allowed_tools=["Read", "Bash", "Glob", "Grep", "WebFetch", "WebSearch"],
            ),
            TeamDefinition(
                team_name="synthesis",
                agent_count=2,
                role=AgentRole.THINK_TANK,
                system_prompt=(
                    "You are a synthesis agent in the think tank. Combine "
                    "findings from research agents into coherent analyses. "
                    "Identify patterns, contradictions, and gaps."
                ),
                allowed_tools=["Read", "Write", "Glob", "Grep"],
            ),
        ],
        max_restarts=2,
        health_check_interval=45.0,
    )


def review_team() -> LaunchConfig:
    """A review-focused setup for auditing existing code or documents."""
    return LaunchConfig(
        teams=[
            TeamDefinition(
                team_name="audit",
                agent_count=3,
                role=AgentRole.REVIEWER,
                system_prompt=(
                    "You are an audit agent. Systematically review the "
                    "assigned codebase or documents for quality, security, "
                    "and correctness issues. Produce a structured report."
                ),
                allowed_tools=["Read", "Glob", "Grep", "Bash"],
            ),
            TeamDefinition(
                team_name="reporting",
                agent_count=1,
                role=AgentRole.LEADER,
                system_prompt=(
                    "You are the reporting lead. Collect findings from audit "
                    "agents, deduplicate, prioritize, and produce a final "
                    "consolidated report."
                ),
                allowed_tools=["Read", "Write", "Glob", "Grep"],
            ),
        ],
        max_restarts=2,
        health_check_interval=30.0,
    )


def full_production_team() -> LaunchConfig:
    """A full production setup with all team types."""
    return LaunchConfig(
        teams=[
            TeamDefinition(
                team_name="coding",
                agent_count=4,
                role=AgentRole.WORKER,
                system_prompt=(
                    "You are a software development agent on the coding team. "
                    "Implement features and fixes as directed by your team lead."
                ),
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            ),
            TeamDefinition(
                team_name="testing",
                agent_count=2,
                role=AgentRole.WORKER,
                system_prompt=(
                    "You are a testing agent. Write and run tests, report "
                    "results and coverage."
                ),
                allowed_tools=["Read", "Bash", "Glob", "Grep"],
            ),
            TeamDefinition(
                team_name="review",
                agent_count=2,
                role=AgentRole.REVIEWER,
                system_prompt=(
                    "You are a code review agent. Review all changes for "
                    "correctness, security, and style."
                ),
                allowed_tools=["Read", "Glob", "Grep"],
            ),
            TeamDefinition(
                team_name="research",
                agent_count=2,
                role=AgentRole.RESEARCHER,
                system_prompt=(
                    "You are a research agent. Investigate technical questions "
                    "and provide well-sourced answers to the team."
                ),
                allowed_tools=["Read", "Bash", "Glob", "Grep", "WebFetch", "WebSearch"],
            ),
            TeamDefinition(
                team_name="think-tank",
                agent_count=2,
                role=AgentRole.THINK_TANK,
                system_prompt=(
                    "You are a think tank agent. Synthesize findings across "
                    "all teams, identify cross-cutting concerns, and propose "
                    "architectural decisions."
                ),
                allowed_tools=["Read", "Write", "Glob", "Grep"],
            ),
        ],
        max_restarts=5,
        health_check_interval=20.0,
    )


# Mapping of preset names to factory functions for programmatic access
PRESETS: dict[str, callable] = {
    "standard_dev_team": standard_dev_team,
    "research_team": research_team,
    "review_team": review_team,
    "full_production_team": full_production_team,
}


def get_preset(name: str) -> LaunchConfig:
    """Return a preset LaunchConfig by name.

    Parameters
    ----------
    name:
        One of: standard_dev_team, research_team, review_team,
        full_production_team.

    Raises
    ------
    ValueError
        If the preset name is not recognized.
    """
    if name not in PRESETS:
        raise ValueError(
            f"Unknown preset {name!r}. Available: {list(PRESETS.keys())}"
        )
    return PRESETS[name]()
