# TODO — splunk-docs-mcp

_Last updated: 2026-04-20_

---

## 🔴 Priority 1 — Pending full crawls (code is ready, just needs running)

- [x] **Full Lantern crawl** — 1,284 pages, 1,192 embeddings (2026-04-20)
- [x] **`splunk-enterprise` crawl** — 3,513 pages indexed
- [x] **`splunk-cloud` crawl** — 2,658 pages indexed

---

## 🔴 Priority 1 — Apply chunking + embeddings to existing DB

- [ ] **Run `uv run splunk-crawl --full`** — rebuilds chunks for all ~8,946 documents and re-embeds (chunked docs get chunk-level embeddings). Estimated runtime: hours for embedding pass.

---

## 🟡 Priority 2 — Public release distribution (Phase 2)

The intended distribution model for public GitHub use:

- [ ] **`src/splunk_docs_mcp/setup.py`** — `splunk-setup` command (download pre-built DB from GitHub Releases)
- [ ] **`pyproject.toml`** — add `splunk-setup = "splunk_docs_mcp.setup:main"` to `[project.scripts]`
- [ ] **`.github/workflows/crawl-and-release.yml`** — weekly cron + `workflow_dispatch`; crawl + publish DB as release asset
- [ ] **README update** — replace crawl step with `uv run splunk-setup`; add data freshness note

See PLAN.md "Phase 2" section for full implementation details.

---

## 🟢 Priority 3 — Nice-to-haves (no blocker)

- [ ] Investigate the 2 ES crawl failures — `sqlite3 data/splunk_docs.db "SELECT url, error FROM crawl_state WHERE status='failed';"` — check if they're meaningful missing pages or transient 404s
- [ ] Add `--delay-jitter` flag to crawler to randomise per-request delay (reduces rate-limiting pattern predictability)
- [ ] Add `pytest` tests for `parse_url_metadata()` covering ES, admin-manual, and Lantern URL patterns
- [ ] Add `pytest` tests for `_section_from_url()` with redirect-destination URLs
- [ ] Add `pytest` tests for `_normalise_url()` edge cases (fragments, query strings, mailto)
- [ ] Add `pytest` test for `_is_target_url()` version-filter logic (ES 8.0 rejected, ES 8.5 allowed, admin-manual and Lantern unaffected)

---

## ⚪ Priority 4 — Future / optional

- [ ] **SPL examples library** — `spl_examples` table + `search_spl` MCP tool (schema stub already in `db.py`)
- [ ] **Multi-version crawling** — add `version` filter parameter to `search_docs` (comment marks where); update `config.py` with additional version entries
- [ ] **Cross-version embedding reuse** — copy embeddings by `content_hash` when a new version shares pages with the old, avoiding re-encoding unchanged content

---

## ✅ Done

- [x] **`splunk-enterprise` full crawl** — 3,513 pages indexed (2026-04-19/20)
- [x] **`splunk-cloud` full crawl** — 2,658 pages indexed (2026-04-19/20)
- [x] **`enterprise-security` expanded** — 1,275 pages (was 743; full re-crawl captured more pages)
- [x] **Lantern source activated** — `CrawlSource` extended with `crawl_delay`, `max_concurrency`, `blocked_path_prefixes`; hardcoded `_BLOCKED_PREFIXES` moved from `crawler.py` to per-source config; Lantern test crawl passed (92 pages, 1 transient failure, 5 s/req rate limiting working) (2026-04-19)
- [x] **MCP instructions decision tree** — `FastMCP(instructions=...)` rewritten as explicit 5-branch decision tree with hard call-count limits; targets 3–4 tool calls per question (2026-04-19)
- [x] **Eager model loading** — `SentenceTransformer` instantiated at module level in `server.py`; eliminates 6 s first-call delay (2026-04-19)
- [x] **Vector/semantic search** — `embedding BLOB` on `documents` table; all-MiniLM-L6-v2 via sentence-transformers; post-crawl embedding pass in `cli.py`; `search_docs_semantic` MCP tool (2026-04-19)
- [x] **End-to-end MCP tool test** — all 6 tools verified against live DB; DB queries 5–38 ms (2026-04-19)
- [x] **`splunk-enterprise` and `splunk-cloud` sources defined** — `CrawlSource` entries in `config.py`; not yet crawled (2026-04-19)
- [x] **Full crawl verified** — 743 ES pages + 216 admin-manual pages, all 6 ES sections populated (2026-04-18)
- [x] **Bug fix: redirect URL** — `_process_url` uses `str(resp.url)` as `urljoin` base so relative hrefs in redirected pages resolve correctly (2026-04-18)
- [x] **Bug fix: version filter** — `_is_target_url` rejects URLs with version segments that don't match `source.version`, preventing ES 8.0–8.4 pages being indexed (2026-04-18)
- [x] `README.md` — setup, crawl, MCP config, tool reference, dev tips
- [x] Timing logging in `server.py` — each tool call logs duration in ms to stderr
- [x] `pyproject.toml` — all dependencies and entry points (`splunk-mcp`, `splunk-crawl`)
- [x] `.gitignore`, `.python-version`
- [x] `config.py` — `CrawlSource` dataclass (with `crawl_delay`, `max_concurrency`, `blocked_path_prefixes`), 5 active sources, `SOURCES_BY_ID`
- [x] `db.py` — full schema, FTS5 content table + triggers, all query helpers, embedding helpers
- [x] `extractor.py` — trafilatura + markdownify fallback, URL metadata parsing, file writer
- [x] `crawler.py` — BFS, redirect fix, version filter, per-source rate limiting
- [x] `cli.py` — argparse with `--sources`, `--section`, `--concurrency`, `--delay`, `--full`, `--db`, `--docs-dir`, `--verbose`; post-crawl embedding pass
- [x] `server.py` — FastMCP app with 6 registered tools, eager model load, decision-tree instructions
- [x] `data/.gitkeep`, `data/docs/.gitkeep`
- [x] `CLAUDE.md`, `PLAN.md`, `TODO.md` context files
