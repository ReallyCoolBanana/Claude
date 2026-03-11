#!/usr/bin/env python3
"""CLI interface for the SDK-based multi-agent team launcher.

Provides subcommands to launch, monitor, stop, restart, and configure
multi-agent teams that communicate via the Proto A coordination system
(JSONL message bus + SQLite shared state).

Usage:
    python sdk_cli.py launch --teams 3 --agents-per-team 2
    python sdk_cli.py launch --config teams.json
    python sdk_cli.py status
    python sdk_cli.py status --team team-01
    python sdk_cli.py stop
    python sdk_cli.py stop --team team-02
    python sdk_cli.py restart --team team-01
    python sdk_cli.py restart --agent team-01-worker-1
    python sdk_cli.py logs
    python sdk_cli.py logs --team team-01 --tail 50
    python sdk_cli.py config --generate 3 2
    python sdk_cli.py config --validate teams.json

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import sqlite3
import sys
import textwrap
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUS_DIR = os.path.join(_SCRIPT_DIR, "bus")
_DB_DIR = os.path.join(_SCRIPT_DIR, "db")
_DB_PATH = os.path.join(_DB_DIR, "state.db")
_LOG_FILE = os.path.join(_SCRIPT_DIR, "runner.log")

# ---------------------------------------------------------------------------
# ANSI color helpers (no external deps)
# ---------------------------------------------------------------------------

_COLOR_ENABLED = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _supports_color() -> bool:
    """Check if the terminal supports ANSI color codes."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return _COLOR_ENABLED


class _C:
    """ANSI color codes, disabled when output is not a terminal."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"

    @classmethod
    def disable(cls) -> None:
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


if not _supports_color():
    _C.disable()


def _ok(text: str) -> str:
    return f"{_C.GREEN}{text}{_C.RESET}"


def _warn(text: str) -> str:
    return f"{_C.YELLOW}{text}{_C.RESET}"


def _err(text: str) -> str:
    return f"{_C.RED}{text}{_C.RESET}"


def _bold(text: str) -> str:
    return f"{_C.BOLD}{text}{_C.RESET}"


def _dim(text: str) -> str:
    return f"{_C.DIM}{text}{_C.RESET}"


def _cyan(text: str) -> str:
    return f"{_C.CYAN}{text}{_C.RESET}"


def _magenta(text: str) -> str:
    return f"{_C.MAGENTA}{text}{_C.RESET}"


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection | None:
    """Open a read-only connection to the shared state DB."""
    if not os.path.exists(_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """Run a query and return results as a list of dicts."""
    conn = _get_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _query_one(sql: str, params: tuple = ()) -> dict | None:
    """Run a query and return the first result or None."""
    results = _query(sql, params)
    return results[0] if results else None


def _get_runner_kv(key: str) -> str | None:
    """Read a value from the runner_state table."""
    row = _query_one("SELECT value FROM runner_state WHERE key = ?", (key,))
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# Bus helpers
# ---------------------------------------------------------------------------

def _read_bus_messages(channel: str | None = None,
                       limit: int = 50) -> list[dict]:
    """Read recent messages from bus JSONL files."""
    if not os.path.isdir(_BUS_DIR):
        return []

    if channel:
        pattern = os.path.join(_BUS_DIR, f"{channel}.jsonl")
        files = glob.glob(pattern)
    else:
        files = sorted(glob.glob(os.path.join(_BUS_DIR, "*.jsonl")))

    messages: list[dict] = []
    now = time.time()
    for filepath in files:
        try:
            with open(filepath, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                read_size = min(size, 65536)
                f.seek(max(0, size - read_size))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue

        for line in tail.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                try:
                    ts_val = float(msg.get("ts", 0))
                    ttl_val = float(msg.get("ttl", 300))
                except (ValueError, TypeError):
                    continue
                if ts_val + ttl_val >= now:
                    messages.append(msg)
            except json.JSONDecodeError:
                continue

    messages.sort(key=lambda m: m.get("ts", 0))
    return messages[-limit:]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_WIDTH = 76
_DEAD_THRESHOLD = 120.0
_STALE_THRESHOLD = 60.0


def _ts_short(ts: float) -> str:
    """Format a Unix timestamp as HH:MM:SS."""
    if not ts:
        return "never"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


def _age_str(ts: float) -> str:
    """Format elapsed time since a timestamp."""
    if not ts:
        return "n/a"
    age = time.time() - ts
    if age < 0:
        return "future"
    if age < 60:
        return f"{age:.0f}s ago"
    if age < 3600:
        return f"{age / 60:.0f}m ago"
    return f"{age / 3600:.1f}h ago"


def _uptime_str(seconds: float) -> str:
    """Format seconds as a human-readable uptime string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}h {int(m)}m"


