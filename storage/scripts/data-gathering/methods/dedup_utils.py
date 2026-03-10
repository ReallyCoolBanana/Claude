#!/usr/bin/env python3
"""
Deduplication Utilities for Data Gathering Methods
===================================================
Provides TF-IDF-based similarity scoring, deduplication, and source merging
for combining results across multiple data gathering methods.

Importable by all methods:
    from dedup_utils import deduplicate_results, similarity_score

No external dependencies — uses only Python standard library.
"""

import math
import re
from collections import Counter


# --- Text Processing ---

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "this", "that",
    "these", "those", "it", "its", "not", "no", "nor", "so", "if", "than",
    "too", "very", "just", "about", "above", "after", "again", "all", "also",
    "am", "any", "as", "because", "before", "between", "both", "each",
    "even", "few", "get", "got", "he", "her", "here", "him", "his", "how",
    "into", "made", "make", "many", "me", "more", "most", "much", "my",
    "new", "now", "off", "old", "only", "other", "our", "out", "over",
    "own", "same", "she", "some", "such", "tell", "then", "them", "there",
    "they", "through", "under", "until", "upon", "use", "used", "using",
    "was", "we", "well", "what", "when", "where", "which", "while", "who",
    "whom", "why", "you", "your",
}


def tokenize(text):
    """Tokenize text into lowercase words, removing stop words."""
    if not text:
        return []
    words = re.findall(r'\b[a-zA-Z]{2,}\b', text.lower())
    return [w for w in words if w not in STOP_WORDS]


def extract_text(result):
    """Extract all meaningful text from a result dict for comparison."""
    parts = []
    for key in ("title", "snippet", "abstract", "extract", "summary",
                "description", "label"):
        val = result.get(key, "")
        if isinstance(val, str) and val:
            parts.append(val)
    return " ".join(parts)


# --- TF-IDF Implementation ---

def compute_tf(tokens):
    """Compute term frequency for a list of tokens."""
    if not tokens:
        return {}
    counter = Counter(tokens)
    total = len(tokens)
    return {word: count / total for word, count in counter.items()}


def compute_idf(documents):
    """Compute inverse document frequency across a list of token lists."""
    n_docs = len(documents)
    if n_docs == 0:
        return {}

    doc_freq = Counter()
    for tokens in documents:
        unique_tokens = set(tokens)
        doc_freq.update(unique_tokens)

    return {
        word: math.log((n_docs + 1) / (freq + 1)) + 1
        for word, freq in doc_freq.items()
    }


def compute_tfidf_vector(tokens, idf):
    """Compute TF-IDF vector for a document given precomputed IDF."""
    tf = compute_tf(tokens)
    return {word: tf_val * idf.get(word, 1.0) for word, tf_val in tf.items()}


def cosine_similarity(vec_a, vec_b):
    """Compute cosine similarity between two sparse vectors (dicts)."""
    if not vec_a or not vec_b:
        return 0.0

    common_keys = set(vec_a.keys()) & set(vec_b.keys())
    dot = sum(vec_a[k] * vec_b[k] for k in common_keys)

    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)


# --- Deduplication ---

def similarity_score(text_a, text_b, idf=None):
    """
    Compute similarity between two text strings using TF-IDF cosine similarity.

    Returns:
        Float between 0.0 (no similarity) and 1.0 (identical).
    """
    tokens_a = tokenize(text_a)
    tokens_b = tokenize(text_b)

    if not tokens_a or not tokens_b:
        return 0.0

    if idf is None:
        idf = compute_idf([tokens_a, tokens_b])

    vec_a = compute_tfidf_vector(tokens_a, idf)
    vec_b = compute_tfidf_vector(tokens_b, idf)

    return cosine_similarity(vec_a, vec_b)


def build_idf_from_results(results):
    """Build IDF dictionary from a list of result dicts."""
    documents = []
    for r in results:
        text = extract_text(r)
        tokens = tokenize(text)
        documents.append(tokens)
    return compute_idf(documents)


def deduplicate_results(results, threshold=0.65, idf=None):
    """
    Deduplicate a list of results using TF-IDF cosine similarity.

    Results with similarity above threshold are merged: the first
    occurrence is kept and subsequent duplicates contribute their
    source information.

    Returns:
        List of deduplicated result dicts with merged source info.
    """
    if not results:
        return []

    if idf is None:
        idf = build_idf_from_results(results)

    # Precompute text and TF-IDF vectors
    vectors = []
    for r in results:
        text = extract_text(r)
        tokens = tokenize(text)
        vec = compute_tfidf_vector(tokens, idf)
        vectors.append(vec)

    # Greedy deduplication
    merged = []
    used = set()

    for i in range(len(results)):
        if i in used:
            continue

        cluster = [i]
        for j in range(i + 1, len(results)):
            if j in used:
                continue
            sim = cosine_similarity(vectors[i], vectors[j])
            if sim >= threshold:
                cluster.append(j)
                used.add(j)

        primary = dict(results[cluster[0]])

        if len(cluster) > 1:
            merged_sources = _collect_sources(primary)
            for idx in cluster[1:]:
                dup = results[idx]
                dup_sources = _collect_sources(dup)
                merged_sources.extend(dup_sources)

                if "corroborating_sources" in dup:
                    existing = primary.get("corroborating_sources", [])
                    for src in dup.get("corroborating_sources", []):
                        if src not in existing:
                            existing.append(src)
                    primary["corroborating_sources"] = existing

                if dup.get("cited_by_count", 0) > primary.get("cited_by_count", 0):
                    primary["cited_by_count"] = dup["cited_by_count"]

            primary["merged_from_sources"] = merged_sources
            primary["duplicate_count"] = len(cluster)

            if "corroboration_score" in primary:
                primary["corroboration_score"] += 0.5 * (len(cluster) - 1)

        merged.append(primary)

    return merged


def _collect_sources(result):
    """Collect source type/origin information from a result."""
    sources = []
    source_type = result.get("source_type", "")
    if source_type:
        sources.append(source_type)
    method_id = result.get("method_id", "")
    if method_id:
        sources.append(method_id)
    url = result.get("url", "") or result.get("arxiv_url", "") or result.get("doi", "")
    if url:
        sources.append(url)
    return sources


def merge_citation_lists(*citation_lists):
    """
    Merge multiple citation/reference lists, removing duplicates.
    """
    seen = set()
    merged = []

    for clist in citation_lists:
        if not clist:
            continue
        for item in clist:
            if isinstance(item, dict):
                key = item.get("doi", "") or item.get("url", "") or str(item.get("title", "")).lower()
            else:
                key = str(item).strip().lower()

            if key and key not in seen:
                seen.add(key)
                merged.append(item)

    return merged
