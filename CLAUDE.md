# splunk-docs-mcp — Claude Code Context

> **START OF SESSION INSTRUCTION:** Read `PLAN.md` and `TODO.md` before doing any work.
> They tell you what has been built, what is broken, and what to do next.
> Never start coding on new requirements without updating the plan first.

---

## Project Overview

A local Python MCP server that crawls Splunk documentation from `help.splunk.com` and `lantern.splunk.com`, stores pages as Markdown files, indexes them in SQLite FTS5, and exposes search and retrieval via MCP tools.

The primary use case is giving Claude (via MCP) accurate, version-specific Splunk knowledge without hallucination — answering questions like "how do I configure correlation searches in ES 8.5?" or "what fields does transforms.conf support?"

---

## Active Crawl Sources

Goal: **current released version + n−1** for each product. ITSI and Observability are planned additions.

| Source ID | Display Name | Version | Base URL | Pages (actual) | Status |
|-----------|-------------|---------|----------|----------------|--------|
| `enterprise-security` | Splunk Enterprise Security 8.5 | 8.5 | `help.splunk.com/en/splunk-enterprise-security-8/` | 738 | ✅ OK |
| `enterprise-security-8-4` | Splunk Enterprise Security 8.4 | 8.4 | `help.splunk.com/en/splunk-enterprise-security-8/` | 431 | ✅ OK — 8.4 has fewer pages; pci-compliance and CIM are 8.5-only sections |
| `enterprise-security-8-3` | Splunk Enterprise Security 8.3 | 8.3 | `help.splunk.com/en/splunk-enterprise-security-8/` | 351 | ✅ OK |
| `admin-manual` | Splunk Configuration File Reference 10.2 | 10.2 | `help.splunk.com/en/data-management/splunk-enterprise-admin-manual/10.2/configuration-file-reference/` | 216 | ✅ OK |
| `splunk-enterprise` | Splunk Enterprise 10.2 | 10.2 | `help.splunk.com/en/splunk-enterprise/` | 3,549 | ✅ OK |
| `splunk-cloud` | Splunk Cloud Platform 10.3.2512 | 10.3.2512 | `help.splunk.com/en/splunk-cloud-platform/` | 2,658 | ✅ OK |
| `soar-on-premises` | Splunk SOAR On-Premises 8.5.0 | 8.5.0 | `help.splunk.com/en/splunk-soar/soar-on-premises/` | TBD | ✅ Added |
| `soar-on-premises-8-4-0` | Splunk SOAR On-Premises 8.4.0 | 8.4.0 | `help.splunk.com/en/splunk-soar/soar-on-premises/` | TBD | ✅ Added (derives from soar-on-premises) |
| `soar-cloud` | Splunk SOAR Cloud | current | `help.splunk.com/en/splunk-soar/soar-cloud/` | TBD | ✅ Added |
| `lantern` | Splunk Lantern | current | `lantern.splunk.com/` | 1,240 | ✅ OK |

No blocking known issues.

---

## Distribution Model (Phase 2 — complete)

- **GitHub Actions** crawls weekly (Sunday 02:00 UTC) + `workflow_dispatch`; 10-source matrix (`crawl` + `crawl-derived` jobs); aggregation job merges (skipping missing DBs) + exports + publishes release
- **Release assets:** `splunk_docs.db` (full merged), `splunk_docs_<source>.db` (per-source), `manifest.json`
- **`splunk-setup`** interactive menu — select sources or `all`; downloads per-source DBs, merges, cleans up WAL temp files
- **`splunk-merge`** combines per-source DBs; `--export-sources` generates per-source files + `manifest.json`
- User flow: `git clone` → `uv sync` → `uv run splunk-setup` → configure MCP → done

## Future Scope (do NOT build yet)

- **Add ITSI, Observability** — most-requested missing products (SOAR is now indexed)

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
| **sentence-transformers** (`all-MiniLM-L6-v2`) | Local offline embeddings (384 dims); generated at crawl time; stored as BLOB in `documents.embedding` |

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
│       ├── config.py      ← CrawlSource dataclass + all 9 active source definitions
│       ├── db.py          ← SQLite schema, connection factory, all query/write helpers
│       ├── extractor.py   ← HTML→Markdown + URL metadata parsing
│       ├── crawler.py     ← async BFS crawler + retry pass
│       ├── cli.py         ← crawl CLI entry point (argparse)
│       ├── merge.py       ← splunk-merge CLI: merge_dbs() + export_sources()
│       ├── server.py      ← MCP server + 6 tool definitions
│       └── setup.py       ← splunk-setup: download pre-built DB from GitHub Releases
├── tests/
│   ├── test_extractor.py  ← parse_url_metadata() tests (18)
│   └── test_crawler.py    ← _normalise_url, _is_target_url, _section_from_url tests (18)
└── data/
    ├── .gitkeep
    └── docs/              ← Markdown files written at crawl time (gitignored)
