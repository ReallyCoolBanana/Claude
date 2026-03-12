# Wikipedia AI Articles - Mass Enrichment Plan

## Goal
Tag, score, categorize, and index all 234 Wikipedia AI articles with cross-referenced link pointers.

---

## Output File Structure

```
storage/sources/wikipedia-ai/
  index.json                    # NEW: Master index (234 entries with tags, scores, categories)
  links-index.json              # NEW: Global link registry (all ext+int links, deduplicated, with back-pointers)
  categories.json               # NEW: Category taxonomy with article lists
  enriched/                     # NEW: One enriched file per article
    Artificial_intelligence.json
    AI_agent.json
    ...                         # (234 files)
```

### Per-Article Enriched File Format (`enriched/*.json`)
```json
{
  "id": "Artificial_intelligence",
  "title": "Artificial intelligence",
  "url": "https://en.wikipedia.org/wiki/Artificial_intelligence",
  "summary": "...(existing summary)...",
  "tags": ["foundational", "broad-overview", "core-ai"],
  "categories": ["Core AI Concepts", "AI Foundations"],
  "usefulness_score": 9.5,
  "usefulness_rationale": "Central article covering all major AI topics",
  "research_tier": "A",
  "data_richness": {
    "summary_length": 1770,
    "ext_links_count": 200,
    "int_links_count": 536,
    "has_categories": true,
    "has_description": true
  },
  "ext_links_ref": "links-index.json#ext",
  "int_links_ref": "links-index.json#int",
  "ext_links": ["http://artint.info/...", "..."],
  "int_links": ["Machine_learning", "Deep_learning", "..."],
  "related_articles": ["Artificial_general_intelligence", "AI_safety", "..."],
  "meta": {
    "wiki_categories": ["Artificial_intelligence", "Computational_fields_of_study"],
    "see_also": ["Content_moderation", "..."],
    "description": "field of computer science...",
    "hypernym": "Intelligence"
  },
  "extracted": "2026-03-12",
  "enriched": "2026-03-12"
}
```

### Master Index Format (`index.json`)
```json
{
  "version": "1.0",
  "total_articles": 234,
  "enriched_date": "2026-03-12",
  "category_summary": { "Core AI Concepts": 15, "AI Safety & Ethics": 12, ... },
  "tier_summary": { "A": 20, "B": 50, "C": 80, "D": 84 },
  "articles": [
    {
      "id": "Artificial_intelligence",
      "title": "Artificial intelligence",
      "tags": ["foundational", "broad-overview"],
      "categories": ["Core AI Concepts"],
      "usefulness_score": 9.5,
      "research_tier": "A",
      "ext_links_count": 200,
      "int_links_count": 536
    },
    ...
  ]
}
```

### Global Links Index Format (`links-index.json`)
```json
{
  "total_ext_links": 3500,
  "total_int_links": 12000,
  "unique_ext_domains": 450,
  "ext_links": {
    "http://artint.info/index.html": {
      "domain": "artint.info",
      "referenced_by": ["Artificial_intelligence", "AI-complete"]
    },
    ...
  },
  "int_links": {
    "Machine_learning": {
      "referenced_by": ["Artificial_intelligence", "Deep_learning", "..."],
      "is_in_collection": true
    },
    ...
  }
}
```

### Category Taxonomy (`categories.json`)
```json
{
  "categories": {
    "Core AI Concepts": {
      "description": "Foundational AI theory and definitions",
      "articles": ["Artificial_intelligence", "AI-complete", ...]
    },
    "AI Safety & Ethics": { ... },
    "Machine Learning & Neural Networks": { ... },
    "AI Applications": { ... },
    "AI Agents & Robotics": { ... },
    "AI History & People": { ... },
    "Knowledge Representation & Reasoning": { ... },
    "Search & Optimization": { ... },
    "Natural Language & Vision": { ... },
    "Hardware & Infrastructure": { ... },
    "AI Policy & Society": { ... },
    "Niche / Low-Value": { ... }
  }
}
```

---

## Scoring Rubric

**Usefulness Score (1-10):**
- Text richness (summary length, section count): 0-3 points
- Link richness (ext + int links): 0-3 points
- Metadata quality (categories, description, seeAlso): 0-2 points
- Topic centrality to AI research: 0-2 points

**Research Tiers:**
- **A** (8-10): Essential reference articles, rich data, highly relevant
- **B** (6-7.9): Solid articles with good data, relevant to AI
- **C** (4-5.9): Useful but narrow or sparse data
- **D** (1-3.9): Peripheral, stub-like, or empty data

---

## Agent Team Structure

### 3 Parallel Worker Agents + 1 Assembler

**Agent 1: "Tagger" (articles 1-78)**
- Reads each article's text + meta + link counts
- Assigns tags, categories, usefulness score, tier
- Writes enriched JSON for each article

**Agent 2: "Tagger" (articles 79-156)**
- Same as Agent 1, different batch

**Agent 3: "Tagger" (articles 157-234)**
- Same as Agent 1, different batch

**Agent 4: "Assembler" (runs after 1-3 complete)**
- Builds `links-index.json` (scans all ext_*.json and int_*.json, deduplicates, adds back-pointers)
- Builds `index.json` (master index from all enriched files)
- Builds `categories.json` (category taxonomy from all enriched files)
- Validates completeness (all 234 articles present)

---

## Execution Order

```
Phase 1 (parallel):  Agent 1 + Agent 2 + Agent 3  (tag/score/categorize)
Phase 2 (sequential): Agent 4                       (assemble indexes)
Phase 3:              Commit + push to branch
```

---

## Tag Vocabulary (starter set, agents can add more)

**Topic Tags:** foundational, agents, safety, ethics, ml, neural-networks, nlp, vision, robotics, search, optimization, knowledge-rep, reasoning, logic, hardware, policy, history, people, applications, cognitive-science, philosophy, biology-inspired, game-theory, automation

**Quality Tags:** broad-overview, deep-dive, stub, sparse-data, rich-data, well-linked, orphan

**Relevance Tags:** core-ai, ai-adjacent, peripheral, niche
