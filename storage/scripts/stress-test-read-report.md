# Storage System READ/RETRIEVAL Stress Test Report

**Team Lead 2 | Date: 2026-03-10**
**Dataset: 50 scripts, 30 API tools, 40 sources (120 total entries)**

---

## 1. Benchmark Results

### 1.1 Category/Type Lookups (1000 iterations each)

| Operation                      | Linear Avg | Indexed Avg | Speedup |
|-------------------------------|-----------|------------|---------|
| Scripts by category (4 cats)   | 2.45 us   | 1.12 us    | 2.2x    |
| Scripts by language (5 langs)  | 2.51 us   | 0.92 us    | 2.7x    |
| Tools by category (4 cats)     | 1.66 us   | 0.81 us    | 2.0x    |
| Tools by auth_type (5 types)   | 1.59 us   | 0.69 us    | 2.3x    |
| Sources by data_type (7 types) | 1.98 us   | 0.68 us    | 2.9x    |
| Sources by access_type (4)     | 2.03 us   | 0.92 us    | 2.2x    |

**Non-existent category lookups** (always full scan):
- Scripts: 2.37 us | Tools: 1.54 us | Sources: 1.80 us

### 1.2 ID Lookups (1000 iterations each)

| Subsystem | Linear Avg | Hash Avg | Speedup | Build Cost | Break-even |
|-----------|-----------|---------|---------|-----------|-----------|
| Scripts   | 1.42 us   | 0.11 us | 12.9x   | 9.65 us   | 7 lookups |
| Tools     | 0.72 us   | 0.11 us | 6.5x    | 5.91 us   | 10 lookups|
| Sources   | 0.92 us   | 0.11 us | 8.4x    | 6.35 us   | 8 lookups |

**Worst-case linear scan** (last entry): 1.49-1.92 us
**Non-existent ID miss speedup** (hash vs linear): 10.7x - 16.5x

### 1.3 Cross-Subsystem & Compound Queries (1000 iterations each)

| Query Type                              | Avg Time  | Notes                           |
|----------------------------------------|----------|--------------------------------|
| Cross-ref: scripts using a source       | 1.92 us  | Linear; 0.29 us indexed (6.6x) |
| 2-field filter: tools cat + team        | 1.56 us  | Avg 1.9 results                |
| 3-field compound: cat + lang + date     | 2.44 us  | Linear; 0.64 us pre-filtered (3.8x) |
| Cross-subsystem: all entries by team    | 5.19 us  | Scans all 120 entries           |
| Tag search across all subsystems        | 10.22 us | O(n*m) - slowest operation      |

---

## 2. Linear Scan vs Indexed Lookup Comparison

| Metric                 | Linear Scan         | Hash/Pre-built Index  |
|-----------------------|--------------------|-----------------------|
| ID lookup             | O(n) - 0.72-1.42 us| O(1) - 0.11 us        |
| Category filter       | O(n) - 1.59-2.51 us| O(k) - 0.68-1.12 us   |
| Compound filter       | O(n) - 2.44 us     | O(k) - 0.64 us        |
| Non-existent lookup   | O(n) - always full  | O(1) - instant miss    |
| Memory overhead       | None               | ~1x data size          |
| Build cost            | None               | 5-10 us (one-time)    |
| Break-even            | N/A                | 7-10 lookups           |

**Key finding**: Hash indexes pay for themselves after just 7-10 lookups. For any session with more than a handful of reads, indexing is always worthwhile.

---

## 3. Scaling Projections

Assuming linear scaling of scan times with entry count:

| Entries | ID Linear | ID Hash | Cat Linear | Cat Indexed | Tag Search | Cross-System |
|---------|----------|---------|-----------|------------|-----------|-------------|
| 120     | 1.0 us   | 0.11 us | 2.0 us    | 0.9 us     | 10 us     | 5 us        |
| 500     | 4.2 us   | 0.11 us | 8.3 us    | 3.8 us     | 42 us     | 21 us       |
| 5,000   | 42 us    | 0.11 us | 83 us     | 38 us      | 420 us    | 210 us      |
| 50,000  | 420 us   | 0.12 us | 833 us    | 375 us     | 4,200 us  | 2,100 us    |