```

---

## Entry Points

```bash
uv run splunk-mcp                                                           # start MCP server (stdio)
uv run splunk-setup                                                         # download pre-built DB from latest release
uv run splunk-crawl                                                         # crawl all 9 sources + chunk + embed + dedup
uv run splunk-crawl --sources enterprise-security                           # single source
uv run splunk-crawl --sources enterprise-security --section user-guide      # single section (dev/test)
uv run splunk-crawl --sources lantern --section Splunk_Success_Framework    # Lantern test section
uv run splunk-crawl --full                                                  # re-extract + re-chunk + re-embed everything
uv run splunk-crawl --rechunk                                               # rebuild chunks only (no re-crawl)
uv run splunk-merge data/a.db data/b.db --output data/splunk_docs.db       # merge per-source DBs
uv run splunk-merge --export-sources data/export/ --db data/splunk_docs.db # export per-source DBs + manifest.json
uv run pytest tests/                                                        # run test suite (36 tests)
```

---

## Key Architectural Decisions

### Source-agnostic design
Everything downstream of `config.py` is source-agnostic. Adding a new crawl source requires only:
1. Add a `CrawlSource` entry to `PHASE1_SOURCES` in `config.py`
2. Zero other changes

`CrawlSource` fields relevant when adding a new source:
- `crawl_delay` — set to match `robots.txt` `Crawl-delay` (default 0.5 s)
- `max_concurrency` — set to 1 for sources with a `Request-rate: 1/N` constraint (e.g. Lantern)
- `blocked_path_prefixes` — full URL prefixes matching `robots.txt` `Disallow` rules; replaces the old hardcoded `_BLOCKED_PREFIXES` in `crawler.py`

### `source` + `version` columns on every row
Every document row in the DB stores its source ID and product version. Search results always include this metadata so it is always clear which version of which product a result is from.

### Version filter on search tools
`search_docs()` and `search_docs_semantic_from_matrix()` accept `version: str | None`. When set, it matches rows where **either** `d.version = ?` OR the `version_tags` JSON array contains the requested version (via `json_each`). This is how shared rows (collapsed by `run_version_merge_pass`) are found when querying for an older version. **Critically, the `is_duplicate = 0` dedup filter is bypassed when `version` is specified** — version-targeted queries must see all docs for that version regardless of whether identical content exists in a higher-priority source.

### Option B — cross-version content deduplication
`run_version_merge_pass(conn, source_pairs)` runs at `merge_dbs` time. For each (derived_source, parent_source) pair from `get_source_version_pairs()`, it finds derived rows whose `content_md_hash` matches a parent row. Those derived rows are deleted; the parent row's `version_tags` is updated to include the derived version (e.g. `["8.5","8.4"]`). Result: DB size stays bounded when n-1 sources are added; shared pages are found by both version queries.

### Module-level DB singleton in `server.py`
DB connection opened once on first use, reused across all tool calls. Simpler and more reliable than MCP framework lifespan API for this use case.

### BM25 title weighting
`bm25(documents_fts, 10.0, 1.0)` weights title matches 10× higher than body matches. Lower score = better match (SQLite BM25 convention).

### Document chunking — large pages split for precise FTS and embedding retrieval
Documents over **8,000 characters** are split into overlapping **1,500-character chunks** (200-char overlap) by a post-crawl `_chunk_pass()` in `cli.py`. Each chunk is stored as a separate `documents` row with `chunk_of = parent_url` and `chunk_index`. The parent row is marked `has_chunks = 1` and excluded from FTS/embedding search; `get_page(url)` reassembles chunks transparently. If `get_page` receives a chunk URL it redirects to the parent automatically.

Chunking constants (`CHUNK_THRESHOLD = 8000`, `CHUNK_SIZE = 1500`, `CHUNK_OVERLAP = 200`) live at the top of `db.py`. Chunk pass runs before the embed pass so chunk rows get embeddings instead of the parent.

### Vector search — chunk-level embeddings, in-process cosine similarity
Short documents (≤ 8,000 chars) are embedded whole; long documents are embedded per chunk (parent rows skipped). Embeddings are 384-dim float32 BLOBs stored on `documents.embedding`. At query time all non-parent embeddings load into a NumPy matrix; dot product computed in-process. Results deduplicated by canonical (parent) URL — highest-scoring chunk per document wins. Model is **eagerly loaded at server startup** (`SentenceTransformer` instantiated at module level in `server.py`) so the first `search_docs_semantic` call has no model-load penalty.

---

## Database Schema Summary

```sql
documents          -- one row per page or chunk; url UNIQUE
                   --   has_chunks      INTEGER DEFAULT 0  (1 = split into chunks; exclude from search)
                   --   chunk_of        TEXT               (NULL = not a chunk; parent URL for chunks)
                   --   chunk_index     INTEGER            (0-based position within parent)
                   --   embedding       BLOB               (384-dim float32; NULL for has_chunks=1 parents)
                   --   content_hash    TEXT               SHA-256 of raw HTML (incremental re-crawl skip)
                   --   content_md_hash TEXT               SHA-256 of extracted Markdown (Option B dedup)
                   --   version_tags    TEXT               JSON array of versions, e.g. '["8.5","8.4"]'
                   --   is_duplicate    INTEGER DEFAULT 0  (1 = same content_md_hash in higher-priority source)
