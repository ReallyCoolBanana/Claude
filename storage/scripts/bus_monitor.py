#!/usr/bin/env python3
"""Bus Monitor - Monitor JSONL message bus channels.

Reads all JSONL bus channels, shows recent messages with timestamps,
supports filtering by team, channel, or message type, and provides
activity summaries.

Usage:
    python bus_monitor.py [OPTIONS]
    python bus_monitor.py --recent 20
    python bus_monitor.py --team PROG-TEAM-3
    python bus_monitor.py --channel tt-tools-to-prog
    python bus_monitor.py --summary
    python bus_monitor.py --watch

Options:
    --bus-dir DIR           Bus directory (default: /root/.claude-agent-comm/bus/)
    --recent N              Show N most recent messages (default: 10)
    --team TEAM             Filter by team name
    --channel CHAN          Filter by channel name
    --type TYPE             Filter by message type
    --since SECONDS         Only show messages from last N seconds
    --summary               Show activity summary instead of messages
    --watch                 Watch for new messages (poll every 2s)
    --json                  Output as JSON
    -h, --help              Show this help
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

DEFAULT_BUS_DIR = '/root/.claude-agent-comm/bus/'


def find_bus_files(bus_dir: str) -> list[str]:
    """Find all JSONL bus files."""
    files = []
    if not os.path.isdir(bus_dir):
        return files

    for name in sorted(os.listdir(bus_dir)):
        if name.endswith('.jsonl'):
            files.append(os.path.join(bus_dir, name))
    return files


def read_messages(filepath: str) -> list[dict]:
    """Read messages from a JSONL bus file."""
    messages = []
    channel = os.path.splitext(os.path.basename(filepath))[0]

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    msg['_channel'] = channel
                    msg['_source_file'] = filepath
                    msg['_line'] = line_num
                    messages.append(msg)
                except json.JSONDecodeError:
                    continue
    except (IOError, OSError):
        pass

    return messages


def read_all_messages(bus_dir: str) -> list[dict]:
    """Read all messages from all bus files."""
    all_msgs = []
    for filepath in find_bus_files(bus_dir):
        all_msgs.extend(read_messages(filepath))
    return all_msgs


def get_timestamp(msg: dict) -> float:
    """Extract timestamp from a message."""
    for key in ('ts', 'timestamp', 'time', 'created_at'):
        val = msg.get(key)
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
                return dt.timestamp()
            except (ValueError, TypeError):
                pass
    return 0.0


def format_timestamp(ts: float) -> str:
    """Format a Unix timestamp to human-readable."""
    if ts <= 0:
        return "unknown"
    try:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')
    except (ValueError, OSError):
        return str(ts)


def filter_messages(messages: list[dict], team: str | None = None,
                    channel: str | None = None, msg_type: str | None = None,
                    since_seconds: float | None = None) -> list[dict]:
    """Filter messages by various criteria."""
    filtered = messages

    if team:
        team_upper = team.upper()
        filtered = [m for m in filtered
                    if team_upper in str(m.get('team', '')).upper()
                    or team_upper in str(m.get('agent_id', '')).upper()
                    or team_upper in str(m.get('from', '')).upper()
                    or team_upper in str(m.get('_channel', '')).upper()]

    if channel:
        channel_lower = channel.lower()
        filtered = [m for m in filtered
                    if channel_lower in m.get('_channel', '').lower()]

    if msg_type:
        type_lower = msg_type.lower()
        filtered = [m for m in filtered
                    if type_lower in str(m.get('type', '')).lower()
                    or type_lower in str(m.get('event', '')).lower()
                    or type_lower in str(m.get('body', {}).get('event', '')).lower()]

    if since_seconds is not None:
        cutoff = time.time() - since_seconds
        filtered = [m for m in filtered if get_timestamp(m) >= cutoff]

    return filtered


def format_message(msg: dict, verbose: bool = False) -> str:
    """Format a single message for display."""
    ts = format_timestamp(get_timestamp(msg))
    channel = msg.get('_channel', '?')
    team = msg.get('team', msg.get('from', '?'))
    msg_type = msg.get('type', msg.get('event', '?'))

    # Extract body content
    body = msg.get('body', {})
    if isinstance(body, dict):
        event = body.get('event', '')
        content = body.get('content', body.get('message', body.get('text', '')))
        if event and content:
            body_str = f"{event}: {content}"
        elif event:
            body_str = event
        elif content:
            body_str = str(content)[:200]
        else:
            body_str = json.dumps(body, default=str)[:200]
    elif isinstance(body, str):
        body_str = body[:200]
    else:
        body_str = str(body)[:200]

    line = f"[{ts}] #{channel} | {team} | {msg_type}"
    if body_str:
        line += f"\n    {body_str}"

    return line


def generate_summary(messages: list[dict]) -> str:
    """Generate an activity summary from messages."""
    if not messages:
        return "No messages found."

    lines = ["=== Bus Activity Summary ===\n"]

    # Channel stats
    channels: dict[str, int] = {}
    teams: dict[str, int] = {}
    types: dict[str, int] = {}

    for msg in messages:
        ch = msg.get('_channel', 'unknown')
        channels[ch] = channels.get(ch, 0) + 1

        team = msg.get('team', msg.get('from', 'unknown'))
        teams[team] = teams.get(team, 0) + 1

        mt = msg.get('type', msg.get('body', {}).get('event', 'unknown'))
        if isinstance(mt, str):
            types[mt] = types.get(mt, 0) + 1

    lines.append(f"Total messages: {len(messages)}")

    # Time range
    timestamps = [get_timestamp(m) for m in messages if get_timestamp(m) > 0]
    if timestamps:
        oldest = format_timestamp(min(timestamps))
        newest = format_timestamp(max(timestamps))
        lines.append(f"Time range: {oldest} -> {newest}")

    lines.append(f"\nChannels ({len(channels)}):")
    for ch, count in sorted(channels.items(), key=lambda x: -x[1]):
        lines.append(f"  {ch}: {count} messages")

    lines.append(f"\nTeams ({len(teams)}):")
    for team, count in sorted(teams.items(), key=lambda x: -x[1]):
        lines.append(f"  {team}: {count} messages")

    lines.append(f"\nMessage Types ({len(types)}):")
    for mt, count in sorted(types.items(), key=lambda x: -x[1]):
        lines.append(f"  {mt}: {count}")

    return '\n'.join(lines)


def watch_bus(bus_dir: str, team: str | None, channel: str | None,
              msg_type: str | None, poll_interval: float = 2.0):
    """Watch for new messages on the bus (blocking loop)."""
    print(f"Watching bus at {bus_dir} (Ctrl+C to stop)...\n", file=sys.stderr)

    # Track file positions
    file_positions: dict[str, int] = {}
    for filepath in find_bus_files(bus_dir):
        try:
            file_positions[filepath] = os.path.getsize(filepath)
        except OSError:
            file_positions[filepath] = 0

    try:
        while True:
            time.sleep(poll_interval)
            for filepath in find_bus_files(bus_dir):
                try:
                    size = os.path.getsize(filepath)
                except OSError:
                    continue

                prev_size = file_positions.get(filepath, 0)
                if size <= prev_size:
                    file_positions[filepath] = size
                    continue

                # Read new content
                try:
                    with open(filepath, 'r') as f:
                        f.seek(prev_size)
                        new_content = f.read()
                except (IOError, OSError):
                    continue

                file_positions[filepath] = size
                channel_name = os.path.splitext(os.path.basename(filepath))[0]

                for line in new_content.strip().split('\n'):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        msg['_channel'] = channel_name
                    except json.JSONDecodeError:
                        continue

                    msgs = filter_messages([msg], team, channel, msg_type)
                    if msgs:
                        print(format_message(msgs[0]))
                        print()

    except KeyboardInterrupt:
        print("\nStopped watching.", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Bus Monitor - Monitor JSONL message bus channels",
    )
    parser.add_argument('--bus-dir', default=DEFAULT_BUS_DIR,
                        help=f'Bus directory (default: {DEFAULT_BUS_DIR})')
    parser.add_argument('--recent', type=int, default=10,
                        help='Show N most recent messages (default: 10)')
    parser.add_argument('--team', help='Filter by team name')
    parser.add_argument('--channel', help='Filter by channel name')
    parser.add_argument('--type', dest='msg_type', help='Filter by message type')
    parser.add_argument('--since', type=float,
                        help='Only show messages from last N seconds')
    parser.add_argument('--summary', action='store_true',
                        help='Show activity summary')
    parser.add_argument('--watch', action='store_true',
                        help='Watch for new messages')
    parser.add_argument('--json', action='store_true',
                        help='Output as JSON')

    args = parser.parse_args()

    if args.watch:
        watch_bus(args.bus_dir, args.team, args.channel, args.msg_type)
        return

    # Read all messages
    all_messages = read_all_messages(args.bus_dir)

    if not all_messages:
        print(f"No messages found in {args.bus_dir}", file=sys.stderr)
        if not os.path.isdir(args.bus_dir):
            print(f"Directory does not exist: {args.bus_dir}", file=sys.stderr)
        return

    # Filter
    filtered = filter_messages(all_messages, args.team, args.channel,
                               args.msg_type, args.since)

    if args.summary:
        print(generate_summary(filtered))
        return

    # Sort by timestamp and take recent
    filtered.sort(key=lambda m: get_timestamp(m))
    if args.recent > 0:
        filtered = filtered[-args.recent:]

    if args.json:
        # Clean internal fields for JSON output
        clean = []
        for m in filtered:
            mc = {k: v for k, v in m.items() if not k.startswith('_') or k == '_channel'}
            clean.append(mc)
        print(json.dumps(clean, indent=2, default=str))
    else:
        if not filtered:
            print("No messages match the filters.")
        else:
            for msg in filtered:
                print(format_message(msg))
                print()


if __name__ == '__main__':
    main()
