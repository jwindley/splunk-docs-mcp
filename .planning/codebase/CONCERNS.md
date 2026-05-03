# Codebase Concerns

**Analysis Date:** 2026-05-03

---

## Tech Debt

### _DEDUP_PRIORITY list is out of sync with PHASE1_SOURCES — HIGH

- **Issue:** `_DEDUP_PRIORITY` in `src/splunk_docs_mcp/db.py` (line 375) contains two ghost entries (`splunk-enterprise-10-1`, `splunk-cloud-10-2`) that were removed from active sources but never cleaned up. It also omits the three SOAR sources (`soar-on-premises`, `soar-on-premises-8-4-0`, `soar-cloud`) that were added later.
- **Files:** `src/splunk_docs_mcp/db.py:375–385`
- **Impact:** SOAR documents are never marked `is_duplicate=1` when identical content exists in another source. Cross-source dedup is silently skipped for SOAR. Any future SOAR-only duplicate content will appear multiple times in unfiltered search results.
- **Fix approach:** Keep `_DEDUP_PRIORITY` in sync with `PHASE1_SOURCES`. Consider deriving it dynamically from `config.py` to prevent future drift, or add a test that asserts both lists contain the same source IDs.

---

### Outdated corpus size comment in db.py — LOW

- **Issue:** `src/splunk_docs_mcp/db.py` (lines 23, 1053) states "fast enough for the current corpus size (~1 000 documents)". Actual corpus is ~9,183 known pages plus chunk rows, putting the real embedding count significantly higher.
- **Files:** `src/splunk_docs_mcp/db.py:23`, `src/splunk_docs_mcp/db.py:1053`
- **Impact:** Misleads future contributors about the actual scale. The math is still fine at current sizes but the comment provides false reassurance.
- **Fix approach:** Update comments to reflect the actual corpus size (~9,000+ pages, potentially 20,000+ rows with chunks).

---

### Hardcoded repository owner in setup.py — MEDIUM

- **Issue:** `src/splunk_docs_mcp/setup.py` (line 18) hardcodes `https://api.github.com/repos/jwindley/splunk-docs-mcp/releases/latest`. This URL is baked in and not configurable.
- **Files:** `src/splunk_docs_mcp/setup.py:18`
- **Impact:** If the repo is forked, transferred, or renamed, all existing installs silently break — users running `uv run splunk-setup` get a 404 with no useful guidance.
- **Fix approach:** Accept this as intentional for a personal project, or expose it as a CLI flag (`--releases-url`) or environment variable for forkability.

---

### `assert` in production code path — LOW

- **Issue:** `src/splunk_docs_mcp/crawler.py` (line 412) uses a bare `assert source.sitemap_url` inside `_fetch_sitemap_urls()`. Python's `-O` flag strips asserts; the function would then attempt `client.get(None)` and raise an obscure `TypeError`.
- **Files:** `src/splunk_docs_mcp/crawler.py:412`
- **Impact:** Benign in normal usage because the function is only called when `source.sitemap_url` is truthy (line 131 in `crawl_source`). Fragile if the call site changes.
- **Fix approach:** Replace with `if not source.sitemap_url: return []`.

---

### `check_same_thread=False` with a shared asyncio connection — MEDIUM

- **Issue:** `src/splunk_docs_mcp/db.py:55` opens the SQLite connection with `check_same_thread=False`. The crawler uses a per-crawl `asyncio.Lock` (`conn_lock`) to serialise all DB writes. However, the MCP server reuses a single module-level connection (`_db` in `server.py`) across all tool calls. FastMCP's request dispatch model is not documented as single-threaded.
- **Files:** `src/splunk_docs_mcp/db.py:55`, `src/splunk_docs_mcp/server.py:90`
- **Impact:** If FastMCP ever dispatches concurrent tool calls on different threads (e.g. thread pool executor), concurrent reads could interleave with a write. In practice the MCP server is read-only at runtime, making this low-risk today.
- **Fix approach:** Verify FastMCP's threading model. If concurrent dispatch is possible, use `threading.Lock` around writes or use `sqlite3.connect()` per-request for the server.

---

### `browse_section` does not filter `is_duplicate` rows — LOW

