#!/usr/bin/env python3
"""
grade_research.py - Research Quality Grading Tool

Evaluates a team's research JSON file and produces a quality grade report.

Usage:
    python grade_research.py <research_json_path> [--output <output_path>]

Grading Criteria:
    - Citation count: number of sources per topic
    - Citation quality: proportion from high-quality academic/industry domains
    - Coverage breadth: number of unique techniques mentioned
    - Depth score: average word count per topic finding
    - Specificity: presence of concrete metrics, code examples, tool names

Input Format:
    The research JSON should follow this structure:
    {
        "team": "team-name",
        "topics": [
            {
                "name": "Topic Name",
                "category": "category-id",
                "findings": "Text of findings...",
                "techniques": ["technique1", "technique2"],
                "references": ["https://arxiv.org/...", ...],
                "metrics": {"sharpe": 1.5, ...},
                "tools_mentioned": ["PyTorch", ...],
                "code_examples": ["snippet or link", ...]
            }
        ]
    }

Output:
    A JSON grade report written to stdout or the specified output path.
"""

import json
import re
import sys
import os
from urllib.parse import urlparse
from collections import Counter


# Domains considered high-quality for financial/ML research
HIGH_QUALITY_DOMAINS = {
    "arxiv.org",
    "ssrn.com",
    "nature.com",
    "ieee.org",
    "acm.org",
    "github.com",
    "reuters.com",
    "bloomberg.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
    "jstor.org",
    "nber.org",
    "federalreserve.gov",
    "bis.org",
    "imf.org",
    "quantopian.com",
    "alpaca.markets",
    "sec.gov",
    "finra.org",
    "scholar.google.com",
    "papers.ssrn.com",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "openreview.net",
    "jmlr.org",
    "proceedings.mlr.press",
}

# Patterns indicating specificity / concreteness
METRIC_PATTERNS = [
    r"\d+\.?\d*\s*%",                    # percentages
    r"sharpe\s*(?:ratio)?\s*[:=]?\s*\d",  # sharpe ratios
    r"(?:annualized|annual)\s+return",     # return mentions
    r"max\s*drawdown",                     # drawdown
    r"(?:alpha|beta)\s*[:=]?\s*[\d.]",     # alpha/beta values
    r"\d+\s*(?:bps|basis\s*points)",       # basis points
    r"p[\s-]*value\s*[:=<>]",              # p-values
    r"r[²2]\s*[:=]?\s*0?\.\d",            # R-squared
    r"auc\s*[:=]?\s*0?\.\d",              # AUC scores
    r"mse|rmse|mae",                       # error metrics
]


