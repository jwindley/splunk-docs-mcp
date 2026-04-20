# TODO — splunk-docs-mcp

_Last updated: 2026-04-20 (Phase 3 planned)_

---

## 🔴 Priority 1 — Tier 1: Foundational (no dependencies, do first)

### Item 10: Crawl date in search results ✅
- [x] Add `d.crawled_at` to SELECT in `search_docs()` FTS join in `db.py`; include `"crawled": crawled_at[:10]` in result dict
- [x] Add `crawled_at` to SELECT in `search_docs_semantic()` in `db.py`; include in result dict

### Item 4: Crawler retry logic ✅
- [x] Add `MAX_RETRIES = 3` and `RETRY_DELAYS = [2, 4, 8]` constants at top of `crawler.py`
- [x] Wrap `httpx.get()` in retry loop in `_process_url()`; retry on `TimeoutException`, `ConnectError`, `ReadError`, and 5xx; do not retry on 4xx
- [x] Verify existing `crawl_state` error recording still fires on final failure

### Item 3: Embedding matrix cache at startup ✅
- [x] Add `get_all_embeddings(conn)` to `db.py` — returns `(matrix: np.ndarray, rows: list[dict])`
- [x] Add `_embed_matrix` / `_embed_rows` module-level variables to `server.py`
- [x] Load cache immediately after `_get_db()` first initialises the connection
- [x] Add `search_docs_semantic_from_matrix()` to `db.py`; uses numpy boolean mask for source pre-filter
- [x] Refactor `search_docs_semantic` tool in `server.py` to use cached matrix
- [x] Add note to `README.md`: restart MCP server after running `splunk-crawl` for semantic search to reflect the updated index

---

## 🟡 Priority 2 — Tier 2: Quality improvements (independent)

### Item 8: Smart chunking + `--rechunk` flag ✅
- [x] Add `_split_content_smart(text, chunk_size, overlap)` to `db.py` — strategy: heading boundaries (`\n## `, `\n### `) → paragraph boundaries (`\n\n`) → character fallback (existing `_split_content()`)
- [x] Add `_accumulate_with_overlap()` helper; heading sections up to `chunk_size * 2` kept whole
- [x] Update `chunk_document()` in `db.py` to call `_split_content_smart()` instead of `_split_content()`
- [x] Add `--rechunk` flag to `cli.py` argparse
- [x] `--rechunk` skips crawl, resets chunks, runs `_chunk_pass()` + `_embed_pass()` for new chunks only

### Item 2: Lantern sitemap discovery ✅
- [x] Add `sitemap_url: str | None = None` field to `CrawlSource` dataclass in `config.py`
- [x] Set `sitemap_url = "https://lantern.splunk.com/sitemap.xml"` on the Lantern source
- [x] Add `_fetch_sitemap_urls(source, section_filter)` async function to `crawler.py`; handles sitemap XML namespace; filters via `_normalise_url()` + `_is_target_url()`
- [x] Sitemap seeding runs at start of `crawl_source()` before BFS seed URLs; `--full` mode uses `get_crawl_timestamps()` to compare `<lastmod>[:10]` against `attempted_at[:10]` and skip unchanged pages without fetching
- [x] BFS fallback intact — discovers remaining ~484 pages not in sitemap

---

## 🟢 Priority 3 — Tier 3: Scalability (item 6 before item 7; item 1 before item 7)

### Item 6: Embedding reuse via content_hash ✅
- [x] Add `CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)` to `init_db()` in `db.py`
- [x] Add `get_embedding_by_hash(conn, content_hash) -> bytes | None` helper to `db.py`
- [x] Modify `_embed_pass()` in `cli.py`: for each document needing an embedding, check `get_embedding_by_hash()` first; copy if found; only batch-encode documents with no matching hash

### Item 1: GHA matrix parallelisation + `merge_dbs()`
- [ ] Create `src/splunk_docs_mcp/merge.py` with `merge_dbs(source_db_paths: list[Path], output_path: Path)`:
  - Creates fresh output DB, calls `init_db()`
  - For each source DB: `ATTACH`, `INSERT OR IGNORE` all `documents` and `crawl_state` rows, `DETACH`
  - Runs `INSERT INTO documents_fts(documents_fts) VALUES('rebuild')` to repopulate FTS5
  - Calls `_chunk_pass()` and `_embed_pass()` (idempotent — existing chunks/embeddings skipped)
- [ ] Add `--export-sources <dir>` flag to `splunk-merge` CLI: exports one `splunk_docs_<source_id>.db` per source + generates `manifest.json`
- [ ] Add `splunk-merge = "splunk_docs_mcp.merge:main"` entry point to `pyproject.toml`
- [ ] Add `merge_source_db(main_conn, source_db_path)` helper to `db.py`
- [ ] Rewrite `.github/workflows/crawl-and-release.yml`:
  - Matrix strategy: one job per source ID; runs `uv run splunk-crawl --sources <id> --db data/<id>.db`
  - Per-job: restore GHA cache keyed `splunk-db-<id>-<date>` (restore-key: `splunk-db-<id>-`) for incremental crawling; upload per-source DB as artifact
  - Aggregation job (`needs: [crawl]`): downloads all artifacts; runs `uv run splunk-merge`; runs `uv run splunk-merge --export-sources`; uploads `splunk_docs.db` + per-source DBs + `manifest.json` as release assets