**Critical thresholds**:
- At **500 entries**: Linear scans still under 50 us -- acceptable for most uses.
- At **5,000 entries**: Linear ID lookup reaches ~42 us; tag searches hit ~0.4 ms. Indexing becomes essential.
- At **50,000 entries**: Linear scans reach 0.4-4.2 ms per operation. JSON file loading itself becomes the bottleneck (~50MB+ files). The flat-file approach breaks down.

Additional scaling concerns:
- **File I/O**: At 50,000 entries, index.json files will be 50-100 MB. Loading time dominates (~100-500 ms).
- **Memory**: Full parse of 50K-entry JSON requires ~200-500 MB RAM.
- **Write contention**: Any update requires rewriting the entire file.

---

## 4. Identified Bottlenecks

### Slow Operations (ranked by cost)

1. **Tag search across subsystems** (10.22 us) -- Slowest by 2x. Requires scanning all 120 entries and checking array membership for each. O(n * m) where m = avg tags per entry.

2. **Cross-subsystem team queries** (5.19 us) -- Must scan 3 separate data structures sequentially. No way to short-circuit.

3. **Compound filters without pre-indexing** (2.44 us) -- Every additional filter field is checked for every entry. Cost scales linearly with both entry count and filter complexity.

4. **Non-existent lookups via linear scan** (1.54-2.37 us) -- Always pays full scan cost. No early termination possible. This is the worst-case scenario for every linear operation.

### Structural Bottlenecks

- **No inverted indexes for tags**: Tags are stored as arrays within entries, requiring full scan + array search.
- **No cross-reference indexes**: Finding "scripts that use source X" requires scanning all scripts instead of a reverse lookup.
- **Separate files per subsystem**: Cross-subsystem queries require loading and scanning multiple files.
- **String-based date comparison**: Works but prevents binary search on date ranges.

---

## 5. Recommendations

### Immediate (Low Effort, High Impact)

1. **Add ID hash indexes to each index.json**: Include a top-level `_id_index` mapping ID -> array position. Build cost is <10 us and pays off after 7-10 lookups. This is already partially done with category indexes.

2. **Add inverted tag index**: Add a `_tag_index` mapping each tag to a list of entry IDs. Reduces tag search from O(n*m) to O(1).
   ```json
   "_tag_index": {
     "production": ["script-001", "script-015", "tool-003"],
     "fast": ["script-002", "script-007"]
   }
   ```

3. **Add reverse cross-reference index on sources**: Add a `_script_to_sources` index so that script->source lookups are O(1).

### Medium-Term (Moderate Effort)

4. **Composite indexes for common query patterns**: Pre-build `(category, language)` and `(category, team)` indexes for compound filters. Reduces 3-field queries from O(n) to O(k).

5. **Consider a unified cross-subsystem index**: A lightweight file mapping `team -> {scripts: [...], tools: [...], sources: [...]}` would eliminate cross-system scan overhead.

6. **Sort entries by date**: Enable binary search for date-range queries. At 5000+ entries, this converts O(n) date scans to O(log n).

### Long-Term (If Scaling Past 5,000 Entries)

7. **Switch from flat JSON to SQLite**: At 5000+ entries, a single SQLite database with proper indexes would provide:
   - O(log n) lookups on any indexed field
   - Compound queries via SQL
   - Atomic writes (no full-file rewrite)
   - ~10x smaller memory footprint (lazy loading)
   - Built-in full-text search

8. **If staying with JSON, shard by category**: Split `scripts/index.json` into `scripts/data-collection.json`, `scripts/analysis.json`, etc. Reduces per-query scan size by 4x.

9. **Add a caching layer**: For repeated queries within a session, an in-memory LRU cache with pre-built indexes eliminates redundant file I/O and scan costs.

---

## 6. Conclusion

At the current scale (120 entries), the flat JSON storage system performs well. All operations complete in under 11 microseconds. However:

- **Hash indexes provide 6-13x speedup** for ID lookups with negligible build cost.
- **Pre-built category indexes provide 2-3x speedup** for filtered queries.
- **Tag searches are the biggest bottleneck**, scaling as O(n * m).
- **The system will remain viable up to ~1,000 entries** without structural changes.
- **Beyond 5,000 entries**, migration to SQLite or indexed sharded files is strongly recommended.

The existing category index infrastructure in the setup script is a good foundation. Extending it with inverted tag indexes and ID hash maps would address all current bottlenecks with minimal effort.
