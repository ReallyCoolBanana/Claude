#!/usr/bin/env python3
"""CLI tool for agents to read/write the Proto A JSONL message bus.

Usage:
    python bus_cli.py write <channel> <agent_id> <team> <msg_type> <body_json>
    python bus_cli.py read <channel> [--since <offset>]
    python bus_cli.py init <db_path>
    python bus_cli.py register <agent_id> <team> <role>
    python bus_cli.py heartbeat <db_path> <agent_id>
    python bus_cli.py status

    Help Protocol commands:
    python bus_cli.py help-request <team> <agent> <description> [--capabilities cap1,cap2] [--priority high]
    python bus_cli.py help-offer <team> <agent> <request_id>
    python bus_cli.py status-update <team> <agent> <status> <progress_pct> [--task "current task"]
    python bus_cli.py work-add <team> <agent> <title> [--description "..."] [--priority medium] [--est-minutes 10]
    python bus_cli.py idle-teams
    python bus_cli.py help-needed
"""

import json
import os
import sys
import time
import uuid

BUS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bus")
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")

VALID_MSG_TYPES = {"info", "blocker", "phase-signal", "heartbeat", "request", "response"}


def write_msg(channel, agent_id, team, msg_type, body):
    """Write a message to a bus channel."""
    os.makedirs(BUS_DIR, exist_ok=True)
    msg = {
        "id": str(uuid.uuid4()),
        "type": msg_type,
        "channel": channel,
        "team": team,
        "agent_id": agent_id,
        "ts": time.time(),
        "ttl": 3600,
        "body": body,
    }
    raw = json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n"
    safe_ch = channel.replace("/", "_").replace("..", "_")
    filepath = os.path.join(BUS_DIR, f"{safe_ch}.jsonl")
    fd = os.open(filepath, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, raw)
    finally:
        os.close(fd)
    return msg["id"]


def read_msgs(channel, since_offset=0):
    """Read messages from a bus channel."""
    safe_ch = channel.replace("/", "_").replace("..", "_")
    filepath = os.path.join(BUS_DIR, f"{safe_ch}.jsonl")
    if not os.path.exists(filepath):
        return [], 0
    with open(filepath, "r") as f:
        f.seek(since_offset)
        data = f.read()
    new_offset = since_offset + len(data.encode("utf-8"))
    msgs = []
    now = time.time()
    for line in data.strip().split("\n"):
        if not line.strip():
            continue
        try:
            m = json.loads(line)
            if m.get("ts", 0) + m.get("ttl", 300) > now:
                msgs.append(m)
        except json.JSONDecodeError:
            continue
    return msgs, new_offset


