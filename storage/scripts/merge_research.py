#!/usr/bin/env python3
"""
merge_research.py - Research Merger and Deduplication Tool

Takes multiple team research JSON files, merges findings by topic area,
deduplicates overlapping techniques, identifies coverage gaps, and outputs
a unified research document.

Usage:
    python merge_research.py <file1.json> <file2.json> [file3.json ...] [--output <path>]

Input Format:
    Each research JSON should follow this structure:
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
    A unified JSON document with merged topics, deduplication notes,
    and gap analysis.
"""

import json
import sys
import os
from collections import defaultdict
from difflib import SequenceMatcher


def normalize_name(name):
    """Normalize a technique/topic name for comparison."""
    return name.strip().lower().replace("-", " ").replace("_", " ")


def similarity(a, b):
    """Compute string similarity ratio between two names."""
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def find_duplicate_techniques(all_techniques):
    """Identify techniques that are likely duplicates across teams.

    Returns list of (technique_a, technique_b, similarity_score) tuples.
    """
    duplicates = []
    names = list(all_techniques.keys())
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            sim = similarity(names[i], names[j])
            if sim >= 0.70:  # threshold for likely duplicate
                duplicates.append({
                    "technique_a": names[i],
                    "technique_b": names[j],
                    "similarity": round(sim, 3),
                    "teams_a": all_techniques[names[i]],
                    "teams_b": all_techniques[names[j]],
                })
    return duplicates


def merge_topics(team_data_list):
    """Merge topics from multiple teams by category.

    Groups topics by their category field and merges findings,
    techniques, references, etc.
    """
    # Group by category
    category_topics = defaultdict(list)

    for team_data in team_data_list:
        team_name = team_data.get("team", "unknown")
        for topic in team_data.get("topics", []):
            category = topic.get("category", "uncategorized")
            entry = dict(topic)
            entry["source_team"] = team_name
            category_topics[category].append(entry)

    merged = {}
    for category, topics in category_topics.items():
        # Merge all topics in the same category
        merged_findings = []
        all_techniques = []
        all_references = []
        all_tools = set()
        all_code = []
        all_metrics = {}
        contributing_teams = set()
        sub_topics = []

        for topic in topics:
            team = topic.get("source_team", "unknown")
            contributing_teams.add(team)

            findings = topic.get("findings", "")
            if findings:
                merged_findings.append({
                    "team": team,
                    "topic_name": topic.get("name", ""),
                    "content": findings,
                })

            for tech in topic.get("techniques", []):
                norm = normalize_name(tech)
                if norm not in [normalize_name(t) for t in all_techniques]:
                    all_techniques.append(tech)

            for ref in topic.get("references", []):
                if ref not in all_references:
                    all_references.append(ref)

            for tool in topic.get("tools_mentioned", []):
                all_tools.add(tool)

            all_code.extend(topic.get("code_examples", []))

            metrics = topic.get("metrics", {})
            for k, v in metrics.items():
                if k not in all_metrics:
                    all_metrics[k] = {"values": [], "sources": []}
                all_metrics[k]["values"].append(v)
                all_metrics[k]["sources"].append(team)

            sub_topics.append(topic.get("name", "unnamed"))

        merged[category] = {
            "category": category,
            "contributing_teams": sorted(contributing_teams),
            "sub_topics": sub_topics,
            "merged_findings": merged_findings,
            "unique_techniques": all_techniques,
            "technique_count": len(all_techniques),
            "all_references": all_references,
            "reference_count": len(all_references),
            "tools_mentioned": sorted(all_tools),
            "code_examples": all_code,
            "merged_metrics": all_metrics,
        }

    return merged


