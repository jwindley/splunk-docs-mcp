<!-- refreshed: 2026-05-03 -->
# Codebase Structure

**Analysis Date:** 2026-05-03

## Directory Layout

```
splunk-docs-mcp/
├── .github/
│   └── workflows/
│       └── crawl-and-release.yml     # Weekly crawl + merge + publish GH Release
├── .planning/
│   └── codebase/                     # GSD codebase map documents (this file)
├── src/
│   └── splunk_docs_mcp/
│       ├── __init__.py               # Empty package init
│       ├── config.py                 # CrawlSource dataclass + all 10 source defs
│       ├── db.py                     # SQLite schema, all query/write/chunk/embed helpers
│       ├── extractor.py              # HTML→Markdown extraction + URL metadata + .md writer
│       ├── crawler.py                # Async BFS crawler (asyncio.Queue + httpx workers)
│       ├── cli.py                    # splunk-crawl entry point; orchestrates post-crawl passes
│       ├── merge.py                  # splunk-merge: merge per-source DBs + export sources
│       ├── server.py                 # MCP server: 6 tool definitions + module-level singletons
│       └── setup.py                  # splunk-setup: download pre-built DB from GH Releases
├── tests/
│   ├── __init__.py
│   ├── test_crawler.py               # Tests: _normalise_url, _is_target_url, _section_from_url
│   └── test_extractor.py             # Tests: parse_url_metadata (18 cases)
├── data/
│   ├── .gitkeep
│   └── docs/                         # Markdown files written at crawl time (gitignored)
│       └── {source_id}/{version}/{section}/{subsection}/{slug}.md
├── CLAUDE.md                         # Project context for Claude Code sessions
├── PLAN.md                           # Build status and next steps
├── TODO.md                           # Prioritised task list
├── pyproject.toml                    # Dependencies, entry points (hatchling)
├── uv.lock                           # Locked dependency tree
└── .python-version                   # "3.12"
```

## Directory Purposes

**`src/splunk_docs_mcp/`:**
- Purpose: All application code — exactly 8 modules
- Key constraint: Adding a new crawl source requires ONLY adding a `CrawlSource` entry to `config.py`; no other module changes

**`tests/`:**
- Purpose: Unit tests for URL parsing and normalisation logic
- Coverage: 36 tests total — `parse_url_metadata()` (18), `_normalise_url()` + `_is_target_url()` + `_section_from_url()` (18)
- Not covered: DB helpers, MCP server tools, crawl/chunk/embed passes

**`data/`:**
- Purpose: Runtime data directory — SQLite DB and crawled Markdown files
- `data/splunk_docs.db` — gitignored; the live search index
- `data/docs/` — gitignored; Markdown files with YAML frontmatter written by `extractor.write_markdown_file()`

**`.github/workflows/`:**
- Purpose: CI/CD — weekly automated crawl and release
- Single workflow: `crawl-and-release.yml`
- Three jobs: `crawl` (parallel matrix), `crawl-derived` (depends on crawl), `merge-and-release` (depends on both)

## Key File Locations

**Entry Points:**
- `src/splunk_docs_mcp/server.py`: `run()` — MCP server (started by `splunk-mcp` command)
- `src/splunk_docs_mcp/cli.py`: `main()` — crawler CLI (started by `splunk-crawl`)
- `src/splunk_docs_mcp/merge.py`: `main()` — merge CLI (started by `splunk-merge`)
- `src/splunk_docs_mcp/setup.py`: `main()` — setup CLI (started by `splunk-setup`)

**Configuration:**
- `pyproject.toml`: dependency list, entry point mappings, pytest config, ruff lint config
- `src/splunk_docs_mcp/config.py`: all crawl source definitions, DB path (`data/splunk_docs.db`), docs dir
- `.python-version`: pins Python 3.12

**Core Logic:**
- `src/splunk_docs_mcp/db.py`: schema, `upsert_document()`, `chunk_document()`, `search_docs()`, `get_all_embeddings()`, `run_dedup_pass()`, `run_version_merge_pass()`
- `src/splunk_docs_mcp/crawler.py`: `crawl_source()`, `_process_url()`, `_is_target_url()`, `_normalise_url()`
- `src/splunk_docs_mcp/extractor.py`: `extract_page()`, `parse_url_metadata()`, `write_markdown_file()`

**CI/CD:**
- `.github/workflows/crawl-and-release.yml`: full pipeline definition

## Entry Points and What They Do

### `splunk-mcp` → `server.py:run()`
Starts the MCP server in stdio transport mode.

At module import (before any tool call):
1. Loads `all-MiniLM-L6-v2` and warms PyTorch JIT with 5 representative queries
2. Opens `data/splunk_docs.db` via `get_connection()`
3. Calls `get_all_embeddings()` to build the in-memory embedding matrix

