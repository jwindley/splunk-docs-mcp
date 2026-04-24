# TODO — splunk-docs-mcp

_Last updated: 2026-04-23_

---

## 🔴 Priority 1 — Do next

### Trigger GHA re-run
- [ ] Go to Actions → Crawl and release → Run workflow (`workflow_dispatch`)
- [ ] Verify all 9 crawl jobs complete — especially Enterprise 10.1 and Cloud 10.2 (known issues)
- [ ] Verify `merge-and-release` job runs even if some crawl jobs fail (new resilient merge step)
- [ ] Confirm `splunk_docs.db` + per-source DBs + `manifest.json` appear in release assets
- [ ] Test `uv run splunk-setup` downloads the new release successfully

### Fix older-version seeding (Enterprise 10.1, Cloud 10.2, ES 8.3)
- [ ] Investigate why `splunk-enterprise-10-1` returns 0 pages (section seeds likely redirect to 10.2)
- [ ] Investigate why `splunk-cloud-10-2` returns only ~112 pages
- [ ] Investigate `enterprise-security-8-3` = 0 pages (BFS from root doesn't discover 8.3 links)
- [ ] Design a better seeding strategy (e.g., version-substitution from known current URLs, or version-specific sitemaps)
- [ ] Re-crawl after fixing seeds with `--full` to get clean discovery

### ES 8.5/8.4 page count investigation
- [ ] ES 8.5 has 738 pages (expected ~1,275); ES 8.4 has 336 pages (expected ~1,200)
- [ ] Check GHA crawl logs for rate-limiting or timeout errors on those jobs
- [ ] Run another GHA crawl and compare counts

---

## 🟡 Priority 2 — Nice to have

### Extend dedup to catch Enterprise vs Cloud overlap
- [ ] Add `content_md_hash TEXT` column to `documents` (hash of extracted Markdown, not raw HTML)
- [ ] Compute `content_md_hash` at crawl time (in `upsert_document`) or in a backfill pass
- [ ] Update `run_dedup_pass()` to also group by `content_md_hash` in addition to `content_hash`
- [ ] Re-run dedup after backfilling — ~2,006 Enterprise pages (~56%) have identical content to Cloud
- [ ] Sections most affected: `search` (673), `alert-and-respond` (272), `spl-search-reference` (203)

### ES crawl failure investigation
- [ ] Run: `sqlite3 data/splunk_docs.db "SELECT url, error FROM crawl_state WHERE status='failed';"`
- [ ] Determine if persistent failures are dead pages (404) or transient errors

---

## ⚫ Priority 3 — Future / optional

- [ ] **'Dead' URL status for permanent 404s** — currently URLs that 404 are stored as `status='failed'` and retried on every run. Adding a `'dead'` status (set when HTTP 404 is received) would exclude those URLs from `get_failed_urls()` and `get_visited_urls()`, stopping them being retried forever.
- [ ] **Weekly full re-fetch for content change detection** — currently incremental mode skips pages already in `crawl_state` as 'fetched', so updated docs are never re-indexed. Options:
  - Use sitemap `<lastmod>` in normal (non-`--full`) mode: re-queue pages where `lastmod > last_crawl_timestamp` (already fetched from sitemap; just need to remove the `if full and` guard in `crawler.py`). Works for Lantern; no help for sources without sitemaps.
  - Add a `--rehash` mode: re-fetch all pages and compare HTML hash, re-extract only on change.
  - Simplest: run weekly cron with `--full` but skip re-embedding when hash unchanged.
- [ ] **SPL examples library** — `spl_examples` table + `search_spl` MCP tool (schema stub already in `db.py`)
- [ ] **Add ITSI, SOAR, Observability** — most-requested missing products
- [ ] **splunk-setup version selection UI Item 9** — ✅ Already done

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
- `crawl-and-release.yml`: release body now lists all 9 sources including Enterprise 10.1 and Cloud 10.2

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
