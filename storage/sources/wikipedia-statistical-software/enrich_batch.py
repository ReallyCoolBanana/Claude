#!/usr/bin/env python3
"""Enrich Wikipedia statistical software articles from local cache files."""

import json
import os
from pathlib import Path

BASE = Path("/home/user/Claude/storage/sources/wikipedia-statistical-software")
BATCH_FILE = Path("/tmp/statsw_batch_1.json")
ENRICHED_DIR = BASE / "enriched"
ENRICHED_DIR.mkdir(exist_ok=True)

TODAY = "2026-03-12"

with open(BATCH_FILE) as f:
    article_ids = json.load(f)

def safe_filename(article_id):
    """Convert article ID to safe filename used in cache."""
    return article_id.replace(":", "__COLON__").replace("/", "__SLASH__").replace("?", "__Q__")

def try_load(filepath):
    """Try to load a JSON file, return None if not found."""
    try:
        with open(filepath) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

def load_article_data(article_id):
    """Load all cached data for an article."""
    safe = safe_filename(article_id)
    text = try_load(BASE / "text-cache" / f"text_{safe}.json")
    meta = try_load(BASE / "cache" / f"meta_{safe}.json")
    ext = try_load(BASE / "cache" / f"ext_{safe}.json")
    intl = try_load(BASE / "cache" / f"int_{safe}.json")
    return text, meta, ext, intl

# Knowledge base for categorization and tagging
ARTICLE_INFO = {
    "SPSS": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "broad-overview", "rich-data", "well-linked", "core-stats"],
        "centrality": 2,
    },
    "SUDAAN": {
        "categories": ["Specialized Analysis", "Epidemiology & Health"],
        "tags": ["specialized", "commercial", "epidemiology", "core-stats"],
        "centrality": 2,
    },
    "Minitab": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "broad-overview", "core-stats"],
        "centrality": 2,
    },
    "Stata": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "broad-overview", "rich-data", "well-linked", "core-stats"],
        "centrality": 2,
    },
    "XLispStat": {
        "categories": ["Legacy & Historical"],
        "tags": ["legacy", "open-source", "core-stats"],
        "centrality": 1,
    },
    "Statistica": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "core-stats"],
        "centrality": 2,
    },
    "SYSTAT_(statistics_package)": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "core-stats"],
        "centrality": 2,
    },
    "Statistical_Lab": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "open-source", "core-stats"],
        "centrality": 1,
    },
    "List_of_information_graphics_software": {
        "categories": ["Comparison & Reference"],
        "tags": ["comparison", "reference", "stats-adjacent", "peripheral"],
        "centrality": 0,
    },
    "Statgraphics": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "core-stats"],
        "centrality": 2,
    },
    "Statistics_Online_Computational_Resource": {
        "categories": ["Comparison & Reference"],
        "tags": ["reference", "open-source", "core-stats"],
        "centrality": 1,
    },
    "Genstat": {
        "categories": ["General Statistical Packages", "Specialized Analysis"],
        "tags": ["general-stats", "commercial", "specialized", "core-stats"],
        "centrality": 2,
    },
    "S-PLUS": {
        "categories": ["General Statistical Packages", "Legacy & Historical"],
        "tags": ["general-stats", "commercial", "legacy", "core-stats"],
        "centrality": 2,
    },
    "List_of_statistical_software": {
        "categories": ["Comparison & Reference"],
        "tags": ["comparison", "reference", "broad-overview", "core-stats"],
        "centrality": 2,
    },
    "MedCalc": {
        "categories": ["Epidemiology & Health", "General Statistical Packages"],
        "tags": ["general-stats", "commercial", "epidemiology", "core-stats"],
        "centrality": 2,
    },
    "Comparison_of_statistical_packages": {
        "categories": ["Comparison & Reference"],
        "tags": ["comparison", "reference", "broad-overview", "core-stats", "rich-data"],
        "centrality": 2,
    },
    "World_Programming_System": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "core-stats"],
        "centrality": 1,
    },
    "BMDP": {
        "categories": ["Legacy & Historical"],
        "tags": ["legacy", "general-stats", "core-stats"],
        "centrality": 1,
    },
    "WinBUGS": {
        "categories": ["Bayesian & Probabilistic"],
        "tags": ["bayesian", "open-source", "deep-dive", "core-stats"],
        "centrality": 2,
    },
    "Table_Producing_Language": {
        "categories": ["Specialized Analysis", "Legacy & Historical"],
        "tags": ["specialized", "legacy", "core-stats"],
        "centrality": 1,
    },
    "TPL_Tables": {
        "categories": ["Specialized Analysis"],
        "tags": ["specialized", "core-stats"],
        "centrality": 1,
    },
    "Winpepi": {
        "categories": ["Epidemiology & Health"],
        "tags": ["epidemiology", "open-source", "specialized", "core-stats"],
        "centrality": 2,
    },
    "EpiData": {
        "categories": ["Epidemiology & Health"],
        "tags": ["epidemiology", "open-source", "core-stats"],
        "centrality": 2,
    },
    "SigmaStat": {
        "categories": ["General Statistical Packages"],
        "tags": ["general-stats", "commercial", "core-stats"],
        "centrality": 2,
    },
    "Primer-E_Primer": {
        "categories": ["Specialized Analysis"],
        "tags": ["specialized", "commercial", "core-stats"],
        "centrality": 1,
    },
    "ASReml": {
        "categories": ["Specialized Analysis"],
        "tags": ["specialized", "commercial", "core-stats"],
        "centrality": 2,
    },
    "MLwiN": {
        "categories": ["Specialized Analysis", "Bayesian & Probabilistic"],
        "tags": ["specialized", "bayesian", "core-stats"],
        "centrality": 2,
    },
    "LISREL": {
        "categories": ["Specialized Analysis"],
        "tags": ["specialized", "commercial", "core-stats", "deep-dive"],
        "centrality": 2,
    },
    "Fathom:_Dynamic_Data_Software": {
        "categories": ["General Statistical Packages", "Legacy & Historical"],
        "tags": ["general-stats", "commercial", "legacy", "core-stats"],
        "centrality": 1,
    },
}

