#!/usr/bin/env python3
"""Text-based dashboard for Proto A coordination system.

Displays a real-time overview of:
- All registered agents and their status (alive, dead, stale)
- Message bus activity (recent messages per channel)
- Team completion status
- Key findings summary

Can be run at any time to check system status.

Usage:
    python dashboard.py                  # Show full dashboard
    python dashboard.py --watch          # Auto-refresh every 5 seconds
    python dashboard.py --watch --interval 2
    python dashboard.py --agents         # Show only agent status
    python dashboard.py --bus            # Show only bus activity
    python dashboard.py --findings       # Show only findings

Uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BUS_DIR = os.path.join(_SCRIPT_DIR, "bus")
_DB_DIR = os.path.join(_SCRIPT_DIR, "db")
_DB_PATH = os.path.join(_DB_DIR, "state.db")

# ---------------------------------------------------------------------------
# Formatting constants
# ---------------------------------------------------------------------------

_WIDTH = 72
_DEAD_THRESHOLD = 120.0  # seconds without heartbeat
_STALE_THRESHOLD = 60.0  # seconds without heartbeat (warning)
_MAX_BUS_MESSAGES = 10   # messages to show per channel
_MAX_FINDINGS = 15       # findings to show in summary


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _clear_screen() -> None:
    """Clear terminal screen (cross-platform)."""
    sys.stdout.write('\033[2J\033[H')
    sys.stdout.flush()


def _header(title: str) -> str:
    """Format a section header."""
    pad = _WIDTH - len(title) - 4
    left = pad // 2
    right = pad - left
    return f"{'=' * left}  {title}  {'=' * right}"


def _subheader(title: str) -> str:
    """Format a subsection header."""
    pad = _WIDTH - len(title) - 4
    left = pad // 2
    right = pad - left
    return f"{'-' * left}  {title}  {'-' * right}"


def _ts_fmt(ts: float) -> str:
    """Format a Unix timestamp as a short human-readable string."""
    if not ts:
        return "never"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%H:%M:%S")


def _age_fmt(ts: float) -> str:
    """Format the age of a timestamp as 'Xs ago' or 'Xm ago'."""
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


def _status_indicator(status: str, last_hb: float) -> str:
    """Return a text status indicator for an agent."""
    age = time.time() - last_hb if last_hb else float("inf")
    if status in ("complete", "stopped"):
        return "[DONE]"
    if status != "alive":
        return "[DEAD]"
    if age > _DEAD_THRESHOLD:
        return "[DEAD]"
    if age > _STALE_THRESHOLD:
        return "[WARN]"
    return "[ OK ]"


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection | None:
    """Open the shared database.  Returns None if unavailable."""
    if not os.path.exists(_DB_PATH):
        return None
    try:
        conn = sqlite3.connect(_DB_PATH, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def _get_agents() -> list[dict]:
    """Fetch all registered agents."""
    conn = _get_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT agent_id, team, role, status, pid, "
            "last_heartbeat, registered_at "
            "FROM agents ORDER BY team, role, agent_id"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _get_phase_signals() -> list[dict]:
    """Fetch recent phase signals."""
    conn = _get_db()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT phase, signal_type, agent_id, ts, data "
            "FROM phase_signals ORDER BY ts DESC LIMIT 10"
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _get_findings_summary() -> dict:
    """Get a summary of team findings."""
    conn = _get_db()
    if conn is None:
        return {"total": 0, "by_category": {}, "by_priority": {}, "recent": []}
    try:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM team_findings"
        ).fetchone()["cnt"]

        cat_rows = conn.execute(
            "SELECT category, COUNT(*) as cnt FROM team_findings "
            "GROUP BY category ORDER BY cnt DESC"
        ).fetchall()
        by_category = {r["category"]: r["cnt"] for r in cat_rows}

        pri_rows = conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM team_findings "
            "GROUP BY priority"
        ).fetchall()
        by_priority = {r["priority"]: r["cnt"] for r in pri_rows}

        recent_rows = conn.execute(
            "SELECT team, agent_id, category, title, priority, ts "
            "FROM team_findings ORDER BY ts DESC LIMIT ?"
            , (_MAX_FINDINGS,)
        ).fetchall()
        recent = [dict(r) for r in recent_rows]

        return {
            "total": total,
            "by_category": by_category,
            "by_priority": by_priority,
            "recent": recent,
        }
    except sqlite3.OperationalError:
        return {"total": 0, "by_category": {}, "by_priority": {}, "recent": []}
    finally:
        conn.close()


def _get_runner_state() -> dict:
    """Read runner state from the database."""
    conn = _get_db()
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT key, value FROM runner_state"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def _get_bus_activity() -> dict[str, list[dict]]:
    """Read recent messages from all bus channel files."""
    activity: dict[str, list[dict]] = {}
    if not os.path.isdir(_BUS_DIR):
        return activity

    for filepath in sorted(glob.glob(os.path.join(_BUS_DIR, "*.jsonl"))):
        channel = os.path.basename(filepath).rsplit(".", 1)[0]
        messages = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                # Read last N lines efficiently by seeking near end
                f.seek(0, 2)  # end
                size = f.tell()
                # Read last 20KB (should contain plenty of messages)
                read_size = min(size, 20480)
                f.seek(max(0, size - read_size))
                tail = f.read()
        except OSError:
            continue

        now = time.time()
        lines = tail.strip().split("\n")
        for line in lines[-_MAX_BUS_MESSAGES:]:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                # Skip expired messages
                if msg.get("ts", 0) + msg.get("ttl", 300) < now:
                    continue
                messages.append(msg)
            except json.JSONDecodeError:
                continue

        if messages:
            activity[channel] = messages[-_MAX_BUS_MESSAGES:]

    return activity


# ---------------------------------------------------------------------------
# Dashboard sections
# ---------------------------------------------------------------------------

def render_header() -> list[str]:
    """Render the dashboard title and timestamp."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        _header("PROTO A COORDINATION DASHBOARD"),
        f"  Generated: {now}",
    ]

    # Runner state
    runner = _get_runner_state()
    if runner:
        status = runner.get("status", "unknown")
        pid = runner.get("pid", "?")
        started = runner.get("started_at")
        if started:
            try:
                uptime = time.time() - float(started)
                up_str = f"{uptime / 60:.0f}m" if uptime > 60 else f"{uptime:.0f}s"
            except (ValueError, TypeError):
                up_str = "?"
        else:
            up_str = "?"
        phase = runner.get("phase", "?")
        lines.append(
            f"  Runner: {status} (pid={pid}, uptime={up_str}, phase={phase})"
        )
    else:
        lines.append("  Runner: not initialised")

    lines.append("")
    return lines


