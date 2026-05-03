# TODO — splunk-docs-mcp

_Last updated: 2026-05-03_

---

## 🔵 Next up — Performance & UX improvements (Phase 4)

Four improvements to do in order. Each is independent and can be committed separately.

### 1. LRU result cache on search tools (easiest, do first)
- Add `@functools.lru_cache` or a manual `dict` cache in `server.py` keyed on `(query, source, version, limit)`
- Apply to both `search_docs` and `search_docs_semantic`
- Cache the DB connection is read-only so staleness is not a concern within a session
- Use `functools.lru_cache` with `maxsize=128`

### 2. Hybrid search tool — `search_docs_hybrid`
- New MCP tool in `server.py` that runs BM25 + semantic search in parallel (using `asyncio` or `concurrent.futures`) and fuses results via Reciprocal Rank Fusion (RRF)
- RRF score = `sum(1 / (k + rank_i))` where k=60 is the standard constant
- Deduplicate by canonical URL; return top-N fused results
- Update tool decision tree in `server.py` `instructions=` to recommend `search_docs_hybrid` as the default first call
- BM25 search is synchronous (SQLite); semantic is in-process numpy — run BM25 in a thread executor so both run concurrently

### 3. Lazy / background model loading
- Currently `_embed_model = SentenceTransformer(...)` runs at module import time in `server.py`, blocking startup for 30-90s on first download
- Move to a background thread: start loading in `threading.Thread` immediately on import, but don't block until first actual call to `search_docs_semantic`
- Use a `threading.Event` to signal readiness; `search_docs_semantic` waits on the event if model not yet ready
- Also pre-load the embedding matrix in the same background thread (currently also blocks first semantic search call)

### 4. Replace numpy matrix scan with `sqlite-vec`
- `sqlite-vec` is a SQLite loadable extension for ANN vector search; install via `pip install sqlite-vec`
- Schema: add a `vec_documents` virtual table using `sqlite_vec` with 384-dim float32 vectors
- At crawl time: after embedding pass, populate `vec_documents` with `(rowid, embedding)` pairs
- At search time: `SELECT rowid, distance FROM vec_documents WHERE embedding MATCH ? ORDER BY distance LIMIT ?` — replaces the full numpy matrix scan
- Eliminates the 23-38MB RAM matrix; search is O(log n) not O(n)
- Migration: `get_all_embeddings()` and `_embed_matrix` global in `server.py` can be removed
- Need to re-run embed pass after adding the virtual table (or populate from existing `documents.embedding` BLOBs in a migration step)
- Add `sqlite-vec` to `pyproject.toml` dependencies
- **Do this last** — it's the most impactful but also the biggest schema/architecture change

---

## ⚫ Priority — Future / optional

- [ ] **Add ITSI, Observability** — most-requested missing products

---

## ✅ Done (2026-04-30)

### Setup menu redesign
- Grouped hierarchical display: current version first, n-1 versions indented beneath
- n-1 per-source exports contain only unique pages (shared pages live in parent DB via version_tags)
- Selecting an n-1 version auto-adds the parent so shared pages are available
- Total download size shown per entry (unique DB + parent DB)
- Export order follows PHASE1_SOURCES order (logical grouping, not alphabetical)

### Option B: cross-version content deduplication (version_tags)
- `content_md_hash` column added (SHA-256 of extracted Markdown) — fixes Enterprise/Cloud overlap (~2,006 pages)
- `version_tags` JSON column added — canonical rows tagged with all versions they cover
- `run_version_merge_pass()` collapses same-content derived rows into parent rows
- `run_dedup_pass()` updated to use `COALESCE(content_md_hash, content_hash)`
- `search_docs` and semantic search version filter now matches via `json_each(version_tags)`
- `merge_dbs()` calls version merge pass before FTS5 rebuild
- `export_sources()` includes shared rows in n-1 per-source exports

