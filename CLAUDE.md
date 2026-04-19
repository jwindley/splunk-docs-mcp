# splunk-docs-mcp — Claude Code Context

> **START OF SESSION INSTRUCTION:** Read `PLAN.md` and `TODO.md` before doing any work.
> They tell you what has been built, what is broken, and what to do next.
> Never start coding on new requirements without updating the plan first.

---

## Project Overview

A local Python MCP server that crawls Splunk documentation from `help.splunk.com`, stores pages as Markdown files, indexes them in SQLite FTS5, and exposes search and retrieval via MCP tools.

The primary use case is giving Claude (via MCP) accurate, version-specific Splunk knowledge without hallucination — answering questions like "how do I configure correlation searches in ES 8.5?" or "what fields does transforms.conf support?"

---

## Phase 1 Scope (what we are building)

| Source ID | Display Name | Version | Base URL |
|-----------|-------------|---------|----------|
| `enterprise-security` | Splunk Enterprise Security 8.5 | 8.5 | `help.splunk.com/en/splunk-enterprise-security-8/` |
| `admin-manual` | Splunk Configuration File Reference 10.2 | 10.2 | `help.splunk.com/en/data-management/splunk-enterprise-admin-manual/10.2/configuration-file-reference/` |

---

## Intended Distribution Model (Phase 2 — do NOT build during Phase 1 POC)

The goal is a public GitHub repo where users never run the crawl. When Phase 1 POC is done:

- **GitHub Actions** crawls weekly + on `workflow_dispatch`, publishes `splunk_docs.db` as a GitHub Release asset (`data-YYYY-MM-DD` tag)
- **`splunk-setup` CLI command** (`src/splunk_docs_mcp/setup.py`) downloads the latest Release asset to `data/splunk_docs.db`
- User flow becomes: `git clone` → `uv sync` → `uv run splunk-setup` → configure MCP → done
- See PLAN.md "Phase 2" for full implementation details

## Future Scope (architecture must accommodate; do NOT build yet)

- **Lantern** — `lantern.splunk.com` (new `CrawlSource` entry only)
- **Core Splunk Enterprise docs** — help.splunk.com (new `CrawlSource` entry only)
- **SPL examples library** — curated JSON → separate `spl_examples` DB table + `search_spl` MCP tool (stub already in `db.py`)
- **Vector / semantic search** — `embedding BLOB` column stub noted in `db.py`; future `search_docs_semantic` tool
- **Multi-version crawling** — `version` column already in schema; `search_docs` has a comment marking where to add a `version` filter parameter

---

## Tech Stack and Why

| Choice | Reason |
|--------|--------|
| **Python** (not TypeScript) | Cleaner scraping ecosystem; trafilatura, BS4, httpx all best-in-class |
| **`mcp` SDK** (`mcp.server.fastmcp.FastMCP`) | Official Anthropic SDK, not the third-party `fastmcp` wrapper |
| **SQLite FTS5** with `porter unicode61` tokenizer | BM25 ranking, phrase search, persistent across restarts, no rebuild on startup, no custom tokenizer code |
| **FTS5 content table pattern** | `content=documents, content_rowid=id` + INSERT/UPDATE/DELETE triggers — no text duplication, auto-synced |
| **trafilatura** (primary extractor) | Text-density heuristics; doesn't rely on CSS class names (help.splunk.com has none stable) |
| **markdownify + BS4** (fallback extractor) | Catches pure-table pages and index pages where trafilatura returns <100 chars |
| **httpx AsyncClient** | Async HTTP; follows redirects; 15s timeout |
| **asyncio BFS crawler** | `asyncio.Queue` + N worker tasks + `queue.join()` — correct termination even when workers discover new links during processing |
| **WAL mode SQLite** | `PRAGMA journal_mode=WAL` — MCP server can read while crawler writes |
| **SHA-256 content hash** | Incremental re-crawl: skip pages whose raw HTML hasn't changed |

---

## Project Structure

```
splunk-docs-mcp/
├── CLAUDE.md              ← you are here
├── PLAN.md                ← build status and next steps
├── TODO.md                ← prioritised task list
├── pyproject.toml         ← deps, entry points
├── uv.lock
├── .python-version        ← "3.12"
├── README.md
├── .gitignore
├── src/
│   └── splunk_docs_mcp/
│       ├── __init__.py
│       ├── config.py      ← CrawlSource dataclass + Phase 1 source definitions
│       ├── db.py          ← SQLite schema, connection factory, upsert/query helpers
│       ├── extractor.py   ← HTML→Markdown + URL metadata parsing
│       ├── crawler.py     ← async BFS crawler
│       ├── cli.py         ← crawl CLI entry point (argparse)
│       └── server.py      ← MCP server + 5 tool definitions
└── data/
    ├── .gitkeep
    └── docs/              ← Markdown files written at crawl time (gitignored)
```

