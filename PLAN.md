# Build Plan — splunk-docs-mcp

_Last updated: 2026-04-20 (Phase 3 — Items 1 and 7 complete)_

---

## Current Status

**Phase 1 and Phase 2 are complete.** All crawls done (~8,946 pages across 5 sources), all 6 tools working, chunking and embeddings applied, and public distribution via GitHub Releases implemented.

---

## What Has Been Built

| File | Status | Notes |
|------|--------|-------|
| `pyproject.toml` | ✅ Done | Deps, entry points (`splunk-mcp`, `splunk-crawl`, `splunk-setup`) |
| `.gitignore` | ✅ Done | Python-appropriate; `data/docs/` and `data/*.db` gitignored |
| `.python-version` | ✅ Done | `3.12` |
| `src/splunk_docs_mcp/__init__.py` | ✅ Done | Empty package init |
| `src/splunk_docs_mcp/config.py` | ✅ Done | `CrawlSource` dataclass (+ `crawl_delay`, `max_concurrency`, `blocked_path_prefixes`), `PHASE1_SOURCES` (5 active sources), `SOURCES_BY_ID`, paths, headers |
| `src/splunk_docs_mcp/db.py` | ✅ Done | Schema, connection factory, FTS5 + triggers, all query helpers, embedding helpers |
| `src/splunk_docs_mcp/extractor.py` | ✅ Done | trafilatura primary, BS4+markdownify fallback, `parse_url_metadata`, `write_markdown_file` |
| `src/splunk_docs_mcp/server.py` | ✅ Done | FastMCP app + 6 tools; eager model load at startup; explicit decision-tree instructions |
| `src/splunk_docs_mcp/cli.py` | ✅ Done | argparse with `--sources`, `--section`, `--concurrency`, `--delay`, `--full`, `--db`, `--docs-dir`, `--verbose`; post-crawl embedding pass |
| `src/splunk_docs_mcp/crawler.py` | ✅ Done | BFS crawler; per-source `crawl_delay`, `max_concurrency`, `blocked_path_prefixes`; redirect-aware link extraction; version-segment filtering |
| `data/.gitkeep` | ✅ Done | |
| `data/docs/.gitkeep` | ✅ Done | |
| `CLAUDE.md` / `PLAN.md` / `TODO.md` | ✅ Done | Session context files |
| `README.md` | ✅ Done | Setup and usage docs for end users; rewritten for Phase 2 (splunk-setup flow) |
| `src/splunk_docs_mcp/setup.py` | ✅ Done | `splunk-setup` command; streams download from GitHub Releases with progress |
| `.github/workflows/crawl-and-release.yml` | ✅ Done | Weekly cron + workflow_dispatch; crawl all sources + publish DB as release asset |

---

## What Works

- **MCP server:** `uv run splunk-mcp` starts on stdio, all 6 tools registered and responding correctly
- **BM25 keyword search:** `search_docs` — FTS5, BM25 ranked, title weighted 10×, snippets; 5–38 ms
- **Semantic search:** `search_docs_semantic` — all-MiniLM-L6-v2 embeddings, in-process cosine similarity; model eagerly loaded at startup (no first-call penalty)
- **Crawl — enterprise-security:** 743 pages indexed, all 6 ES sections populated, ES 8.5 only (version filter working)
- **Crawl — admin-manual:** 216 pages indexed
- **Crawl — lantern (test section):** 92 pages indexed (`Splunk_Success_Framework`); rate limiting (5 s/req, concurrency=1) working correctly; `robots.txt` blocked paths respected
- **Incremental re-crawl:** unchanged pages skipped via SHA-256 hash comparison
- **SQLite WAL mode:** MCP server can read while crawler writes
- **Embeddings:** generated post-crawl for all indexed pages; stored as 384-dim float32 BLOBs
- **`--section` dev flag:** limits crawl to one section for fast pipeline testing

---

## What Is Incomplete

| Item | Status |
|------|--------|
| Full Lantern crawl | ✅ Done — 1,284 pages, 1,192 embeddings generated |
| Full `splunk-enterprise` crawl | ✅ Done — 3,513 pages |
| Full `splunk-cloud` crawl | ✅ Done — 2,658 pages |
| Phase 2 — public distribution via GitHub Releases | ✅ Done (2026-04-20) |

---

## Bugs Fixed (2026-04-18) — both in production code

### Bug 1 — Crawler used pre-redirect URL as urljoin base (`crawler.py`)
**Symptom:** All ES sections except `user-guide` had only 1 page in the DB — the seed URL itself.  
**Root cause:** Section seed URLs redirect to a deeper page. The HTML there uses relative hrefs designed to be resolved against the redirect destination, but `_process_url` was passing the original pre-redirect URL to `urljoin()`, producing doubled/malformed paths that 404.  
**Fix:** Capture `final_url = _normalise_url(str(resp.url)) or url` after the response and pass it to `_extract_links()` instead of `url`. Also pre-mark `final_url` as visited to prevent double-processing.

