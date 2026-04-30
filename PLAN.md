# Build Plan — splunk-docs-mcp

_Last updated: 2026-04-30 (Option B: version_tags + content_md_hash; SOAR indexing; dead-URL status)_

---

## Current Status

**Phase 1, 2, Phase 3, and Option B are complete.** GHA workflow runs all 10 sources (ES 8.3/8.4/8.5, Enterprise 10.2, Cloud 10.3.2512, admin-manual, SOAR on-prem 8.4/8.5, SOAR Cloud, Lantern). Cross-version content deduplication via `version_tags` is live.

---

## What Has Been Built

| File | Status | Notes |
|------|--------|-------|
| `pyproject.toml` | ✅ Done | Deps + entry points: `splunk-mcp`, `splunk-crawl`, `splunk-setup`, `splunk-merge` |
| `.gitignore` | ✅ Done | Includes merge temp patterns (*.tmp, *.tmp-wal, *.tmp-shm) |
| `.python-version` | ✅ Done | `3.12` |
| `src/splunk_docs_mcp/__init__.py` | ✅ Done | |
| `src/splunk_docs_mcp/config.py` | ✅ Done | 10 active sources; `get_source_version_pairs()` for version merge |
| `src/splunk_docs_mcp/db.py` | ✅ Done | Schema + all helpers; `content_md_hash`; `version_tags`; `run_version_merge_pass()`; `run_dedup_pass()` uses `content_md_hash`; version filter matches `json_each(version_tags)` |
| `src/splunk_docs_mcp/extractor.py` | ✅ Done | |
| `src/splunk_docs_mcp/server.py` | ✅ Done | 6 tools; `version=` filter on `search_docs` + `search_docs_semantic`; source instructions |
| `src/splunk_docs_mcp/cli.py` | ✅ Done | `--delay-jitter`; `_dedup_pass()`; exit 1 only if failure rate >5% |
| `src/splunk_docs_mcp/crawler.py` | ✅ Done | Retry pass after BFS; failed URLs excluded from visited set; auth-redirect detection (4xx after off-domain redirect → skipped, not failed) |
| `src/splunk_docs_mcp/merge.py` | ✅ Done | `merge_dbs()`, `export_sources()`, `splunk-merge` CLI |
| `src/splunk_docs_mcp/setup.py` | ✅ Done | Grouped hierarchical menu (product → versions); n-1 auto-adds parent; total MB shown per entry; WAL cleanup after merge |
| `tests/test_extractor.py` | ✅ Done | 18 tests for `parse_url_metadata()` |
| `tests/test_crawler.py` | ✅ Done | 18 tests for `_normalise_url`, `_is_target_url`, `_section_from_url` |
| `.github/workflows/crawl-and-release.yml` | ✅ Done | 10-source matrix (crawl + crawl-derived + merge-and-release); resilient merge (skips missing DBs) |
| `README.md` | ✅ Done | Hallucination motivation at top; uv install instructions; simplified sources table; n−1 coverage model |

---

## What Works

- **MCP server:** all 6 tools; `version=` filter on both search tools
- **Multi-version search:** `search_docs(query, version="8.4")` matches rows by `version` column AND `version_tags` JSON array
- **Cross-version dedup (Option B):** `run_version_merge_pass` collapses same-content n-1 rows into parent rows tagged with both versions; DB size stays bounded as more n-1 sources are added
- **Cross-source dedup:** `is_duplicate=1` suppresses duplicate content (now using `content_md_hash` — fixes Enterprise/Cloud Markdown-identical pages); bypassed when `version=` is set
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

No blocking issues. The previously noted Enterprise/Cloud dedup gap is resolved by `content_md_hash` in `run_dedup_pass`.

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
- **Item 5b** ✅ — Extend dedup to use `content_md_hash` for Enterprise/Cloud overlap (done in Option B)
- **Item 9** ✅ — `splunk-setup` version selection UI