def _agent_status_badge(status: str, last_hb: float) -> str:
    """Return a colorized status badge for an agent."""
    age = time.time() - last_hb if last_hb else float("inf")
    if status in ("complete", "stopped"):
        return _dim("[DONE]")
    if status != "alive" or age > _DEAD_THRESHOLD:
        return _err("[DEAD]")
    if age > _STALE_THRESHOLD:
        return _warn("[WARN]")
    return _ok("[ OK ]")


def _separator(char: str = "=") -> str:
    return char * _WIDTH


def _section_header(title: str) -> str:
    pad = _WIDTH - len(title) - 4
    left = pad // 2
    right = pad - left
    return f"{'=' * left}  {_bold(title)}  {'=' * right}"


def _sub_header(title: str) -> str:
    pad = _WIDTH - len(title) - 4
    left = pad // 2
    right = pad - left
    return f"{'-' * left}  {_cyan(title)}  {'-' * right}"


def _print_table(headers: list[str], rows: list[list[str]],
                 col_widths: list[int] | None = None) -> None:
    """Print a simple formatted table."""
    if not col_widths:
        col_widths = []
        for i, h in enumerate(headers):
            max_w = len(h)
            for row in rows:
                if i < len(row):
                    # Strip ANSI codes for width calculation
                    clean = row[i]
                    for code in ("\033[0m", "\033[1m", "\033[2m",
                                 "\033[31m", "\033[32m", "\033[33m",
                                 "\033[34m", "\033[35m", "\033[36m",
                                 "\033[37m"):
                        clean = clean.replace(code, "")
                    max_w = max(max_w, len(clean))
            col_widths.append(min(max_w + 2, 40))

    # Header line
    header_line = ""
    for i, h in enumerate(headers):
        header_line += _bold(h.ljust(col_widths[i]))
    print(f"  {header_line}")
    print(f"  {'-' * sum(col_widths)}")

    for row in rows:
        line = ""
        for i, cell in enumerate(row):
            if i < len(col_widths):
                # Pad based on visible length (strip ANSI for counting)
                clean = cell
                for code in ("\033[0m", "\033[1m", "\033[2m",
                             "\033[31m", "\033[32m", "\033[33m",
                             "\033[34m", "\033[35m", "\033[36m",
                             "\033[37m"):
                    clean = clean.replace(code, "")
                padding = col_widths[i] - len(clean)
                line += cell + " " * max(padding, 1)
        print(f"  {line}")


# ---------------------------------------------------------------------------
# Subcommand: launch
# ---------------------------------------------------------------------------