def extract_domain(url):
    """Extract the base domain from a URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Strip www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return ""


def grade_citations(topics):
    """Evaluate citation count and quality across all topics.

    Returns:
        dict with citation_count, avg_per_topic, quality_ratio, quality_grade,
        domain_breakdown, and details per topic.
    """
    total_refs = 0
    high_quality_refs = 0
    domain_counter = Counter()
    topic_details = []

    for topic in topics:
        refs = topic.get("references", [])
        count = len(refs)
        total_refs += count

        hq_count = 0
        for ref in refs:
            domain = extract_domain(ref)
            domain_counter[domain] += 1
            if any(hq in domain for hq in HIGH_QUALITY_DOMAINS):
                hq_count += 1
                high_quality_refs += 1

        topic_details.append({
            "topic": topic.get("name", "unnamed"),
            "citation_count": count,
            "high_quality_count": hq_count,
        })

    num_topics = max(len(topics), 1)
    avg_per_topic = total_refs / num_topics
    quality_ratio = high_quality_refs / max(total_refs, 1)

    # Grade: A=excellent, B=good, C=adequate, D=poor, F=failing
    if avg_per_topic >= 5 and quality_ratio >= 0.6:
        quality_grade = "A"
    elif avg_per_topic >= 3 and quality_ratio >= 0.4:
        quality_grade = "B"
    elif avg_per_topic >= 2 and quality_ratio >= 0.2:
        quality_grade = "C"
    elif avg_per_topic >= 1:
        quality_grade = "D"
    else:
        quality_grade = "F"

    return {
        "total_citations": total_refs,
        "avg_citations_per_topic": round(avg_per_topic, 2),
        "high_quality_count": high_quality_refs,
        "quality_ratio": round(quality_ratio, 3),
        "quality_grade": quality_grade,
        "domain_breakdown": dict(domain_counter.most_common(15)),
        "per_topic": topic_details,
    }


def grade_coverage(topics):
    """Evaluate coverage breadth: how many unique techniques are mentioned.

    Returns:
        dict with unique_techniques count, list, and coverage_grade.
    """
    all_techniques = set()
    for topic in topics:
        for tech in topic.get("techniques", []):
            all_techniques.add(tech.strip().lower())

    count = len(all_techniques)

    if count >= 20:
        grade = "A"
    elif count >= 12:
        grade = "B"
    elif count >= 6:
        grade = "C"
    elif count >= 3:
        grade = "D"
    else:
        grade = "F"

    return {
        "unique_technique_count": count,
        "techniques": sorted(all_techniques),
        "coverage_grade": grade,
    }


def grade_depth(topics):
    """Evaluate depth: average word count per topic finding.

    Returns:
        dict with avg_word_count, per_topic details, and depth_grade.
    """
    word_counts = []
    topic_details = []

    for topic in topics:
        findings = topic.get("findings", "")
        wc = len(findings.split())
        word_counts.append(wc)
        topic_details.append({
            "topic": topic.get("name", "unnamed"),
            "word_count": wc,
        })

    avg_wc = sum(word_counts) / max(len(word_counts), 1)

    if avg_wc >= 300:
        grade = "A"
    elif avg_wc >= 150:
        grade = "B"
    elif avg_wc >= 75:
        grade = "C"
    elif avg_wc >= 30:
        grade = "D"
    else:
        grade = "F"

    return {
        "avg_word_count": round(avg_wc, 1),
        "total_word_count": sum(word_counts),
        "per_topic": topic_details,
        "depth_grade": grade,
    }


def grade_specificity(topics):
    """Evaluate specificity: concrete metrics, code examples, tool names.

    Returns:
        dict with metrics_found, tools_mentioned, code_examples, and specificity_grade.
    """
    total_metrics = 0
    total_tools = 0
    total_code = 0
    metric_matches = []

    for topic in topics:
        findings = topic.get("findings", "")

        # Count metric pattern matches in findings text
        topic_metric_count = 0
        for pattern in METRIC_PATTERNS:
            matches = re.findall(pattern, findings, re.IGNORECASE)
            topic_metric_count += len(matches)
            metric_matches.extend(matches)

        # Also count explicit metrics dict entries
        metrics_dict = topic.get("metrics", {})
        topic_metric_count += len(metrics_dict)
        total_metrics += topic_metric_count

        # Tools mentioned
        tools = topic.get("tools_mentioned", [])
        total_tools += len(tools)

        # Code examples
        code = topic.get("code_examples", [])
        total_code += len(code)

    # Composite specificity score (0-100)
    num_topics = max(len(topics), 1)
    score = 0
    score += min(30, (total_metrics / num_topics) * 10)   # up to 30 points
    score += min(30, (total_tools / num_topics) * 10)      # up to 30 points
    score += min(40, (total_code / num_topics) * 20)       # up to 40 points

    if score >= 70:
        grade = "A"
    elif score >= 50:
        grade = "B"
    elif score >= 30:
        grade = "C"
    elif score >= 15:
        grade = "D"
    else:
        grade = "F"

    all_tools = set()
    for topic in topics:
        for t in topic.get("tools_mentioned", []):
            all_tools.add(t)

    return {
        "metrics_found": total_metrics,
        "tools_mentioned": sorted(all_tools),
        "tools_count": total_tools,
        "code_examples_count": total_code,
        "specificity_score": round(score, 1),
        "specificity_grade": grade,
    }


def compute_overall_grade(grades):
    """Compute a weighted overall grade from individual dimension grades."""
    grade_values = {"A": 4.0, "B": 3.0, "C": 2.0, "D": 1.0, "F": 0.0}
    weights = {
        "citation": 0.25,
        "coverage": 0.20,
        "depth": 0.25,
        "specificity": 0.30,
    }

    weighted_sum = 0.0
    for dim, weight in weights.items():
        g = grades.get(dim, "F")
        weighted_sum += grade_values.get(g, 0.0) * weight

    if weighted_sum >= 3.5:
        return "A"
    elif weighted_sum >= 2.5:
        return "B"
    elif weighted_sum >= 1.5:
        return "C"
    elif weighted_sum >= 0.5:
        return "D"
    else:
        return "F"


def grade_research(filepath):
    """Main grading function. Reads research JSON and returns grade report."""
    with open(filepath, "r") as f:
        data = json.load(f)

    team = data.get("team", "unknown")
    topics = data.get("topics", [])

    citation_report = grade_citations(topics)
    coverage_report = grade_coverage(topics)
    depth_report = grade_depth(topics)
    specificity_report = grade_specificity(topics)

    dimension_grades = {
        "citation": citation_report["quality_grade"],
        "coverage": coverage_report["coverage_grade"],
        "depth": depth_report["depth_grade"],
        "specificity": specificity_report["specificity_grade"],
    }

    overall = compute_overall_grade(dimension_grades)

    report = {
        "grade_report": {
            "team": team,
            "source_file": os.path.abspath(filepath),
            "overall_grade": overall,
            "dimension_grades": dimension_grades,
            "num_topics": len(topics),
        },
        "citations": citation_report,
        "coverage": coverage_report,
        "depth": depth_report,
        "specificity": specificity_report,
        "recommendations": generate_recommendations(
            citation_report, coverage_report, depth_report, specificity_report
        ),
    }

    return report


def generate_recommendations(citations, coverage, depth, specificity):
    """Generate actionable recommendations based on grades."""
    recs = []

    if citations["quality_grade"] in ("D", "F"):
        recs.append(
            "Increase citation count and prioritize high-quality sources "
            "(arxiv.org, ssrn.com, ieee.org, acm.org, nature.com)."
        )
    elif citations["quality_ratio"] < 0.4:
        recs.append(
            "Citation count is adequate but quality ratio is low. "
            "Replace weaker sources with peer-reviewed or institutional references."
        )

    if coverage["coverage_grade"] in ("D", "F"):
        recs.append(
            f"Only {coverage['unique_technique_count']} unique techniques found. "
            "Broaden research to cover more AI/ML trading techniques."
        )

    if depth["depth_grade"] in ("D", "F"):
        recs.append(
            f"Average finding length is {depth['avg_word_count']} words. "
            "Expand findings with more detailed analysis per topic."
        )

    if specificity["specificity_grade"] in ("D", "F"):
        recs.append(
            "Findings lack concrete specifics. Add quantitative metrics, "
            "name specific tools/libraries, and include code examples where possible."
        )

    if not recs:
        recs.append("Research quality is strong across all dimensions. Maintain this standard.")

    return recs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    filepath = sys.argv[1]
    output_path = None

    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            output_path = sys.argv[idx + 1]

    if not os.path.exists(filepath):
        print(f"Error: file not found: {filepath}", file=sys.stderr)
        sys.exit(1)

    report = grade_research(filepath)
    report_json = json.dumps(report, indent=2)

    if output_path:
        with open(output_path, "w") as f:
            f.write(report_json)
        print(f"Grade report written to: {output_path}")
    else:
        print(report_json)


if __name__ == "__main__":
    main()