def render_agents() -> list[str]:
    """Render the agent status section."""
    lines = [_subheader("AGENTS")]
    agents = _get_agents()

    if not agents:
        lines.append("  No agents registered")
        lines.append("")
        return lines

    # Group by team
    by_team: dict[str, list[dict]] = defaultdict(list)
    for a in agents:
        by_team[a["team"]].append(a)

    alive_count = 0
    dead_count = 0

    for team_name in sorted(by_team.keys()):
        team_agents = by_team[team_name]
        lines.append(f"  Team: {team_name}")

        for a in team_agents:
            indicator = _status_indicator(a["status"], a["last_heartbeat"])
            age = _age_fmt(a["last_heartbeat"])
            lines.append(
                f"    {indicator} {a['agent_id']:<24} "
                f"{a['role']:<12} hb={age}"
            )
            if indicator == "[ OK ]":
                alive_count += 1
            else:
                dead_count += 1

    lines.append(f"  Total: {alive_count} alive, {dead_count} dead/stale "
                 f"({len(agents)} registered)")
    lines.append("")
    return lines


def render_bus_activity() -> list[str]:
    """Render the message bus activity section."""
    lines = [_subheader("MESSAGE BUS")]
    activity = _get_bus_activity()

    if not activity:
        lines.append("  No bus activity")
        lines.append("")
        return lines

    total_msgs = sum(len(msgs) for msgs in activity.values())
    lines.append(f"  Channels: {len(activity)}, Recent messages: {total_msgs}")
    lines.append("")

    for channel in sorted(activity.keys()):
        msgs = activity[channel]
        lines.append(f"  [{channel}] ({len(msgs)} recent)")

        # Show the last few messages
        for msg in msgs[-5:]:
            ts = _ts_fmt(msg.get("ts", 0))
            agent = msg.get("agent_id", "?")
            msg_type = msg.get("type", "?")
            body = msg.get("body", {})

            # Compact body representation
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

            lines.append(
                f"    {ts} {agent:<16} {msg_type:<14} {body_str}"
            )
        lines.append("")

    return lines