- **Issue:** `db.browse_section()` (`src/splunk_docs_mcp/db.py:836–860`) lists pages by `section` and `source` but does not filter on `is_duplicate = 0`. The function is always called with an explicit `source=`, so duplicates from a different source won't appear. However, if a source contains internal duplicates (same content at two URLs), both would be listed.
- **Files:** `src/splunk_docs_mcp/db.py:836`
- **Impact:** Low in practice because dedup operates across sources, not within a single source. Noted as a conceptual inconsistency — `search_docs` applies the filter but `browse_section` does not.
- **Fix approach:** Add `AND is_duplicate = 0` to the `browse_section` query for consistency.

---

## Scalability Concerns

### In-memory embedding matrix — MEDIUM

- **Issue:** `server.py` (line 92) loads the entire embedding matrix into RAM at startup via `get_all_embeddings()`. At ~9,000 pages plus chunk rows (estimate: 15,000–25,000 total rows), this is approximately 23–38 MB of float32 data — acceptable now. Adding ITSI, Observability, or more n-1 versions could push this to 80–100 MB+.
- **Files:** `src/splunk_docs_mcp/server.py:92`, `src/splunk_docs_mcp/db.py:935–979`
- **Impact:** Memory grows linearly with corpus size. The whole matrix is rebuilt in `get_all_embeddings()` every server startup but never refreshed during a session. New embeddings from a re-crawl are invisible until the server restarts.
- **Fix approach:** For current scale, this is fine. Long-term: consider ANN indexes (FAISS, sqlite-vec) if corpus exceeds 50,000 rows or latency becomes measurable.

---

### SQLite single-file DB with large BLOBs — MEDIUM

- **Issue:** The merged `splunk_docs.db` stores all embeddings (384-dim float32 BLOBs) alongside full Markdown content. At ~9,000+ pages with chunk rows, the DB is large. SQLite performs well as a single-reader single-writer system but the file size and BLOB scanning (during `get_all_embeddings`) creates a cold-start cost.
- **Files:** `src/splunk_docs_mcp/db.py`, `data/splunk_docs.db` (gitignored)
- **Impact:** The `get_all_embeddings()` full scan at server startup takes time proportional to total embedding BLOB bytes. Users with slow disks or cloud-mounted filesystems will see longer startup times.
- **Fix approach:** Acceptable at current scale. Potential optimisation: store embeddings in a separate `embeddings` table so the main document scan and embedding scan can be tuned independently.

---

### Embedding matrix is never refreshed during server lifetime — LOW

- **Issue:** `_embed_matrix` and `_embed_rows` (loaded at `server.py:92`) are module-level globals, populated once at startup. If the DB is updated by a crawl while the server is running, new embeddings are invisible to `search_docs_semantic` until the server process is restarted.
- **Files:** `src/splunk_docs_mcp/server.py:92–93`
- **Impact:** Expected behaviour for a local tool. Users running a crawl must restart the MCP server. No automatic staleness signal is shown in tool output.
- **Fix approach:** Document this restart requirement clearly (already implied but not explicit). Optionally add a `get_index_info` field showing `embedding_matrix_loaded_at` to make the staleness visible.

---

## Security Considerations

### No authentication on MCP server — LOW

- **Issue:** The MCP server runs over stdio with no authentication. Any process that can launch `uv run splunk-mcp` can call any tool.
- **Files:** `src/splunk_docs_mcp/server.py`
- **Impact:** Acceptable for a local development tool. Would be a concern if exposed over a network transport (e.g. SSE). The current stdio transport is local-only by design.
- **Current mitigation:** stdio transport; no network listener.
- **Recommendations:** If SSE/network transport is ever added, add token-based auth.

---

### External HTTP crawling without robots.txt auto-parsing — LOW

- **Issue:** `robots.txt` compliance is implemented manually: `crawl_delay` and `blocked_path_prefixes` are hardcoded per source in `config.py`. There is no automated `robots.txt` fetching or re-checking. If `help.splunk.com` or `lantern.splunk.com` update their `robots.txt`, the crawler won't notice.
- **Files:** `src/splunk_docs_mcp/config.py:141–157`
- **Impact:** Potential ToS violation if robots rules change. Low probability but silent.
- **Current mitigation:** User-Agent identifies the crawler honestly (`splunk-docs-mcp-crawler/0.1 (local knowledge base indexer; not for commercial use)`). Crawl delays are respected via `crawl_delay` and `max_concurrency`.
- **Recommendations:** Periodically verify `robots.txt` rules for each source match the hardcoded `blocked_path_prefixes`. Consider adding a one-time robots.txt fetch at crawl start for validation.

