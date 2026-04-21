# TODO — splunk-docs-mcp

_Last updated: 2026-04-21 (Phase 3 complete except Item 9)_

---

## 🔴 Priority 1 — Do next (GHA re-run)

### Trigger GHA re-run
- [ ] Go to Actions → Crawl and release → Run workflow (`workflow_dispatch`)
- [ ] Verify all 9 crawl jobs complete successfully (exit 0)
- [ ] Verify `merge-and-release` job runs and publishes release
- [ ] Confirm `splunk_docs.db` + per-source DBs + `manifest.json` in release assets
- [ ] Test `uv run splunk-setup` downloads the new release successfully

### Item 9: `splunk-setup` version selection UI ✅
- [x] Fetches `manifest.json` from latest release; falls back to monolithic `splunk_docs.db` if absent
- [x] Default mode: numbered menu listing source name, page count, size in MB; accepts comma-separated selection or `'all'`
- [x] `--all` flag: skips menu; prints total size + confirmation prompt
- [x] Downloads selected per-source DBs to `.tmp` files; single-source skips merge; multi-source merges via `merge_dbs()`; atomic rename to `data/splunk_docs.db`

---

## 🟡 Priority 2 — Nice to have

### ES crawl failure investigation
- [ ] Run: `sqlite3 data/splunk_docs.db "SELECT url, error FROM crawl_state WHERE status='failed';"`
- [ ] Determine if the 2 persistent ES failures are dead pages (404) or something fixable

---

## ⚫ Priority 3 — Future / optional

- [ ] **'Dead' URL status for permanent 404s** — currently URLs that 404 are stored as `status='failed'` and retried on every run. Adding a `'dead'` status (set when HTTP 404 is received) would exclude those URLs from `get_failed_urls()` and `get_visited_urls()`, stopping them being retried forever. Affects ~22 section-seed URLs per run across Enterprise/Cloud sources.
- [ ] **Weekly full re-fetch for content change detection** — currently incremental mode skips pages already in `crawl_state` as 'fetched', so updated docs are never re-indexed. Options:
  - Use sitemap `<lastmod>` in normal (non-`--full`) mode: re-queue pages where `lastmod > last_crawl_timestamp` (already fetched from sitemap; just need to remove the `if full and` guard in `crawler.py`). Works for Lantern; no help for sources without sitemaps.
  - Add a `--rehash` mode: re-fetch all pages and compare HTML hash, re-extract only on change, skip re-embedding if content unchanged. More thorough but slower than lastmod.
  - Simplest: run weekly cron with `--full` but skip re-embedding when hash unchanged (currently `--full` clears all embeddings unconditionally — could add `--full-crawl-only` that re-fetches without clearing embeddings).
- [ ] **SPL examples library** — `spl_examples` table + `search_spl` MCP tool (schema stub already in `db.py`)
- [ ] **Multi-version expansion** — add more ES or Enterprise versions to `config.py` as needed

---

## ✅ Done (Phase 3, 2026-04-20/21)

### Item 1: GHA matrix parallelisation + `merge_dbs()` ✅
- `src/splunk_docs_mcp/merge.py` — `merge_dbs()`, `export_sources()`, `splunk-merge` CLI (merge + export modes)
- `db.merge_source_db()` — ATTACH + INSERT OR IGNORE; auto-assigns IDs; FTS5 triggers fire correctly
- `pyproject.toml` — `splunk-merge` entry point added
- `.github/workflows/crawl-and-release.yml` — 9-job matrix + aggregation job

### Item 7: Multi-version crawling ✅
- 4 new `CrawlSource` entries: `enterprise-security-8-4`, `enterprise-security-8-3`, `splunk-enterprise-10-1`, `splunk-cloud-10-2`
- `version: str | None` filter on `search_docs()` and `search_docs_semantic_from_matrix()`
- Both search tools in `server.py` expose `version=` parameter with valid value list
- MCP instructions updated: 9-source list + version filter guidance

### Item 5: Cross-source deduplication ✅
- `is_duplicate INTEGER DEFAULT 0` column (ALTER TABLE migration)
- `run_dedup_pass(conn)` — groups by `content_hash` across sources; priority: ES 8.5 > ES 8.4 > ES 8.3 > admin-manual > Enterprise 10.2 > Enterprise 10.1 > Cloud 10.3.2512 > Cloud 10.2 > Lantern
- `search_docs()` + `search_docs_semantic_from_matrix()`: apply `is_duplicate=0` filter unless `version=` is set (version-specific queries see all docs regardless of dedup)
- `_dedup_pass()` in `cli.py` runs after every crawl

### Priority 5 items ✅
- `--delay-jitter SECONDS` flag: uniform random jitter added to each request delay
- `tests/test_extractor.py`: 18 tests for `parse_url_metadata()` (ES, admin-manual, Lantern)
- `tests/test_crawler.py`: 18 tests for `_normalise_url`, `_is_target_url`, `_section_from_url`
- All 36 tests pass

### GHA fixes (2026-04-21) ✅
- `cli.py`: exit 1 only if failure rate >5% (was: any failure = exit 1)
- `crawler.py`: retry pass after BFS re-attempts failed URLs once; `get_visited_urls()` excludes failed rows so they're retried on next incremental run
- `db.py`: `get_failed_urls()` helper; `get_visited_urls()` excludes `status='failed'`
- `crawl-and-release.yml`: `continue-on-error: true` on crawl jobs; `if: always()` on cache-save and artifact-upload steps

### README overhaul ✅
- Why it exists: learning MCP, fun side project, vibe-coded
- Works with any MCP-compatible client (not just Claude)
- "Getting the best results" section + custom instructions tip
- All 9 sources with `source=` and `version=` filter docs
- Cross-source dedup behaviour documented
- `--delay-jitter`, `--rechunk`, `splunk-merge` in building-locally section

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
- [x] Full crawls of all original 5 sources (~8,946 pages total)
