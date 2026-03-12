#!/usr/bin/env python3
"""Import repository data into SQLite + FTS5 knowledge database.

Populates storage/data/knowledge.db from the existing file-based data:
  - knowledge-base/entries/*.md (YAML frontmatter)
  - teams/sessions/TEAM-*.md (YAML frontmatter)
  - storage/coordination/sops/*.json
  - storage/scripts/index.json (script catalog)
  - storage/sources/SRC-*.json
  - storage/api-tools/index.json (tool catalog)

Usage:
    python import_to_db.py             # incremental import
    python import_to_db.py --rebuild   # drop and recreate everything
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # storage/scripts -> repo root
DB_PATH = REPO_ROOT / "storage" / "data" / "knowledge.db"

KB_DIR = REPO_ROOT / "knowledge-base" / "entries"
SOP_DIR = REPO_ROOT / "storage" / "coordination" / "sops"
SRC_DIR = REPO_ROOT / "storage" / "sources"
SCRIPTS_INDEX = REPO_ROOT / "storage" / "scripts" / "index.json"
TOOLS_INDEX = REPO_ROOT / "storage" / "api-tools" / "index.json"
TEAMS_INDEX = REPO_ROOT / "teams" / "sessions" / "index.json"
TEAMS_DIR = REPO_ROOT / "teams" / "sessions"

# ---------------------------------------------------------------------------
# Schema DDL  (from db_schema_design.json migration_sql)
# ---------------------------------------------------------------------------

MIGRATION_SQL = r"""
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    numeric_id INTEGER NOT NULL,
    resource_type TEXT NOT NULL CHECK(resource_type IN ('KB','SOP','SRC','SCR','TEAM','TOOL')),
    title TEXT NOT NULL,
    summary TEXT CHECK(length(summary) <= 200),
    category TEXT DEFAULT NULL,
    status TEXT DEFAULT 'active',
    confidence TEXT DEFAULT NULL CHECK(confidence IS NULL OR confidence IN ('low','medium','high','very-high')),
    file_path TEXT NOT NULL UNIQUE,
    content_hash TEXT DEFAULT NULL,
    created_date TEXT DEFAULT (date('now')),
    updated_date TEXT DEFAULT (date('now')),
    team TEXT DEFAULT NULL,
    char_count INTEGER DEFAULT 0,
    token_estimate INTEGER GENERATED ALWAYS AS (char_count / 4) STORED,
    version TEXT DEFAULT '1.0',
    description TEXT DEFAULT NULL,
    extra_json TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    entry_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    tag TEXT NOT NULL CHECK(length(tag) > 0),
    UNIQUE(entry_id, tag)
);

CREATE TABLE IF NOT EXISTS relationships (
    source_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    rel_type TEXT NOT NULL CHECK(rel_type IN ('builds_on','supersedes','references','related')),
    metadata TEXT DEFAULT NULL,
    UNIQUE(source_id, target_id, rel_type)
);

CREATE TABLE IF NOT EXISTS id_sequences (
    resource_type TEXT PRIMARY KEY CHECK(resource_type IN ('KB','SOP','SRC','SCR','TEAM','TOOL')),
    next_id INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (datetime('now')),
    entry_id TEXT NOT NULL,
    change_type TEXT NOT NULL CHECK(change_type IN ('insert','update','delete')),
    changed_fields TEXT DEFAULT NULL,
    summary TEXT DEFAULT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_entries_resource_type ON entries(resource_type);
CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category);
CREATE INDEX IF NOT EXISTS idx_entries_status ON entries(status);
CREATE INDEX IF NOT EXISTS idx_entries_team ON entries(team);
CREATE INDEX IF NOT EXISTS idx_entries_created_date ON entries(created_date);
CREATE INDEX IF NOT EXISTS idx_entries_type_category ON entries(resource_type, category);
CREATE INDEX IF NOT EXISTS idx_entries_type_date ON entries(resource_type, created_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_type_numeric ON entries(resource_type, numeric_id);

CREATE INDEX IF NOT EXISTS idx_tags_entry_id ON tags(entry_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);

CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_id);
CREATE INDEX IF NOT EXISTS idx_rel_type ON relationships(rel_type);