### Bug 2 — Version filter missing; crawler indexed ES 8.0–8.4 alongside 8.5 (`crawler.py`)
**Symptom:** Crawl log showed fetches of `/install/8.0/`, `/administer/8.1/` etc. — wrong versions.  
**Root cause:** The `url_prefix` filter `splunk-enterprise-security-8/` matches all ES versions. Cross-version nav links in the HTML were being followed.  
**Fix:** In `_is_target_url()`, extract version-number path segments from the URL after the prefix. If any version segments are present and none match `source.version`, reject the URL.

---

## Crawl Results

```
[enterprise-security] stored=1275  (2026-04-19/20, full crawl)
[admin-manual]        stored=216   (2026-04-18)
[splunk-enterprise]   stored=3513  (2026-04-19/20, full crawl)
[splunk-cloud]        stored=2658  (2026-04-19/20, full crawl)
[lantern]             stored=1284  (2026-04-20, full crawl, 1192 embeddings generated)
TOTAL                 8946
```

Page counts from `SELECT source, COUNT(*) FROM documents GROUP BY source` on 2026-04-20.

---

## Fixes Applied (2026-04-20)

### Fix 1 — Document chunking
Documents over 8,000 characters are now split into 1,500-character overlapping chunks (200-char overlap) stored as separate rows in `documents` with `chunk_of = parent_url` and `chunk_index`.

- FTS5 and embeddings now index at chunk level → search surfaces the relevant section, not the whole document.
- Parent rows are marked `has_chunks = 1` and excluded from search queries.
- `get_page(url)` reassembles all chunks transparently; if called with a chunk URL it redirects to the parent.
- A new `_chunk_pass()` in `cli.py` runs after each crawl (before the embed pass). With `--full` it deletes and rebuilds all chunks.
- Schema additions: `has_chunks INTEGER DEFAULT 0`, `chunk_of TEXT`, `chunk_index INTEGER` + index on `chunk_of`. Added via `ALTER TABLE` migrations — safe for existing DBs.

### Fix 2 — Confidence signalling in tool descriptions
`search_docs` and `search_docs_semantic` docstrings now explicitly instruct Claude to state uncertainty when retrieved content does not directly address the question. The `FastMCP(instructions=...)` block now includes a mandatory CONFIDENCE AND UNCERTAINTY section with the same guidance. Also corrected stale `source=` option lists across all five tool parameter descriptions.

---

## Next Steps

See `TODO.md` Phase 3 for the full prioritised work queue.

---

## Phase 2 — Public release distribution ✅ Complete (2026-04-20)

- GitHub Actions weekly cron + `workflow_dispatch` publishes `splunk_docs.db` as a release asset
- `splunk-setup` CLI downloads the latest release asset; atomic write; progress bar
- User flow: `git clone` → `uv sync` → `uv run splunk-setup` → configure MCP → done

---

## Phase 3 — Improvements (planned 2026-04-20)

Ten improvements across four tiers. See `TODO.md` for subtask breakdown.

### Tier 1 — Foundational (no dependencies)
- **Item 10**: Add `crawled_at` date to `search_docs` and `search_docs_semantic` result dicts (`db.py`)
- **Item 4**: Exponential backoff retry (3 attempts, 2/4/8 s) in `_process_url()` (`crawler.py`)
- **Item 3**: Module-level embedding matrix cache in `server.py` loaded once at startup; source pre-filter via numpy boolean indexing. **Note**: restart MCP server after `splunk-crawl` to refresh semantic search index.

### Tier 2 — Quality (independent)
- **Item 8**: Smart chunking — heading → paragraph → character fallback in `_split_content_smart()`; `--rechunk` CLI flag (`db.py`, `cli.py`)
- **Item 2**: Lantern sitemap seeding — `sitemap_url` field on `CrawlSource`; `<lastmod>` pre-fetch skip; BFS fallback for sitemap-missing pages. Sitemap confirmed at `lantern.splunk.com/sitemap.xml` (~800 URLs, `<lastmod>` on all entries; sitemap is incomplete — BFS fallback covers remaining ~484 pages).

### Tier 3 — Scalability (item 6 before item 7; item 1 before item 7)
- **Item 6**: Embedding reuse via `content_hash` — index on `content_hash`; copy embedding from existing row with same hash before encoding (`db.py`, `cli.py`)
- **Item 1**: ✅ GHA matrix parallelisation — `merge.py` + `merge_source_db()` in `db.py` + `splunk-merge` entry point; GHA workflow rewritten with matrix (5 parallel jobs) + aggregation job; per-source DB caching + per-source export + `manifest.json`
- **Item 7**: ✅ Multi-version crawling — 4 new `CrawlSource` entries (ES 8.3/8.4, Enterprise 10.1, Cloud 10.2); `version` filter on `search_docs` + `search_docs_semantic`; instructions updated; GHA now 9-job matrix

### Tier 4 — Polish (requires items 1 and 7)
- **Item 5**: Cross-source deduplication — `is_duplicate INTEGER DEFAULT 0` column; `_dedup_pass()` in `cli.py`; suppress duplicate URLs from FTS and semantic search (`db.py`)
- **Item 9**: `splunk-setup` version selection UI — `manifest.json` schema; interactive CLI menu or `--all` flag; per-source DB download + merge; `--export-sources` flag on `splunk-merge` (not a separate entry point); backward-compat fallback to monolithic DB

### Future / Phase 4+
- **SPL examples library** — `spl_examples` table + `search_spl` MCP tool (schema stub already in `db.py`)