def analyze_gaps(team_data_list):
    """Identify gaps: topics covered by one team but not others.

    Returns a dict mapping each category to which teams covered it
    and which teams did not.
    """
    team_categories = defaultdict(set)
    all_teams = set()

    for team_data in team_data_list:
        team_name = team_data.get("team", "unknown")
        all_teams.add(team_name)
        for topic in team_data.get("topics", []):
            cat = topic.get("category", "uncategorized")
            team_categories[cat].add(team_name)

    gaps = []
    for cat, teams_covering in sorted(team_categories.items()):
        missing_teams = all_teams - teams_covering
        if missing_teams:
            gaps.append({
                "category": cat,
                "covered_by": sorted(teams_covering),
                "missing_from": sorted(missing_teams),
                "coverage_ratio": f"{len(teams_covering)}/{len(all_teams)}",
            })

    return gaps


def analyze_technique_overlap(team_data_list):
    """Build a map of technique -> teams that mention it, and find duplicates."""
    technique_teams = defaultdict(set)

    for team_data in team_data_list:
        team_name = team_data.get("team", "unknown")
        for topic in team_data.get("topics", []):
            for tech in topic.get("techniques", []):
                technique_teams[normalize_name(tech)].add(team_name)

    # Techniques mentioned by multiple teams (confirmed across teams)
    confirmed = {
        tech: sorted(teams)
        for tech, teams in technique_teams.items()
        if len(teams) > 1
    }

    # Techniques unique to a single team
    unique = {
        tech: sorted(teams)[0]
        for tech, teams in technique_teams.items()
        if len(teams) == 1
    }

    # Find potential duplicates with different names
    duplicates = find_duplicate_techniques(
        {tech: sorted(teams) for tech, teams in technique_teams.items()}
    )

    return {
        "confirmed_across_teams": confirmed,
        "unique_to_single_team": unique,
        "potential_duplicates": duplicates,
        "total_unique_techniques": len(technique_teams),
    }


def merge_research(filepaths):
    """Main merge function. Reads all files and produces unified output."""
    team_data_list = []

    for fp in filepaths:
        with open(fp, "r") as f:
            data = json.load(f)
        data["_source_file"] = os.path.abspath(fp)
        team_data_list.append(data)

    merged_topics = merge_topics(team_data_list)
    gaps = analyze_gaps(team_data_list)
    technique_analysis = analyze_technique_overlap(team_data_list)

    # Summary statistics
    all_teams = sorted(set(td.get("team", "unknown") for td in team_data_list))
    total_topics = sum(len(td.get("topics", [])) for td in team_data_list)
    total_categories = len(merged_topics)

    result = {
        "unified_research": {
            "title": "Merged AI Quantitative Trading Techniques Research",
            "generated_date": "2026-03-10",
            "source_teams": all_teams,
            "source_files": [td.get("_source_file", "") for td in team_data_list],
            "summary": {
                "total_input_topics": total_topics,
                "merged_categories": total_categories,
                "total_unique_techniques": technique_analysis["total_unique_techniques"],
                "gap_count": len(gaps),
                "potential_duplicate_count": len(technique_analysis["potential_duplicates"]),
            },
        },
        "merged_by_category": merged_topics,
        "gap_analysis": gaps,
        "technique_overlap": technique_analysis,
    }

    return result


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    filepaths = []
    output_path = None

    i = 1
    while i < len(sys.argv):
        if sys.argv[i] == "--output":
            if i + 1 < len(sys.argv):
                output_path = sys.argv[i + 1]
                i += 2
                continue
            else:
                print("Error: --output requires a path argument", file=sys.stderr)
                sys.exit(1)
        else:
            filepaths.append(sys.argv[i])
        i += 1

    if not filepaths:
        print("Error: no input files specified", file=sys.stderr)
        sys.exit(1)

    for fp in filepaths:
        if not os.path.exists(fp):
            print(f"Error: file not found: {fp}", file=sys.stderr)
            sys.exit(1)

    result = merge_research(filepaths)
    result_json = json.dumps(result, indent=2)

    if output_path:
        with open(output_path, "w") as f:
            f.write(result_json)
        print(f"Merged research written to: {output_path}")
    else:
        print(result_json)


if __name__ == "__main__":
    main()
