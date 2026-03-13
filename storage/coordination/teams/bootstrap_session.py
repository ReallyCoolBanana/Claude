#!/usr/bin/env python3
"""Bootstrap the multi-team session: create channels, initialize DB, register teams."""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "prototype"))

from agent_comm.core import CommDir
from agent_comm.bus import BusWriter

COMM_DIR = os.path.expanduser("~/.claude-agent-comm")
SESSION_CONFIG = os.path.join(os.path.dirname(__file__), "multi_team_session.json")


def bootstrap():
    # Initialize comm directory
    comm = CommDir(COMM_DIR)
    comm.ensure_dirs()
    comm.validate()
    print(f"[OK] Comm directory ready at {comm.path}")

    # Load session config
    with open(SESSION_CONFIG) as f:
        config = json.load(f)

    # Create a bootstrap writer to initialize all channels
    writer = BusWriter(comm.bus_dir, "bootstrap", "SYSTEM")

    # Initialize global channels
    for name, channel in config["channels"].items():
        writer.publish(channel, "info", {
            "event": "channel_created",
            "session_id": config["session_id"],
            "channel": channel,
            "purpose": name,
            "ts": time.time(),
        })
        print(f"  [channel] {channel} ({name})")

    # Initialize team channels
    all_teams = []
    for category in ["data_gatherers", "sorter_archivers", "think_tanks", "programming_teams"]:
        for team in config["teams"].get(category, []):
            all_teams.append(team)
            for ch in team.get("channels", []):
                writer.publish(ch, "info", {
                    "event": "channel_created",
                    "team": team["team_id"],
                    "purpose": team["focus"],
                })
                print(f"  [channel] {ch} (team: {team['team_id']})")

    # Broadcast session start
    writer.publish(config["channels"]["global"], "phase-signal", {
        "phase": "session-start",
        "session_id": config["session_id"],
        "total_teams": len(all_teams),
        "team_ids": [t["team_id"] for t in all_teams],
        "started_at": time.time(),
    })

    print(f"\n[OK] Session {config['session_id']} bootstrapped with {len(all_teams)} teams")
    return config


if __name__ == "__main__":
    bootstrap()