def cmd_launch(args: argparse.Namespace) -> int:
    """Launch teams using the multi_team_runner."""
    # Build the command to invoke multi_team_runner.py
    runner_script = os.path.join(_SCRIPT_DIR, "multi_team_runner.py")
    if not os.path.exists(runner_script):
        print(_err("Error: multi_team_runner.py not found"))
        return 1

    # Check if already running
    pid_str = _get_runner_kv("pid")
    status = _get_runner_kv("status")
    if pid_str and status == "running":
        pid = int(pid_str)
        try:
            os.kill(pid, 0)  # Check if process exists
            print(_warn(f"Runner already active (pid={pid}). "
                        f"Use 'stop' first or 'restart'."))
            return 1
        except (ProcessLookupError, PermissionError):
            pass  # Process is gone, safe to start

    # Build argv for the runner
    runner_args = [sys.executable, runner_script]
    if args.config:
        runner_args.extend(["--config", args.config])
    else:
        runner_args.extend(["--teams", str(args.teams)])
        runner_args.extend(["--agents-per-team", str(args.agents_per_team)])

    if args.foreground:
        runner_args.append("--foreground")
    else:
        runner_args.append("--no-foreground")

    print(_bold("Launching multi-agent teams..."))
    if args.config:
        print(f"  Config: {args.config}")
    else:
        print(f"  Teams: {args.teams}, Agents/team: {args.agents_per_team}")

    if args.dry_run:
        print(f"\n  {_dim('Dry run -- would execute:')}")
        print(f"  {' '.join(runner_args)}")
        return 0

    # Execute the runner
    os.execv(sys.executable, runner_args)
    return 0  # unreachable after exec


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """Show status of running agents and teams."""
    print()
    print(_section_header("SDK AGENT STATUS"))
    print()

    # Runner info
    runner_status = _get_runner_kv("status") or "not started"
    runner_pid = _get_runner_kv("pid")
    runner_started = _get_runner_kv("started_at")
    runner_phase = _get_runner_kv("phase") or "unknown"

    pid_alive = False
    if runner_pid:
        try:
            os.kill(int(runner_pid), 0)
            pid_alive = True
        except (ProcessLookupError, PermissionError, ValueError):
            pass

    if runner_status == "running" and pid_alive:
        status_display = _ok("RUNNING")
    elif runner_status == "running" and not pid_alive:
        status_display = _err("STALE (process gone)")
    elif runner_status == "stopped":
        status_display = _dim("STOPPED")
    else:
        status_display = _warn(runner_status.upper())

    uptime = ""
    if runner_started:
        try:
            uptime = _uptime_str(time.time() - float(runner_started))
        except (ValueError, TypeError):
            uptime = "?"

    print(f"  Runner:  {status_display}")
    if runner_pid:
        print(f"  PID:     {runner_pid}" + (" (alive)" if pid_alive else " (dead)"))
    if uptime:
        print(f"  Uptime:  {uptime}")
    print(f"  Phase:   {runner_phase}")
    print(f"  DB:      {_DB_PATH}")
    print(f"  Bus:     {_BUS_DIR}")
    print()

    # Agent listing
    team_filter = args.team if hasattr(args, "team") and args.team else None
    if team_filter:
        agents = _query(
            "SELECT agent_id, team, role, status, pid, last_heartbeat, "
            "registered_at FROM agents WHERE team = ? ORDER BY role, agent_id",
            (team_filter,),
        )
    else:
        agents = _query(
            "SELECT agent_id, team, role, status, pid, last_heartbeat, "
            "registered_at FROM agents ORDER BY team, role, agent_id",
        )

    if not agents:
        print(_dim("  No agents registered"))
        print()
        return 0

    # Group by team
    by_team: dict[str, list[dict]] = defaultdict(list)
    for a in agents:
        by_team[a["team"]].append(a)

    alive = dead = stale = done = 0
    print(_sub_header("AGENTS"))
    print()

    for team_name in sorted(by_team.keys()):
        team_agents = by_team[team_name]
        print(f"  {_bold(team_name)}")

        rows = []
        for a in team_agents:
            badge = _agent_status_badge(a["status"], a["last_heartbeat"])
            hb = _age_str(a["last_heartbeat"])
            pid_s = str(a.get("pid", "?"))

            # Count stats
            age = time.time() - a["last_heartbeat"] if a["last_heartbeat"] else float("inf")
            if a["status"] in ("complete", "stopped"):
                done += 1
            elif a["status"] != "alive" or age > _DEAD_THRESHOLD:
                dead += 1
            elif age > _STALE_THRESHOLD:
                stale += 1
            else:
                alive += 1

            rows.append([badge, a["agent_id"], a["role"], f"pid={pid_s}", f"hb={hb}"])

        _print_table(
            ["Status", "Agent ID", "Role", "PID", "Heartbeat"],
            rows,
        )
        print()

    # Summary
    total = alive + dead + stale + done
    parts = []
    if alive:
        parts.append(_ok(f"{alive} alive"))
    if stale:
        parts.append(_warn(f"{stale} stale"))
    if dead:
        parts.append(_err(f"{dead} dead"))
    if done:
        parts.append(_dim(f"{done} done"))
    print(f"  Total: {total} agents -- {', '.join(parts)}")

    # Findings count
    findings = _query_one("SELECT COUNT(*) as cnt FROM team_findings")
    if findings and findings["cnt"] > 0:
        print(f"  Findings: {findings['cnt']}")

    # Bus channel count
    if os.path.isdir(_BUS_DIR):
        channels = glob.glob(os.path.join(_BUS_DIR, "*.jsonl"))
        print(f"  Bus channels: {len(channels)}")

    print()

    if args.json:
        data = {
            "runner": {
                "status": runner_status,
                "pid": runner_pid,
                "phase": runner_phase,
                "uptime": uptime,
            },
            "agents": agents,
            "summary": {
                "alive": alive,
                "stale": stale,
                "dead": dead,
                "done": done,
                "total": total,
            },
        }
        print(json.dumps(data, indent=2, default=str))

    return 0