def render_team_completion() -> list[str]:
    """Render team completion status based on phase signals."""
    lines = [_subheader("PHASE SIGNALS")]
    signals = _get_phase_signals()

    if not signals:
        lines.append("  No phase signals recorded")
        lines.append("")
        return lines

    for sig in signals[:8]:
        ts = _ts_fmt(sig["ts"])
        data = ""
        if sig.get("data"):
            try:
                d = json.loads(sig["data"])
                if isinstance(d, dict):
                    data = json.dumps(d, separators=(",", ":"))
                    if len(data) > 30:
                        data = data[:27] + "..."
            except (json.JSONDecodeError, TypeError):
                data = str(sig["data"])[:30]

        lines.append(
            f"  {ts} [{sig['signal_type']:<12}] "
            f"{sig['phase']:<20} by {sig['agent_id']}"
            + (f" {data}" if data else "")
        )

    lines.append("")
    return lines


def render_findings() -> list[str]:
    """Render the findings summary section."""
    lines = [_subheader("FINDINGS")]
    summary = _get_findings_summary()

    total = summary["total"]
    if total == 0:
        lines.append("  No findings recorded")
        lines.append("")
        return lines

    lines.append(f"  Total: {total}")

    # By priority
    by_pri = summary.get("by_priority", {})
    if by_pri:
        pri_parts = []
        for p in ("critical", "high", "medium", "low"):
            count = by_pri.get(p, 0)
            if count:
                marker = "(!)" if p in ("critical", "high") else ""
                pri_parts.append(f"{p}={count}{marker}")
        lines.append(f"  Priority: {', '.join(pri_parts)}")

    # By category
    by_cat = summary.get("by_category", {})
    if by_cat:
        lines.append(f"  Categories: {', '.join(f'{k}({v})' for k, v in by_cat.items())}")

    # Recent findings
    recent = summary.get("recent", [])
    if recent:
        lines.append("")
        lines.append("  Recent findings:")
        for f in recent[:8]:
            ts = _ts_fmt(f.get("ts", 0))
            title = f.get("title", "?")
            if len(title) > 40:
                title = title[:37] + "..."
            pri = f.get("priority", "?")
            team = f.get("team", "?")
            lines.append(f"    {ts} [{pri:<8}] {team:<10} {title}")

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Full dashboard
# ---------------------------------------------------------------------------

def render_dashboard(
    show_agents: bool = True,
    show_bus: bool = True,
    show_findings: bool = True,
) -> str:
    """Render the complete dashboard as a string."""
    sections: list[str] = []
    sections.extend(render_header())

    if show_agents:
        sections.extend(render_agents())
    if show_bus:
        sections.extend(render_bus_activity())
        sections.extend(render_team_completion())
    if show_findings:
        sections.extend(render_findings())

    sections.append("=" * _WIDTH)
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def _watch_loop(interval: float, **kwargs) -> None:
    """Continuously refresh the dashboard."""
    try:
        while True:
            _clear_screen()
            print(render_dashboard(**kwargs))
            print(f"\n  [Refreshing every {interval}s — press Ctrl+C to stop]")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Text-based dashboard for Proto A coordination",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Auto-refresh the dashboard",
    )
    parser.add_argument(
        "--interval", type=float, default=5.0,
        help="Refresh interval in seconds (with --watch)",
    )
    parser.add_argument(
        "--agents", action="store_true",
        help="Show only agent status",
    )
    parser.add_argument(
        "--bus", action="store_true",
        help="Show only bus activity",
    )
    parser.add_argument(
        "--findings", action="store_true",
        help="Show only findings summary",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw data as JSON",
    )

    args = parser.parse_args()

    # If specific sections requested, only show those
    if args.agents or args.bus or args.findings:
        show_agents = args.agents
        show_bus = args.bus
        show_findings = args.findings
    else:
        show_agents = True
        show_bus = True
        show_findings = True

    if args.json:
        data = {
            "timestamp": time.time(),
            "agents": _get_agents() if show_agents else [],
            "bus_activity": (
                {ch: msgs for ch, msgs in _get_bus_activity().items()}
                if show_bus else {}
            ),
            "phase_signals": _get_phase_signals() if show_bus else [],
            "findings": _get_findings_summary() if show_findings else {},
            "runner": _get_runner_state(),
        }
        print(json.dumps(data, indent=2, default=str))
        return

    if args.watch:
        _watch_loop(
            args.interval,
            show_agents=show_agents,
            show_bus=show_bus,
            show_findings=show_findings,
        )
    else:
        print(render_dashboard(
            show_agents=show_agents,
            show_bus=show_bus,
            show_findings=show_findings,
        ))


if __name__ == "__main__":
    main()