---

### XML parsing of untrusted sitemap — LOW

- **Issue:** `crawler.py:_fetch_sitemap_urls()` (line 396) parses the sitemap using `xml.etree.ElementTree`, which is vulnerable to XML entity expansion attacks (billion laughs attack). The sitemap is fetched from `https://lantern.splunk.com/sitemap.xml` — a trusted source in practice, but the parser is unprotected.
- **Files:** `src/splunk_docs_mcp/crawler.py:425`
- **Impact:** Minimal real-world risk since the sitemap URL is hardcoded to a trusted host. A MITM attacker could serve a malicious XML payload.
- **Fix approach:** Use `defusedxml` library for sitemap parsing, or apply `xml.etree.ElementTree` with an explicit entity limit.

---

## Operational Concerns

### GitHub Actions crawl runs `--full` every week — MEDIUM

- **Issue:** `crawl-and-release.yml` passes `--full` to every crawl job (lines `Crawl ${{ matrix.source }}`). This re-crawls and re-embeds every page from scratch every Sunday, even if content hasn't changed.
- **Files:** `.github/workflows/crawl-and-release.yml`
- **Impact:** 240-minute per-job timeout; full re-crawl of ~9,000 pages takes hours. Wastes server time and external HTTP bandwidth. The incremental hash-skip logic in the crawler (`content_hash` comparison) exists precisely to avoid this, but `--full` bypasses it.
- **Fix approach:** Consider removing `--full` from the GHA workflow for established sources and only using it on the first run or when crawler logic changes. The DB cache (`actions/cache`) already provides persistence between runs.

---

### GitHub Actions uses per-source DB cache without TTL invalidation — LOW

- **Issue:** The per-source DB cache key uses `${{ github.run_id }}` for the save key and no TTL for the restore key (`restore-keys: splunk-db-{source}-`). The cache restore can return a DB from an older run that may contain stale content.
- **Files:** `.github/workflows/crawl-and-release.yml`
- **Impact:** Low impact because `--full` re-crawls everything anyway. If `--full` is ever removed from the workflow, stale cache entries could prevent new pages from being indexed.
- **Fix approach:** Acceptable with `--full`. If incremental mode is enabled later, add a weekly or monthly cache TTL.

---

### Release assets include full merged DB (~hundreds of MB) — LOW

- **Issue:** Every weekly release publishes `splunk_docs.db` (full merged index) plus 10 per-source DBs. At current scale the merged DB is likely 300–600 MB. GitHub Releases has a 2 GB per-asset limit but large assets cause slow downloads and inflate storage.
- **Files:** `.github/workflows/crawl-and-release.yml` (publish step)
- **Impact:** Users who run `splunk-setup --all` must download the combined size of all per-source DBs. The per-source design mitigates this for selective installs.
- **Fix approach:** Already mitigated by per-source selective download. The monolithic `splunk_docs.db` could potentially be dropped from the release once the per-source workflow is mature.

---

### GHA jobs use `continue-on-error: true` at the job level — MEDIUM

- **Issue:** Both `crawl` and `crawl-derived` jobs use `continue-on-error: true`. A job failure is silently absorbed; the release publishes with whatever sources succeeded. The merge step logs warnings for missing DBs but the release is still published.
- **Files:** `.github/workflows/crawl-and-release.yml:11,53`
- **Impact:** A complete crawl failure for a high-value source (e.g. `splunk-enterprise`) would result in a release missing that source with no alert. Users who downloaded a full DB would get an outdated index silently.
- **Fix approach:** Add a manifest validation step that fails the release if any primary source (non-n-1) is missing. Or add GitHub Actions Slack/email notification on job failure.

---

## Missing Functionality Gaps

### ITSI and Observability not indexed — HIGH

