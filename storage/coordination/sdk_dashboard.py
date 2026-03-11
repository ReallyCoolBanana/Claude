#!/usr/bin/env python3
"""Enhanced dashboard for SDK-launched multi-agent teams.

Builds on the existing dashboard.py patterns to provide a richer view of
SDK-launched agents, including:
  - Team status grid with color-coded health indicators
  - Individual agent health with heartbeat timing
  - Message bus activity with channel breakdown
  - Restart history and phase transitions
  - Auto-refreshing terminal UI

Usage:
    python sdk_dashboard.py                    # Full dashboard, one-shot
    python sdk_dashboard.py --watch            # Auto-refresh every 3 seconds
    python sdk_dashboard.py --watch --interval 1
    python sdk_dashboard.py --section agents   # Only agent section
    python sdk_dashboard.py --section bus      # Only bus activity
    python sdk_dashboard.py --section grid     # Only team grid
    python sdk_dashboard.py --compact          # Compact single-line-per-agent view
    python sdk_dashboard.py --json             # Machine-readable JSON output

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
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
# Thresholds
# ---------------------------------------------------------------------------

_DEAD_THRESHOLD = 120.0   # seconds without heartbeat -> dead
_STALE_THRESHOLD = 60.0   # seconds without heartbeat -> warning
_WIDTH = 80               # terminal width target
_MAX_BUS_MSGS = 8         # messages per channel
_MAX_RESTART_EVENTS = 10  # restart events to show

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_COLOR_ENABLED = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _C:
    """ANSI escape codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"
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
    BG_BLUE = "\033[44m"

    @classmethod
    def disable(cls) -> None:
        for attr in dir(cls):
            if attr.isupper() and not attr.startswith("_"):
                setattr(cls, attr, "")


if not _COLOR_ENABLED or os.environ.get("NO_COLOR"):
    _C.disable()
if os.environ.get("FORCE_COLOR"):
    # Re-enable -- reload defaults
    _C.RESET = "\033[0m"
    _C.BOLD = "\033[1m"
    _C.DIM = "\033[2m"
    _C.RED = "\033[31m"
    _C.GREEN = "\033[32m"
    _C.YELLOW = "\033[33m"
    _C.BLUE = "\033[34m"
    _C.MAGENTA = "\033[35m"
    _C.CYAN = "\033[36m"


def _c(text: str, *codes: str) -> str:
    """Apply ANSI codes to text."""
    if not codes:
        return text
    return "".join(codes) + text + _C.RESET


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------

class _DB:
    """Context manager for a shared read-only DB connection."""

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "_DB":
        if os.path.exists(_DB_PATH):
            try:
                self.conn = sqlite3.connect(_DB_PATH, timeout=5)
                self.conn.row_factory = sqlite3.Row
            except sqlite3.OperationalError:
                self.conn = None
        return self

    def __exit__(self, *exc: Any) -> bool:
        if self.conn is not None:
            self.conn.close()
            self.conn = None
        return False

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        if self.conn is None:
            return []
        try:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    def query_one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def kv(self, key: str) -> str | None:
        row = self.query_one(
            "SELECT value FROM runner_state WHERE key = ?", (key,),
        )
        return row["value"] if row else None


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------

def _collect_agents(db: _DB) -> list[dict]:
    return db.query(
        "SELECT agent_id, team, role, status, pid, last_heartbeat, "
        "registered_at FROM agents ORDER BY team, role, agent_id"
    )


def _collect_runner_info(db: _DB) -> dict:
    status = db.kv("status") or "not started"
    pid = db.kv("pid")
    started = db.kv("started_at")
    phase = db.kv("phase") or "unknown"

    pid_alive = False
    if pid:
        try:
            os.kill(int(pid), 0)
            pid_alive = True
        except (ProcessLookupError, PermissionError, ValueError):
            pass

    uptime = 0.0
    if started:
        try:
            uptime = time.time() - float(started)
        except (ValueError, TypeError):
            pass

    return {
        "status": status,
        "pid": pid,
        "pid_alive": pid_alive,
        "started_at": started,
        "uptime": uptime,
        "phase": phase,
    }