def compute_score(summary_text, ext_links, int_links, meta, centrality):
    """Compute usefulness score based on rubric."""
    score = 0
    rationale_parts = []

    # Text richness (0-3)
    slen = len(summary_text) if summary_text else 0
    if slen == 0:
        text_score = 0
    elif slen < 500:
        text_score = 1
    elif slen < 1000:
        text_score = 2
    else:
        text_score = 3
    score += text_score
    rationale_parts.append(f"text={text_score}/3 ({slen} chars)")

    # Link richness (0-3)
    total_links = len(ext_links) + len(int_links)
    if total_links == 0:
        link_score = 0
    elif total_links < 20:
        link_score = 1
    elif total_links < 100:
        link_score = 2
    else:
        link_score = 3
    score += link_score
    rationale_parts.append(f"links={link_score}/3 ({total_links} total)")

    # Metadata (0-2)
    has_cats = bool(meta.get("categories"))
    has_desc = bool(meta.get("description"))
    if has_cats:
        score += 1
        rationale_parts.append("has categories")
    if has_desc:
        score += 1
        rationale_parts.append("has description")

    # Centrality (0-2)
    score += centrality
    centrality_label = {0: "peripheral", 1: "related", 2: "core"}[centrality]
    rationale_parts.append(f"centrality={centrality_label}")

    return min(score, 10), "; ".join(rationale_parts)

def get_tier(score):
    if score >= 8:
        return "A"
    elif score >= 6:
        return "B"
    elif score >= 4:
        return "C"
    else:
        return "D"

def extract_categories_from_internal(int_links):
    """Extract wiki categories from internal links."""
    cats = []
    for link in int_links:
        if link.startswith("Category:"):
            cats.append(link.replace("Category:", ""))
    return cats

def extract_see_also(int_links):
    """Internal links that aren't categories make up related context."""
    return [l for l in int_links if not l.startswith("Category:")]

def human_title(article_id):
    """Convert article ID to human-readable title."""
    t = article_id.replace("_", " ").replace(":", ": ")
    # Fix common patterns
    if t == "SYSTAT (statistics package)":
        t = "SYSTAT"
    if t == "Fathom:  Dynamic Data Software":
        t = "Fathom: Dynamic Data Software"
    if t == "Primer-E Primer":
        t = "PRIMER-E"
    return t