- **Issue:** Splunk IT Service Intelligence (ITSI) and Splunk Observability Cloud are listed as "most-requested missing products" in `TODO.md` but not yet added.
- **Files:** `src/splunk_docs_mcp/config.py` (no entries for these products), `TODO.md`
- **Impact:** Users asking Claude about ITSI or Observability get no documentation coverage — the exact hallucination problem the tool is designed to prevent.
- **Fix approach:** Add `CrawlSource` entries to `PHASE1_SOURCES` in `config.py`. Identify seed URLs and `url_prefix` for each. No other code changes required.

---

### SPL examples library not built — MEDIUM

- **Issue:** `db.py` contains a commented-out `spl_examples` table schema stub (lines 135–149). The `search_spl` MCP tool described in `CLAUDE.md` and `TODO.md` does not exist.
- **Files:** `src/splunk_docs_mcp/db.py:135–149`, `TODO.md`
- **Impact:** No SPL example search capability. Users writing SPL queries get no curated example guidance from the MCP server.
- **Fix approach:** Uncomment the schema stub, create a curated JSON dataset of SPL examples, add an ingestion script, and implement the `search_spl` tool in `server.py`.

---

### No Splunk Enterprise n-1 (10.1) coverage — MEDIUM

- **Issue:** `splunk-enterprise-10-1` and `splunk-cloud-10-2` were dropped from the crawl matrix (`TODO.md`: "Enterprise 10.1 and Cloud 10.2 dropped… seeding problem unsolvable without major rework"). These ghost entries remain in `_DEDUP_PRIORITY` in `db.py` (see tech debt item above).
- **Files:** `src/splunk_docs_mcp/db.py:383–384`
- **Impact:** No n-1 coverage for Splunk Enterprise or Splunk Cloud Platform. Users on 10.1/10.2 Cloud get current-version docs only. The dropped source IDs in `_DEDUP_PRIORITY` are dead entries.
- **Fix approach:** Either solve the seeding problem (investigate sitemap-based discovery for enterprise/cloud n-1) or remove the dead entries from `_DEDUP_PRIORITY`.

---

## Test Coverage Gaps

### No tests for DB layer — HIGH

- **Issue:** `src/splunk_docs_mcp/db.py` (1,104 lines, the largest file) has zero test coverage. None of the 36 existing tests touch `upsert_document`, `run_dedup_pass`, `run_version_merge_pass`, `search_docs`, `get_page`, or `merge_source_db`.
- **Files:** `tests/` (only `test_extractor.py` and `test_crawler.py` exist)
- **Impact:** The most complex and critical code — deduplication logic, version merge pass, FTS5 search, chunk assembly — is entirely untested. Regressions here would be invisible.
- **Fix approach:** Add `tests/test_db.py` with in-memory SQLite fixtures. Priority tests: `run_dedup_pass` correctness, `run_version_merge_pass` with matching/non-matching content, `search_docs` with/without version filter, `get_page` chunk reassembly.

---

### No tests for the MCP server tools — HIGH

- **Issue:** `src/splunk_docs_mcp/server.py` (479 lines) has no tests. Tool parameter validation, source validation, and the error response paths are untested.
- **Files:** `src/splunk_docs_mcp/server.py`, `tests/`
- **Impact:** Breaking changes to tool signatures or response formats would not be caught by CI.
- **Fix approach:** Add `tests/test_server.py`. Use a test DB fixture. Test each of the 6 MCP tools with valid and invalid inputs. Mock the embedding model to avoid ML dependencies in CI.

---

### No integration tests for the crawl pipeline — MEDIUM

- **Issue:** The full pipeline (crawl → chunk → embed → dedup) has no end-to-end test. `cli.py`, `crawler.py`, and `extractor.py` are only tested at the unit level (URL parsing, normalisation). The interaction between these stages is untested.
- **Files:** `src/splunk_docs_mcp/cli.py`, `tests/`
- **Impact:** A regression in how chunks are created and then queried, or in how `_embed_pass` interacts with `_chunk_pass`, would not be caught automatically.
- **Fix approach:** Add an integration test that crawls a small number of pages from a local HTTP fixture (or mocked `httpx`), runs the full pipeline against an in-memory DB, and asserts search results are retrievable.

---

### No tests for merge.py — MEDIUM