CREATE INDEX IF NOT EXISTS idx_changes_timestamp ON changes(timestamp);
CREATE INDEX IF NOT EXISTS idx_changes_entry_id ON changes(entry_id);

-- FTS5
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title,
    summary,
    description,
    content=entries,
    content_rowid=rowid,
    tokenize='unicode61 remove_diacritics 2'
);

-- FTS sync triggers
CREATE TRIGGER IF NOT EXISTS entries_fts_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, title, summary, description)
    VALUES (new.rowid, new.title, new.summary, new.description);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, summary, description)
    VALUES ('delete', old.rowid, old.title, old.summary, old.description);
    INSERT INTO entries_fts(rowid, title, summary, description)
    VALUES (new.rowid, new.title, new.summary, new.description);
END;

CREATE TRIGGER IF NOT EXISTS entries_fts_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, summary, description)
    VALUES ('delete', old.rowid, old.title, old.summary, old.description);
END;

-- Change-log triggers
CREATE TRIGGER IF NOT EXISTS trg_changes_insert AFTER INSERT ON entries BEGIN
    INSERT INTO changes(entry_id, change_type, summary)
    VALUES (new.id, 'insert', 'New ' || new.resource_type || ': ' || new.title);
END;

CREATE TRIGGER IF NOT EXISTS trg_changes_update AFTER UPDATE ON entries BEGIN
    INSERT INTO changes(entry_id, change_type, summary)
    VALUES (new.id, 'update', 'Updated ' || new.resource_type || ': ' || new.title);
END;

CREATE TRIGGER IF NOT EXISTS trg_changes_delete AFTER DELETE ON entries BEGIN
    INSERT INTO changes(entry_id, change_type, summary)
    VALUES (old.id, 'delete', 'Deleted ' || old.resource_type || ': ' || old.title);
END;

-- Views

CREATE VIEW IF NOT EXISTS v_summary AS
SELECT resource_type,
       COUNT(*) AS entry_count,
       MAX(updated_date) AS last_updated,
       GROUP_CONCAT(DISTINCT category) AS categories
FROM entries
GROUP BY resource_type
ORDER BY resource_type;

CREATE VIEW IF NOT EXISTS v_compact AS
SELECT e.id, e.title, e.resource_type, e.category, e.created_date AS date
FROM entries e
ORDER BY e.resource_type, e.numeric_id;

CREATE VIEW IF NOT EXISTS v_recent AS
SELECT e.id, e.title, e.resource_type, e.summary, e.created_date AS date
FROM entries e
ORDER BY e.created_date DESC, e.numeric_id DESC
LIMIT 10;

CREATE VIEW IF NOT EXISTS v_tag_cloud AS
SELECT tag, COUNT(*) AS usage_count
FROM tags
GROUP BY tag
ORDER BY usage_count DESC;

CREATE VIEW IF NOT EXISTS v_category_counts AS
SELECT resource_type, category, COUNT(*) AS count
FROM entries
GROUP BY resource_type, category
ORDER BY resource_type, count DESC;

CREATE VIEW IF NOT EXISTS v_kb_index AS
SELECT e.id, e.title, e.created_date AS date, e.team, e.category, e.status, e.confidence,
       (SELECT json_group_array(t.tag) FROM tags t WHERE t.entry_id = e.id) AS tags,
       (SELECT json_group_array(r.target_id) FROM relationships r WHERE r.source_id = e.id AND r.rel_type = 'builds_on') AS builds_on
FROM entries e
WHERE e.resource_type = 'KB'
ORDER BY e.numeric_id;

CREATE VIEW IF NOT EXISTS v_sop_index AS
SELECT e.id, e.title, e.file_path AS filename, e.version, e.status, e.description,
       (SELECT json_group_array(r.target_id) FROM relationships r WHERE r.source_id = e.id AND r.rel_type = 'supersedes') AS supersedes
FROM entries e
WHERE e.resource_type = 'SOP'
ORDER BY e.numeric_id;