def init_db():
    """Initialize the SQLite shared state database."""
    import sqlite3
    os.makedirs(DB_DIR, exist_ok=True)
    db_path = os.path.join(DB_DIR, "state.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            team TEXT NOT NULL,
            role TEXT NOT NULL,
            pid INTEGER,
            status TEXT DEFAULT 'alive',
            last_heartbeat REAL NOT NULL,
            registered_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_endpoint TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            called_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rate_limit_config (
            api_endpoint TEXT PRIMARY KEY,
            max_calls INTEGER NOT NULL,
            window_seconds INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS phase_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            ts REAL NOT NULL,
            data TEXT
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id TEXT UNIQUE NOT NULL,
            channel TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            msg_type TEXT NOT NULL,
            body TEXT NOT NULL,
            ts REAL NOT NULL,
            expires_at REAL,
            in_reply_to TEXT
        );
        CREATE TABLE IF NOT EXISTS team_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            priority TEXT DEFAULT 'medium',
            ts REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS think_tank (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            proposal TEXT NOT NULL,
            votes_for INTEGER DEFAULT 0,
            votes_against INTEGER DEFAULT 0,
            status TEXT DEFAULT 'proposed',
            ts REAL NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    print(f"Initialized state DB at {db_path}")
    return db_path


def register_agent(agent_id, team, role):
    """Register an agent in shared state."""
    import sqlite3
    db_path = os.path.join(DB_DIR, "state.db")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    now = time.time()
    conn.execute("""
        INSERT INTO agents (agent_id, team, role, pid, status, last_heartbeat, registered_at)
        VALUES (?, ?, ?, ?, 'alive', ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            team=excluded.team, role=excluded.role, pid=excluded.pid,
            status='alive', last_heartbeat=excluded.last_heartbeat
    """, (agent_id, team, role, os.getpid(), now, now))
    conn.commit()
    conn.close()
    print(f"Registered {agent_id} ({team}/{role})")


def post_finding(team, agent_id, category, title, content, priority="medium"):
    """Post a finding to the shared think tank database."""
    import sqlite3
    db_path = os.path.join(DB_DIR, "state.db")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        INSERT INTO team_findings (team, agent_id, category, title, content, priority, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (team, agent_id, category, title, content, priority, time.time()))
    conn.commit()
    conn.close()


def get_all_findings():
    """Get all findings from all teams."""
    import sqlite3
    db_path = os.path.join(DB_DIR, "state.db")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM team_findings ORDER BY ts").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def post_proposal(team, proposal):
    """Post a think tank proposal."""
    import sqlite3
    db_path = os.path.join(DB_DIR, "state.db")
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        INSERT INTO think_tank (team, proposal, status, ts)
        VALUES (?, ?, 'proposed', ?)
    """, (team, proposal, time.time()))
    conn.commit()
    conn.close()


def get_status():
    """Get current system status."""
    import sqlite3
    db_path = os.path.join(DB_DIR, "state.db")
    if not os.path.exists(db_path):
        print("No state DB found")
        return
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    agents = [dict(r) for r in conn.execute("SELECT * FROM agents").fetchall()]
    findings = conn.execute("SELECT COUNT(*) as cnt FROM team_findings").fetchone()["cnt"]
    proposals = conn.execute("SELECT COUNT(*) as cnt FROM think_tank").fetchone()["cnt"]
    conn.close()
    status = {
        "agents": agents,
        "total_findings": findings,
        "total_proposals": proposals,
        "bus_files": os.listdir(BUS_DIR) if os.path.exists(BUS_DIR) else [],
    }
    print(json.dumps(status, indent=2, default=str))
    return status


def _get_help_protocol(team, agent_id):
    """Create a HelpProtocol instance using the standard DB and bus paths."""
    # Import from same directory
    _this_dir = os.path.dirname(os.path.abspath(__file__))
    if _this_dir not in sys.path:
        sys.path.insert(0, _this_dir)
    from help_protocol import HelpProtocol
    os.makedirs(DB_DIR, exist_ok=True)
    db_path = os.path.join(DB_DIR, "state.db")
    return HelpProtocol(db_path, BUS_DIR, team, agent_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init":
        init_db()
    elif cmd == "write":
        if len(sys.argv) < 7:
            print("Usage: bus_cli.py write <channel> <agent_id> <team> <msg_type> <body_json>")
            sys.exit(1)
        body = json.loads(sys.argv[6])
        mid = write_msg(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5], body)
        print(f"Published: {mid}")
    elif cmd == "read":
        channel = sys.argv[2] if len(sys.argv) > 2 else "global"
        offset = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[3] == "--since" else 0
        msgs, new_offset = read_msgs(channel, offset)
        print(json.dumps({"messages": msgs, "offset": new_offset}, indent=2, default=str))
    elif cmd == "register":
        if len(sys.argv) < 5:
            print("Usage: bus_cli.py register <agent_id> <team> <role>")
            sys.exit(1)
        register_agent(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "status":
        get_status()
    elif cmd == "help-request":
        if len(sys.argv) < 5:
            print("Usage: bus_cli.py help-request <team> <agent> <description> [--capabilities cap1,cap2] [--priority high]")
            sys.exit(1)
        team, agent, desc = sys.argv[2], sys.argv[3], sys.argv[4]
        caps = []
        priority = "medium"
        i = 5
        while i < len(sys.argv):
            if sys.argv[i] == "--capabilities" and i + 1 < len(sys.argv):
                caps = sys.argv[i + 1].split(",")
                i += 2
            elif sys.argv[i] == "--priority" and i + 1 < len(sys.argv):
                priority = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        hp = _get_help_protocol(team, agent)
        # Create a work item first, then request help
        wid = hp.add_work_item(desc, description=desc, priority=priority, required_caps=caps)
        rid = hp.request_help(wid, desc)
        hp.close()
        print(json.dumps({"request_id": rid, "work_item_id": wid}))

    elif cmd == "help-offer":
        if len(sys.argv) < 5:
            print("Usage: bus_cli.py help-offer <team> <agent> <request_id>")
            sys.exit(1)
        team, agent, request_id = sys.argv[2], sys.argv[3], int(sys.argv[4])
        hp = _get_help_protocol(team, agent)
        won = hp.offer_help(request_id)
        hp.close()
        print(json.dumps({"accepted": won, "request_id": request_id}))

    elif cmd == "status-update":
        if len(sys.argv) < 6:
            print("Usage: bus_cli.py status-update <team> <agent> <status> <progress_pct> [--task 'current task']")
            sys.exit(1)
        team, agent = sys.argv[2], sys.argv[3]
        st, pct = sys.argv[4], float(sys.argv[5])
        task = ""
        i = 6
        while i < len(sys.argv):
            if sys.argv[i] == "--task" and i + 1 < len(sys.argv):
                task = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        hp = _get_help_protocol(team, agent)
        hp.update_status(st, pct, task)
        hp.close()
        print(f"Updated {team} status: {st} ({pct}%)")

    elif cmd == "work-add":
        if len(sys.argv) < 5:
            print("Usage: bus_cli.py work-add <team> <agent> <title> [--description '...'] [--priority medium] [--est-minutes 10]")
            sys.exit(1)
        team, agent, title = sys.argv[2], sys.argv[3], sys.argv[4]
        desc = ""
        priority = "medium"
        est_min = 0.0
        i = 5
        while i < len(sys.argv):
            if sys.argv[i] == "--description" and i + 1 < len(sys.argv):
                desc = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--priority" and i + 1 < len(sys.argv):
                priority = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--est-minutes" and i + 1 < len(sys.argv):
                est_min = float(sys.argv[i + 1])
                i += 2
            else:
                i += 1
        hp = _get_help_protocol(team, agent)
        wid = hp.add_work_item(title, description=desc, priority=priority, est_minutes=est_min)
        hp.close()
        print(json.dumps({"work_item_id": wid}))

    elif cmd == "idle-teams":
        hp = _get_help_protocol("_system", "_cli")
        teams = hp.get_idle_teams()
        hp.close()
        print(json.dumps({"idle_teams": teams}, indent=2, default=str))

    elif cmd == "help-needed":
        hp = _get_help_protocol("_system", "_cli")
        needed = hp.get_teams_needing_help()
        open_reqs = hp.get_open_help_requests()
        hp.close()
        print(json.dumps({
            "teams_needing_help": needed,
            "open_requests": open_reqs,
        }, indent=2, default=str))

    elif cmd == "auto-assign":
        hp = _get_help_protocol("_system", "_cli")
        assignments = hp.auto_assign_idle_teams()
        hp.close()
        print(json.dumps({"assignments": assignments}, indent=2, default=str))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
