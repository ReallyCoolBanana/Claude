#!/usr/bin/env python3
"""Build 3 master index files from enriched articles and link cache."""

import json
import os
from collections import defaultdict
from urllib.parse import urlparse

BASE = "/home/user/Claude/storage/sources/wikipedia-statistical-software"
ENRICHED_DIR = os.path.join(BASE, "enriched")
CACHE_DIR = os.path.join(BASE, "cache")


def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Wrote {path}")


def build_index():
    """Task 1: Build index.json"""
    enriched_files = sorted(f for f in os.listdir(ENRICHED_DIR) if f.endswith(".json"))
    articles = []
    category_counts = defaultdict(int)
    tier_counts = defaultdict(int)
    tag_counts = defaultdict(int)

    for fname in enriched_files:
        data = load_json(os.path.join(ENRICHED_DIR, fname))
        dr = data.get("data_richness", {})
        article = {
            "id": data["id"],
            "title": data.get("title", data["id"]),
            "tags": data.get("tags", []),
            "categories": data.get("categories", []),
            "usefulness_score": data.get("usefulness_score", 0),
            "research_tier": data.get("research_tier", "D"),
            "ext_links_count": dr.get("ext_links_count", 0),
            "int_links_count": dr.get("int_links_count", 0),
            "summary_length": dr.get("summary_length", 0),
        }
        articles.append(article)

        for cat in article["categories"]:
            category_counts[cat] += 1
        tier_counts[article["research_tier"]] += 1
        for tag in article["tags"]:
            tag_counts[tag] += 1

    # Sort by usefulness_score descending
    articles.sort(key=lambda a: a["usefulness_score"], reverse=True)

    index = {
        "version": "1.0",
        "total_articles": len(articles),
        "enriched_date": "2026-03-12",
        "category_summary": dict(sorted(category_counts.items())),
        "tier_summary": dict(sorted(tier_counts.items())),
        "tag_summary": dict(sorted(tag_counts.items(), key=lambda x: -x[1])),
        "articles": articles,
    }
    save_json(os.path.join(BASE, "index.json"), index)
    return articles  # needed for links-index is_in_collection check


def build_links_index(enriched_ids):
    """Task 2: Build links-index.json"""
    enriched_id_set = set(enriched_ids)

    ext_links = {}  # url -> { domain, referenced_by }
    int_links = {}  # article_name -> { referenced_by, is_in_collection }
    total_ext = 0
    total_int = 0

    cache_files = os.listdir(CACHE_DIR)

    for fname in sorted(cache_files):
        fpath = os.path.join(CACHE_DIR, fname)
        if not fname.endswith(".json"):
            continue

        data = load_json(fpath)

        # Extract article id from filename: ext_ARTICLEID.json or int_ARTICLEID.json
        if fname.startswith("ext_"):
            article_id = fname[4:-5]  # strip ext_ and .json
            links = data.get("external", [])
            total_ext += len(links)
            for url in links:
                try:
                    domain = urlparse(url).netloc
                except Exception:
                    domain = ""
                if url not in ext_links:
                    ext_links[url] = {"domain": domain, "referenced_by": []}
                if article_id not in ext_links[url]["referenced_by"]:
                    ext_links[url]["referenced_by"].append(article_id)

        elif fname.startswith("int_"):
            article_id = fname[4:-5]
            links = data.get("internal", [])
            total_int += len(links)
            for link_name in links:
                if link_name not in int_links:
                    int_links[link_name] = {
                        "referenced_by": [],
                        "is_in_collection": link_name in enriched_id_set,
                    }
                if article_id not in int_links[link_name]["referenced_by"]:
                    int_links[link_name]["referenced_by"].append(article_id)

    unique_ext_domains = set()
    for info in ext_links.values():
        if info["domain"]:
            unique_ext_domains.add(info["domain"])

    links_index = {
        "version": "1.0",
        "built": "2026-03-12",
        "total_ext_links": total_ext,
        "total_int_links": total_int,
        "unique_ext_links": len(ext_links),
        "unique_int_links": len(int_links),
        "unique_ext_domains": len(unique_ext_domains),
        "ext_links": ext_links,
        "int_links": int_links,
    }
    save_json(os.path.join(BASE, "links-index.json"), links_index)


def build_categories():
    """Task 3: Build categories.json"""
    enriched_files = sorted(f for f in os.listdir(ENRICHED_DIR) if f.endswith(".json"))
    categories = defaultdict(list)

    # Category descriptions
    cat_descriptions = {
        "General Statistical Packages": "Comprehensive multi-purpose statistical software suites",
        "Specialized Analysis": "Software focused on specific statistical methods or domains",
        "Programming & Scripting": "Programming languages and scripting environments for statistics",
        "Bayesian & MCMC": "Software for Bayesian inference and Markov chain Monte Carlo methods",
        "Survey & Epidemiology": "Tools for survey analysis, epidemiological studies, and public health",
        "Comparison Articles": "Wikipedia articles comparing statistical software packages",
        "Data Manipulation": "Tools focused on data wrangling, transformation, and preparation",
        "Machine Learning & AI": "Software for machine learning, deep learning, and AI applications",
        "Visualization": "Tools primarily focused on data visualization and graphics",
        "Time Series & Econometrics": "Software for time series analysis and econometric modeling",
        "Bioinformatics & Genomics": "Statistical tools for biological data analysis",
        "Quality & Reliability": "Software for quality control, reliability engineering, and Six Sigma",
        "Spreadsheet-Based": "Statistical tools built on or integrated with spreadsheet platforms",
        "Education & Teaching": "Statistical software designed for educational purposes",
    }

    for fname in enriched_files:
        data = load_json(os.path.join(ENRICHED_DIR, fname))
        entry = {
            "id": data["id"],
            "usefulness_score": data.get("usefulness_score", 0),
            "research_tier": data.get("research_tier", "D"),
        }
        for cat in data.get("categories", []):
            categories[cat].append(entry)

    # Sort articles within each category by usefulness_score descending
    cats_output = {}
    for cat_name in sorted(categories.keys()):
        articles = sorted(categories[cat_name], key=lambda a: a["usefulness_score"], reverse=True)
        cats_output[cat_name] = {
            "description": cat_descriptions.get(cat_name, f"Articles in the {cat_name} category"),
            "count": len(articles),
            "articles": articles,
        }

    result = {
        "version": "1.0",
        "built": "2026-03-12",
        "total_categories": len(cats_output),
        "categories": cats_output,
    }
    save_json(os.path.join(BASE, "categories.json"), result)


def main():
    print("Building index.json...")
    articles = build_index()
    enriched_ids = [a["id"] for a in articles]

    print("Building links-index.json...")
    build_links_index(enriched_ids)

    print("Building categories.json...")
    build_categories()

    print("Done.")


if __name__ == "__main__":
    main()