- **Issue:** `src/splunk_docs_mcp/merge.py` (272 lines) — including `merge_dbs()`, `export_sources()`, and `_export_source_db()` — has no tests.
- **Files:** `src/splunk_docs_mcp/merge.py`, `tests/`
- **Impact:** The merge pipeline is the aggregation step run by GitHub Actions before every release. Silent regressions here could produce malformed or incomplete release DBs.
- **Fix approach:** Add `tests/test_merge.py`. Test `merge_dbs()` with two small per-source DBs and assert the merged DB has correct row counts, FTS5 is functional, and the version merge pass runs.

---

## Dependency Risks

### `sentence-transformers` model downloaded on first run — MEDIUM

- **Issue:** On a fresh install, `uv run splunk-crawl` (embed pass) and `uv run splunk-mcp` both trigger automatic download of `all-MiniLM-L6-v2` from Hugging Face Hub (`~/.cache/torch/sentence_transformers`). This is a ~90 MB download with no user-facing progress indication in the MCP server startup path.
- **Files:** `src/splunk_docs_mcp/server.py:67`, `src/splunk_docs_mcp/cli.py:337`
- **Impact:** First-run `splunk-mcp` startup silently blocks for 30–90 seconds while the model downloads. Users may believe the server has hung.
- **Fix approach:** Add a clear log message before the `SentenceTransformer(...)` call indicating the model is being downloaded. Optionally check for the cached model path before loading and log a warning if the download will occur.

---

### `mcp>=1.0.0` broad version pin — LOW

- **Issue:** `pyproject.toml` pins `mcp>=1.0.0` with no upper bound. The Anthropic MCP SDK is actively evolving; a major breaking version bump (2.x) would be picked up automatically on fresh installs.
- **Files:** `pyproject.toml`
- **Impact:** Low today; MCP is relatively stable. Breaking change in MCP SDK transport or tool registration API would cause silent startup failure.
- **Fix approach:** Consider a tighter pin (`mcp>=1.0.0,<2.0`) and update intentionally when new major versions release.

---

### `trafilatura` extractor is a black box — LOW

- **Issue:** `trafilatura` is the primary HTML-to-Markdown extractor with `favor_recall=True`. Its output quality depends on its internal heuristics, which can change across minor versions. There are no regression tests for extraction quality.
- **Files:** `src/splunk_docs_mcp/extractor.py:106–115`
- **Impact:** A `trafilatura` upgrade could silently degrade extraction quality (shorter content, missing tables) on `help.splunk.com` pages. The 100-char minimum threshold provides a floor but does not detect quality regressions above it.
- **Fix approach:** Pin `trafilatura` to a minor version range in `pyproject.toml`. Consider a small golden-file test asserting that known pages extract to expected Markdown.

---

### No `uv.lock` pinning for transitive ML dependencies — LOW

- **Issue:** `uv.lock` pins exact versions for reproducible installs. However, `sentence-transformers` depends on PyTorch (`torch`) which is not listed in `pyproject.toml` directly. PyTorch version resolution changes frequently and can affect embedding quality.
- **Files:** `pyproject.toml`, `uv.lock`
- **Impact:** A fresh environment could pull a different PyTorch version than the one used to generate the stored embeddings. In practice `all-MiniLM-L6-v2` is stable across PyTorch versions, but this is not guaranteed.
- **Fix approach:** `uv.lock` should handle this via lockfile resolution. Verify the lock includes pinned `torch` versions on all relevant platforms.

---

## Fragile Areas

### URL derivation for n-1 sources depends on version string in URL path — MEDIUM

- **Issue:** `cli.py:_run()` (line 170) derives older-version URLs by replacing `/{parent.version}/` with `/{source.version}/` via simple string substitution. This works only when the version appears as an isolated path segment (e.g. `.../8.5/...` → `.../8.4/...`). SOAR URLs include multi-part version strings (`8.5.0`, `8.4.0`); the pattern works for these today but is brittle.
- **Files:** `src/splunk_docs_mcp/cli.py:169–172`
- **Impact:** If a source version string (e.g. `8.5.0`) appears in non-version path contexts (e.g. a page titled "8.5.0 Release Notes"), the substitution produces incorrect derived URLs. Current sources are not affected but this is a hidden assumption.
- **Fix approach:** The derivation is already filtered by `_is_target_url()` which validates version segments in paths, providing a second layer of defence. The risk is low but fragile — document the assumption explicitly.

