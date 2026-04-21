# Build Plan — splunk-docs-mcp

_Last updated: 2026-04-21 (Phase 3 complete except Item 9; GHA first run fixed)_

---

## Current Status

**Phase 1, 2, and most of Phase 3 are complete.** All Phase 3 Tier 1–3 items and most Tier 4 items are done. The GHA workflow ran for the first time and produced a failure (fixed — see below). A second GHA run is needed to produce the first public release.

**One item outstanding:** Item 9 (splunk-setup version selection UI). Deferred due to token budget; carries to next session.

---

## What Has Been Built

| File | Status | Notes |
|------|--------|-------|
| `pyproject.toml` | ✅ Done | Deps + entry points: `splunk-mcp`, `splunk-crawl`, `splunk-setup`, `splunk-merge` |
| `.gitignore` | ✅ Done | |
| `.python-version` | ✅ Done | `3.12` |
| `src/splunk_docs_mcp/__init__.py` | ✅ Done | |
| `src/splunk_docs_mcp/config.py` | ✅ Done | 9 active sources (ES 8.3/8.4/8.5, admin-manual 10.2, Enterprise 10.1/10.2, Cloud 10.2/10.3.2512, Lantern) |
| `src/splunk_docs_mcp/db.py` | ✅ Done | Schema + all helpers; `is_duplicate` column; `run_dedup_pass()`; `merge_source_db()`; `get_failed_urls()`; version filter on search functions |
| `src/splunk_docs_mcp/extractor.py` | ✅ Done | |
| `src/splunk_docs_mcp/server.py` | ✅ Done | 6 tools; `version=` filter on `search_docs` + `search_docs_semantic`; 9-source instructions |
| `src/splunk_docs_mcp/cli.py` | ✅ Done | `--delay-jitter`; `_dedup_pass()`; exit 1 only if failure rate >5% |
| `src/splunk_docs_mcp/crawler.py` | ✅ Done | Retry pass after BFS; failed URLs excluded from visited set; `--delay-jitter` support |
| `src/splunk_docs_mcp/merge.py` | ✅ Done | `merge_dbs()`, `export_sources()`, `splunk-merge` CLI |
| `src/splunk_docs_mcp/setup.py` | ✅ Done | `splunk-setup` downloads latest release asset |
| `tests/test_extractor.py` | ✅ Done | 18 tests for `parse_url_metadata()` |
| `tests/test_crawler.py` | ✅ Done | 18 tests for `_normalise_url`, `_is_target_url`, `_section_from_url` |
| `.github/workflows/crawl-and-release.yml` | ✅ Done | 9-job matrix; `continue-on-error`; `if: always()` on cache/artifact steps |
| `README.md` | ✅ Done | Rewritten: why it exists, vibe-coded, any MCP client, setup tips, all 9 sources |

---

## What Works

- **MCP server:** all 6 tools; `version=` filter on both search tools; 9-source instructions
- **Multi-version search:** `search_docs(query, version="8.4")` filters correctly across sources
- **Cross-source dedup:** `is_duplicate=1` suppresses duplicate content in general searches; bypassed when `version=` is set so version-specific queries see all docs
- **BM25 keyword search:** FTS5, BM25 ranked, title weighted 10×, snippets
- **Semantic search:** all-MiniLM-L6-v2 embeddings, matrix cached at startup
- **Crawler retry pass:** after main BFS, failed URLs are re-attempted once; recovers transient timeouts/5xx
- **Incremental re-crawl:** failed URLs now excluded from visited set so they're retried on next run
- **`splunk-merge`:** merges per-source DBs + exports per-source files + `manifest.json`
- **36 passing tests:** `parse_url_metadata`, `_normalise_url`, `_is_target_url`, `_section_from_url`
- **GHA workflow:** 9-job matrix, per-source DB caching, `continue-on-error`, `if: always()` safety net

---

## GHA First Run (2026-04-21) — What Happened and Fixes Applied

**What happened:**
- Crawl completed successfully (~9,452 pages, 46,299 embeddings)
- 35 pages failed (0.4%) due to transient network errors
- `cli.py` exited with code 1 for any non-zero failures
- GHA skipped the cache-save and upload-artifact steps (they ran after the failed step)
- `merge-and-release` job had no artifacts → no release published

**Fixes applied (all committed and pushed):**
1. `cli.py`: exit 1 only if failure rate >5% of total pages — 0.4% now exits 0
2. `crawler.py`: retry pass after main BFS re-attempts all failed URLs once
3. `db.py`: `get_visited_urls()` excludes `status='failed'` rows — failed pages retried on next incremental run
4. `crawl-and-release.yml`: `continue-on-error: true` on crawl jobs; `if: always()` on cache-save and artifact-upload steps

**Action required:** Trigger another `workflow_dispatch` run to produce the first release.

---

## What Is Incomplete

| Item | Status |
|------|--------|
| Item 9 — `splunk-setup` version selection UI | ❌ Not started |
| GHA second run (produce first release) | ⏳ Needs manual trigger |
| ES crawl failure investigation (2 specific URLs) | ❌ Not investigated |
| 4 new sources first crawl (ES 8.3/8.4, Enterprise 10.1, Cloud 10.2) | ⏳ Will happen on next GHA run |

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
- **Item 9** ❌ — `splunk-setup` version selection UI (not started; next session)

---

## Item 9 — splunk-setup version selection UI (next session)

- Define `manifest.json` schema: `{generated_at, total_pages, sources: [{source_id, display_name, version, pages, chunks, file_name, size_bytes}]}` — schema already generated by `splunk-merge --export-sources`
- Update `setup.py`:
  - Fetch `manifest.json` from latest release (fall back to monolithic `splunk_docs.db` if not found)
  - Default mode: display numbered menu of sources; accept comma-separated selection or `'all'`
  - `--all` flag: skip menu; print size warning + confirmation prompt
  - Download selected per-source DBs to `.tmp` files; merge via `merge_dbs()`; atomic rename