---

## Entry Points

```bash
uv run splunk-mcp                                                # start MCP server (stdio)
uv run splunk-crawl                                             # crawl all Phase 1 sources
uv run splunk-crawl --sources enterprise-security               # single source
uv run splunk-crawl --sources enterprise-security --section user-guide  # single section (dev/test)
uv run splunk-crawl --full                                      # re-extract even unchanged pages
```

---

## Key Architectural Decisions

### Source-agnostic design
Everything downstream of `config.py` is source-agnostic. Adding a new crawl source (Lantern, core Splunk) requires only:
1. Add a `CrawlSource` entry to `PHASE1_SOURCES` in `config.py`
2. Zero other changes

### `source` + `version` columns on every row
Every document row in the DB stores its source ID and product version. Search results always include this metadata so it is always clear which version of which product a result is from.

### No version filter parameter on `search_docs` (Phase 1)
Phase 1 indexes exactly one version per source. A version filter parameter would add complexity with no benefit. The comment `# Future: add version filter here` marks where to add it in Phase 2.

### Module-level DB singleton in `server.py`
DB connection opened once on first use, reused across all tool calls. Simpler and more reliable than MCP framework lifespan API for this use case.

### BM25 title weighting
`bm25(documents_fts, 10.0, 1.0)` weights title matches 10× higher than body matches. Lower score = better match (SQLite BM25 convention).

---

## Database Schema Summary

```sql
documents          -- one row per page; url UNIQUE
documents_fts      -- FTS5 virtual table (content=documents); auto-synced via triggers
crawl_state        -- per-URL crawl status; used by crawler only, not MCP server
```

DB file: `data/splunk_docs.db` (gitignored — regenerated by crawl)

---

## Markdown File Layout

```
data/docs/{source_id}/{version}/{section}/{subsection}/{slug}.md
```

Files include YAML frontmatter with `title`, `url`, `source`, `version`, `section`, `subsection`, `crawled`.

---

## MCP Tools Exposed

| Tool | Purpose |
|------|---------|
| `search_docs` | BM25 FTS5 keyword search; `query`, optional `source`, `limit=5` |
| `get_page` | Full Markdown content by exact URL |
| `list_sections` | Source → section → page count inventory |
| `browse_section` | All pages in a section (title + URL list) |
| `get_index_info` | DB stats: total pages, sources, last crawl time, DB size |

---

## Known Crawler Behaviours (hard-won lessons)

### Redirect-aware link extraction
`help.splunk.com` section seed URLs (e.g. `.../administer/8.5`) redirect to a deeper page. The HTML at the redirect destination uses relative hrefs designed to be resolved against the *redirect destination's* directory, not the original seed URL. `_process_url` captures `final_url = _normalise_url(str(resp.url)) or url` immediately after the response and passes that to `_extract_links()`. This was a critical bug fix — using the pre-redirect URL produced doubled paths that 404'd silently.

### Version segment filtering in `_is_target_url`
The URL prefix `splunk-enterprise-security-8/` matches all versions (8.0, 8.1, 8.2 … 8.5). The crawler extracts version-number path segments from the URL and rejects any URL where a version segment is present but doesn't match `source.version`. This prevents cross-version nav links from pulling in older ES docs. The admin-manual source is unaffected (its version is baked into the `url_prefix`).

### When to use `--full`
`crawl_state` records every attempted URL. If a crawl run contained bugs (e.g. malformed URLs were visited and recorded as fetched/failed), a subsequent incremental crawl will skip those URLs. Always use `uv run splunk-crawl --full` after fixing crawler URL-handling bugs to force a clean re-crawl.

---

## Conventions to Follow

- **Never modify crawler, DB, or server code to handle a new source** — only `config.py`
- **Never add a version filter to `search_docs` in Phase 1** — comment marks where to add it later
- **Always store version metadata** in every document row
- **Update `PLAN.md`, `TODO.md`, and `CLAUDE.md`** when tasks are completed or new ones are discovered — these three files are the only project state files; do NOT create separate plan files
- **Update the plan before coding** when requirements change — stop, update plan, get approval, then code
- **`--section` flag** is the intended way to test the crawl pipeline quickly during development
- **`uv run splunk-crawl --full`** is required after fixing crawler URL-handling bugs

---

## Claude Desktop / Claude Code Config

```json
{
  "mcpServers": {
    "splunk-docs": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/splunk-docs-mcp", "splunk-mcp"]
    }
  }
}
```
