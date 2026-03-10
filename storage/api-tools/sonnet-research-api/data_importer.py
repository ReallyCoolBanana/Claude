"""
Data Importer - Imports research results into the Claude Agent repository.

Bridges the gap between Sonnet research output and the repo's knowledge-base,
sources, and api-tools storage structures.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Repo layout constants
# ---------------------------------------------------------------------------

_DEFAULT_REPO_ROOT = "/home/user/Claude"


# ---------------------------------------------------------------------------
# DataImporter
# ---------------------------------------------------------------------------

class DataImporter:
    """Import research results into the repository's storage structure.

    Supports writing into:

    * **knowledge-base/entries/** -- Markdown entries using the KB template.
    * **storage/sources/** -- JSON source definitions.
    * **storage/api-tools/** -- JSON API-tool definitions.

    After importing, call :meth:`update_indexes` to refresh the relevant
    ``index.json`` files so that other teams can discover the new data.

    Usage::

        importer = DataImporter()
        importer.import_to_knowledge_base(results, category="methodology")
        importer.import_to_sources(results)
        importer.update_indexes()
    """

    def __init__(self, repo_root: str = _DEFAULT_REPO_ROOT) -> None:
        self._repo_root = Path(repo_root)
        self._kb_dir = self._repo_root / "knowledge-base"
        self._kb_entries_dir = self._kb_dir / "entries"
        self._sources_dir = self._repo_root / "storage" / "sources"
        self._api_tools_dir = self._repo_root / "storage" / "api-tools"

        # Track what was imported so update_indexes() knows what to refresh.
        self._imported_kb_entries: list[dict[str, Any]] = []
        self._imported_sources: list[dict[str, Any]] = []
        self._imported_tools: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Knowledge Base
    # ------------------------------------------------------------------

    def import_to_knowledge_base(
        self,
        results: list[dict[str, Any]],
        category: str = "methodology",
        team: str = "sonnet-research",
    ) -> list[str]:
        """Create knowledge-base entries from research results.

        Each result dict (as produced by ``SonnetResearcher.format_results()``)
        is converted into a Markdown file following the KB template.

        Args:
            results: List of research result dicts.
            category: KB category to assign (e.g. ``"methodology"``).
            team: Team name written into the YAML front-matter.

        Returns:
            List of file paths that were written.
        """
        self._kb_entries_dir.mkdir(parents=True, exist_ok=True)

        next_id = self._next_kb_id()
        written_paths: list[str] = []

        for result in results:
            entry_id = f"KB-{next_id:04d}"
            topic = result.get("topic", "Untitled Research")
            summary = result.get("summary", "")
            depth = result.get("depth", "standard")
            timestamp = result.get("timestamp", datetime.now(timezone.utc).isoformat())
            queries_made = result.get("queries_made", [])
            token_usage = result.get("token_usage", {})

            # Build the method section from queries.
            method_lines: list[str] = [
                f"Automated research via Sonnet Research API at **{depth}** depth.",
            ]
            if queries_made:
                method_lines.append(f"\n{len(queries_made)} queries were executed.")

            # Build tags from the topic.
            tags = _extract_tags(topic)
            tags_str = ", ".join(tags) if tags else "research"

            content = _render_kb_entry(
                entry_id=entry_id,
                date=_today(),
                team=team,
                category=category,
                tags=tags_str,
                title=topic,
                context=f"Research conducted on: {topic}",
                method="\n".join(method_lines),
                result_section=summary or "(No summary produced.)",
                lessons="See summary above for key findings.",
                recommendations="Review and validate findings before acting on them.",
            )

            filepath = self._kb_entries_dir / f"{entry_id}.md"
            filepath.write_text(content, encoding="utf-8")
            written_paths.append(str(filepath))

            self._imported_kb_entries.append({
                "id": entry_id,
                "title": topic,
                "date": _today(),
                "team": team,
                "category": category,
                "tags": tags,
                "status": "experimental",
                "confidence": "medium",
            })

            next_id += 1

        return written_paths

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def import_to_sources(
        self,
        results: list[dict[str, Any]],
        team: str = "sonnet-research",
    ) -> list[str]:
        """Register data sources discovered during research.

        Scans each result's summary for URLs and creates source entries.  If
        no URLs are found the result is still recorded as a generic source
        reference.

        Args:
            results: List of research result dicts.
            team: Team name for attribution.

        Returns:
            List of file paths that were written.
        """
        self._sources_dir.mkdir(parents=True, exist_ok=True)
        next_id = self._next_source_id()
        written_paths: list[str] = []

        for result in results:
            source_id = f"SRC-{next_id:04d}"
            topic = result.get("topic", "Unknown Source")
            summary = result.get("summary", "")

            # Try to extract a URL from the summary; fall back to empty.
            url = _extract_first_url(summary)

            source_def: dict[str, Any] = {
                "id": source_id,
                "name": topic,
                "url": url,
                "api_docs_url": "",
                "data_types": _extract_tags(topic),
                "access_type": "unknown",
                "auth_method": "none",
                "rate_limits": "",
                "response_format": "json",
                "reliability": "unknown",
                "last_verified": _today(),
                "notes": f"Discovered via Sonnet research on '{topic}'.",
                "added_by_team": team,
            }

            filepath = self._sources_dir / f"{source_id}.json"
            filepath.write_text(
                json.dumps(source_def, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            written_paths.append(str(filepath))

            self._imported_sources.append(source_def)
            next_id += 1

        return written_paths

    # ------------------------------------------------------------------
    # API Tools
    # ------------------------------------------------------------------

    def import_to_api_tools(
        self,
        tool_definitions: list[dict[str, Any]],
        team: str = "sonnet-research",
    ) -> list[str]:
        """Register discovered API tools in ``storage/api-tools/``.

        Each element of *tool_definitions* should be a dict with at least
        ``name`` and ``description`` keys.

        Args:
            tool_definitions: List of tool definition dicts.
            team: Team name for attribution.

        Returns:
            List of file paths that were written.
        """
        self._api_tools_dir.mkdir(parents=True, exist_ok=True)
        next_id = self._next_tool_id()
        written_paths: list[str] = []

        for tool_def in tool_definitions:
            tool_id = f"TOOL-{next_id:04d}"
            name = tool_def.get("name", f"tool-{next_id}")
            description = tool_def.get("description", "")
            category = tool_def.get("category", "analysis")
            auth_type = tool_def.get("auth_type", "none")

            entry: dict[str, Any] = {
                "id": tool_id,
                "name": name,
                "description": description,
                "category": category,
                "auth_type": auth_type,
                "added_by_team": team,
                "date_added": _today(),
                **{k: v for k, v in tool_def.items()
                   if k not in ("name", "description", "category", "auth_type")},
            }

            filepath = self._api_tools_dir / f"{tool_id}.json"
            filepath.write_text(
                json.dumps(entry, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            written_paths.append(str(filepath))

            self._imported_tools.append(entry)
            next_id += 1

        return written_paths

    # ------------------------------------------------------------------
    # Index updates
    # ------------------------------------------------------------------

    def update_indexes(self) -> list[str]:
        """Refresh all relevant ``index.json`` files.

        Merges newly imported items into the existing indexes without
        overwriting prior entries.

        Returns:
            List of index file paths that were updated.
        """
        updated: list[str] = []

        if self._imported_kb_entries:
            self._update_kb_index()
            updated.append(str(self._kb_dir / "index.json"))

        if self._imported_sources:
            self._update_sources_index()
            updated.append(str(self._sources_dir / "index.json"))

        if self._imported_tools:
            self._update_api_tools_index()
            updated.append(str(self._api_tools_dir / "index.json"))

        return updated

    # ------------------------------------------------------------------
    # Private: index update helpers
    # ------------------------------------------------------------------

    def _update_kb_index(self) -> None:
        index_path = self._kb_dir / "index.json"
        index = _load_json(index_path, default={
            "version": "1.0",
            "last_updated": _today(),
            "entry_count": 0,
            "entries": [],
            "categories": {},
            "tag_index": {},
        })

        existing_ids = {e["id"] for e in index.get("entries", [])}

        for entry in self._imported_kb_entries:
            if entry["id"] in existing_ids:
                continue
            index["entries"].append(entry)
            index["entry_count"] = len(index["entries"])

            # Update category index.
            cat = entry.get("category", "methodology")
            index.setdefault("categories", {}).setdefault(cat, []).append(entry["id"])

            # Update tag index.
            for tag in entry.get("tags", []):
                index.setdefault("tag_index", {}).setdefault(tag, []).append(entry["id"])

        index["last_updated"] = _today()
        _save_json(index_path, index)

    def _update_sources_index(self) -> None:
        index_path = self._sources_dir / "index.json"
        index = _load_json(index_path, default={
            "version": "1.0",
            "last_updated": _today(),
            "sources": [],
            "by_data_type": {},
            "by_access_type": {},
        })

        existing_ids = {s.get("id") for s in index.get("sources", []) if "id" in s}

        for src in self._imported_sources:
            if src["id"] in existing_ids:
                continue
            index["sources"].append({
                "id": src["id"],
                "name": src["name"],
                "url": src.get("url", ""),
                "data_types": src.get("data_types", []),
                "access_type": src.get("access_type", "unknown"),
            })

            # Update by_data_type.
            for dt in src.get("data_types", []):
                index.setdefault("by_data_type", {}).setdefault(dt, []).append(src["id"])

            # Update by_access_type.
            at = src.get("access_type", "unknown")
            index.setdefault("by_access_type", {}).setdefault(at, []).append(src["id"])

        index["last_updated"] = _today()
        _save_json(index_path, index)

    def _update_api_tools_index(self) -> None:
        index_path = self._api_tools_dir / "index.json"
        index = _load_json(index_path, default={
            "version": "1.0",
            "last_updated": _today(),
            "tools": [],
            "categories": {},
            "auth_types": {},
        })

        existing_ids = {t.get("id") for t in index.get("tools", []) if "id" in t}

        for tool in self._imported_tools:
            if tool["id"] in existing_ids:
                continue
            index["tools"].append({
                "id": tool["id"],
                "name": tool["name"],
                "description": tool.get("description", ""),
                "category": tool.get("category", "analysis"),
            })

            cat = tool.get("category", "analysis")
            index.setdefault("categories", {}).setdefault(cat, []).append(tool["id"])

            auth = tool.get("auth_type", "none")
            index.setdefault("auth_types", {}).setdefault(auth, []).append(tool["id"])

        index["last_updated"] = _today()
        _save_json(index_path, index)

    # ------------------------------------------------------------------
    # Private: ID generators (read current indexes to find next available)
    # ------------------------------------------------------------------

    def _next_kb_id(self) -> int:
        index_path = self._kb_dir / "index.json"
        index = _load_json(index_path, default={"entries": []})
        ids = [
            int(e["id"].split("-")[1])
            for e in index.get("entries", [])
            if isinstance(e.get("id"), str) and e["id"].startswith("KB-")
        ]
        return (max(ids) + 1) if ids else 1

    def _next_source_id(self) -> int:
        index_path = self._sources_dir / "index.json"
        index = _load_json(index_path, default={"sources": []})
        ids = [
            int(s["id"].split("-")[1])
            for s in index.get("sources", [])
            if isinstance(s.get("id"), str) and s["id"].startswith("SRC-")
        ]
        return (max(ids) + 1) if ids else 1

    def _next_tool_id(self) -> int:
        index_path = self._api_tools_dir / "index.json"
        index = _load_json(index_path, default={"tools": []})
        ids = [
            int(t["id"].split("-")[1])
            for t in index.get("tools", [])
            if isinstance(t.get("id"), str) and t["id"].startswith("TOOL-")
        ]
        return (max(ids) + 1) if ids else 1


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Load a JSON file, returning *default* if the file is missing or invalid."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _extract_tags(text: str) -> list[str]:
    """Derive simple lowercase tags from a topic string."""
    import re

    stopwords = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "is", "it", "as",
    }
    words = re.findall(r"[a-z0-9]+", text.lower())
    seen: set[str] = set()
    tags: list[str] = []
    for w in words:
        if w not in stopwords and w not in seen and len(w) > 2:
            seen.add(w)
            tags.append(w)
    return tags


def _extract_first_url(text: str) -> str:
    """Return the first URL found in *text*, or an empty string."""
    import re

    match = re.search(r"https?://[^\s)\]\"'>]+", text)
    return match.group(0) if match else ""


def _render_kb_entry(
    *,
    entry_id: str,
    date: str,
    team: str,
    category: str,
    tags: str,
    title: str,
    context: str,
    method: str,
    result_section: str,
    lessons: str,
    recommendations: str,
) -> str:
    """Render a knowledge-base Markdown entry following TEMPLATE.md."""
    return (
        f"---\n"
        f"id: {entry_id}\n"
        f"date: {date}\n"
        f"team: {team}\n"
        f"role: researcher\n"
        f"category: {category}\n"
        f"tags: [{tags}]\n"
        f"status: experimental\n"
        f"confidence: medium\n"
        f"builds_on: []\n"
        f"---\n"
        f"# {title}\n"
        f"\n"
        f"## Context\n"
        f"{context}\n"
        f"\n"
        f"## Method\n"
        f"{method}\n"
        f"\n"
        f"## Result\n"
        f"{result_section}\n"
        f"\n"
        f"## Lessons Learned\n"
        f"{lessons}\n"
        f"\n"
        f"## Recommendations\n"
        f"{recommendations}\n"
    )