---

### Chunk URL format `{parent_url}#chunk-{i}` relies on fragment convention — LOW

- **Issue:** Chunk rows are stored with synthetic URLs of the form `{parent_url}#chunk-{i}` (e.g. `https://help.splunk.com/.../page#chunk-2`). The `#chunk-` convention is not validated; if a real page URL happened to contain `#chunk-` as a fragment, `get_page()` would misinterpret it.
- **Files:** `src/splunk_docs_mcp/db.py:649`, `src/splunk_docs_mcp/db.py:785–789`
- **Impact:** Negligible in practice because `_normalise_url()` strips all fragments before storing real page URLs. Chunk URLs are only inserted by `chunk_document()`, not from live crawled pages.
- **Fix approach:** No action needed. The invariant is maintained by `_normalise_url()`.

---

### `_safe_name()` in extractor can produce collisions — LOW

- **Issue:** `extractor.py:_safe_name()` (line 241) replaces all non-`\w\-\.` characters with `-`, then strips leading/trailing `-`. Two different URL segments that differ only in special characters would produce the same filename, causing silent file overwrites in `data/docs/`.
- **Files:** `src/splunk_docs_mcp/extractor.py:241–242`
- **Impact:** Files are gitignored and regenerated from the DB. The file write is informational (Markdown export); the canonical data is in SQLite. A filename collision produces a silently overwritten Markdown file but the DB row is correct (keyed by URL, not filename).
- **Fix approach:** Low priority. If deduplication of Markdown files matters, add a counter suffix on collision.

---

## Known Limitations and Edge Cases

### SHA-256 incremental skip uses raw HTML hash, not content hash — LOW

- **Issue:** The incremental re-crawl skip (`crawler.py:360`) compares `new_hash` (SHA-256 of raw HTML) against the stored `content_hash`. Any change to the HTML — including navigation updates, sidebar changes, or script tag modifications — invalidates the skip even if the actual article text is unchanged.
- **Files:** `src/splunk_docs_mcp/crawler.py:355–363`, `src/splunk_docs_mcp/db.py:82`
- **Impact:** More pages are re-fetched and re-extracted on incremental crawls than strictly necessary when `help.splunk.com` updates its navigation structure. In practice `--full` is used in CI anyway, so this is only relevant for local incremental runs.
- **Fix approach:** Could compare `content_md_hash` (extracted Markdown hash) instead after extraction to skip re-embedding unchanged content. Partially mitigated by the embedding reuse via `content_hash` in `cli.py:_embed_pass()`.

---

### Version filter in search bypasses `is_duplicate` — expected but surprising — LOW

- **Issue:** When `version=` is passed to `search_docs()` or `search_docs_semantic_from_matrix()`, the `is_duplicate = 0` filter is deliberately skipped. This is documented in `CLAUDE.md` but is a non-obvious behaviour that surprises contributors.
- **Files:** `src/splunk_docs_mcp/db.py:723–726`
- **Impact:** Version-targeted searches return results that include content flagged as duplicate of a higher-priority source. The intent is correct (find all pages for that version) but the coupling between `version=` and dedup bypass is an implicit convention.
- **Fix approach:** Add an inline comment on the `else` branch at line 724 explaining *why* the dedup filter is bypassed when `version` is set (currently only the outer comment at line 714 explains it).

---

### Sitemap only covers ~800 of Lantern's 1,284 pages — LOW

- **Issue:** `config.py` comments note the Lantern sitemap covers ~800/1,284 pages; BFS discovers the remaining ~484. The sitemap-based `<lastmod>` skip optimisation only works for the ~800 sitemap-listed pages.
- **Files:** `src/splunk_docs_mcp/config.py:337–338`
- **Impact:** ~484 Lantern pages are always re-crawled via BFS even when unchanged, since they lack sitemap `<lastmod>` entries. Combined with Lantern's 5-second crawl delay, this extends Lantern crawl time significantly.
- **Fix approach:** Accepted limitation. The `content_hash` skip at the HTTP response level still prevents re-extraction and re-embedding of unchanged BFS-discovered pages.

---

*Concerns audit: 2026-05-03*
