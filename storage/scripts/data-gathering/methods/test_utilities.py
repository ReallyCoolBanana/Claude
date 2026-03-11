#!/usr/bin/env python3
"""
Tests for data-gathering utility modules.

Covers:
- ddg_utils: rate limiter thread safety, config locks
- request_cache: cache operations, TOCTOU-safe reads
- dedup_utils: tokenize, similarity_score, deduplicate_results

Run:
    python3 -m pytest test_utilities.py -v
    # or
    python3 test_utilities.py
"""

import os
import sys
import json
import base64
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

# Ensure the methods directory is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ddg_utils
import dedup_utils


class TestDdgUtilsRateLimiterThreadSafety(unittest.TestCase):
    """NEW-PROTO-007: Verify rate limiter is thread-safe."""

    def test_rate_limiter_lock_exists(self):
        """Rate limiter should use a threading.Lock."""
        self.assertIsInstance(ddg_utils._rate_limit_lock, type(threading.Lock()))

    def test_config_lock_exists(self):
        """Config mutation functions should use a threading.Lock."""
        self.assertIsInstance(ddg_utils._config_lock, type(threading.Lock()))

    def test_concurrent_rate_limit_enforcement(self):
        """Multiple threads calling _enforce_rate_limit should not corrupt state."""
        ddg_utils._last_request_time = 0.0
        original_rate = ddg_utils.RATE_LIMIT_SECONDS
        ddg_utils.RATE_LIMIT_SECONDS = 0.01  # Fast for testing

        errors = []

        def call_rate_limit():
            try:
                ddg_utils._enforce_rate_limit()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_rate_limit) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        ddg_utils.RATE_LIMIT_SECONDS = original_rate
        self.assertEqual(errors, [], f"Rate limiter raised errors under concurrency: {errors}")

    def test_set_user_agent_thread_safe(self):
        """set_user_agent should not corrupt state under concurrency."""
        original = ddg_utils.DEFAULT_USER_AGENT
        agents = [f"Agent-{i}" for i in range(20)]

        def set_agent(ua):
            ddg_utils.set_user_agent(ua)

        threads = [threading.Thread(target=set_agent, args=(a,)) for a in agents]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        # Value should be one of the agents we set (not corrupted)
        self.assertIn(ddg_utils.DEFAULT_USER_AGENT, agents)
        ddg_utils.set_user_agent(original)

    def test_set_rate_limit_thread_safe(self):
        """set_rate_limit should not corrupt state under concurrency."""
        original = ddg_utils.RATE_LIMIT_SECONDS
        values = [0.1 * i for i in range(1, 11)]

        def set_limit(v):
            ddg_utils.set_rate_limit(v)

        threads = [threading.Thread(target=set_limit, args=(v,)) for v in values]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertIn(ddg_utils.RATE_LIMIT_SECONDS, values)
        ddg_utils.set_rate_limit(original)


class TestRequestCache(unittest.TestCase):
    """NEW-PROTO-005, NEW-PROTO-006: Cache operations and parameter naming."""

    def test_clear_cache_no_all_parameter(self):
        """clear_cache should use 'purge_all' not 'all' (shadowing builtin)."""
        import request_cache
        import inspect
        sig = inspect.signature(request_cache.clear_cache)
        param_names = list(sig.parameters.keys())
        self.assertNotIn("all", param_names,
                         "clear_cache should not shadow the builtin 'all'")
        self.assertIn("purge_all", param_names,
                      "clear_cache should use 'purge_all' parameter name")

    def test_cache_hit_uses_try_except(self):
        """cached_request should use try/except, not os.path.exists for TOCTOU safety."""
        import request_cache
        import inspect
        source = inspect.getsource(request_cache.cached_request)
        # The cache-hit section should NOT use os.path.exists
        # We check the section between "cache hit" and "cache miss"
        hit_section_start = source.find("cache hit")
        miss_section_start = source.find("cache miss")
        if hit_section_start >= 0 and miss_section_start >= 0:
            hit_section = source[hit_section_start:miss_section_start]
            self.assertNotIn("os.path.exists", hit_section,
                             "Cache hit check should use try/except, not os.path.exists")

    def test_cache_key_deterministic(self):
        """Cache keys should be deterministic for the same inputs."""
        import request_cache
        key1 = request_cache._cache_key("https://example.com", {"User-Agent": "test"})
        key2 = request_cache._cache_key("https://example.com", {"User-Agent": "test"})
        self.assertEqual(key1, key2)

    def test_cache_key_differs_for_different_urls(self):
        """Different URLs should produce different cache keys."""
        import request_cache
        key1 = request_cache._cache_key("https://example.com/a")
        key2 = request_cache._cache_key("https://example.com/b")
        self.assertNotEqual(key1, key2)


class TestDedupUtils(unittest.TestCase):
    """Tests for dedup_utils functions."""

    def test_tokenize_removes_stop_words(self):
        """tokenize should remove common stop words."""
        tokens = dedup_utils.tokenize("this is a test of the tokenizer")
        self.assertNotIn("this", tokens)
        self.assertNotIn("is", tokens)
        self.assertNotIn("the", tokens)
        self.assertIn("test", tokens)
        self.assertIn("tokenizer", tokens)

    def test_tokenize_empty_input(self):
        """tokenize should handle empty/None input gracefully."""
        self.assertEqual(dedup_utils.tokenize(""), [])
        self.assertEqual(dedup_utils.tokenize(None), [])

    def test_similarity_score_identical(self):
        """Identical texts should have similarity close to 1.0."""
        text = "quantum computing research papers published recently"
        score = dedup_utils.similarity_score(text, text)
        self.assertGreater(score, 0.99)

    def test_similarity_score_unrelated(self):
        """Completely unrelated texts should have low similarity."""
        score = dedup_utils.similarity_score(
            "quantum computing entanglement superposition",
            "medieval pottery glazing techniques kiln"
        )
        self.assertLess(score, 0.3)

    def test_deduplicate_results_merges_duplicates(self):
        """deduplicate_results should merge near-duplicate entries."""
        results = [
            {"title": "Quantum Computing Overview", "snippet": "An introduction to quantum computing principles and applications"},
            {"title": "Quantum Computing Overview", "snippet": "An introduction to quantum computing principles and applications in science"},
            {"title": "Medieval Pottery Techniques", "snippet": "A guide to traditional pottery glazing methods"},
        ]
        deduped = dedup_utils.deduplicate_results(results, threshold=0.5)
        # The two quantum computing entries should merge, pottery stays separate
        self.assertLessEqual(len(deduped), 2,
                             "Near-duplicate entries should be merged")

    def test_deduplicate_empty_list(self):
        """deduplicate_results should handle empty input."""
        self.assertEqual(dedup_utils.deduplicate_results([]), [])

    def test_cosine_similarity_orthogonal(self):
        """Orthogonal vectors should have zero similarity."""
        vec_a = {"alpha": 1.0, "beta": 0.0}
        vec_b = {"gamma": 1.0, "delta": 0.0}
        sim = dedup_utils.cosine_similarity(vec_a, vec_b)
        self.assertAlmostEqual(sim, 0.0)

    def test_merge_citation_lists_deduplicates(self):
        """merge_citation_lists should remove duplicate citations."""
        list_a = [{"doi": "10.1234/abc", "title": "Paper A"}]
        list_b = [{"doi": "10.1234/abc", "title": "Paper A"}, {"doi": "10.5678/xyz", "title": "Paper B"}]
        merged = dedup_utils.merge_citation_lists(list_a, list_b)
        self.assertEqual(len(merged), 2)


if __name__ == "__main__":
    unittest.main()