CREATE VIEW IF NOT EXISTS v_scripts_index AS
SELECT e.id, e.title AS name,
       json_extract(e.extra_json, '$.filename') AS filename,
       json_extract(e.extra_json, '$.language') AS language,
       e.category, e.description,
       e.created_date AS added, e.team AS added_by_team,
       (SELECT json_group_array(t.tag) FROM tags t WHERE t.entry_id = e.id) AS tags
FROM entries e
WHERE e.resource_type = 'SCR'
ORDER BY e.numeric_id;

CREATE VIEW IF NOT EXISTS v_sources_index AS
SELECT e.id, e.title AS name,
       json_extract(e.extra_json, '$.url') AS url,
       json_extract(e.extra_json, '$.data_types') AS data_types,
       json_extract(e.extra_json, '$.access_type') AS access_type,
       (SELECT json_group_array(t.tag) FROM tags t WHERE t.entry_id = e.id) AS tags
FROM entries e
WHERE e.resource_type = 'SRC'
ORDER BY e.numeric_id;

CREATE VIEW IF NOT EXISTS v_tools_index AS
SELECT e.id, e.title AS name, e.description,
       json_extract(e.extra_json, '$.path') AS path,
       e.category,
       json_extract(e.extra_json, '$.auth_type') AS auth_type,
       e.created_date AS date_added, e.team,
       json_extract(e.extra_json, '$.modules') AS modules,
       json_extract(e.extra_json, '$.entry_point') AS entry_point,
       json_extract(e.extra_json, '$.requires') AS requires,
       (SELECT json_group_array(t.tag) FROM tags t WHERE t.entry_id = e.id) AS tags
FROM entries e
WHERE e.resource_type = 'TOOL'
ORDER BY e.numeric_id;

CREATE VIEW IF NOT EXISTS v_teams_index AS
SELECT e.id AS team_id,
       json_extract(e.extra_json, '$.filename') AS filename,
       e.created_date AS date,
       e.description AS objective,
       (SELECT json_group_array(r.target_id) FROM relationships r WHERE r.source_id = e.id AND r.rel_type = 'builds_on') AS builds_on_knowledge,
       json_extract(e.extra_json, '$.members') AS members
FROM entries e
WHERE e.resource_type = 'TEAM'
ORDER BY e.numeric_id;

CREATE VIEW IF NOT EXISTS v_ancestry AS
SELECT r.source_id AS child_id, e1.title AS child_title,
       r.target_id AS parent_id, e2.title AS parent_title
FROM relationships r
JOIN entries e1 ON r.source_id = e1.id
JOIN entries e2 ON r.target_id = e2.id
WHERE r.rel_type = 'builds_on'
ORDER BY r.source_id;

-- Seed ID sequences
INSERT OR IGNORE INTO id_sequences(resource_type, next_id) VALUES ('KB', 1);
INSERT OR IGNORE INTO id_sequences(resource_type, next_id) VALUES ('SOP', 1);
INSERT OR IGNORE INTO id_sequences(resource_type, next_id) VALUES ('SRC', 1);
INSERT OR IGNORE INTO id_sequences(resource_type, next_id) VALUES ('SCR', 1);
INSERT OR IGNORE INTO id_sequences(resource_type, next_id) VALUES ('TEAM', 1);
INSERT OR IGNORE INTO id_sequences(resource_type, next_id) VALUES ('TOOL', 1);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_yaml_frontmatter(text):
    """Parse YAML frontmatter from Markdown files (between --- delimiters).

    Uses a simple regex-based parser to avoid PyYAML dependency.
    """
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.DOTALL)
    if not m:
        return {}, text

    raw = m.group(1)
    body = text[m.end():]
    meta = {}

    for line in raw.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        colon_idx = line.find(':')
        if colon_idx == -1:
            continue

        key = line[:colon_idx].strip()
        val = line[colon_idx + 1:].strip()

        # Handle inline lists: [item1, item2, ...]
        if val.startswith('[') and val.endswith(']'):
            inner = val[1:-1].strip()
            if inner:
                items = [item.strip().strip('"').strip("'") for item in inner.split(',')]
                meta[key] = [i for i in items if i]
            else:
                meta[key] = []
        # Handle quoted strings
        elif val.startswith('"') and val.endswith('"'):
            meta[key] = val[1:-1]
        elif val.startswith("'") and val.endswith("'"):
            meta[key] = val[1:-1]
        elif val.lower() == 'null' or val == '~' or val == '':
            meta[key] = None
        elif val.lower() in ('true', 'yes'):
            meta[key] = True
        elif val.lower() in ('false', 'no'):
            meta[key] = False
        else:
            meta[key] = val

    return meta, body