### SOAR indexing
- `soar-on-premises` 8.5.0 (current) and `soar-on-premises-8-4-0` 8.4.0 (n-1) added
- `soar-cloud` (current) added
- URL derivation for SOAR 8.4.0 via `derive_from="soar-on-premises"`

### 'Dead' URL status for permanent 404s
- `crawler.py`: HTTP 404 responses now stored as `status='dead'` not `status='failed'`
- Dead URLs are not retried on subsequent crawls (excluded from `get_failed_urls`)

---

## ✅ Done (2026-04-26)

### ES 8.4 coverage — confirmed correct
- Sitemap ground truth: ES 8.5 = 566, ES 8.4 = 308, ES 8.3 = 290 (our BFS exceeds these in all cases)
- `common-information-model` and `splunk-app-for-pci-compliance` are 8.5-only sections (404 on 8.4) — not missing pages, just new sections added in 8.5
- Added `api-reference/8.4` seed; 8.4 now crawls 431 pages (sitemap has 308, we exceed it)
- ES 8.4 page count is lower than 8.5 because 8.5 genuinely added ~330 pages across three new sections

---

## ✅ Done (2026-04-25)

### Lantern crawl fix
- `config.py`: added `https://lantern.splunk.com/hc` to `_LANTERN_BLOCKED` (auth-gated Help Center section)
- `crawler.py`: added auth-redirect detection — 4xx after off-domain redirect counts as skipped, not failed

### ES 8.5 page count investigation
- Confirmed 738 is correct and complete: all 14 sections on the ES 8.5 landing page are found via BFS
- The ~1,275 expected figure was a bad estimate; 738 is the real page count

### ES 8.4 page count investigation
- Root cause identified: `api-reference`, `common-information-model`, and most of `pci-compliance` are missing because they're only linked with `/8.5/` version segments on the live site
- See Priority 1 fix above

---

## ✅ Done (2026-04-23)

### README overhaul
- Hallucination motivation prominent at top
- uv install instructions (Homebrew, curl, PowerShell)
- Removed "Merging per-source databases" section (CI-only, confuses users)
- Simplified sources table with n−1 coverage model
- ITSI/SOAR/Observability listed as planned additions

### Bug fixes
- `setup.py`: clean up stale WAL/SHM files after merge temp rename
- `.gitignore`: added `*.tmp`, `*.tmp-wal`, `*.tmp-shm` patterns for merge temp files
- `crawl-and-release.yml`: merge step now skips missing per-source DBs instead of failing

### GHA re-run
- Triggered and verified: all sources except Lantern succeeded; Lantern fixed 2026-04-25
- ES 8.3 confirmed working (351 pages) — prior "0 pages" bug fixed in earlier session
- Enterprise 10.1 and Cloud 10.2 dropped from crawl matrix (seeding problem unsolvable without major rework; n−1 coverage maintained via ES 8.3/8.4 and Cloud/Enterprise current+1 back version)

---

## ✅ Done (Phase 3, 2026-04-20/21)

### Item 1: GHA matrix parallelisation + `merge_dbs()` ✅
### Item 7: Multi-version crawling ✅
### Item 5: Cross-source deduplication ✅
### Item 9: splunk-setup version selection UI ✅
### Priority 5 items ✅ (jitter, tests)
### GHA fixes (2026-04-21) ✅

---

## ✅ Done (Phase 1 + 2, earlier sessions)

- [x] **Phase 2 — Public release distribution** — `setup.py`, GHA workflow skeleton, README
- [x] **Item 10** — `crawled_at` in search result metadata
- [x] **Item 4** — Exponential backoff retry (3 attempts, 2/4/8 s)
- [x] **Item 3** — Embedding matrix cache at server startup
- [x] **Item 8** — Smart chunking + `--rechunk` flag
- [x] **Item 2** — Lantern sitemap-based URL discovery
- [x] **Item 6** — Embedding reuse via `content_hash`
- [x] All core files: `config.py`, `db.py`, `extractor.py`, `crawler.py`, `cli.py`, `server.py`
- [x] Full crawls of all original 5 sources
