# TODO — splunk-docs-mcp

_Last updated: 2026-04-19_

---


## 🟡 Priority 3 — Public release distribution (Phase 2, after POC is done)

The intended distribution model for public GitHub use:

- [ ] **`src/splunk_docs_mcp/setup.py`** — `splunk-setup` command (download pre-built DB from GitHub Releases)
- [ ] **`pyproject.toml`** — add `splunk-setup = "splunk_docs_mcp.setup:main"` to `[project.scripts]`
- [ ] **`.github/workflows/crawl-and-release.yml`** — weekly cron + `workflow_dispatch`; crawl + publish DB as release asset
- [ ] **README update** — replace crawl step with `uv run splunk-setup`; add data freshness note

See PLAN.md "Phase 2" section for full implementation details.

---

## ⚪ Priority 5 — Future / optional (no current need)

- [ ] **Cross-version embedding reuse** — when crawling a new version (e.g. ES 8.5 → 8.6), many pages are identical. Before generating an embedding for a new URL, check if any existing document has the same `content_hash` and copy that embedding instead of re-encoding. Would make the embed pass near-instant for unchanged pages on a version upgrade. Only worth building once multi-version crawling is active.

---

## 🟢 Priority 4 — Nice-to-haves (no blocker)

- [ ] Investigate the 2 ES crawl failures (was Priority 3) — `sqlite3 data/splunk_docs.db "SELECT url, error FROM crawl_state WHERE status='failed';"` — check if they're meaningful missing pages or just transient 404s
- [ ] Add `--delay-jitter` flag to crawler to randomise delay (reduces rate-limiting pattern predictability)
- [ ] Add `pytest` tests for `parse_url_metadata()` covering ES and admin-manual URL patterns
- [ ] Add `pytest` tests for `_section_from_url()` with redirect-destination URLs
- [ ] Add `pytest` tests for `_normalise_url()` edge cases (fragments, query strings, mailto)
- [ ] Add `pytest` test for `_is_target_url()` version-filter logic (ES 8.0 rejected, ES 8.5 allowed, admin-manual unaffected)

---

## ✅ Done

- [x] **Vector/semantic search** — `embedding BLOB` on `documents` table; all-MiniLM-L6-v2 via sentence-transformers; post-crawl embedding pass in `cli.py`; `search_docs_semantic` MCP tool (2026-04-19). Re-run `uv run splunk-crawl` to populate embeddings for existing DBs.
- [x] **Eager model loading** — `SentenceTransformer` instantiated at module level in `server.py`; eliminates 6 s first-call delay (2026-04-19)
- [x] **MCP instructions decision tree** — `FastMCP(instructions=...)` rewritten as explicit 5-branch decision tree with hard call-count limits; targets 3–4 tool calls per question (2026-04-19)
- [x] End-to-end MCP tool test — all 5 tools verified against live DB (2026-04-19); DB queries 5–38 ms
- [x] `README.md` — setup, crawl, MCP config, tool reference, dev tips
- [x] Timing logging in `server.py` — each tool call logs duration in ms to stderr
- [x] `pyproject.toml` with all dependencies and entry points
- [x] `.gitignore` (Python-appropriate, replaced Node.js template)
- [x] `.python-version` (`3.12`)
- [x] `config.py` — `CrawlSource` dataclass, `PHASE1_SOURCES`, `SOURCES_BY_ID`
- [x] `db.py` — full schema, FTS5 content table + triggers, all query helpers
- [x] `extractor.py` — trafilatura + markdownify fallback, URL metadata parsing, file writer
- [x] `cli.py` — argparse with `--section`, `--full`, `--verbose` flags
- [x] `server.py` — FastMCP app with 5 registered tools
- [x] `data/.gitkeep`, `data/docs/.gitkeep`
- [x] `CLAUDE.md`, `PLAN.md`, `TODO.md` context files
- [x] **Bug fix:** Crawler redirect URL bug — `_process_url` now uses `str(resp.url)` as `urljoin` base so relative hrefs in redirected pages resolve correctly
- [x] **Bug fix:** Crawler version filter — `_is_target_url` now rejects URLs with version segments that don't match `source.version`, preventing ES 8.0–8.4 pages being indexed
- [x] Full crawl verified: 743 ES pages + 216 admin-manual pages, all 6 ES sections populated
