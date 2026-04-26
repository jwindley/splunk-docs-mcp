# Build Plan — splunk-docs-mcp

_Last updated: 2026-04-25 (Lantern fix, ES page count investigation, stale items cleared)_

---

## Current Status

**Phase 1, 2, and Phase 3 are complete.** GHA workflow is running cleanly for all active sources. One known coverage gap remains: ES 8.4 is missing ~370 pages from sections only linked by the current (8.5) site navigation.

---

## What Has Been Built

| File | Status | Notes |
|------|--------|-------|
| `pyproject.toml` | ✅ Done | Deps + entry points: `splunk-mcp`, `splunk-crawl`, `splunk-setup`, `splunk-merge` |
| `.gitignore` | ✅ Done | Includes merge temp patterns (*.tmp, *.tmp-wal, *.tmp-shm) |
| `.python-version` | ✅ Done | `3.12` |
| `src/splunk_docs_mcp/__init__.py` | ✅ Done | |
| `src/splunk_docs_mcp/config.py` | ✅ Done | 7 active sources (ES 8.3/8.4/8.5, admin-manual 10.2, Enterprise 10.2, Cloud 10.3.2512, Lantern) |
| `src/splunk_docs_mcp/db.py` | ✅ Done | Schema + all helpers; `is_duplicate` column; `run_dedup_pass()`; `merge_source_db()`; `get_failed_urls()`; version filter on search functions |
| `src/splunk_docs_mcp/extractor.py` | ✅ Done | |
| `src/splunk_docs_mcp/server.py` | ✅ Done | 6 tools; `version=` filter on `search_docs` + `search_docs_semantic`; source instructions |
| `src/splunk_docs_mcp/cli.py` | ✅ Done | `--delay-jitter`; `_dedup_pass()`; exit 1 only if failure rate >5% |
| `src/splunk_docs_mcp/crawler.py` | ✅ Done | Retry pass after BFS; failed URLs excluded from visited set; auth-redirect detection (4xx after off-domain redirect → skipped, not failed) |
| `src/splunk_docs_mcp/merge.py` | ✅ Done | `merge_dbs()`, `export_sources()`, `splunk-merge` CLI |
| `src/splunk_docs_mcp/setup.py` | ✅ Done | Interactive menu; per-source selection; WAL cleanup after merge |
| `tests/test_extractor.py` | ✅ Done | 18 tests for `parse_url_metadata()` |
| `tests/test_crawler.py` | ✅ Done | 18 tests for `_normalise_url`, `_is_target_url`, `_section_from_url` |
| `.github/workflows/crawl-and-release.yml` | ✅ Done | 7-job matrix; resilient merge (skips missing DBs) |
| `README.md` | ✅ Done | Hallucination motivation at top; uv install instructions; simplified sources table; n−1 coverage model |

---

## What Works

- **MCP server:** all 6 tools; `version=` filter on both search tools
- **Multi-version search:** `search_docs(query, version="8.4")` filters correctly across sources
- **Cross-source dedup:** `is_duplicate=1` suppresses duplicate content in general searches; bypassed when `version=` is set
- **BM25 keyword search:** FTS5, BM25 ranked, title weighted 10×, snippets
- **Semantic search:** all-MiniLM-L6-v2 embeddings, matrix cached at startup
- **Crawler retry pass:** after main BFS, failed URLs are re-attempted once
- **Auth-redirect detection:** pages that redirect to external SSO (403) are skipped cleanly, not counted as failures
- **Incremental re-crawl:** failed URLs excluded from visited set so they're retried on next run
- **`splunk-merge`:** merges per-source DBs + exports per-source files + `manifest.json`
- **`splunk-setup`:** interactive menu; single-source skips merge; multi-source merges; WAL cleanup
- **36 passing tests:** `parse_url_metadata`, `_normalise_url`, `_is_target_url`, `_section_from_url`
- **GHA workflow:** 7-job matrix, per-source DB caching, `continue-on-error`, resilient merge

---

## Known Issues

### 1. Enterprise vs Cloud dedup gap

**Symptom:** The current dedup (`run_dedup_pass`) is based on raw HTML hash (`content_hash`). Enterprise and Cloud pages are served from different URLs so their HTML hashes differ even when extracted Markdown is identical.

**Impact:** ~2,006 Enterprise pages (56%) share content with Cloud. Sections most affected: `search` (673), `alert-and-respond` (272), `spl-search-reference` (203), `create-dashboards-and-reports` (176). General searches return both versions of identical articles.

**Fix needed:** Add a `content_md_hash` column; use it in `run_dedup_pass()` alongside `content_hash`.

---

## What Is Incomplete

| Item | Status |
|------|--------|
| Enterprise vs Cloud dedup via `content_md_hash` | ❌ Not started |

---

## Phase 3 — Improvements Status

### Tier 1 — Foundational ✅ All done
- **Item 10** ✅ — `crawled_at` in search results
- **Item 4** ✅ — Exponential backoff retry (3 attempts, 2/4/8 s)
- **Item 3** ✅ — Embedding matrix cache at startup

### Tier 2 — Quality ✅ All done
- **Item 8** ✅ — Smart chunking (heading → paragraph → character fallback) + `--rechunk`
- **Item 2** ✅ — Lantern sitemap seeding + `<lastmod>` skip + BFS fallback

### Tier 3 — Scalability ✅ All done
- **Item 6** ✅ — Embedding reuse via `content_hash`
- **Item 1** ✅ — GHA matrix (7 parallel jobs) + `merge_dbs()` + `splunk-merge` CLI
- **Item 7** ✅ — Multi-version crawling (ES 8.3/8.4) + `version=` filter on search tools

### Tier 4 — Polish (partial)
- **Item 5** ✅ — Cross-source deduplication (`is_duplicate` column; version-bypass logic)
- **Item 5b** ❌ — Extend dedup to use `content_md_hash` for Enterprise/Cloud overlap
- **Item 9** ✅ — `splunk-setup` version selection UI