Exposes 6 tools to Claude via FastMCP:
- `search_docs` — BM25 FTS5 keyword search
- `search_docs_semantic` — cosine-similarity vector search
- `get_page` — full page content by URL
- `list_sections` — section inventory grouped by source
- `browse_section` — page listing within a section
- `get_index_info` — DB stats

### `splunk-crawl` → `cli.py:main()`
Full write pipeline. Default: all sources, incremental.

Execution order per invocation:
1. For each source with `derive_from` set: load parent URLs, substitute version segment, pass as `extra_seeds`
2. `crawl_source()` per source (sequentially): BFS crawl → `upsert_document()` → retry pass
3. `_chunk_pass()`: split all docs > 8000 chars into 1500-char overlapping chunk rows
4. `_embed_pass()`: encode `title + content_md` with MiniLM, store as BLOB in `documents.embedding`
5. `_dedup_pass()`: mark cross-source duplicates with `is_duplicate=1`

Key flags:
- `--full`: re-extract all pages; clear and rebuild chunks and embeddings
- `--rechunk`: skip crawl, rebuild chunks + embeddings only
- `--section SLUG`: crawl one section only (fast dev/test path)
- `--sources SOURCE_ID [...]`: crawl specific sources only
- `--derive-db PATH`: path to parent source DB for URL derivation (used in CI)

### `splunk-merge` → `merge.py:main()`
Two mutually exclusive modes:

**Merge mode** (default): `splunk-merge data/a.db data/b.db --output data/out.db`
1. `merge_source_db()` per input DB (ATTACH + INSERT OR IGNORE)
2. `run_version_merge_pass()` — collapse identical cross-version rows into parent with `version_tags`
3. FTS5 explicit rebuild: `INSERT INTO documents_fts(documents_fts) VALUES('rebuild')`
4. `run_dedup_pass()` — mark cross-source duplicates

**Export mode**: `splunk-merge --export-sources data/export/ --db data/splunk_docs.db`
1. For each source in PHASE1_SOURCES order: `_export_source_db()` writes `splunk_docs_{source_id}.db`
2. Writes `manifest.json` with source metadata (pages, shared_pages, parent_source_id, size_bytes)

### `splunk-setup` → `setup.py:main()`
Interactive user-facing onboarding.

1. Fetch latest release info from GitHub API
2. Download and parse `manifest.json`
3. Display grouped source selection menu (current versions + indented n-1 children with auto-include notice)
4. Download selected per-source DBs with streaming progress
5. If single source: rename to `data/splunk_docs.db`
6. If multiple sources: `merge_dbs()` locally → `data/splunk_docs.db`, clean up WAL files

Fallback: if no `manifest.json` in release, downloads monolithic `splunk_docs.db` directly.

## Module Dependency Graph

```
config.py          (no internal imports)
    ↑
    ├── extractor.py
    │       ↑
    │       └── crawler.py
    │               ↑
    │               └── cli.py
    │
    ├── db.py          (no internal imports)
    │       ↑
    │       ├── crawler.py
    │       ├── cli.py
    │       ├── merge.py
    │       └── server.py
    │
    ├── crawler.py     (imports: config, db, extractor)
    │       ↑
    │       └── cli.py
    │
    ├── merge.py       (imports: config, db)
    │       ↑
    │       └── setup.py  (lazy import inside function)
    │
    └── server.py      (imports: config, db)
```

**Strict rule:** No circular imports. Dependency direction flows from entry points inward to `db.py` and `config.py`. `db.py` and `config.py` have no internal imports.

## Public API Surface

### MCP Tools (consumed by Claude via MCP protocol)

| Tool | Signature | Purpose |
|------|-----------|---------|
| `search_docs` | `(query, source=None, version=None, limit=5)` | BM25 FTS5 keyword search |
| `search_docs_semantic` | `(query, source=None, version=None, limit=5)` | Cosine-similarity vector search |
| `get_page` | `(url)` | Full Markdown content by exact URL |
| `list_sections` | `(source=None)` | Section inventory with page counts |
| `browse_section` | `(section, source, subsection=None)` | Pages in a section |
| `get_index_info` | `()` | DB stats: pages, sources, last crawl, size |

All tools return plain dicts/lists of dicts. Error responses are `[{"error": "..."}]`. Empty/not-found responses are `[{"message": "..."}]`.

### CLI Commands

| Command | Entry Point | Primary Args |
|---------|-------------|-------------|
| `splunk-crawl` | `cli:main` | `--sources`, `--section`, `--full`, `--rechunk`, `--db`, `--delay`, `--concurrency` |
| `splunk-merge` | `merge:main` | positional `SOURCE_DB...`, `--output`, `--export-sources`, `--db` |
| `splunk-setup` | `setup:main` | `--all` |
| `splunk-mcp` | `server:run` | (none — reads `data/splunk_docs.db` at the default path) |

### Key `db.py` Public Functions (used by server and/or merge)

