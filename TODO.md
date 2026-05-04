# TODO — splunk-docs-mcp

_Last updated: 2026-05-04_

---

## 🔴 Immediate: verify 2026-05-04 crawl and tighten thresholds

### Context
All 15 jobs green on the 2026-05-04 manual run (first run with browser UA fix).
First ever successful crawl of `splunk-enterprise-n1` (10.0) and `splunk-cloud-n1` (10.2.2510).
78,838 total rows in merged DB.

### Crawl results (baseline page counts — update CLAUDE.md after verifying)

| Source | Crawled | In DB | Notes |
|--------|---------|-------|-------|
| enterprise-security | 730 | 779 | ES 8.5 |
| admin-manual | 288 | 288 | Config ref 10.2 |
| splunk-enterprise | 3,636 | 3,736 | Enterprise 10.2 |
| splunk-cloud | 2,683 | 2,912 | Cloud 10.3.2512 |
| soar-on-premises | 354 | 363 | SOAR On-Prem 8.5.0 |
| soar-cloud | 342 | 354 | SOAR Cloud |
| lantern | 1 (incremental) | 1,279 | Unchanged since last run |
| enterprise-security-n1 | 477 stored, 349 skipped | — | ES 8.4 |
| enterprise-security-n2 | 405 stored, 413 skipped | — | ES 8.3 |
| admin-manual-n1 | 501 stored, 288 skipped | — | Config ref 10.0 |
| soar-on-premises-n1 | 363 | — | SOAR On-Prem 8.4.0 |
| **splunk-enterprise-n1** | **3,785 stored, 440 skipped** | — | **Enterprise 10.0 — first run** |
| **splunk-cloud-n1** | **2,838 stored, 17 skipped** | — | **Cloud 10.2.2510 — first run** |

### Step 1 — Download and smoke-test the new DB
```bash
uv run splunk-setup   # select "all" or individual sources
```
Then start MCP server and run these queries via Claude:
- `get_index_info` — verify total pages, all 13 sources listed, DB size reasonable
- `search_docs("inputs.conf", version="10.0")` — should return Enterprise 10.0 results
- `search_docs("inputs.conf", version="10.2.2510")` — should return Cloud 10.2.2510 results
- `search_docs("correlation search", version="8.5")` — ES 8.5 results
- `search_docs("correlation search", version="8.4")` — ES 8.4 results (version_tags match)
- `search_docs("playbook", source="soar-cloud")` — SOAR Cloud results
- `search_docs_semantic("how to configure indexes")` — general semantic search

### Step 2 — Tighten SOAR page count thresholds in the workflow
Now that we have real SOAR page counts (soar-on-premises=363, soar-cloud=354),
update `.github/workflows/crawl-and-release.yml` verify step:
- `soar-on-premises`: 50 → **200**
- `soar-cloud`: 50 → **200**

### Step 3 — Update CLAUDE.md sources table
Update the "Active Crawl Sources" table with confirmed page counts for all sources,
especially the new n-1 sources.

---

## 🟢 Content expansion (Phase 5)

### Splunk REST API docs
- Splunk Enterprise and Cloud both have a REST API reference at a separate URL tree on help.splunk.com
- Identify the seed URL and `url_prefix` for the REST API reference (e.g. `help.splunk.com/en/splunk-enterprise/rest-api-reference/`)
- Add as a new `CrawlSource` — no other code changes needed
- Large and valuable: covers every endpoint, request/response params, auth methods
- Check robots.txt for `Crawl-delay` before adding

### Splunk SDK docs
- Splunk provides Python, JavaScript, and Java SDKs — docs live at `dev.splunk.com` or similar
- Investigate URL structure and robots.txt compliance before adding
- Python SDK is highest priority (most common for automation and custom apps)
- May need a separate `CrawlSource` with different `url_prefix` and `blocked_path_prefixes`
- Worth checking if `dev.splunk.com` allows crawling — may need to fall back to GitHub-hosted docs

---

## 🟡 Operational improvements

### Cross-source dedup: storage not just search (future)
- Currently `is_duplicate = 1` suppresses duplicates in search results but keeps both rows in the DB — so Enterprise and Cloud share a large overlap (~2,006+ pages) that is stored twice
- The version dedup (`version_tags`) is storage-efficient: it deletes the derived row and tags the parent
- A proper fix would apply the same delete+tag approach cross-source: delete the Cloud duplicate, update the Enterprise row's `version_tags` to include Cloud, and make `get_page(cloud_url)` redirect to the Enterprise row via `content_md_hash` lookup
- Impact grows as more n-1 sources are added (Enterprise 10.0 + Cloud 10.2.2510 will add more overlap)
- Not urgent — search quality is unaffected; this is a DB size / download size concern

---