def truncate(text, max_len=200):
    """Truncate text to max_len characters."""
    if not text:
        return None
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def extract_summary(body, max_len=200):
    """Extract a summary from markdown body text."""
    if not body:
        return None
    # Strip markdown headings and get first substantive paragraph
    lines = []
    for line in body.strip().split('\n'):
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith('#'):
            continue
        lines.append(stripped)

    text = ' '.join(lines)
    return truncate(text, max_len)


def content_hash(text):
    """SHA-256 hash of content."""
    return hashlib.sha256(text.encode('utf-8', errors='replace')).hexdigest()


def extract_numeric_id(entry_id, prefix):
    """Extract numeric portion from an ID like KB-0001 -> 1."""
    try:
        return int(entry_id.replace(prefix + '-', '').lstrip('0') or '0')
    except (ValueError, AttributeError):
        return 0


def rel_path(filepath):
    """Return path relative to repo root."""
    try:
        return str(Path(filepath).relative_to(REPO_ROOT))
    except ValueError:
        return str(filepath)


# ---------------------------------------------------------------------------
# Import functions
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self.counts = {'KB': 0, 'SOP': 0, 'SRC': 0, 'SCR': 0, 'TEAM': 0, 'TOOL': 0}
        self.tags_created = 0
        self.relationships_found = 0
        self.errors = []

    def report(self):
        print("\n=== Import Statistics ===")
        for rtype, count in sorted(self.counts.items()):
            print(f"  {rtype:6s}: {count:3d} entries")
        total = sum(self.counts.values())
        print(f"  {'TOTAL':6s}: {total:3d} entries")
        print(f"  Tags created:       {self.tags_created}")
        print(f"  Relationships found: {self.relationships_found}")
        if DB_PATH.exists():
            size_kb = DB_PATH.stat().st_size / 1024
            print(f"  DB size:            {size_kb:.1f} KB")
        if self.errors:
            print(f"\n  Errors ({len(self.errors)}):")
            for e in self.errors:
                print(f"    - {e}")