| Function | Caller | Purpose |
|----------|--------|---------|
| `get_connection(db_path)` | all modules | Open SQLite connection with WAL + row_factory |
| `init_db(conn)` | all modules | Create tables/indexes/triggers; safe to call on existing DB |
| `upsert_document(conn, doc)` | `crawler.py` | Insert or update a page row; triggers FTS sync |
| `chunk_document(conn, parent)` | `cli.py` | Split large doc into chunk rows; mark parent `has_chunks=1` |
| `search_docs(conn, query, ...)` | `server.py` | BM25 FTS5 search with dedup |
| `get_all_embeddings(conn)` | `server.py` | Load full embedding matrix into NumPy array |
| `search_docs_semantic_from_matrix(matrix, rows, q_vec, ...)` | `server.py` | In-process cosine similarity search |
| `get_page(conn, url)` | `server.py` | Fetch page; reassemble chunks; redirect chunk URLs to parent |
| `list_sections(conn, source)` | `server.py` | Grouped section/page count query |
| `browse_section(conn, section, source, ...)` | `server.py` | Page listing within a section |
| `run_dedup_pass(conn)` | `cli.py`, `merge.py` | Mark cross-source duplicates `is_duplicate=1` |
| `run_version_merge_pass(conn, pairs)` | `merge.py` | Collapse identical cross-version rows into parent with `version_tags` |
| `merge_source_db(conn, source_db_path)` | `merge.py` | ATTACH + INSERT OR IGNORE from another DB |

## Naming Conventions

**Files:** All lowercase with underscores (`config.py`, `extractor.py`). No module prefixes.

**Functions:** `snake_case`. Private helpers prefixed with `_` (`_process_url`, `_extract_links`, `_chunk_pass`).

**Classes/dataclasses:** `PascalCase` (`CrawlSource`, `ExtractedPage`, `CrawlStats`).

**Constants:** `UPPER_SNAKE_CASE` (`CHUNK_THRESHOLD`, `PHASE1_SOURCES`, `DB_PATH`).

**Source IDs:** `kebab-case` strings (`enterprise-security`, `admin-manual`, `enterprise-security-8-4`). These are stored verbatim in the DB `source` column and used as file suffixes (`splunk_docs_{source_id}.db`).

**CLI flags:** `--kebab-case` (`--derive-db`, `--docs-dir`, `--rechunk`).

## Where to Add New Code

**New crawl source:**
- Only file to edit: `src/splunk_docs_mcp/config.py`
- Add a `CrawlSource(...)` entry to `PHASE1_SOURCES`
- For n-1 versions: set `derive_from='parent-source-id'` on the new source
- No changes needed to crawler, db, server, merge, setup, or workflow

**New MCP tool:**
- Add `@mcp.tool()` decorated function to `src/splunk_docs_mcp/server.py`
- Add corresponding DB query helper to `src/splunk_docs_mcp/db.py`
- Follow existing pattern: `db_` alias import, `time.perf_counter()` timing, structured error dict on failure

**New CLI flag for `splunk-crawl`:**
- Add argument to `_build_parser()` in `src/splunk_docs_mcp/cli.py`
- Pass via `args` namespace to `_run()` or the relevant pass function

**New post-crawl processing pass:**
- Add `_mypass(args, sources)` function in `src/splunk_docs_mcp/cli.py`
- Call it in `_run()` after `_dedup_pass()` (or in the appropriate order)
- Add corresponding DB helpers to `src/splunk_docs_mcp/db.py`

**New unit tests:**
- Location: `tests/test_{module_name}.py`
- Existing pattern: pure-function tests with no DB or network fixtures (`test_extractor.py`, `test_crawler.py`)
- For DB-touching tests: use an in-memory SQLite DB (`get_connection(Path(":memory:"))`); call `init_db()` before use

**New DB column (schema migration):**
- Add to the `CREATE TABLE` in `db.py:init_db()`
- Add `ALTER TABLE documents ADD COLUMN` block with `try/except OperationalError` for existing DB migration
- Update `upsert_document()` INSERT and ON CONFLICT SET clauses
- Update `merge_source_db()` SELECT and INSERT column lists

## Special Directories

**`data/`:**
- Purpose: Runtime data — DB and crawled Markdown files
- Generated: Yes (by `splunk-crawl` and `splunk-setup`)
- Committed: Only `data/.gitkeep` — the DB and `data/docs/` are gitignored

**`.planning/codebase/`:**
- Purpose: GSD codebase map documents consumed by `/gsd-plan-phase` and `/gsd-execute-phase`
- Generated: Yes (by `/gsd-map-codebase`)
- Committed: Yes

**`.github/workflows/`:**
- Purpose: CI/CD pipeline
- Generated: No
- Committed: Yes

**`.venv/`:**
- Purpose: uv-managed virtual environment
- Generated: Yes (by `uv sync`)
- Committed: No (gitignored)

---

*Structure analysis: 2026-05-03*