## ⚫ Priority — Future / optional

- [ ] **Add ITSI, Observability** — most-requested missing products

---

## ✅ Done (2026-05-04) — Enterprise 10.0 and Cloud 10.2.2510 n-1 sources

- Previous attempts pre-dated `derive_from` — the mechanism that makes this possible was already used for ES 8.4/8.3
- Both products use version as an isolated path segment (`/10.2/`, `/10.3.2512/`) — identical URL substitution pattern
- Section hubs (e.g. `/administer/install-and-upgrade`) redirect to current-version content pages which have `<select id="version-select">`
- `_enterprise_source()` and `_cloud_source()` factory functions added to `config.py`; replace the old inline definitions
- `splunk-enterprise-n1: "10.0"` and `splunk-cloud-n1: "10.2.2510"` added to `versions.json`
- `splunk-discover-versions` now covers Enterprise (`/administer/install-and-upgrade`) and Cloud (`/administer/admin-manual`) version selectors
- GHA `crawl-derived` matrix and merge step updated; release body updated
- Note: no 10.1 exists for either product — Splunk skipped it; 10.0 is the true n-1

---

## ✅ Done (2026-05-04) — Tier 2: automatic version discovery

- `splunk-discover-versions` CLI added (`src/splunk_docs_mcp/discover.py`, entry point in `pyproject.toml`)
- All three versioned products use `<select id="version-select">` with `<option>` elements in static HTML — parsed with BS4
- `version_discovery_url: str | None = None` field added to `CrawlSource` dataclass
- Discovery URL set on primary sources: `enterprise-security` (section page), `admin-manual` (hub redirect), `soar-on-premises` (release-notes hub)
- Derived n-1/n-2 sources have no discovery URL — managed automatically via key naming (`{src}-n1`, `{src}-n2`)
- Null version support added to `config.py`: `_load_versions()` returns `str | None`; factories return `None` for null; `PHASE1_SOURCES` filters out `None`
- GHA: `discover-versions` job runs first (before `crawl`); commits updated `versions.json` back to repo; uploads artifact; crawl jobs download artifact to use discovered versions within same run
- `continue-on-error: true` on discover job — crawl falls back to repo's versions.json if discovery fails
- Site requires browser-like User-Agent and Accept headers; custom `_HEADERS` in `discover.py`
- `--dry-run` flag prints changes without writing

---

## ✅ Done (2026-05-03) — Phase 5: admin-manual n-1

### n-1 for admin-manual (config file reference) ✅
- Previous version is 10.0 (versions available: 10.2, 10.0, 9.4, 9.3… — no 10.1 published)
- Added `admin-manual-n1` CrawlSource in `config.py` with `derive_from="admin-manual"`
- Added to `crawl-derived` GHA matrix; merge step and release notes updated

---

## ✅ Done (2026-05-03) — Phase 4: Performance & UX improvements

### LRU result cache
- `@functools.lru_cache(maxsize=128)` on `_search_docs_cached`, `_search_docs_semantic_cached`, `_search_docs_hybrid_cached` in `server.py`; keyed on `(query, source, version, limit)`

### Hybrid search tool — `search_docs_hybrid`
- Runs BM25 + semantic in parallel via `ThreadPoolExecutor(max_workers=2)`; fuses results via RRF (k=60)
- Prefers BM25 result dict (has snippet) when a URL appears in both; strips individual `score`, returns `rrf_score`
- Added to decision tree as default first call for unknown topics; `search_docs`/`search_docs_semantic` demoted to targeted fallbacks

### Background model loading
- `SentenceTransformer` loaded in a daemon thread; `_model_ready = threading.Event()` signals readiness
- `search_docs_semantic` and `search_docs_hybrid` call `_model_ready.wait()` (no-op once set); server available immediately on startup
- Embedding matrix loading eliminated entirely (replaced by sqlite-vec)

### sqlite-vec ANN vector search
- `sqlite-vec` 0.1.9 added to dependencies; `vec0` virtual table (`vec_documents`) created in `init_db`
- `upsert_vec_embedding()` added to `db.py`; `_embed_pass` in `cli.py` populates `vec_documents` alongside `documents.embedding`
- `search_docs_semantic_vec()` replaces numpy matrix scan: fetches `limit*4` candidates via ANN, joins to `documents` for metadata + version/source filtering, deduplicates chunks→parent, returns cosine similarity (L2→cos conversion)
- One-time auto-migration in `init_db` copies existing `documents.embedding` BLOBs to `vec_documents`
- Eliminates 23-38MB in-process RAM matrix; `get_all_embeddings()` and `_embed_matrix` global removed from server.py

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
- `soar-on-premises` 8.5.0 (current) and `soar-on-premises-n1` 8.4.0 (n-1) added
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
