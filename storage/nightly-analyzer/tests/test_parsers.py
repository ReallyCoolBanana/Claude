#!/usr/bin/env python3
"""
Unit tests for LLM response parsers.

These tests don't require Neo4j, LanceDB, or the GPU.
Run with: python -m pytest tests/test_parsers.py -v

Expected output:
    tests/test_parsers.py::test_parse_llm_score_normal PASSED
    tests/test_parsers.py::test_parse_llm_score_percentage PASSED
    ...
    ====================== 15 passed in 0.1s ======================
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm_edge_analysis import parse_llm_score
from causal_inference import parse_discovery_response, parse_validation_response
from condensation import parse_condensation_response
from emergence_detection import parse_emergence_response


# ---------------------------------------------------------------------------
# Edge Analysis Parser Tests
# ---------------------------------------------------------------------------

def test_parse_llm_score_normal():
    response = "SCORE: 0.75\nREASONING: These concepts are strongly related."
    result = parse_llm_score(response)
    assert result is not None
    score, reasoning = result
    assert score == 0.75
    assert "strongly related" in reasoning


def test_parse_llm_score_percentage():
    """Handle LLMs that respond with percentage instead of decimal."""
    response = "SCORE: 75\nREASONING: High overlap."
    result = parse_llm_score(response)
    assert result is not None
    score, _ = result
    assert score == 0.75


def test_parse_llm_score_clamping():
    """Scores outside [0,1] should be clamped."""
    response = "SCORE: 1.5\nREASONING: Very strong."
    result = parse_llm_score(response)
    assert result is not None
    assert result[0] == 1.0


def test_parse_llm_score_missing_reasoning():
    response = "SCORE: 0.5"
    result = parse_llm_score(response)
    assert result is not None
    assert result[0] == 0.5
    assert result[1] == "No reasoning provided"


def test_parse_llm_score_extra_text():
    response = "Let me think about this.\n\nSCORE: 0.6\nREASONING: Moderate link."
    result = parse_llm_score(response)
    assert result is not None
    assert result[0] == 0.6


def test_parse_llm_score_unparseable():
    response = "I cannot evaluate this relationship."
    result = parse_llm_score(response)
    assert result is None


# ---------------------------------------------------------------------------
# Causal Discovery Parser Tests
# ---------------------------------------------------------------------------

def test_parse_discovery_normal():
    response = (
        "CAUSAL: YES\n"
        "CONFIDENCE: 0.72\n"
        "DIRECTION: A_CAUSES_B\n"
        "MECHANISM: API latency caused the alert."
    )
    result = parse_discovery_response(response)
    assert result is not None
    assert result["is_causal"] is True
    assert result["confidence"] == 0.72
    assert result["direction"] == "A_CAUSES_B"
    assert "latency" in result["mechanism"]


def test_parse_discovery_no_causal():
    response = (
        "CAUSAL: NO\n"
        "CONFIDENCE: 0.2\n"
        "DIRECTION: NONE\n"
        "MECHANISM: N/A"
    )
    result = parse_discovery_response(response)
    assert result is not None
    assert result["is_causal"] is False


def test_parse_validation_normal():
    response = (
        "STILL_CAUSAL: YES\n"
        "NEW_CONFIDENCE: 0.85\n"
        "REASONING: New evidence confirms the connection."
    )
    result = parse_validation_response(response)
    assert result is not None
    assert result["still_causal"] is True
    assert result["new_confidence"] == 0.85


# ---------------------------------------------------------------------------
# Condensation Parser Tests
# ---------------------------------------------------------------------------

def test_parse_condensation_normal():
    response = (
        "SUMMARY: The user investigated API issues across three services.\n"
        "KEY_FACTS: API latency, DB bottleneck, cache miss\n"
        "CONFIDENCE: 0.85"
    )
    result = parse_condensation_response(response)
    assert result is not None
    assert "API issues" in result["summary"]
    assert len(result["key_facts"]) == 3
    assert result["confidence"] == 0.85


# ---------------------------------------------------------------------------
# Emergence Parser Tests
# ---------------------------------------------------------------------------

def test_parse_emergence_normal():
    response = (
        "EMERGENT: YES\n"
        "CONFIDENCE: 0.78\n"
        "INSIGHT: The recurring latency issues suggest a systemic problem.\n"
        "CATEGORY: PATTERN\n"
        "SOURCE_PATHS: 1,2,3"
    )
    result = parse_emergence_response(response)
    assert result is not None
    assert result["is_emergent"] is True
    assert result["confidence"] == 0.78
    assert result["category"] == "PATTERN"
    assert result["source_paths"] == [1, 2, 3]


def test_parse_emergence_not_emergent():
    response = (
        "EMERGENT: NO\n"
        "CONFIDENCE: 0.3\n"
        "INSIGHT: N/A\n"
        "CATEGORY: PATTERN\n"
        "SOURCE_PATHS: "
    )
    result = parse_emergence_response(response)
    assert result is not None
    assert result["is_emergent"] is False


if __name__ == "__main__":
    # Run all tests manually
    test_functions = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test_fn in test_functions:
        try:
            test_fn()
            print(f"  PASSED: {test_fn.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAILED: {test_fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