def enrich_article(article_id):
    text_data, meta_data, ext_data, int_data = load_article_data(article_id)

    summary = ""
    title = human_title(article_id)
    extracted = TODAY

    if text_data:
        summary = text_data.get("summary", "")
        # Use text title only if it looks cleaned up (no underscores)
        cached_title = text_data.get("title", "")
        if cached_title and "_" not in cached_title:
            title = cached_title
        extracted = text_data.get("extracted", TODAY)

    ext_links = ext_data.get("external", []) if ext_data else []
    int_links = int_data.get("internal", []) if int_data else []
    meta = meta_data if meta_data else {"categories": [], "seeAlso": [], "label": "", "description": "", "hypernym": ""}

    wiki_categories = extract_categories_from_internal(int_links)
    see_also = meta.get("seeAlso", [])

    info = ARTICLE_INFO.get(article_id, {
        "categories": ["Niche / Low-Value"],
        "tags": ["peripheral", "sparse-data"],
        "centrality": 0,
    })

    centrality = info["centrality"]
    tags = list(info["tags"])
    categories = list(info["categories"])

    # Add/fix data-quality tags based on actual data
    summary_length = len(summary)
    if summary_length < 200:
        # Short summary: add stub/sparse, remove rich-data if present
        if "stub" not in tags:
            tags.append("stub")
        if "sparse-data" not in tags:
            tags.append("sparse-data")
        if "rich-data" in tags:
            tags.remove("rich-data")
    elif summary_length > 800:
        if "rich-data" not in tags:
            tags.append("rich-data")
        # Remove stub/sparse if present
        if "stub" in tags:
            tags.remove("stub")
        if "sparse-data" in tags:
            tags.remove("sparse-data")

    total_links = len(ext_links) + len(int_links)
    if total_links > 50 and "well-linked" not in tags:
        tags.append("well-linked")
    elif total_links <= 50 and "well-linked" in tags:
        tags.remove("well-linked")

    score, rationale = compute_score(summary, ext_links, int_links, meta, centrality)
    tier = get_tier(score)

    safe = safe_filename(article_id)
    url = f"https://en.wikipedia.org/wiki/{article_id}"

    enriched = {
        "id": article_id,
        "title": title,
        "url": url,
        "summary": summary,
        "tags": tags,
        "categories": categories,
        "usefulness_score": score,
        "usefulness_rationale": rationale,
        "research_tier": tier,
        "data_richness": {
            "summary_length": summary_length,
            "ext_links_count": len(ext_links),
            "int_links_count": len(int_links),
            "has_categories": bool(wiki_categories),
            "has_description": bool(meta.get("description")),
        },
        "ext_links": ext_links,
        "int_links": [l for l in int_links if not l.startswith("Category:")],
        "related_articles": [],
        "meta": {
            "wiki_categories": wiki_categories,
            "see_also": see_also,
            "description": meta.get("description", ""),
            "hypernym": meta.get("hypernym", ""),
        },
        "extracted": extracted,
        "enriched": TODAY,
    }

    outpath = ENRICHED_DIR / f"{safe}.json"
    with open(outpath, "w") as f:
        json.dump(enriched, f, indent=2)

    return article_id, score, tier

# Process all articles
print(f"Processing {len(article_ids)} articles...")
results = []
for aid in article_ids:
    try:
        aid_str, score, tier = enrich_article(aid)
        results.append((aid_str, score, tier))
        print(f"  [{tier}] {score:4.1f}  {aid_str}")
    except Exception as e:
        print(f"  ERROR  {aid}: {e}")
        import traceback
        traceback.print_exc()

print(f"\nDone. {len(results)}/{len(article_ids)} articles enriched.")
print(f"Output: {ENRICHED_DIR}/")

# Summary
for tier in ["A", "B", "C", "D"]:
    tier_items = [r for r in results if r[2] == tier]
    if tier_items:
        print(f"  Tier {tier}: {len(tier_items)} articles")