def _collect_phase_signals(db: _DB, limit: int = 10) -> list[dict]:
    return db.query(
        "SELECT phase, signal_type, agent_id, ts, data "
        "FROM phase_signals ORDER BY ts DESC LIMIT ?",
        (limit,),
    )


def _collect_findings_summary(db: _DB) -> dict:
    total_row = db.query_one("SELECT COUNT(*) as cnt FROM team_findings")
    total = total_row["cnt"] if total_row else 0

    by_team = {}
    for row in db.query(
        "SELECT team, COUNT(*) as cnt FROM team_findings GROUP BY team"
    ):
        by_team[row["team"]] = row["cnt"]

    by_priority = {}
    for row in db.query(
        "SELECT priority, COUNT(*) as cnt FROM team_findings GROUP BY priority"
    ):
        by_priority[row["priority"]] = row["cnt"]

    return {"total": total, "by_team": by_team, "by_priority": by_priority}


def _collect_bus_activity() -> dict[str, list[dict]]:
    """Read recent messages from all bus channels."""
    activity: dict[str, list[dict]] = {}
    if not os.path.isdir(_BUS_DIR):
        return activity

    now = time.time()
    for filepath in sorted(glob.glob(os.path.join(_BUS_DIR, "*.jsonl"))):
        channel = os.path.basename(filepath).rsplit(".", 1)[0]
        try:
            with open(filepath, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                read_size = min(size, 32768)
                f.seek(max(0, size - read_size))
                tail = f.read().decode("utf-8", errors="replace")
        except OSError:
            continue

        messages: list[dict] = []
        for line in tail.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                try:
                    ts = float(msg.get("ts", 0))
                    ttl = float(msg.get("ttl", 300))
                except (ValueError, TypeError):
                    continue
                if ts + ttl >= now:
                    messages.append(msg)
            except json.JSONDecodeError:
                continue

        if messages:
            activity[channel] = messages[-_MAX_BUS_MSGS:]

    return activity


def _collect_restart_events() -> list[dict]:
    """Scan bus messages for restart/dead agent events."""
    events: list[dict] = []
    global_path = os.path.join(_BUS_DIR, "global.jsonl")
    if not os.path.exists(global_path):
        return events

    try:
        with open(global_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, 65536)
            f.seek(max(0, size - read_size))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return events

    for line in tail.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            body = msg.get("body", {})
            event = body.get("event", "")
            if event in ("agent-dead", "runner-started", "runner-stopped",
                         "agents-registered", "phase-transition"):
                events.append({
                    "ts": msg.get("ts", 0),
                    "event": event,
                    "agent_id": body.get("agent_id", msg.get("agent_id", "")),
                    "team": body.get("team", msg.get("team", "")),
                    "detail": body,
                })
        except json.JSONDecodeError:
            continue

    events.sort(key=lambda e: e["ts"])
    return events[-_MAX_RESTART_EVENTS:]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _ts_fmt(ts: float) -> str:
    if not ts:
        return "never"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


def _age_fmt(ts: float) -> str:
    if not ts:
        return "n/a"
    age = time.time() - ts
    if age < 0:
        return "future"
    if age < 60:
        return f"{age:.0f}s"
    if age < 3600:
        return f"{age / 60:.0f}m"
    return f"{age / 3600:.1f}h"


def _uptime_fmt(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m{int(s)}s"
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{int(h)}h{int(m)}m"


def _agent_health(status: str, last_hb: float) -> str:
    """Classify agent health: 'ok', 'stale', 'dead', 'done'."""
    if status in ("complete", "stopped"):
        return "done"
    age = time.time() - last_hb if last_hb else float("inf")
    if status != "alive" or age > _DEAD_THRESHOLD:
        return "dead"
    if age > _STALE_THRESHOLD:
        return "stale"
    return "ok"


def _health_badge(health: str) -> str:
    """Colorized badge for agent health."""
    badges = {
        "ok":    _c("[ OK ]", _C.GREEN),
        "stale": _c("[WARN]", _C.YELLOW),
        "dead":  _c("[DEAD]", _C.RED),
        "done":  _c("[DONE]", _C.DIM),
    }
    return badges.get(health, _c("[????]", _C.DIM))


def _bar(value: int, max_value: int, width: int = 20,
         fill_char: str = "#", empty_char: str = ".") -> str:
    """Simple text progress bar."""
    if max_value <= 0:
        return empty_char * width
    filled = min(int(value / max_value * width), width)
    return fill_char * filled + empty_char * (width - filled)


def _header_line(title: str) -> str:
    pad = _WIDTH - len(title) - 4
    left = pad // 2
    right = pad - left
    return f"{'=' * left}  {_c(title, _C.BOLD)}  {'=' * right}"


def _sub_line(title: str) -> str:
    pad = _WIDTH - len(title) - 4
    left = pad // 2
    right = pad - left
    return f"{'-' * left}  {_c(title, _C.CYAN)}  {'-' * right}"


# ---------------------------------------------------------------------------
# Render sections
# ---------------------------------------------------------------------------

def _render_title(runner: dict) -> list[str]:
    """Render the dashboard header with runner info."""
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        _header_line("SDK AGENT DASHBOARD"),
        f"  {_c('Generated:', _C.DIM)} {now_str}",
    ]

    # Runner status line
    st = runner["status"]
    if st == "running" and runner["pid_alive"]:
        st_display = _c("RUNNING", _C.GREEN, _C.BOLD)
    elif st == "running" and not runner["pid_alive"]:
        st_display = _c("STALE", _C.RED, _C.BOLD)
    elif st == "stopped":
        st_display = _c("STOPPED", _C.DIM)
    else:
        st_display = _c(st.upper(), _C.YELLOW)

    pid_str = runner["pid"] or "?"
    up_str = _uptime_fmt(runner["uptime"]) if runner["uptime"] > 0 else "n/a"
    phase = runner["phase"]

    lines.append(
        f"  Runner: {st_display}  "
        f"pid={pid_str}  uptime={up_str}  phase={_c(phase, _C.MAGENTA)}"
    )
    lines.append("")
    return lines


def _render_team_grid(agents: list[dict]) -> list[str]:
    """Render a compact team status grid."""
    lines = [_sub_line("TEAM GRID")]

    if not agents:
        lines.append("  No agents registered")
        lines.append("")
        return lines

    by_team: dict[str, list[dict]] = defaultdict(list)
    for a in agents:
        by_team[a["team"]].append(a)

    # Grid header
    lines.append("")
    lines.append(f"  {'Team':<16} {'Agents':>7}  {'Health':>26}  {'Status'}")
    lines.append(f"  {'-' * 16} {'-' * 7}  {'-' * 26}  {'-' * 16}")

    for team_name in sorted(by_team.keys()):
        team_agents = by_team[team_name]
        total = len(team_agents)

        counts = {"ok": 0, "stale": 0, "dead": 0, "done": 0}
        for a in team_agents:
            h = _agent_health(a["status"], a["last_heartbeat"])
            counts[h] += 1

        # Health mini-bar
        bar_parts: list[str] = []
        for _ in range(counts["ok"]):
            bar_parts.append(_c("#", _C.GREEN))
        for _ in range(counts["stale"]):
            bar_parts.append(_c("~", _C.YELLOW))
        for _ in range(counts["dead"]):
            bar_parts.append(_c("X", _C.RED))
        for _ in range(counts["done"]):
            bar_parts.append(_c(".", _C.DIM))
        # Pad to fixed width
        while len(bar_parts) < 10:
            bar_parts.append(" ")
        health_bar = "[" + "".join(bar_parts[:10]) + "]"

        # Compact status summary
        parts: list[str] = []
        if counts["ok"]:
            parts.append(_c(f"{counts['ok']}ok", _C.GREEN))
        if counts["stale"]:
            parts.append(_c(f"{counts['stale']}stale", _C.YELLOW))
        if counts["dead"]:
            parts.append(_c(f"{counts['dead']}dead", _C.RED))
        if counts["done"]:
            parts.append(_c(f"{counts['done']}done", _C.DIM))
        status_str = " ".join(parts)

        lines.append(
            f"  {_c(team_name, _C.BOLD):<28} {total:>3}    "
            f"{health_bar}  {status_str}"
        )

    lines.append("")
    return lines


def _render_agents_detail(agents: list[dict]) -> list[str]:
    """Render detailed per-agent information."""
    lines = [_sub_line("AGENT DETAIL")]

    if not agents:
        lines.append("  No agents registered")
        lines.append("")
        return lines

    by_team: dict[str, list[dict]] = defaultdict(list)
    for a in agents:
        by_team[a["team"]].append(a)

    alive_total = dead_total = stale_total = done_total = 0

    for team_name in sorted(by_team.keys()):
        team_agents = by_team[team_name]
        lines.append(f"  {_c(team_name, _C.BOLD, _C.UNDERLINE)}")

        for a in team_agents:
            health = _agent_health(a["status"], a["last_heartbeat"])
            badge = _health_badge(health)
            hb = _age_fmt(a["last_heartbeat"])
            pid_s = str(a.get("pid") or "?")
            role = a["role"]

            if health == "ok":
                alive_total += 1
            elif health == "stale":
                stale_total += 1
            elif health == "dead":
                dead_total += 1
            else:
                done_total += 1

            agent_name = a["agent_id"]
            # Highlight coordinators
            if role == "coordinator":
                role_display = _c(role, _C.MAGENTA)
            else:
                role_display = _c(role, _C.DIM)

            lines.append(
                f"    {badge} {agent_name:<26} {role_display:<22} "
                f"pid={pid_s:<7} hb={hb}"
            )

        lines.append("")

    # Summary
    total = alive_total + dead_total + stale_total + done_total
    summary_parts = []
    if alive_total:
        summary_parts.append(_c(f"{alive_total} alive", _C.GREEN))
    if stale_total:
        summary_parts.append(_c(f"{stale_total} stale", _C.YELLOW))
    if dead_total:
        summary_parts.append(_c(f"{dead_total} dead", _C.RED))
    if done_total:
        summary_parts.append(_c(f"{done_total} done", _C.DIM))
    lines.append(f"  Total: {total} agents -- {', '.join(summary_parts)}")
    lines.append("")
    return lines


def _render_agents_compact(agents: list[dict]) -> list[str]:
    """Render a compact single-line-per-agent view."""
    lines = [_sub_line("AGENTS (compact)")]

    if not agents:
        lines.append("  No agents registered")
        lines.append("")
        return lines

    for a in agents:
        health = _agent_health(a["status"], a["last_heartbeat"])
        badge = _health_badge(health)
        hb = _age_fmt(a["last_heartbeat"])
        lines.append(
            f"  {badge} {a['team']:<14} {a['agent_id']:<26} "
            f"{a['role']:<12} hb={hb}"
        )

    lines.append("")
    return lines


def _render_bus_activity(activity: dict[str, list[dict]]) -> list[str]:
    """Render message bus activity."""
    lines = [_sub_line("MESSAGE BUS")]

    if not activity:
        lines.append("  No bus activity")
        lines.append("")
        return lines

    total_msgs = sum(len(msgs) for msgs in activity.values())
    lines.append(
        f"  Channels: {_c(str(len(activity)), _C.CYAN)}  "
        f"Active messages: {_c(str(total_msgs), _C.CYAN)}"
    )
    lines.append("")

    for channel in sorted(activity.keys()):
        msgs = activity[channel]
        lines.append(f"  {_c(f'[{channel}]', _C.BOLD)} ({len(msgs)} recent)")

        for msg in msgs[-5:]:
            ts = _ts_fmt(msg.get("ts", 0))
            agent = msg.get("agent_id", "?")
            msg_type = msg.get("type", "?")
            body = msg.get("body", {})

            if isinstance(body, dict):
                event = body.get("event", "")
                if event:
                    body_str = event
                else:
                    body_str = json.dumps(body, separators=(",", ":"))
                    if len(body_str) > 40:
                        body_str = body_str[:37] + "..."
            else:
                body_str = str(body)[:40]

            # Color by type
            type_colors = {
                "info": _C.CYAN,
                "phase-signal": _C.MAGENTA,
                "blocker": _C.RED,
                "heartbeat": _C.DIM,
                "request": _C.YELLOW,
                "response": _C.GREEN,
            }
            tc = type_colors.get(msg_type, _C.DIM)
            lines.append(
                f"    {_c(ts, _C.DIM)} {_c(msg_type, tc):<22} "
                f"{agent:<16} {body_str}"
            )
        lines.append("")

    return lines


def _render_restart_history(events: list[dict]) -> list[str]:
    """Render restart and lifecycle events."""
    lines = [_sub_line("LIFECYCLE EVENTS")]

    if not events:
        lines.append("  No lifecycle events recorded")
        lines.append("")
        return lines

    for evt in events:
        ts = _ts_fmt(evt["ts"])
        event_name = evt["event"]

        # Color-code events
        event_colors = {
            "runner-started": _C.GREEN,
            "runner-stopped": _C.RED,
            "agent-dead": _C.RED,
            "agents-registered": _C.CYAN,
            "phase-transition": _C.MAGENTA,
        }
        ec = event_colors.get(event_name, _C.DIM)

        detail_parts: list[str] = []
        d = evt.get("detail", {})
        if event_name == "agent-dead":
            detail_parts.append(f"agent={d.get('agent_id', '?')}")
            detail_parts.append(f"team={d.get('team', '?')}")
            elapsed = d.get("elapsed", "?")
            detail_parts.append(f"elapsed={elapsed}s")
        elif event_name == "phase-transition":
            detail_parts.append(f"{d.get('from', '?')} -> {d.get('to', '?')}")
        elif event_name == "agents-registered":
            detail_parts.append(f"count={d.get('count', '?')}")
        elif event_name == "runner-started":
            detail_parts.append(f"pid={d.get('pid', '?')}")
            detail_parts.append(f"teams={d.get('teams', '?')}")

        detail_str = "  ".join(detail_parts)
        lines.append(
            f"  {_c(ts, _C.DIM)} {_c(event_name, ec):<24} {detail_str}"
        )

    lines.append("")
    return lines


def _render_findings_summary(summary: dict) -> list[str]:
    """Render findings summary."""
    lines = [_sub_line("FINDINGS")]

    total = summary.get("total", 0)
    if total == 0:
        lines.append("  No findings recorded")
        lines.append("")
        return lines

    lines.append(f"  Total: {_c(str(total), _C.BOLD)}")

    by_priority = summary.get("by_priority", {})
    if by_priority:
        parts = []
        for p in ("critical", "high", "medium", "low"):
            cnt = by_priority.get(p, 0)
            if cnt:
                if p in ("critical", "high"):
                    parts.append(_c(f"{p}={cnt}", _C.RED, _C.BOLD))
                elif p == "medium":
                    parts.append(_c(f"{p}={cnt}", _C.YELLOW))
                else:
                    parts.append(_c(f"{p}={cnt}", _C.DIM))
        lines.append(f"  Priority: {', '.join(parts)}")

    by_team = summary.get("by_team", {})
    if by_team:
        team_parts = [f"{t}({c})" for t, c in sorted(by_team.items())]
        lines.append(f"  By team: {', '.join(team_parts)}")

    lines.append("")
    return lines


def _render_phase_signals(signals: list[dict]) -> list[str]:
    """Render recent phase signals."""
    lines = [_sub_line("PHASE SIGNALS")]

    if not signals:
        lines.append("  No phase signals")
        lines.append("")
        return lines

    for sig in signals[:8]:
        ts = _ts_fmt(sig["ts"])
        phase = sig["phase"]
        sig_type = sig["signal_type"]
        agent = sig["agent_id"]

        data_str = ""
        if sig.get("data"):
            try:
                d = json.loads(sig["data"])
                if isinstance(d, dict):
                    data_str = json.dumps(d, separators=(",", ":"))
                    if len(data_str) > 30:
                        data_str = data_str[:27] + "..."
            except (json.JSONDecodeError, TypeError):
                data_str = str(sig["data"])[:30]

        lines.append(
            f"  {_c(ts, _C.DIM)} "
            f"{_c(sig_type, _C.MAGENTA):<16} "
            f"{_c(phase, _C.BOLD):<22} by {agent}"
            + (f"  {_c(data_str, _C.DIM)}" if data_str else "")
        )

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Full dashboard render
# ---------------------------------------------------------------------------

_VALID_SECTIONS = {"grid", "agents", "bus", "events", "findings", "phases"}


def render_dashboard(
    sections: set[str] | None = None,
    compact: bool = False,
) -> str:
    """Render the complete dashboard as a string.

    Parameters
    ----------
    sections:
        Set of section names to include. None means all.
        Valid: grid, agents, bus, events, findings, phases
    compact:
        Use compact single-line agent view instead of detailed.
    """
    show_all = sections is None
    show = sections or _VALID_SECTIONS

    output: list[str] = []

    with _DB() as db:
        runner = _collect_runner_info(db)
        agents = _collect_agents(db)

        output.extend(_render_title(runner))

        if "grid" in show or show_all:
            output.extend(_render_team_grid(agents))

        if "agents" in show or show_all:
            if compact:
                output.extend(_render_agents_compact(agents))
            else:
                output.extend(_render_agents_detail(agents))

        bus_activity = _collect_bus_activity()
        if "bus" in show or show_all:
            output.extend(_render_bus_activity(bus_activity))

        if "phases" in show or show_all:
            signals = _collect_phase_signals(db)
            output.extend(_render_phase_signals(signals))

        if "events" in show or show_all:
            events = _collect_restart_events()
            output.extend(_render_restart_history(events))

        if "findings" in show or show_all:
            findings = _collect_findings_summary(db)
            output.extend(_render_findings_summary(findings))

    output.append("=" * _WIDTH)
    return "\n".join(output)


def render_json() -> str:
    """Render dashboard data as JSON for machine consumption."""
    with _DB() as db:
        runner = _collect_runner_info(db)
        agents = _collect_agents(db)
        signals = _collect_phase_signals(db)
        findings = _collect_findings_summary(db)

    bus_activity = _collect_bus_activity()
    # Convert bus messages to serializable form
    bus_serializable = {}
    for ch, msgs in bus_activity.items():
        bus_serializable[ch] = msgs

    events = _collect_restart_events()

    # Compute agent health counts
    health_counts = {"ok": 0, "stale": 0, "dead": 0, "done": 0}
    for a in agents:
        h = _agent_health(a["status"], a["last_heartbeat"])
        health_counts[h] += 1

    data = {
        "timestamp": time.time(),
        "runner": runner,
        "agents": agents,
        "agent_health": health_counts,
        "bus_activity": bus_serializable,
        "phase_signals": signals,
        "lifecycle_events": events,
        "findings": findings,
    }
    return json.dumps(data, indent=2, default=str)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def _clear_screen() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _watch_loop(interval: float, **kwargs: Any) -> None:
    """Continuously refresh the dashboard."""
    try:
        while True:
            _clear_screen()
            print(render_dashboard(**kwargs))
            print(
                f"\n  {_c(f'[Auto-refresh every {interval}s -- Ctrl+C to stop]', _C.DIM)}"
            )
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Enhanced dashboard for SDK-launched multi-agent teams",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            sections:
              grid      Team status grid with health bars
              agents    Detailed per-agent status
              bus       Message bus channel activity
              phases    Phase signal history
              events    Lifecycle events (starts, stops, dead agents)
              findings  Team findings summary

            examples:
              %(prog)s                            # Full dashboard
              %(prog)s --watch                    # Auto-refresh
              %(prog)s --watch --interval 1       # Fast refresh
              %(prog)s --section grid --section bus
              %(prog)s --compact                  # Compact agent view
              %(prog)s --json                     # JSON output
        """),
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Auto-refresh the dashboard",
    )
    parser.add_argument(
        "--interval", type=float, default=3.0,
        help="Refresh interval in seconds (default: 3)",
    )
    parser.add_argument(
        "--section", action="append", dest="sections",
        choices=sorted(_VALID_SECTIONS),
        help="Show only specific sections (can repeat)",
    )
    parser.add_argument(
        "--compact", action="store_true",
        help="Use compact single-line-per-agent view",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output data as JSON",
    )

    args = parser.parse_args()

    if args.json:
        print(render_json())
        return

    sections = set(args.sections) if args.sections else None
    kwargs = {"sections": sections, "compact": args.compact}

    if args.watch:
        _watch_loop(args.interval, **kwargs)
    else:
        print(render_dashboard(**kwargs))


# Needed for the epilog dedent
import textwrap  # noqa: E402 (already imported implicitly by argparse usage)

if __name__ == "__main__":
    main()
