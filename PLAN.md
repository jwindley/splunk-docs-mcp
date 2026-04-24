# Build Plan — splunk-docs-mcp

_Last updated: 2026-04-23 (README overhaul, WAL fix, GHA merge resilience, dedup analysis)_

---

## Current Status

**Phase 1, 2, and most of Phase 3 are complete.** GHA workflow is running but three sources have crawl issues (see Known Issues below). A GHA re-run is needed to incorporate the fixes from this session.

---

## What Has Been Built

| File | Status | Notes |
|------|--------|-------|
| `pyproject.toml` | ✅ Done | Deps + entry points: `splunk-mcp`, `splunk-crawl`, `splunk-setup`, `splunk-merge` |
| `.gitignore` | ✅ Done | Includes merge temp patterns (*.tmp, *.tmp-wal, *.tmp-shm) |
| `.python-version` | ✅ Done | `3.12` |
| `src/splunk_docs_mcp/__init__.py` | ✅ Done | |
| `src/splunk_docs_mcp/config.py` | ✅ Done | 9 active sources (ES 8.3/8.4/8.5, admin-manual 10.2, Enterprise 10.1/10.2, Cloud 10.2/10.3.2512, Lantern) |
| `src/splunk_docs_mcp/db.py` | ✅ Done | Schema + all helpers; `is_duplicate` column; `run_dedup_pass()`; `merge_source_db()`; `get_failed_urls()`; version filter on search functions |
| `src/splunk_docs_mcp/extractor.py` | ✅ Done | |
| `src/splunk_docs_mcp/server.py` | ✅ Done | 6 tools; `version=` filter on `search_docs` + `search_docs_semantic`; 9-source instructions |
| `src/splunk_docs_mcp/cli.py` | ✅ Done | `--delay-jitter`; `_dedup_pass()`; exit 1 only if failure rate >5% |
| `src/splunk_docs_mcp/crawler.py` | ✅ Done | Retry pass after BFS; failed URLs excluded from visited set; `--delay-jitter` support |
| `src/splunk_docs_mcp/merge.py` | ✅ Done | `merge_dbs()`, `export_sources()`, `splunk-merge` CLI |
| `src/splunk_docs_mcp/setup.py` | ✅ Done | Interactive menu; per-source selection; WAL cleanup after merge |
| `tests/test_extractor.py` | ✅ Done | 18 tests for `parse_url_metadata()` |
| `tests/test_crawler.py` | ✅ Done | 18 tests for `_normalise_url`, `_is_target_url`, `_section_from_url` |
| `.github/workflows/crawl-and-release.yml` | ✅ Done | 9-job matrix; resilient merge (skips missing DBs); corrected release body |
| `README.md` | ✅ Done | Hallucination motivation at top; uv install instructions; simplified sources table; n−1 coverage model; removed CI-only merge section |

---

## What Works

- **MCP server:** all 6 tools; `version=` filter on both search tools; 9-source instructions
- **Multi-version search:** `search_docs(query, version="8.4")` filters correctly across sources
- **Cross-source dedup:** `is_duplicate=1` suppresses duplicate content in general searches; bypassed when `version=` is set so version-specific queries see all docs
- **BM25 keyword search:** FTS5, BM25 ranked, title weighted 10×, snippets
- **Semantic search:** all-MiniLM-L6-v2 embeddings, matrix cached at startup
- **Crawler retry pass:** after main BFS, failed URLs are re-attempted once; recovers transient timeouts/5xx
- **Incremental re-crawl:** failed URLs excluded from visited set so they're retried on next run
- **`splunk-merge`:** merges per-source DBs + exports per-source files + `manifest.json`
- **`splunk-setup`:** interactive menu; single-source skips merge; multi-source merges; WAL cleanup
- **36 passing tests:** `parse_url_metadata`, `_normalise_url`, `_is_target_url`, `_section_from_url`
- **GHA workflow:** 9-job matrix, per-source DB caching, `continue-on-error`, resilient merge (skips missing per-source DBs)

---

## Known Issues

### 1. Enterprise 10.1 and Cloud 10.2 — near-zero pages crawled

**Symptom:** `splunk-enterprise-10-1` has 0 pages; `splunk-cloud-10-2` has only ~112 pages (expected ~2,500).

**Root cause:** The section-level seed URLs for older versions (e.g., `/get-started/10.1`) redirect to the current version's section page. All links on the redirect destination are for the current version (10.2/10.3.2512), and the version filter rejects them. The only pages discovered are those from section seeds that happen to redirect to a page still versioned at 10.2 (e.g., the universal forwarder manual).

**Impact:** Users cannot search Cloud 10.2 or Enterprise 10.1 documentation.

**Fix needed:** Alternative seeding strategy for older versions. Options:
- Crawl the current version's sitemap/pages, then substitute the version segment in URLs and attempt to fetch the older version equivalent.
- Find version-specific sitemaps or index pages that list older-version content explicitly.
- Use `--full` after fixing seeds to ensure clean re-discovery.

### 2. ES 8.5 and 8.4 — lower page counts than expected

**Symptom:** ES 8.5 = 738 pages (expected ~1,275); ES 8.4 = 336 pages (expected ~1,200).

**Likely cause:** GHA rate limiting or timeouts during the crawl run. The version filter and seeds look correct for these sources.

**Fix:** Trigger another GHA run; monitor for rate-limiting errors in the crawl logs.

### 3. ES 8.3 — 0 pages crawled

**Symptom:** `enterprise-security-8-3` has 0 pages.

**Root cause:** The ES 8.3 config intentionally omits version-specific section seeds (they redirect to 8.5). BFS from the root is meant to discover 8.3 pages, but the root page likely only links to the current version, and no 8.3 links are reachable. This has the same fundamental cause as issue #1.

**Fix:** Same as issue #1 — need a better seeding strategy for older ES versions.

### 4. Enterprise vs Cloud dedup gap

**Symptom:** The current dedup (`run_dedup_pass`) is based on raw HTML hash (`content_hash`). Enterprise and Cloud pages are served from different URLs and have different HTML, so their raw HTML hashes differ even when the extracted Markdown content is identical.

**Impact:** ~2,006 Enterprise pages (56%) share matching titles and content lengths with Cloud pages. Sections with the most overlap: `search` (673), `alert-and-respond` (272), `spl-search-reference` (203), `create-dashboards-and-reports` (176). Without dedup, searches return both Enterprise and Cloud versions of identical articles.

**Fix needed:** Add a `content_md_hash` column and use it (in addition to `content_hash`) in `run_dedup_pass()`. This would let the dedup detect identical Markdown content across different HTML sources.

---

## What Is Incomplete

| Item | Status |
|------|--------|
| Item 9 — `splunk-setup` version selection UI | ✅ Done |
| GHA re-run to produce updated release | ⏳ Needs manual trigger |
| Enterprise 10.1 / Cloud 10.2 seed strategy fix | ❌ Not started |
| ES 8.3 seed strategy fix | ❌ Not started |
| Enterprise vs Cloud dedup via content_md_hash | ❌ Not started |

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
- **Item 1** ✅ — GHA matrix (9 parallel jobs) + `merge_dbs()` + `splunk-merge` CLI
- **Item 7** ✅ — Multi-version crawling (4 new sources) + `version=` filter on search tools

### Tier 4 — Polish (partial)
- **Item 5** ✅ — Cross-source deduplication (`is_duplicate` column; version-bypass logic)
- **Item 5b** ❌ — Extend dedup to use `content_md_hash` for Enterprise/Cloud overlap
- **Item 9** ✅ — `splunk-setup` version selection UI
