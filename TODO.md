# TODO — splunk-docs-mcp

_Last updated: 2026-04-18_

---

## 🔴 Priority 1 — Write README

The project is functionally complete but has no user-facing documentation. A new user can't set it up without it.

Create `README.md` with:
- What this project is (one paragraph — local MCP server for Splunk docs, version-specific, no hallucination)
- Prerequisites: `uv`, Python 3.12
- Setup: `git clone`, `uv sync`
- Crawl command: `uv run splunk-crawl` (and how long it takes / what it produces)
- MCP config JSON block for Claude Desktop / Claude Code
- Available MCP tools (table: name, what it does, key params)
- Development tip: `--sources enterprise-security --section user-guide` for fast pipeline test without a full crawl

---

## 🔴 Priority 2 — End-to-end MCP tool test

The server starts and tools are registered, but they haven't been exercised against the live fully-populated DB. Do this before considering Phase 1 done.

Start the server:
```bash
uv run splunk-mcp
```

Test queries to run (via Claude Desktop or an MCP client):
- `search_docs("correlation rule")` — should return ES 8.5 results
- `search_docs("transforms.conf")` — should return admin-manual results
- `search_docs("notable event", source="enterprise-security")` — source filter
- `get_page(<url from search result>)` — full Markdown content
- `list_sections()` — should show both sources with all 6 ES sections + admin-manual
- `browse_section("administer", "enterprise-security")` — should list many pages (not just 1)
- `get_index_info()` — should show ~959 total pages, last crawl timestamp

---

## 🟡 Priority 3 — Public release distribution (Phase 2, after POC is done)

The intended distribution model for public GitHub use:

- [ ] **`src/splunk_docs_mcp/setup.py`** — `splunk-setup` command (download pre-built DB from GitHub Releases)
- [ ] **`pyproject.toml`** — add `splunk-setup = "splunk_docs_mcp.setup:main"` to `[project.scripts]`
- [ ] **`.github/workflows/crawl-and-release.yml`** — weekly cron + `workflow_dispatch`; crawl + publish DB as release asset
- [ ] **README update** — replace crawl step with `uv run splunk-setup`; add data freshness note

See PLAN.md "Phase 2" section for full implementation details.

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