def insert_entry(cur, entry_id, numeric_id, resource_type, title, summary, category,
                 status, confidence, file_path, chash, created_date, updated_date,
                 team, char_count, version, description, extra_json):
    """Insert or replace an entry."""
    cur.execute("""
        INSERT OR REPLACE INTO entries
        (id, numeric_id, resource_type, title, summary, category, status, confidence,
         file_path, content_hash, created_date, updated_date, team, char_count, version,
         description, extra_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (entry_id, numeric_id, resource_type, title, summary, category,
          status or 'active', confidence, file_path, chash, created_date, updated_date,
          team, char_count, version or '1.0', description, extra_json))


def insert_tags(cur, entry_id, tags_list, stats):
    """Insert tags for an entry."""
    if not tags_list:
        return
    for tag in tags_list:
        if tag:
            tag = str(tag).strip()
            if tag:
                cur.execute("INSERT OR IGNORE INTO tags (entry_id, tag) VALUES (?, ?)",
                            (entry_id, tag))
                stats.tags_created += 1


def import_kb_entries(cur, stats):
    """Import knowledge-base/entries/KB-*.md files."""
    if not KB_DIR.exists():
        return
    for filepath in sorted(KB_DIR.glob("KB-*.md")):
        try:
            text = filepath.read_text(encoding='utf-8', errors='replace')
            meta, body = parse_yaml_frontmatter(text)

            entry_id = meta.get('id', filepath.stem)
            numeric_id = extract_numeric_id(entry_id, 'KB')
            title_line = ''
            for line in body.strip().split('\n'):
                if line.strip().startswith('# '):
                    title_line = line.strip()[2:].strip()
                    break
            title = meta.get('title', title_line or entry_id)
            summary = extract_summary(body)
            category = meta.get('category')
            status = meta.get('status', 'active')
            confidence = meta.get('confidence')
            created_date = meta.get('date')
            team = meta.get('team')

            insert_entry(cur, entry_id, numeric_id, 'KB', title, summary, category,
                         status, confidence, rel_path(filepath), content_hash(text),
                         created_date, created_date, team, len(text), '1.0',
                         extract_summary(body, 200), None)

            insert_tags(cur, entry_id, meta.get('tags', []), stats)

            # builds_on relationships (deferred - targets may not exist yet)
            for target in (meta.get('builds_on') or []):
                if target:
                    stats.relationships_found += 1

            stats.counts['KB'] += 1
        except Exception as e:
            stats.errors.append(f"KB {filepath.name}: {e}")


def import_sops(cur, stats):
    """Import storage/coordination/sops/sop_*.json files."""
    if not SOP_DIR.exists():
        return
    for filepath in sorted(SOP_DIR.glob("sop_*.json")):
        try:
            text = filepath.read_text(encoding='utf-8', errors='replace')
            data = json.loads(text)

            sop_id = data.get('id', '')
            if not sop_id:
                # Derive from filename: sop_020_bug_fix_workflow.json -> SOP-020
                m = re.match(r'sop_(\d+)', filepath.stem)
                sop_id = f"SOP-{m.group(1)}" if m else filepath.stem

            numeric_id = extract_numeric_id(sop_id, 'SOP')
            version = data.get('version', '1.0')

            # Handle versioned SOPs (e.g. v2 files): make ID unique
            unique_id = sop_id
            if 'v2' in filepath.stem or 'v3' in filepath.stem:
                ver_match = re.search(r'v(\d+)', filepath.stem)
                if ver_match:
                    unique_id = f"{sop_id}-v{ver_match.group(1)}"

            title = data.get('title', sop_id)
            purpose = data.get('purpose', data.get('description', ''))
            summary = truncate(purpose)
            status = data.get('status', 'active')
            created_date = data.get('created', data.get('date'))
            team_val = data.get('team')

            insert_entry(cur, unique_id, numeric_id, 'SOP', title, summary, None,
                         status, None, rel_path(filepath), content_hash(text),
                         created_date, created_date, team_val, len(text), str(version),
                         truncate(purpose, 200), None)

            stats.counts['SOP'] += 1
        except Exception as e:
            stats.errors.append(f"SOP {filepath.name}: {e}")


def import_sources(cur, stats):
    """Import storage/sources/SRC-*.json files."""
    if not SRC_DIR.exists():
        return
    for filepath in sorted(SRC_DIR.glob("SRC-*.json")):
        try:
            text = filepath.read_text(encoding='utf-8', errors='replace')
            data = json.loads(text)

            src_id = data.get('id', filepath.stem.split('-', 1)[0])
            numeric_id = extract_numeric_id(src_id, 'SRC')
            name = data.get('name', src_id)
            description = data.get('notes', data.get('description', ''))
            summary = truncate(description)

            extra = json.dumps({
                'url': data.get('url'),
                'data_types': data.get('data_types', []),
                'access_type': data.get('access_type', 'unknown'),
                'auth_method': data.get('auth_method'),
                'rate_limits': data.get('rate_limits'),
                'response_format': data.get('response_format'),
            })

            last_verified = data.get('last_verified')
            team_val = data.get('added_by_team')

            insert_entry(cur, src_id, numeric_id, 'SRC', name, summary, None,
                         'active', None, rel_path(filepath), content_hash(text),
                         last_verified, last_verified, team_val, len(text), '1.0',
                         truncate(description, 200), extra)

            # Tags from data_types and explicit tags
            all_tags = list(data.get('data_types', [])) + list(data.get('tags', []))
            insert_tags(cur, src_id, all_tags, stats)

            stats.counts['SRC'] += 1
        except Exception as e:
            stats.errors.append(f"SRC {filepath.name}: {e}")


def import_scripts(cur, stats):
    """Import scripts from storage/scripts/index.json."""
    if not SCRIPTS_INDEX.exists():
        return
    try:
        data = json.loads(SCRIPTS_INDEX.read_text(encoding='utf-8', errors='replace'))
    except Exception as e:
        stats.errors.append(f"Scripts index: {e}")
        return

    for script in data.get('scripts', []):
        try:
            scr_id = script.get('id', '')
            if not scr_id:
                continue
            numeric_id = extract_numeric_id(scr_id, 'SCR')
            name = script.get('name', scr_id)
            description = script.get('description', '')
            summary = truncate(description)
            category = script.get('category')
            filename = script.get('filename', '')
            created_date = script.get('added')
            team_val = script.get('added_by_team')

            extra = json.dumps({
                'filename': filename,
                'language': script.get('language', 'python'),
                'dependencies': script.get('dependencies', []),
            })

            file_path = f"storage/scripts/{filename}" if filename else f"storage/scripts/{scr_id}"

            insert_entry(cur, scr_id, numeric_id, 'SCR', name, summary, category,
                         'active', None, file_path, None,
                         created_date, created_date, team_val, 0, '1.0',
                         truncate(description, 200), extra)

            insert_tags(cur, scr_id, script.get('tags', []), stats)

            stats.counts['SCR'] += 1
        except Exception as e:
            stats.errors.append(f"SCR {script.get('id', '?')}: {e}")


def import_tools(cur, stats):
    """Import API tools from storage/api-tools/index.json."""
    if not TOOLS_INDEX.exists():
        return
    try:
        data = json.loads(TOOLS_INDEX.read_text(encoding='utf-8', errors='replace'))
    except Exception as e:
        stats.errors.append(f"Tools index: {e}")
        return

    for tool in data.get('tools', []):
        try:
            tool_id = tool.get('id', '')
            if not tool_id:
                continue
            numeric_id = extract_numeric_id(tool_id, 'TOOL')
            name = tool.get('name', tool_id)
            description = tool.get('description', '')
            summary = truncate(description)
            category = tool.get('category')
            created_date = tool.get('date_added', tool.get('added'))
            team_val = tool.get('team')

            extra = json.dumps({
                'path': tool.get('path'),
                'auth_type': tool.get('auth_type'),
                'modules': tool.get('modules', []),
                'entry_point': tool.get('entry_point'),
                'requires': tool.get('requires', []),
            })

            path_val = tool.get('path', '')
            file_path = f"storage/api-tools/{path_val}" if path_val else f"storage/api-tools/{tool_id}"

            insert_entry(cur, tool_id, numeric_id, 'TOOL', name, summary, category,
                         'active', None, file_path, None,
                         created_date, created_date, team_val, 0, '1.0',
                         truncate(description, 200), extra)

            insert_tags(cur, tool_id, tool.get('tags', []), stats)

            stats.counts['TOOL'] += 1
        except Exception as e:
            stats.errors.append(f"TOOL {tool.get('id', '?')}: {e}")


def import_teams(cur, stats):
    """Import team session logs from teams/sessions/index.json + TEAM-*.md files."""
    if not TEAMS_INDEX.exists():
        return
    try:
        data = json.loads(TEAMS_INDEX.read_text(encoding='utf-8', errors='replace'))
    except Exception as e:
        stats.errors.append(f"Teams index: {e}")
        return

    for session in data.get('sessions', []):
        try:
            team_id = session.get('team_id', '')
            if not team_id:
                continue
            numeric_id = extract_numeric_id(team_id, 'TEAM')
            objective = session.get('objective', team_id)
            summary = truncate(objective)
            created_date = session.get('date')
            filename = session.get('filename', '')

            # Read the actual MD file for char_count and content hash
            md_path = TEAMS_DIR / filename if filename else None
            char_count = 0
            chash = None
            if md_path and md_path.exists():
                text = md_path.read_text(encoding='utf-8', errors='replace')
                char_count = len(text)
                chash = content_hash(text)

            extra = json.dumps({
                'filename': filename,
                'members': session.get('members', []),
            })

            file_path = f"teams/sessions/{filename}" if filename else f"teams/sessions/{team_id}"

            insert_entry(cur, team_id, numeric_id, 'TEAM', objective, summary, None,
                         'active', None, file_path, chash,
                         created_date, created_date, team_id, char_count, '1.0',
                         summary, extra)

            # builds_on relationships (deferred)
            for kb_ref in (session.get('builds_on_knowledge') or []):
                if kb_ref:
                    stats.relationships_found += 1

            stats.counts['TEAM'] += 1
        except Exception as e:
            stats.errors.append(f"TEAM {session.get('team_id', '?')}: {e}")


def build_relationships(cur, stats):
    """Build relationships after all entries are imported."""
    # KB builds_on from frontmatter
    if KB_DIR.exists():
        for filepath in sorted(KB_DIR.glob("KB-*.md")):
            try:
                text = filepath.read_text(encoding='utf-8', errors='replace')
                meta, _ = parse_yaml_frontmatter(text)
                entry_id = meta.get('id', filepath.stem)
                for target in (meta.get('builds_on') or []):
                    if target:
                        # Check target exists
                        row = cur.execute("SELECT id FROM entries WHERE id = ?", (str(target),)).fetchone()
                        if row:
                            cur.execute("""INSERT OR IGNORE INTO relationships
                                          (source_id, target_id, rel_type) VALUES (?, ?, 'builds_on')""",
                                        (entry_id, str(target)))
            except Exception:
                pass

    # TEAM builds_on_knowledge
    if TEAMS_INDEX.exists():
        try:
            data = json.loads(TEAMS_INDEX.read_text(encoding='utf-8', errors='replace'))
            for session in data.get('sessions', []):
                team_id = session.get('team_id', '')
                for kb_ref in (session.get('builds_on_knowledge') or []):
                    if kb_ref:
                        row = cur.execute("SELECT id FROM entries WHERE id = ?", (str(kb_ref),)).fetchone()
                        if row:
                            cur.execute("""INSERT OR IGNORE INTO relationships
                                          (source_id, target_id, rel_type) VALUES (?, ?, 'builds_on')""",
                                        (team_id, str(kb_ref)))
        except Exception:
            pass


def update_id_sequences(cur):
    """Update id_sequences to reflect the current max IDs."""
    for rtype in ['KB', 'SOP', 'SRC', 'SCR', 'TEAM', 'TOOL']:
        row = cur.execute("SELECT MAX(numeric_id) FROM entries WHERE resource_type = ?",
                          (rtype,)).fetchone()
        max_id = row[0] if row and row[0] else 0
        cur.execute("UPDATE id_sequences SET next_id = ? WHERE resource_type = ?",
                    (max_id + 1, rtype))


def generate_tiered_indexes(db):
    """Generate SUMMARY.json and INDEX_COMPACT.json files in each relevant directory."""
    cur = db.cursor()

    # Level 0: SUMMARY.json per directory
    summary_rows = cur.execute("SELECT * FROM v_summary").fetchall()
    col_names = [d[0] for d in cur.description]
    summary_data = [dict(zip(col_names, row)) for row in summary_rows]

    # Map resource types to directories
    dir_map = {
        'KB': REPO_ROOT / "knowledge-base",
        'SOP': SOP_DIR,
        'SRC': SRC_DIR,
        'SCR': REPO_ROOT / "storage" / "scripts",
        'TEAM': TEAMS_DIR,
        'TOOL': REPO_ROOT / "storage" / "api-tools",
    }

    for entry in summary_data:
        rtype = entry['resource_type']
        target_dir = dir_map.get(rtype)
        if target_dir and target_dir.exists():
            # Get ID range
            range_row = cur.execute(
                "SELECT MIN(id), MAX(id) FROM entries WHERE resource_type = ?",
                (rtype,)
            ).fetchone()
            id_range = f"{range_row[0]} to {range_row[1]}" if range_row[0] else "none"

            summary_obj = {
                "directory": str(target_dir.relative_to(REPO_ROOT)),
                "purpose": f"Contains {entry['entry_count']} {rtype} entries",
                "entry_count": entry['entry_count'],
                "categories": entry['categories'].split(',') if entry['categories'] else [],
                "last_updated": entry['last_updated'],
                "id_range": id_range,
            }
            out_path = target_dir / "SUMMARY.json"
            out_path.write_text(json.dumps(summary_obj, indent=2) + '\n', encoding='utf-8')

    # Also write a global SUMMARY.json in storage/data/
    global_summary = {
        "generated": "auto",
        "description": "Level 0 summary of all resource types",
        "types": summary_data,
    }
    (REPO_ROOT / "storage" / "data" / "SUMMARY.json").write_text(
        json.dumps(global_summary, indent=2) + '\n', encoding='utf-8')

    # Level 1: INDEX_COMPACT.json per directory
    compact_rows = cur.execute("SELECT * FROM v_compact").fetchall()
    col_names = [d[0] for d in cur.description]
    compact_data = [dict(zip(col_names, row)) for row in compact_rows]

    for rtype, target_dir in dir_map.items():
        if target_dir and target_dir.exists():
            type_entries = [e for e in compact_data if e['resource_type'] == rtype]
            compact_obj = {
                "directory": str(target_dir.relative_to(REPO_ROOT)),
                "entry_count": len(type_entries),
                "entries": type_entries,
            }
            out_path = target_dir / "INDEX_COMPACT.json"
            out_path.write_text(json.dumps(compact_obj, indent=2) + '\n', encoding='utf-8')

    # Global compact index
    global_compact = {
        "generated": "auto",
        "description": "Level 1 compact index of all entries",
        "entry_count": len(compact_data),
        "entries": compact_data,
    }
    (REPO_ROOT / "storage" / "data" / "INDEX_COMPACT.json").write_text(
        json.dumps(global_compact, indent=2) + '\n', encoding='utf-8')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Import repository data into SQLite + FTS5 knowledge database."
    )
    parser.add_argument('--rebuild', action='store_true',
                        help='Drop and recreate the database from scratch')
    args = parser.parse_args()

    # Ensure data directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Handle rebuild
    if args.rebuild and DB_PATH.exists():
        print(f"Removing existing database: {DB_PATH}")
        DB_PATH.unlink()
        # Also remove WAL and SHM files
        for suffix in ['-wal', '-shm']:
            p = DB_PATH.parent / (DB_PATH.name + suffix)
            if p.exists():
                p.unlink()

    print(f"Database: {DB_PATH}")
    print("Applying schema...")

    db = sqlite3.connect(str(DB_PATH))
    db.executescript(MIGRATION_SQL)

    stats = Stats()
    cur = db.cursor()

    print("Importing KB entries...")
    import_kb_entries(cur, stats)

    print("Importing SOPs...")
    import_sops(cur, stats)

    print("Importing sources...")
    import_sources(cur, stats)

    print("Importing scripts...")
    import_scripts(cur, stats)

    print("Importing API tools...")
    import_tools(cur, stats)

    print("Importing team session logs...")
    import_teams(cur, stats)

    print("Building relationships...")
    build_relationships(cur, stats)

    print("Updating ID sequences...")
    update_id_sequences(cur)

    db.commit()

    # Rebuild FTS index to ensure consistency
    print("Rebuilding FTS index...")
    db.execute("INSERT INTO entries_fts(entries_fts) VALUES ('rebuild');")
    db.commit()

    print("Generating tiered index files...")
    generate_tiered_indexes(db)

    # Count actual relationships
    rel_count = db.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    stats.relationships_found = rel_count

    # Count actual tags
    tag_count = db.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    stats.tags_created = tag_count

    stats.report()

    db.close()
    print("\nDone.")


if __name__ == '__main__':
    main()