# ---------------------------------------------------------------------------
# Subcommand: stop
# ---------------------------------------------------------------------------

def cmd_stop(args: argparse.Namespace) -> int:
    """Gracefully stop running agents or the entire runner."""
    pid_str = _get_runner_kv("pid")
    status = _get_runner_kv("status")

    if not pid_str:
        print(_warn("No runner PID found. Nothing to stop."))
        return 1

    pid = int(pid_str)

    if args.team:
        # Mark specific team agents as stopped in the DB
        conn = _get_db()
        if conn is None:
            print(_err("Cannot connect to database"))
            return 1
        try:
            cur = conn.execute(
                "UPDATE agents SET status = 'stopped' WHERE team = ?",
                (args.team,),
            )
            conn.commit()
            count = cur.rowcount
            print(_ok(f"Marked {count} agents in team '{args.team}' as stopped"))
        except sqlite3.OperationalError as e:
            print(_err(f"Database error: {e}"))
            return 1
        finally:
            conn.close()
        return 0

    # Stop the whole runner
    print(f"Sending SIGTERM to runner (pid={pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        print(_ok(f"Shutdown signal sent to pid={pid}"))

        # Wait briefly for process to exit
        if args.wait:
            deadline = time.time() + args.timeout
            while time.time() < deadline:
                try:
                    os.kill(pid, 0)
                    time.sleep(0.5)
                except ProcessLookupError:
                    print(_ok("Runner has exited"))
                    return 0
            print(_warn("Runner still running after timeout"))
    except ProcessLookupError:
        print(_warn(f"Process {pid} not found (already stopped?)"))
        # Update state
        conn = _get_db()
        if conn:
            try:
                conn.execute(
                    "INSERT INTO runner_state (key, value, updated_at) "
                    "VALUES ('status', 'stopped', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value='stopped', "
                    "updated_at=excluded.updated_at",
                    (time.time(),),
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass
            finally:
                conn.close()
    except PermissionError:
        print(_err(f"Permission denied sending signal to pid={pid}"))
        return 1

    return 0


# ---------------------------------------------------------------------------
# Subcommand: restart
# ---------------------------------------------------------------------------

def cmd_restart(args: argparse.Namespace) -> int:
    """Restart failed agents or teams."""
    if args.agent:
        # Re-register a specific agent
        conn = _get_db()
        if conn is None:
            print(_err("Cannot connect to database"))
            return 1
        try:
            row = conn.execute(
                "SELECT * FROM agents WHERE agent_id = ?",
                (args.agent,),
            ).fetchone()
            if not row:
                print(_err(f"Agent '{args.agent}' not found"))
                return 1
            row = dict(row)
            conn.execute(
                "UPDATE agents SET status = 'alive', last_heartbeat = ? "
                "WHERE agent_id = ?",
                (time.time(), args.agent),
            )
            conn.commit()
            print(_ok(f"Reset agent '{args.agent}' to alive"))
            print(f"  Team: {row['team']}, Role: {row['role']}")
        except sqlite3.OperationalError as e:
            print(_err(f"Database error: {e}"))
            return 1
        finally:
            conn.close()
        return 0

    if args.team:
        # Re-register all agents in a team
        conn = _get_db()
        if conn is None:
            print(_err("Cannot connect to database"))
            return 1
        try:
            cur = conn.execute(
                "UPDATE agents SET status = 'alive', last_heartbeat = ? "
                "WHERE team = ?",
                (time.time(), args.team),
            )
            conn.commit()
            print(_ok(f"Reset {cur.rowcount} agents in team '{args.team}' to alive"))
        except sqlite3.OperationalError as e:
            print(_err(f"Database error: {e}"))
            return 1
        finally:
            conn.close()
        return 0

    # Restart all dead/stale agents
    conn = _get_db()
    if conn is None:
        print(_err("Cannot connect to database"))
        return 1
    try:
        cutoff = time.time() - _DEAD_THRESHOLD
        cur = conn.execute(
            "UPDATE agents SET status = 'alive', last_heartbeat = ? "
            "WHERE (status != 'alive' AND status != 'complete') "
            "OR (status = 'alive' AND last_heartbeat < ?)",
            (time.time(), cutoff),
        )
        conn.commit()
        count = cur.rowcount
        if count:
            print(_ok(f"Reset {count} dead/stale agents to alive"))
        else:
            print(_dim("No dead or stale agents found"))
    except sqlite3.OperationalError as e:
        print(_err(f"Database error: {e}"))
        return 1
    finally:
        conn.close()

    return 0


# ---------------------------------------------------------------------------
# Subcommand: logs
# ---------------------------------------------------------------------------

def cmd_logs(args: argparse.Namespace) -> int:
    """View or tail agent logs and bus messages."""
    if args.source == "runner" or (not args.source and not args.channel):
        # Show runner log file
        if not os.path.exists(_LOG_FILE):
            print(_dim("No runner log file found"))
            return 0

        print(_section_header("RUNNER LOG"))
        print(f"  {_dim(_LOG_FILE)}")
        print()

        try:
            with open(_LOG_FILE, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                read_size = min(size, args.bytes)
                f.seek(max(0, size - read_size))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError as e:
            print(_err(f"Cannot read log: {e}"))
            return 1

        lines = tail.strip().split("\n")
        for line in lines[-args.tail:]:
            # Colorize log levels
            if " [ERROR] " in line or " [CRITICAL] " in line:
                print(f"  {_err(line)}")
            elif " [WARNING] " in line:
                print(f"  {_warn(line)}")
            elif " [INFO] " in line:
                print(f"  {line}")
            else:
                print(f"  {_dim(line)}")
        print()
        return 0

    if args.source == "bus" or args.channel:
        # Show bus messages
        channel = args.channel
        print(_section_header("BUS MESSAGES"))
        if channel:
            print(f"  Channel: {channel}")
        print()

        messages = _read_bus_messages(channel=channel, limit=args.tail)

        if not messages:
            print(_dim("  No messages found"))
            print()
            return 0

        # Filter by team if specified
        if args.team:
            messages = [m for m in messages if m.get("team") == args.team]

        for msg in messages:
            ts = _ts_short(msg.get("ts", 0))
            agent = msg.get("agent_id", "?")
            team = msg.get("team", "?")
            msg_type = msg.get("type", "?")
            body = msg.get("body", {})

            # Format body compactly
            if isinstance(body, dict):
                event = body.get("event", "")
                if event:
                    body_str = event
                else:
                    body_str = json.dumps(body, separators=(",", ":"))
                    if len(body_str) > 50:
                        body_str = body_str[:47] + "..."
            else:
                body_str = str(body)[:50]

            type_color = {
                "info": _cyan,
                "phase-signal": _magenta,
                "blocker": _err,
                "heartbeat": _dim,
                "request": _warn,
                "response": _ok,
            }.get(msg_type, _dim)

            print(f"  {_dim(ts)} {type_color(msg_type):<22} "
                  f"{agent:<20} {body_str}")
        print()
        return 0

    # Default: show both
    cmd_logs_args = argparse.Namespace(**vars(args))
    cmd_logs_args.source = "runner"
    cmd_logs(cmd_logs_args)
    cmd_logs_args.source = "bus"
    cmd_logs(cmd_logs_args)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: config
# ---------------------------------------------------------------------------

def cmd_config(args: argparse.Namespace) -> int:
    """Validate or generate configuration files."""
    if args.validate:
        return _config_validate(args.validate)

    if args.generate:
        return _config_generate(args.generate[0], args.generate[1],
                                output=args.output)

    if args.show:
        return _config_show_current()

    # Default: show current
    return _config_show_current()


def _config_validate(path: str) -> int:
    """Validate a configuration JSON file."""
    print(_bold(f"Validating: {path}"))
    print()

    if not os.path.exists(path):
        print(_err(f"  File not found: {path}"))
        return 1

    try:
        with open(path) as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        print(_err(f"  Invalid JSON: {e}"))
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    # Check required fields
    if "teams" not in cfg:
        errors.append("Missing required field: 'teams'")
    elif not isinstance(cfg["teams"], list):
        errors.append("'teams' must be a list")
    else:
        seen_names: set[str] = set()
        seen_agents: set[str] = set()
        for i, team in enumerate(cfg["teams"]):
            prefix = f"teams[{i}]"
            if "name" not in team:
                errors.append(f"{prefix}: missing 'name'")
            elif team["name"] in seen_names:
                errors.append(f"{prefix}: duplicate team name '{team['name']}'")
            else:
                seen_names.add(team["name"])

            if "agents" not in team:
                errors.append(f"{prefix}: missing 'agents'")
            elif not isinstance(team["agents"], list):
                errors.append(f"{prefix}: 'agents' must be a list")
            elif len(team["agents"]) == 0:
                warnings.append(f"{prefix}: team has no agents")
            else:
                has_coordinator = False
                for j, agent in enumerate(team["agents"]):
                    ap = f"{prefix}.agents[{j}]"
                    if "agent_id" not in agent:
                        errors.append(f"{ap}: missing 'agent_id'")
                    elif agent["agent_id"] in seen_agents:
                        errors.append(f"{ap}: duplicate agent_id "
                                      f"'{agent['agent_id']}'")
                    else:
                        seen_agents.add(agent["agent_id"])

                    if "role" not in agent:
                        errors.append(f"{ap}: missing 'role'")
                    elif agent["role"] == "coordinator":
                        has_coordinator = True

                if not has_coordinator:
                    warnings.append(f"{prefix}: no coordinator agent "
                                    f"(recommended)")

    # Optional fields
    for key in ("heartbeat_interval", "dead_agent_timeout", "cleanup_interval"):
        if key in cfg and not isinstance(cfg[key], (int, float)):
            errors.append(f"'{key}' must be a number")
        elif key in cfg and cfg[key] <= 0:
            errors.append(f"'{key}' must be positive")

    # Report
    if errors:
        print(_err(f"  INVALID -- {len(errors)} error(s):"))
        for e in errors:
            print(f"    {_err('x')} {e}")
    else:
        total_teams = len(cfg.get("teams", []))
        total_agents = sum(
            len(t.get("agents", []))
            for t in cfg.get("teams", [])
        )
        print(_ok(f"  VALID -- {total_teams} teams, {total_agents} agents"))

    if warnings:
        print()
        print(_warn(f"  {len(warnings)} warning(s):"))
        for w in warnings:
            print(f"    {_warn('!')} {w}")

    print()
    return 1 if errors else 0


def _config_generate(num_teams: int, agents_per_team: int,
                     output: str | None = None) -> int:
    """Generate a configuration file."""
    teams = []
    for i in range(1, num_teams + 1):
        name = f"team-{i:02d}"
        agents = [{"agent_id": f"{name}-lead", "role": "coordinator"}]
        for j in range(1, agents_per_team):
            agents.append({
                "agent_id": f"{name}-worker-{j}",
                "role": "worker",
            })
        teams.append({"name": name, "agents": agents})

    cfg = {
        "teams": teams,
        "heartbeat_interval": 30,
        "dead_agent_timeout": 120,
        "cleanup_interval": 600,
    }

    formatted = json.dumps(cfg, indent=2)

    if output:
        with open(output, "w") as f:
            f.write(formatted + "\n")
        total_agents = sum(len(t["agents"]) for t in teams)
        print(_ok(f"Generated config: {num_teams} teams, "
                  f"{total_agents} agents -> {output}"))
    else:
        print(formatted)

    return 0


def _config_show_current() -> int:
    """Show the currently stored runner configuration."""
    cfg_str = _get_runner_kv("config")
    if not cfg_str:
        print(_dim("No runner configuration stored"))
        return 0

    try:
        cfg = json.loads(cfg_str)
        print(_bold("Current runner configuration:"))
        print()
        print(json.dumps(cfg, indent=2))
    except json.JSONDecodeError:
        print(_warn("Stored configuration is not valid JSON"))
        print(cfg_str)

    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="sdk_cli",
        description="CLI interface for the SDK multi-agent team launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              %(prog)s launch --teams 3 --agents-per-team 2
              %(prog)s launch --config teams.json --foreground
              %(prog)s status
              %(prog)s status --team team-01 --json
              %(prog)s stop
              %(prog)s stop --team team-02
              %(prog)s restart --agent team-01-worker-1
              %(prog)s logs --tail 100
              %(prog)s logs --source bus --channel global
              %(prog)s config --generate 4 3 --output teams.json
              %(prog)s config --validate teams.json
        """),
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # -- launch -------
    p_launch = sub.add_parser(
        "launch",
        help="Start teams from config or inline args",
        description="Launch multi-agent teams. Uses multi_team_runner.py.",
    )
    launch_src = p_launch.add_mutually_exclusive_group()
    launch_src.add_argument(
        "--config", type=str, metavar="PATH",
        help="Path to team config JSON file",
    )
    p_launch.add_argument(
        "--teams", type=int, default=3,
        help="Number of teams (default: 3)",
    )
    p_launch.add_argument(
        "--agents-per-team", type=int, default=2,
        help="Agents per team (default: 2)",
    )
    p_launch.add_argument(
        "--foreground", action="store_true", default=True,
        help="Run in foreground (default)",
    )
    p_launch.add_argument(
        "--no-foreground", action="store_false", dest="foreground",
        help="Run in background mode",
    )
    p_launch.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be executed without running",
    )

    # -- status -------
    p_status = sub.add_parser(
        "status",
        help="Show running agents and their health",
        description="Display agent status, health, and message counts.",
    )
    p_status.add_argument(
        "--team", type=str,
        help="Filter by team name",
    )
    p_status.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )

    # -- stop ---------
    p_stop = sub.add_parser(
        "stop",
        help="Graceful shutdown of all or specific teams",
        description="Stop the runner or specific teams.",
    )
    p_stop.add_argument(
        "--team", type=str,
        help="Stop only agents in this team",
    )
    p_stop.add_argument(
        "--wait", action="store_true",
        help="Wait for the process to exit",
    )
    p_stop.add_argument(
        "--timeout", type=float, default=30.0,
        help="Max seconds to wait (with --wait, default: 30)",
    )

    # -- restart ------
    p_restart = sub.add_parser(
        "restart",
        help="Restart failed agents or teams",
        description="Reset dead/stale agents back to alive status.",
    )
    p_restart.add_argument(
        "--team", type=str,
        help="Restart all agents in this team",
    )
    p_restart.add_argument(
        "--agent", type=str,
        help="Restart a specific agent by ID",
    )

    # -- logs ---------
    p_logs = sub.add_parser(
        "logs",
        help="View or tail agent logs",
        description="View runner logs and bus messages.",
    )
    p_logs.add_argument(
        "--source", choices=["runner", "bus"],
        help="Log source: runner log file or bus messages",
    )
    p_logs.add_argument(
        "--channel", type=str,
        help="Bus channel to read (implies --source bus)",
    )
    p_logs.add_argument(
        "--team", type=str,
        help="Filter bus messages by team",
    )
    p_logs.add_argument(
        "--tail", type=int, default=50,
        help="Number of lines/messages to show (default: 50)",
    )
    p_logs.add_argument(
        "--bytes", type=int, default=65536,
        help="Max bytes to read from log file (default: 64KB)",
    )

    # -- config -------
    p_config = sub.add_parser(
        "config",
        help="Validate or generate config files",
        description="Work with team configuration files.",
    )
    config_action = p_config.add_mutually_exclusive_group()
    config_action.add_argument(
        "--validate", type=str, metavar="PATH",
        help="Validate a config JSON file",
    )
    config_action.add_argument(
        "--generate", type=int, nargs=2,
        metavar=("TEAMS", "AGENTS_PER_TEAM"),
        help="Generate a config with N teams and M agents each",
    )
    config_action.add_argument(
        "--show", action="store_true",
        help="Show the current runner configuration",
    )
    p_config.add_argument(
        "--output", "-o", type=str,
        help="Output file path (for --generate)",
    )

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_SUBCOMMAND_MAP = {
    "launch": cmd_launch,
    "status": cmd_status,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "logs": cmd_logs,
    "config": cmd_config,
}


def main() -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    handler = _SUBCOMMAND_MAP.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
