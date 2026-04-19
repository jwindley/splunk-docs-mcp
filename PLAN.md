# Build Plan — splunk-docs-mcp

_Last updated: 2026-04-19_

---

## Current Status

**Phase 1 is complete.** All code is written, crawled, tested, and documented. The MCP server is in use and working — DB queries run in 5–38 ms, tool results verified 2026-04-19.

Latest changes (2026-04-19):
- `search_docs_semantic` model now eagerly loaded at server startup (eliminates 6 s first-call delay)
- `server.py` `instructions` string rewritten as an explicit decision tree with hard call limits (target: 3–4 tool calls per question, down from 7–8)

---

## What Has Been Built

| File | Status | Notes |
|------|--------|-------|
| `pyproject.toml` | ✅ Done | Deps, entry points (`splunk-mcp`, `splunk-crawl`) |
| `.gitignore` | ✅ Done | Python-appropriate; `data/docs/` and `data/*.db` gitignored |
| `.python-version` | ✅ Done | `3.12` |
| `src/splunk_docs_mcp/__init__.py` | ✅ Done | Empty package init |
| `src/splunk_docs_mcp/config.py` | ✅ Done | `CrawlSource` dataclass, `PHASE1_SOURCES`, `SOURCES_BY_ID`, paths, headers |
| `src/splunk_docs_mcp/db.py` | ✅ Done | Schema, connection factory, FTS5 + triggers, all query helpers, embedding helpers, `search_docs_semantic` |
| `src/splunk_docs_mcp/extractor.py` | ✅ Done | trafilatura primary, BS4+markdownify fallback, `parse_url_metadata`, `write_markdown_file` |
| `src/splunk_docs_mcp/server.py` | ✅ Done | FastMCP app + 6 tools: `search_docs`, `search_docs_semantic`, `get_page`, `list_sections`, `browse_section`, `get_index_info` |
| `src/splunk_docs_mcp/cli.py` | ✅ Done | argparse with `--sources`, `--section`, `--concurrency`, `--delay`, `--full`, `--db`, `--docs-dir`, `--verbose`; post-crawl embedding pass |
| `src/splunk_docs_mcp/crawler.py` | ✅ Done | Two bugs fixed 2026-04-18; verified with full crawl |
| `data/.gitkeep` | ✅ Done | |
| `data/docs/.gitkeep` | ✅ Done | |
| `CLAUDE.md` / `PLAN.md` / `TODO.md` | ✅ Done | Session context files |
| `README.md` | ✅ Done | Setup and usage docs for end users |

---

## What Works

- **Full crawl:** `uv run splunk-crawl` completes successfully for both sources
- **Index coverage:** 743 ES pages + 216 admin-manual pages (959 total), all sections populated
- **Incremental re-crawl:** unchanged pages skipped via SHA-256 hash comparison
- **MCP server starts:** `uv run splunk-mcp` runs on stdio, all 6 tools registered
- **SQLite WAL mode:** server can read while crawler writes
- **`--section` dev flag:** limits crawl to one section for fast pipeline testing
- **Version filtering:** crawler only indexes ES 8.5 pages, ignores cross-version nav links

---

## What Is Incomplete

---

## Bugs Fixed This Session (2026-04-18)

### Bug 1 — Crawler used pre-redirect URL as urljoin base (`crawler.py`)
**Symptom:** All ES sections except `user-guide` had only 1 page in the DB — the seed URL itself.  
**Root cause:** Section seed URLs redirect to a deeper page. The HTML there uses relative hrefs designed to be resolved against the redirect destination, but `_process_url` was passing the original pre-redirect URL to `urljoin()`, producing doubled/malformed paths that 404.  
**Fix:** Capture `final_url = _normalise_url(str(resp.url)) or url` after the response and pass it to `_extract_links()` instead of `url`. Also pre-mark `final_url` as visited to prevent double-processing.

### Bug 2 — Version filter missing; crawler indexed ES 8.0–8.4 alongside 8.5 (`crawler.py`)
**Symptom:** Crawl log showed fetches of `/install/8.0/`, `/administer/8.1/` etc. — wrong versions.  
**Root cause:** The `url_prefix` filter `splunk-enterprise-security-8/` matches all ES versions. Cross-version nav links in the HTML were being followed.  
**Fix:** In `_is_target_url()`, extract version-number path segments from the URL after the prefix. If any version segments are present and none match `source.version`, reject the URL.

---

## Crawl Results (post-fix, 2026-04-18)

```
[enterprise-security] stored=743  skipped=0  failed=2  total=745
[admin-manual]        stored=216  skipped=0  failed=0  total=216
```

The 2 ES failures are expected to be transient 404s or network blips, not structural issues. All 6 ES sections confirmed populated.

---

## Next Steps (priority order)

Phase 1 is complete, including vector/semantic search (2026-04-19). Next up is Phase 2 (public release distribution) — see TODO Priority 3.

---

## Phase 2 — Public release distribution (planned, not started)

The end goal is a public GitHub repo where users never have to run the crawl. Planned approach:

### Distribution model
- **GitHub Actions** crawls on a weekly cron schedule + `workflow_dispatch` (manual trigger)
- Publishes `splunk_docs.db` as a GitHub Release asset tagged `data-YYYY-MM-DD`
- `make_latest: true` so `/releases/latest` always points at the freshest index
- Uses `softprops/action-gh-release@v2` + auto-provided `GITHUB_TOKEN` (no extra secrets)
- Requires `permissions: contents: write` on the job

### New CLI command: `splunk-setup`
- New file: `src/splunk_docs_mcp/setup.py`; entry point `splunk_docs_mcp.setup:main`
- Calls GitHub API `/releases/latest`, finds `splunk_docs.db` asset, streams download with progress
- Atomic write: download to `DB_PATH.parent / (DB_PATH.name + ".tmp")`, then rename
- Imports `DB_PATH`, `DATA_DIR` from `config.py`; uses `httpx` (already a dependency)
- No new dependencies

### pyproject.toml change
```toml
splunk-setup = "splunk_docs_mcp.setup:main"
```

### README update
Replace "run splunk-crawl" with "run splunk-setup"; add data freshness note.

### User experience (post-Phase-2)
`git clone` → `uv sync` → `uv run splunk-setup` → add MCP config → done.

---

## Phase 3+ (not started, not planned in detail)

- Additional crawl sources: Lantern, core Splunk Enterprise (add `CrawlSource` to `config.py` only)
- SPL examples library: `spl_examples` table + `search_spl` tool (schema stub in `db.py`)
- ~~Vector/semantic search~~ — **Done (2026-04-19).** `embedding BLOB` on `documents`, all-MiniLM-L6-v2, post-crawl pass in `cli.py`, `search_docs_semantic` MCP tool.
- Multi-version crawling with version filter on `search_docs` (comment marks where to add it)
