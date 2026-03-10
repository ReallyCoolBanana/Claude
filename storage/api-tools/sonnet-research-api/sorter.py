"""
Result Sorter - Sorts, groups, categorizes, and filters research results.

Operates on lists of result dicts produced by SonnetResearcher.format_results().
"""

import json
import os
import re
import time
from collections import defaultdict
from typing import Optional


class ResultSorter:
    """
    Sorts and organizes research results by relevance, depth, topic,
    or custom categories.

    Usage:
        sorter = ResultSorter(results)
        ranked = sorter.sort_by_relevance("machine learning")
        grouped = sorter.sort_by_topic()
        sorter.export_sorted("/path/to/output.json")
    """

    def __init__(self, results: list[dict]):
        """
        Initialize with a list of research result dicts.

        Args:
            results: List of dicts from SonnetResearcher.format_results().
                     Expected keys: topic, summary, queries, depth, timestamp, etc.
        """
        self._results = list(results)
        self._sorted_results: list[dict] = list(results)
        self._groups: dict = {}

    @property
    def results(self) -> list[dict]:
        """The current sorted/filtered result set."""
        return list(self._sorted_results)

    def sort_by_relevance(self, query: str) -> list[dict]:
        """
        Sort results by keyword relevance to a query string.

        Scores each result by counting occurrences of query keywords in
        the result's topic and summary fields.

        Args:
            query: The relevance query string.

        Returns:
            Results sorted by descending relevance score.
        """
        query_keywords = self._extract_keywords(query.lower())

        scored = []
        for result in self._results:
            score = self._compute_relevance_score(result, query_keywords)
            scored.append((score, result))

        scored.sort(key=lambda x: x[0], reverse=True)
        self._sorted_results = [item[1] for item in scored]
        return self.results

    def sort_by_depth(self) -> dict[str, list[dict]]:
        """
        Group results by their research depth level.

        Returns:
            Dict mapping depth level (e.g. "standard", "deep") to list of results.
        """
        groups: dict[str, list[dict]] = defaultdict(list)
        for result in self._results:
            depth = result.get("depth", "unknown")
            groups[depth].append(result)

        self._groups = dict(groups)
        return dict(groups)

    def sort_by_topic(self) -> dict[str, list[dict]]:
        """
        Group results by topic similarity using simple keyword clustering.

        Extracts keywords from each result's topic, then clusters results
        that share significant keyword overlap.

        Returns:
            Dict mapping cluster label to list of results.
        """
        # Extract keywords per result
        result_keywords = []
        for result in self._results:
            topic = result.get("topic", "")
            summary = result.get("summary", "")
            keywords = self._extract_keywords(f"{topic} {summary}".lower())
            result_keywords.append(keywords)

        # Simple greedy clustering: assign each result to the first cluster
        # whose label keywords overlap with the result's keywords
        clusters: dict[str, list[dict]] = {}
        cluster_keys: dict[str, set[str]] = {}

        for idx, result in enumerate(self._results):
            keywords = result_keywords[idx]
            assigned = False

            for label, label_kws in cluster_keys.items():
                overlap = keywords & label_kws
                if len(overlap) >= 1:
                    clusters[label].append(result)
                    label_kws.update(keywords)
                    assigned = True
                    break

            if not assigned:
                label = result.get("topic", f"cluster-{len(clusters)}")
                clusters[label] = [result]
                cluster_keys[label] = set(keywords)

        self._groups = clusters
        return dict(clusters)

    def categorize(self, categories: list[str]) -> dict[str, list[dict]]:
        """
        Assign each result to the best-matching category using keyword matching.

        Args:
            categories: List of category names/labels.

        Returns:
            Dict mapping category name to list of matching results.
        """
        categorized: dict[str, list[dict]] = {cat: [] for cat in categories}
        categorized["uncategorized"] = []

        cat_keywords = {
            cat: self._extract_keywords(cat.lower()) for cat in categories
        }

        for result in self._results:
            topic = result.get("topic", "")
            summary = result.get("summary", "")
            text_keywords = self._extract_keywords(f"{topic} {summary}".lower())

            best_cat = None
            best_score = 0

            for cat, kws in cat_keywords.items():
                score = len(text_keywords & kws)
                if score > best_score:
                    best_score = score
                    best_cat = cat

            if best_cat and best_score > 0:
                categorized[best_cat].append(result)
            else:
                categorized["uncategorized"].append(result)

        # Remove empty uncategorized if nothing landed there
        if not categorized["uncategorized"]:
            del categorized["uncategorized"]

        self._groups = categorized
        return categorized

    def filter_results(
        self,
        min_queries: Optional[int] = None,
        has_summary: bool = True,
    ) -> list[dict]:
        """
        Filter results based on criteria.

        Args:
            min_queries: Minimum number of queries in the result. None to skip.
            has_summary: If True, only include results that have a non-empty summary.

        Returns:
            Filtered list of results.
        """
        filtered = []
        for result in self._results:
            if has_summary:
                summary = result.get("summary", "")
                if not summary or not summary.strip():
                    continue

            if min_queries is not None:
                queries = result.get("queries", [])
                if len(queries) < min_queries:
                    continue

            filtered.append(result)

        self._sorted_results = filtered
        return self.results

    def export_sorted(self, output_path: str, format: str = "json") -> str:
        """
        Save current sorted/filtered results to a file.

        Args:
            output_path: Path to write the output file.
            format: Output format - currently only "json" is supported.

        Returns:
            The path the file was written to.
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        data = {
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "result_count": len(self._sorted_results),
            "results": self._sorted_results,
        }

        if self._groups:
            data["groups"] = {
                key: [r.get("topic", "unknown") for r in vals]
                for key, vals in self._groups.items()
            }

        if format == "json":
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported export format: {format}")

        return output_path

    def generate_index(self, results: list[dict]) -> dict:
        """
        Create an index.json compatible with the repo's storage format.

        Args:
            results: List of research result dicts.

        Returns:
            Index dict ready to be written as JSON.
        """
        entries = []
        for idx, result in enumerate(results):
            entry = {
                "id": f"RES-{idx + 1:04d}",
                "topic": result.get("topic", "unknown"),
                "depth": result.get("depth", "standard"),
                "timestamp": result.get("timestamp", ""),
                "has_summary": bool(result.get("summary")),
                "query_count": len(result.get("queries", [])),
            }
            entries.append(entry)

        return {
            "version": "1.0",
            "last_updated": time.strftime("%Y-%m-%d"),
            "result_count": len(entries),
            "entries": entries,
        }

    # ── Internal helpers ──────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract meaningful keywords from text, filtering stopwords."""
        stopwords = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
            "for", "of", "with", "by", "from", "is", "it", "as", "was",
            "are", "be", "been", "being", "have", "has", "had", "do", "does",
            "did", "will", "would", "could", "should", "may", "might", "can",
            "this", "that", "these", "those", "not", "no", "so", "if", "then",
            "than", "too", "very", "just", "about", "up", "out", "into",
        }
        words = set(re.findall(r"[a-z0-9]+", text))
        return words - stopwords

    @staticmethod
    def _compute_relevance_score(result: dict, query_keywords: set[str]) -> int:
        """Compute a relevance score for a result against query keywords."""
        score = 0
        topic = result.get("topic", "").lower()
        summary = result.get("summary", "").lower()

        # Topic matches are weighted higher
        topic_words = set(re.findall(r"[a-z0-9]+", topic))
        summary_words = set(re.findall(r"[a-z0-9]+", summary))

        score += len(query_keywords & topic_words) * 3
        score += len(query_keywords & summary_words) * 1

        return score