documents_fts      -- FTS5 virtual table (content=documents); auto-synced via triggers
crawl_state        -- per-URL crawl status; used by crawler only, not MCP server
                   --   status: 'fetched' | 'skipped' | 'failed' | 'dead'
                   --   'dead' = HTTP 404; excluded from retries permanently
                   --   'failed' URLs retried on next incremental crawl
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
| `search_docs` | BM25 FTS5 keyword search; best for exact terms, config key names, quoted phrases |
| `search_docs_semantic` | Cosine-similarity vector search (all-MiniLM-L6-v2); best for natural-language / concept queries |
| `get_page` | Full Markdown content by exact URL |
| `list_sections` | Source → section → page count inventory |
| `browse_section` | All pages in a section (title + URL list) |
| `get_index_info` | DB stats: total pages, embedded pages, sources, last crawl time, DB size |

### Tool usage decision tree (encoded in server `instructions`; target 3–4 calls per question)

| Situation | Recommended path | Target calls |
|-----------|-----------------|--------------|
| Topic known, section known | `browse_section` → `get_page` | 2 |
| Topic known, section uncertain | `list_sections` → `browse_section` → `get_page` | 3 |
| Unknown — exact term / config key | `search_docs` → `get_page` | 2–3 |
| Unknown — concept / natural language | `search_docs_semantic` → `get_page` | 2–3 |
| Poor results from first search | Switch tools once → `get_page` → **STOP** | ≤4 |

**Hard limits per question:** 2 search calls total · 1 `list_sections` call · 3 `get_page` calls · 0 `get_index_info` calls unless user asks.

The full decision tree text lives in the `instructions=` argument to `FastMCP(...)` in `server.py`. Edit it there — do not duplicate it here.

---

## Known Crawler Behaviours (hard-won lessons)

### Redirect-aware link extraction
`help.splunk.com` section seed URLs (e.g. `.../administer/8.5`) redirect to a deeper page. The HTML at the redirect destination uses relative hrefs designed to be resolved against the *redirect destination's* directory, not the original seed URL. `_process_url` captures `final_url = _normalise_url(str(resp.url)) or url` immediately after the response and passes that to `_extract_links()`. This was a critical bug fix — using the pre-redirect URL produced doubled paths that 404'd silently.

### Version segment filtering in `_is_target_url`
The URL prefix `splunk-enterprise-security-8/` matches all versions (8.0, 8.1, 8.2 … 8.5). The crawler extracts version-number path segments from the URL and rejects any URL where a version segment is present but doesn't match `source.version`. This prevents cross-version nav links from pulling in older ES docs. The admin-manual source is unaffected (its version is baked into the `url_prefix`).

### Crawler retry behaviour
Two layers of retry for transient failures:
1. **Per-request retry** (`_process_url`): 3 attempts with 2/4/8 s delays for `TimeoutException`, `ConnectError`, `ReadError`, and 5xx responses.
2. **Post-BFS retry pass** (`crawl_source`): after the main BFS finishes, all URLs still in `crawl_state` with `status='failed'` are re-queued and attempted once more. Recovers from brief outages mid-crawl.
3. **Next incremental run**: `get_visited_urls()` excludes `status='failed'` rows, so pages that fail all retries are automatically re-attempted on the next crawl without `--full`.

**Exit code policy:** `splunk-crawl` exits 1 only if the failure rate exceeds 5% of total pages attempted. A handful of transient failures exits 0.

### When to use `--full`
`crawl_state` records every attempted URL. If a crawl run contained bugs (e.g. malformed URLs were visited and recorded as fetched/failed), a subsequent incremental crawl will skip those URLs. Always use `uv run splunk-crawl --full` after fixing crawler URL-handling bugs to force a clean re-crawl.

### Lantern URL structure differences from help.splunk.com
- **No version numbers in paths** — `version="current"` and the version-segment filter in `_is_target_url` never triggers (no numeric segments present), so no false rejections.
- **PascalCase_with_underscores** path segments (e.g. `Security_Use_Cases`) instead of kebab-case.
- **Up to 4 path levels** — `/{Section}/{Subsection}/{Group}/{Article}`. `parse_url_metadata` maps level 1 → `section`, level 2 → `subsection`, last segment → `slug`; the intermediate level 3 group (e.g. `Optimizing_storage`) is not stored as metadata but is preserved in the URL itself.
- **robots.txt constraints** — `Crawl-delay: 5`, `Request-rate: 1/5`. Handled by `crawl_delay=5.0, max_concurrency=1` on the `CrawlSource`; the crawler applies these automatically without any CLI flags needed.
- **Blocked paths** — `/Special:*`, `/Template:*`, `/User:*`, `/deki/`, `/@*` per `robots.txt`. Stored in `blocked_path_prefixes` on the source; `_normalise_url()` strips query strings so `?action=` and `?title=Special:` variants are already neutralised before the prefix check.

---

## Conventions to Follow

- **Never modify crawler, DB, or server code to handle a new source** — only `config.py`
- **Always store version metadata** in every document row
- **Version filter bypasses dedup** — `is_duplicate=0` filter is skipped when `version=` is set; this is intentional so version-specific queries see all docs
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