### Item 7: Multi-version crawling
- [ ] Add `version: str | None = None` parameter to `search_docs()` in `db.py` at `# Future` comment (line ~369); add `AND version = :version` to query when provided
- [ ] Mirror `version` parameter in `search_docs` tool in `server.py`; update tool docstring with available version values
- [ ] Add 4 new `CrawlSource` entries to `PHASE1_SOURCES` in `config.py`:
  - `splunk-enterprise-10-1` — Splunk Enterprise 10.1, same `url_prefix` as 10.2, `version="10.1"`
  - `enterprise-security-8-4` — ES 8.4, same `url_prefix` as 8.5, `version="8.4"`
  - `enterprise-security-8-3` — ES 8.3, same `url_prefix` as 8.5, `version="8.3"`
  - `splunk-cloud-10-2` — Splunk Cloud 10.2, same `url_prefix` as 10.3.2512, `version="10.2"`
- [ ] Update MCP tool instructions in `server.py` to document when to use `version=` filter
- [ ] Update source table in `CLAUDE.md`

---

## ⚪ Priority 4 — Tier 4: Polish (requires items 1 and 7)

### Item 5: Cross-source deduplication
- [ ] Add `is_duplicate INTEGER DEFAULT 0` column migration to `init_db()` in `db.py` (ALTER TABLE ... ADD COLUMN, safe for existing DBs)
- [ ] Add `run_dedup_pass(conn, source_ids)` to `db.py`: group by `content_hash` across sources; retain highest-priority source row; mark others + their chunks `is_duplicate = 1`. Priority order: `enterprise-security` > `admin-manual` > `splunk-enterprise` > `splunk-cloud` > `lantern`
- [ ] Update `search_docs()` and `search_docs_semantic()` WHERE clauses: add `AND is_duplicate = 0`
- [ ] Add `_dedup_pass()` call to `cli.py` after `_embed_pass()` (runs every crawl; resets + reruns on `--full`)
- [ ] `get_page(url)` requires no change — `is_duplicate` only affects search, not direct URL lookup

### Item 9: `splunk-setup` version selection UI
- [ ] Define `manifest.json` schema: `{generated_at, total_pages, sources: [{source_id, display_name, version, pages, chunks, file_name, size_bytes}]}`
- [ ] Add `--export-sources` manifest generation to `merge.py` (item 1 subtask above)
- [ ] Update GHA workflow to upload per-source DBs + `manifest.json` as release assets
- [ ] Update `setup.py`:
  - Fetch `manifest.json` from latest release (fall back to monolithic `splunk_docs.db` if not found)
  - Default mode: display numbered menu; accept comma-separated selection or `'all'`
  - `--all` flag: skip menu; print size warning + confirmation prompt
  - Download selected per-source DBs to `.tmp` files; merge via `merge_dbs()`; atomic rename

---

## 🔵 Priority 5 — Existing nice-to-haves (carried from Phase 2)

- [ ] Investigate the 2 ES crawl failures: `sqlite3 data/splunk_docs.db "SELECT url, error FROM crawl_state WHERE status='failed';"`
- [ ] Add `--delay-jitter` flag to crawler to randomise per-request delay
- [ ] Add `pytest` tests for `parse_url_metadata()` covering ES, admin-manual, and Lantern URL patterns
- [ ] Add `pytest` tests for `_section_from_url()` with redirect-destination URLs
- [ ] Add `pytest` tests for `_normalise_url()` edge cases (fragments, query strings, mailto)
- [ ] Add `pytest` test for `_is_target_url()` version-filter logic (ES 8.0 rejected, ES 8.5 allowed, admin-manual and Lantern unaffected)

---

## ⚫ Priority 6 — Future / optional

- [ ] **SPL examples library** — `spl_examples` table + `search_spl` MCP tool (schema stub already in `db.py`)

---

## ✅ Done

- [x] **Phase 2 — Public release distribution** (2026-04-20)
  - `src/splunk_docs_mcp/setup.py` — `splunk-setup` command
  - `.github/workflows/crawl-and-release.yml` — weekly cron + workflow_dispatch
  - README rewrite for public audience
- [x] **Run `uv run splunk-crawl --full`** — chunks rebuilt and embeddings generated for all sources (2026-04-20)
- [x] **Full Lantern crawl** — 1,284 pages, 1,192 embeddings (2026-04-20)
- [x] **`splunk-enterprise` full crawl** — 3,513 pages (2026-04-19/20)
- [x] **`splunk-cloud` full crawl** — 2,658 pages (2026-04-19/20)
- [x] **`enterprise-security` expanded** — 1,275 pages (full re-crawl 2026-04-19/20)
- [x] **Lantern source activated** — `CrawlSource` extended with `crawl_delay`, `max_concurrency`, `blocked_path_prefixes` (2026-04-19)
- [x] **MCP instructions decision tree** — 5-branch decision tree with hard call-count limits (2026-04-19)
- [x] **Eager model loading** — `SentenceTransformer` at module level in `server.py` (2026-04-19)
- [x] **Vector/semantic search** — all-MiniLM-L6-v2 embeddings; `search_docs_semantic` MCP tool (2026-04-19)
- [x] **Document chunking** — 8,000-char threshold, 1,500-char chunks, 200-char overlap; `_chunk_pass()` in `cli.py` (2026-04-20)
- [x] **Confidence signalling** — `search_docs` and `search_docs_semantic` docstrings + instructions (2026-04-20)
- [x] **Full crawl verified** — 743 ES pages + 216 admin-manual, all 6 sections (2026-04-18)
- [x] **Bug fix: redirect URL** — `_process_url` uses `str(resp.url)` as urljoin base (2026-04-18)
- [x] **Bug fix: version filter** — `_is_target_url` rejects wrong-version URLs (2026-04-18)
- [x] All core files: `config.py`, `db.py`, `extractor.py`, `crawler.py`, `cli.py`, `server.py`
