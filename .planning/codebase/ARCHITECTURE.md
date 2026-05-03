<!-- refreshed: 2026-05-03 -->
# Architecture

**Analysis Date:** 2026-05-03

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│                        GitHub Actions (weekly)                        │
│  crawl matrix (7 sources) + crawl-derived (3 n-1 sources)            │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ uv run splunk-crawl --sources X --db X.db
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    splunk-crawl  (cli.py)                             │
│  1. crawl_source() per source  (crawler.py, async BFS)               │
│  2. _chunk_pass()  — split docs > 8000 chars into 1500-char chunks   │
│  3. _embed_pass()  — generate 384-dim float32 embeddings (MiniLM)    │
│  4. _dedup_pass()  — mark cross-source duplicates is_duplicate=1     │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ upsert_document() / chunk_document()
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    SQLite DB  data/splunk_docs.db                     │
│  documents        — pages + chunks + embeddings (BLOB)               │
│  documents_fts    — FTS5 content table (BM25, porter unicode61)      │
│  crawl_state      — per-URL crawl progress                           │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ merge_dbs() + export_sources()
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│               splunk-merge  (merge.py)                                │
│  version merge pass  — collapse identical cross-version rows          │
│  FTS5 rebuild        — rebuild index after merge                      │
│  dedup pass          — mark cross-source duplicates                   │
│  export_sources()    — write per-source DBs + manifest.json           │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ GitHub Release assets
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                splunk-setup  (setup.py)                               │
│  fetch manifest.json → interactive source selection menu              │
│  download per-source DB(s) → merge locally → data/splunk_docs.db     │
└────────────────────────┬─────────────────────────────────────────────┘
                         │ SQLite read (WAL mode)
                         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                  splunk-mcp  (server.py)                              │
│  6 MCP tools via FastMCP stdio transport                              │
│  Module-level: DB connection + embedding matrix + MiniLM model       │
└──────────────────────────────────────────────────────────────────────┘
         ▲
         │ MCP stdio protocol
         │
┌────────┴───────────────┐
│  Claude Desktop /      │
│  Claude Code           │
└────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| `config.py` | All crawl source definitions; paths; HTTP headers | `src/splunk_docs_mcp/config.py` |
| `extractor.py` | HTML → Markdown; title extraction; URL metadata parsing; .md file writer | `src/splunk_docs_mcp/extractor.py` |
| `crawler.py` | Async BFS crawl per source; link extraction; URL filtering; retry logic | `src/splunk_docs_mcp/crawler.py` |
| `db.py` | SQLite schema; all read/write helpers; chunking; embedding storage; dedup logic | `src/splunk_docs_mcp/db.py` |
| `cli.py` | `splunk-crawl` entry point; orchestrates crawl → chunk → embed → dedup passes | `src/splunk_docs_mcp/cli.py` |
| `merge.py` | `splunk-merge` entry point; merges per-source DBs; exports per-source DBs + manifest | `src/splunk_docs_mcp/merge.py` |
| `server.py` | MCP server; 6 tool definitions; module-level DB + embedding matrix + model | `src/splunk_docs_mcp/server.py` |
| `setup.py` | `splunk-setup` entry point; downloads release assets; runs local merge | `src/splunk_docs_mcp/setup.py` |

## Pattern Overview

**Overall:** Pipeline architecture — crawl → enrich → index → serve.

**Key Characteristics:**
- Source-agnostic: all downstream code reads from `CrawlSource` dataclass; adding a source requires only a new entry in `PHASE1_SOURCES` in `config.py`
- Write path (crawl/merge) and read path (MCP server) are strictly separated; they share only the DB schema
- SQLite is the single source of truth for both FTS5 keyword search and vector search (embeddings stored as BLOBs)
- No in-process state shared between write and read paths; WAL mode allows concurrent access

## Layers

**Configuration Layer:**
- Purpose: Central definition of all crawl sources, paths, HTTP headers
- Location: `src/splunk_docs_mcp/config.py`
- Contains: `CrawlSource` dataclass, `PHASE1_SOURCES` list, `SOURCES_BY_ID` dict, path constants
- Depends on: nothing (stdlib only)
- Used by: `crawler.py`, `cli.py`, `merge.py`, `server.py`, `setup.py`

**Extraction Layer:**
- Purpose: Convert raw HTML into structured Markdown with URL-derived metadata
- Location: `src/splunk_docs_mcp/extractor.py`
- Contains: `ExtractedPage` dataclass, `extract_page()`, `parse_url_metadata()`, `write_markdown_file()`
- Depends on: `config.py`, trafilatura, BS4, markdownify
- Used by: `crawler.py`

**Crawler Layer:**
- Purpose: Async BFS HTTP crawl; discovers and fetches all in-scope pages
- Location: `src/splunk_docs_mcp/crawler.py`
- Contains: `crawl_source()`, `_process_url()`, `_extract_links()`, `_is_target_url()`, `_normalise_url()`
- Depends on: `config.py`, `db.py`, `extractor.py`, httpx, BeautifulSoup
- Used by: `cli.py`

**Database Layer:**
- Purpose: All SQLite interactions — schema, write helpers, read helpers, chunking, embeddings, dedup
- Location: `src/splunk_docs_mcp/db.py`
- Contains: `init_db()`, `upsert_document()`, `chunk_document()`, `search_docs()`, `get_all_embeddings()`, `run_dedup_pass()`, `run_version_merge_pass()`, and more
- Depends on: stdlib only (sqlite3, json, hashlib)
- Used by: `crawler.py`, `cli.py`, `merge.py`, `server.py`

**CLI Layer:**
- Purpose: Orchestrate the full write pipeline; expose `splunk-crawl` command
- Location: `src/splunk_docs_mcp/cli.py`
- Contains: `main()`, `_run()`, `_chunk_pass()`, `_embed_pass()`, `_dedup_pass()`
- Depends on: `config.py`, `crawler.py`, `db.py`, sentence-transformers
- Used by: entry point only

**Merge Layer:**
- Purpose: Combine per-source DBs; run version collapse and dedup; export per-source DBs + manifest
- Location: `src/splunk_docs_mcp/merge.py`
- Contains: `merge_dbs()`, `export_sources()`, `_export_source_db()`
- Depends on: `config.py`, `db.py`
- Used by: `setup.py` (local merge after download), `splunk-merge` CLI, GitHub Actions

**Server Layer:**
- Purpose: Expose MCP tools; hold module-level singletons (DB, embedding matrix, model)
- Location: `src/splunk_docs_mcp/server.py`
- Contains: `FastMCP` instance, 6 tool functions, `run()`
- Depends on: `config.py`, `db.py`, mcp SDK, sentence-transformers, numpy
- Used by: `splunk-mcp` entry point

**Setup Layer:**
- Purpose: User-facing download CLI; fetches release assets; runs local merge
- Location: `src/splunk_docs_mcp/setup.py`
- Contains: `main()`, `_fetch_release()`, `_fetch_manifest()`, `_select_sources()`, `_download_file()`
- Depends on: `config.py`, `merge.py`, httpx
- Used by: `splunk-setup` entry point

## Data Flow

### Primary Crawl Path

1. `cli.py:main()` parses args, resolves `CrawlSource` objects from `SOURCES_BY_ID`
2. For sources with `derive_from` set, `get_crawled_urls_for_source()` fetches parent URLs from DB and substitutes the version segment to generate derived seeds (`cli.py:_run()` lines ~153–180)
3. `crawler.py:crawl_source()` initialises a `asyncio.Queue`, seeds it, spawns N worker coroutines
4. Each worker calls `_process_url()`: HTTP GET → SHA-256 hash check → `extractor.extract_page()` → `db.upsert_document()` → `db.mark_crawl_state()` → `_extract_links()` → enqueue new links
5. After BFS completes, a retry pass re-attempts all `status='failed'` URLs
6. `cli.py:_chunk_pass()` calls `db.get_documents_needing_chunking()` then `db.chunk_document()` for each large doc
7. `cli.py:_embed_pass()` loads `SentenceTransformer('all-MiniLM-L6-v2')`, encodes `title + content_md`, calls `db.update_embedding()`
8. `cli.py:_dedup_pass()` calls `db.run_dedup_pass()` to mark cross-source duplicates

### GitHub Actions Release Path

1. `crawl` job matrix (7 parallel jobs) runs `splunk-crawl --full` per source into `data/<source>.db`
2. `crawl-derived` job matrix (3 jobs, runs after `crawl`) derives n-1 seeds from parent artifact DBs
3. `merge-and-release` job downloads all per-source DB artifacts, runs `splunk-merge` to produce `splunk_docs.db`, then `splunk-merge --export-sources` to generate per-source DBs + `manifest.json`, then publishes a GitHub Release
4. User runs `splunk-setup`, downloads manifest, selects sources, downloads per-source DBs, local `merge_dbs()` produces `data/splunk_docs.db`

### MCP Query Path

1. `server.py` module loads at startup: opens DB, calls `get_all_embeddings()` to build `_embed_matrix` (NumPy array, shape `(N, 384)`), loads `SentenceTransformer` model
2. `search_docs` tool: calls `db.search_docs()` → FTS5 MATCH query with `bm25(documents_fts, 10.0, 1.0)` weighting
3. `search_docs_semantic` tool: encodes query with `_embed_model.encode()`, calls `db.search_docs_semantic_from_matrix()` → NumPy dot product against `_embed_matrix`
4. `get_page` tool: calls `db.get_page()` → reassembles chunks if `has_chunks=1`
5. `browse_section` / `list_sections` / `get_index_info` tools: direct SQL queries via `db.py` helpers

**State Management:**
- Module-level singletons in `server.py`: `_db` (sqlite3.Connection), `_embed_model` (SentenceTransformer), `_embed_matrix` (numpy.ndarray), `_embed_rows` (list[dict])
- These are initialised once at import time and reused across all tool calls — no lazy loading in the server

## SQLite Schema

### `documents` table
The core store. One row per crawled page or chunk.

```sql
CREATE TABLE documents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL UNIQUE,      -- canonical URL (or {url}#chunk-N for chunks)
    title        TEXT NOT NULL,
    source       TEXT NOT NULL,             -- e.g. 'enterprise-security'
    version      TEXT NOT NULL,             -- e.g. '8.5', '10.2', 'current'
    section      TEXT,                      -- first non-version path segment
    subsection   TEXT,                      -- second non-version path segment
    slug         TEXT,                      -- last path segment
    file_path    TEXT NOT NULL,             -- relative path under data/docs/
    content_md   TEXT NOT NULL,             -- Markdown body
    content_hash TEXT NOT NULL,             -- SHA-256 of raw HTML (incremental re-crawl skip)
    content_md_hash TEXT,                   -- SHA-256 of extracted Markdown (cross-version dedup)
    version_tags TEXT,                      -- JSON array e.g. '["8.5","8.4"]'
    crawled_at   TEXT NOT NULL,             -- ISO-8601 UTC
    embedding    BLOB,                      -- 384-dim float32 (all-MiniLM-L6-v2)
    has_chunks   INTEGER DEFAULT 0,         -- 1 = split into chunks; excluded from search
    chunk_of     TEXT,                      -- NULL for parent rows; parent URL for chunk rows
    chunk_index  INTEGER,                   -- 0-based position within parent
    is_duplicate INTEGER DEFAULT 0         -- 1 = same content in higher-priority source
);
```

**Key column semantics:**
- `has_chunks=1` rows are excluded from FTS and vector search; `get_page()` reassembles them from chunk rows
- `chunk_of IS NOT NULL` rows are the searchable units for large documents; their `url` is `{parent_url}#chunk-N`
- `is_duplicate=1` rows are suppressed from search results unless `version=` is specified
- `version_tags` is updated by `run_version_merge_pass()` when an n-1 row is collapsed into the parent

### `documents_fts` virtual table
FTS5 content table — no text duplication, auto-synced via triggers.

```sql
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title,
    content_md,
    content=documents,      -- source of truth is documents table
    content_rowid=id,
    tokenize='porter unicode61'
);
```

Three triggers (`documents_ai`, `documents_ad`, `documents_au`) on `documents` keep `documents_fts` in sync on every INSERT/DELETE/UPDATE. The FTS index survives server restarts without rebuild. After `merge_dbs()` it is explicitly rebuilt: `INSERT INTO documents_fts(documents_fts) VALUES('rebuild')`.

BM25 query weights title 10x over body:
```sql
bm25(documents_fts, 10.0, 1.0)
```
Lower (more negative) score = better match.

### `crawl_state` table
Crawler-only. Records every URL attempted, used for incremental resume.

```sql
CREATE TABLE crawl_state (
    url          TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    status       TEXT NOT NULL,   -- 'fetched' | 'skipped' | 'failed' | 'dead'
    error        TEXT,
    attempted_at TEXT NOT NULL
);
```

`'failed'` URLs are excluded from `get_visited_urls()` so they are retried on the next crawl run. `'dead'` (HTTP 404) URLs are treated as visited and never retried.

### Indexes
```sql
idx_documents_source           ON documents(source)
idx_documents_version          ON documents(version)
idx_documents_section          ON documents(section)
idx_documents_source_section   ON documents(source, section)
idx_documents_chunk_of         ON documents(chunk_of)
idx_documents_content_hash     ON documents(content_hash)
idx_documents_content_md_hash  ON documents(content_md_hash)
```

## Async BFS Crawler Design

**Entry point:** `crawler.py:crawl_source()`

**Queue model:**
```
asyncio.Queue  ←  seeds (from CrawlSource.seed_urls + derived seeds + sitemap)
     ↓
N worker coroutines (default 3, capped by source.max_concurrency)
     ↓  _process_url()
     ├── HTTP GET (httpx.AsyncClient, follow_redirects=True, timeout=15s)
     ├── SHA-256 hash check → skip if unchanged (incremental mode)
     ├── extractor.extract_page() → upsert_document()
     ├── _extract_links() → enqueue new links
     └── asyncio.sleep(delay)  ← rate limiting
     ↓
queue.join()  ← waits for ALL items including newly discovered ones
     ↓
retry pass  ← re-queues all status='failed' URLs, runs workers again
```

**Incremental mode (default):**
- `get_visited_urls()` preloads all non-failed URLs from `crawl_state` into `visited` set
- `_process_url()` computes SHA-256 of fetched HTML; skips extraction if hash matches stored `content_hash`
- Links are still extracted from skipped pages so newly linked pages are discovered

**Version filtering (`_is_target_url()`):**
- All links must start with `source.url_prefix`
- Links starting with any `source.blocked_path_prefixes` entry are rejected
- Version segments (e.g. `8.0`, `8.1`) extracted from the URL path after the prefix; if any are present and none match `source.version`, the URL is rejected — prevents cross-version nav links from pulling in wrong-version content

**Redirect handling:**
- `final_url = _normalise_url(str(resp.url))` captures the post-redirect URL
- `_extract_links()` uses `final_url` as the `urljoin()` base so relative hrefs resolve correctly

**Per-request retry (within `_process_url()`):**
- 3 attempts for `TimeoutException`, `ConnectError`, `ReadError`, HTTP 5xx
- Delays: 2s, 4s, 8s
- HTTP 404 → mark `status='dead'` (never retried)
- 4xx after redirect outside source prefix → mark `status='skipped'` (auth-gated, not a crawl failure)

**Derived source seeding:**
- Sources with `derive_from` set (e.g. `enterprise-security-8-4` derives from `enterprise-security`) cannot be fully discovered by BFS because the site's nav always links to the current version
- `cli.py:_run()` fetches all `status='fetched'` URLs for the parent source and substitutes the parent version string for the derived version to generate candidate seed URLs
- These derived seeds are passed as `extra_seeds` to `crawl_source()`

## FTS5 Content Table Pattern

**Why content table:** Avoids duplicating text between `documents.content_md` and the FTS index. The FTS5 `content=documents` parameter means the index only stores positional data; the actual text is always read from `documents`.

**Trigger-based sync:** Three `AFTER INSERT/DELETE/UPDATE` triggers on `documents` issue the FTS5 shadow-table protocol operations. This means any `upsert_document()` call automatically keeps the FTS index current — no explicit FTS maintenance code in the application layer.

**Post-merge rebuild:** After `merge_dbs()` inserts rows via `ATTACH` + `INSERT OR IGNORE` (which bypasses triggers), the FTS index is rebuilt explicitly:
```sql
INSERT INTO documents_fts(documents_fts) VALUES('rebuild')
```

**Search query:**
```sql
SELECT ..., snippet(documents_fts, 1, '**', '**', '…', 32) AS snippet,
           bm25(documents_fts, 10.0, 1.0) AS score
FROM documents_fts
JOIN documents d ON d.id = documents_fts.rowid
WHERE documents_fts MATCH ?
  AND d.has_chunks = 0
  AND d.is_duplicate = 0   -- omitted when version= is specified
ORDER BY score
```

Parent rows (`has_chunks=1`) are excluded; chunk rows are searched and results are deduplicated back to canonical (parent) URLs in Python.

## Vector Search Design

**Embedding generation (crawl time):**
- Model: `all-MiniLM-L6-v2` via sentence-transformers (384-dim float32)
- Input: `f"{title}\n\n{content_md}"` for each document/chunk row
- Stored as raw `float32` bytes: `emb.astype("float32").tobytes()` → `documents.embedding` BLOB
- Parent rows (`has_chunks=1`) are skipped; chunk rows are embedded instead
- Hash reuse: `get_embedding_by_hash()` avoids re-encoding identical content across sources

**Matrix cache (server startup):**
- `server.py` calls `db.get_all_embeddings()` once at startup
- Returns `(matrix, rows)` where `matrix` is `np.ndarray` shape `(N, 384)` float32 and `rows` is a parallel list of metadata dicts
- Cached as module-level `_embed_matrix`, `_embed_rows`

**Query path:**
```python
q_vec = _embed_model.encode(query, normalize_embeddings=True).astype(np.float32)
scores = _embed_matrix @ q_vec          # dot product of unit-norm vectors = cosine similarity
ranked = np.argsort(scores)[::-1]       # descending similarity
```

**Deduplication:** Multiple chunks of the same parent may rank highly. Results are deduplicated by canonical URL (chunk's `chunk_of` field), keeping the highest-scoring chunk per document.

**Version/source filtering:** Applied via NumPy boolean mask before the dot product:
- `version` filter: matches rows where `r["version"] == version OR version in r["version_tags"]`
- Without `version`: `is_duplicate=False` mask applied (same logic as FTS search)

**Scaling note:** All embeddings load into RAM. Current corpus (~8,000+ chunk rows) is well within the sub-millisecond arithmetic regime. A vector DB would be needed above ~100k rows.

## Option B: Cross-Version Content Deduplication

**Problem:** n-1 sources (e.g. ES 8.4) share most content with the current version (ES 8.5). Without dedup, `search_docs()` returns duplicate results and DB size grows linearly with versions added.

**Solution:** `db.run_version_merge_pass(conn, source_pairs)` runs during `merge_dbs()`.

```
For each (derived_source, parent_source) pair [e.g. (enterprise-security-8-4, enterprise-security)]:
  1. Load all parent rows into a dict keyed by content_md_hash
  2. For each derived row:
     - If content_md_hash matches a parent row:
       a. Delete the derived row and its chunks from documents
       b. Update parent row's version_tags to include derived version
          e.g. version_tags: '["8.5"]' → '["8.5","8.4"]'
     - If no match: leave derived row in place (unique content)
```

**Version-targeted search:** When `version=` is specified in `search_docs()` or `search_docs_semantic()`, the `is_duplicate=0` filter is bypassed and the query matches both:
- Rows where `d.version = ?` (unique derived-version content)
- Rows where `version_tags` JSON array contains the requested version (shared canonical rows)

This means a search for `version='8.4'` finds both ES 8.4-specific pages and ES 8.5 pages whose content also covers 8.4.

**Cross-source dedup (is_duplicate flag):** `run_dedup_pass()` separately handles pages that appear verbatim across different sources (e.g. Enterprise and Cloud sharing content). Uses `content_md_hash` with a priority order (`_DEDUP_PRIORITY` in `db.py`) to pick the canonical row. Lower-priority duplicates get `is_duplicate=1` and are suppressed from search unless `version=` is set.

## Document Chunking Strategy

**Why chunk:** Large pages (e.g. configuration file references with hundreds of conf keys) produce poor FTS snippets and imprecise vector embeddings. Chunking ensures FTS snippets point to the relevant section and embeddings capture specific content rather than a page-wide average.

**Constants** (`db.py` top-level):
```python
CHUNK_THRESHOLD = 8_000   # chars; documents longer than this are split
CHUNK_SIZE      = 1_500   # chars per chunk
CHUNK_OVERLAP   = 200     # overlap between consecutive chunks
```

**Smart splitting strategy (`db._split_content_smart()`):**
1. Split on `##` / `###` heading boundaries (keeps config stanzas intact)
2. Greedily pack heading-sections into ≤1500-char chunks (`_accumulate_with_overlap()`)
3. If a chunk still exceeds `CHUNK_SIZE * 2`, split on paragraph breaks (`\n\n`)
4. Final fallback: character-based splitting with overlap (`_split_content()`)

**Chunk row format:**
- `url`: `{parent_url}#chunk-{i}`
- `title`: `{parent_title} [{i+1}/{n}]`
- `chunk_of`: `{parent_url}`
- `chunk_index`: 0-based
- Parent row: `has_chunks=1` (excluded from FTS and vector search)

**`get_page()` reassembly:**
- If called with a chunk URL → silently redirects to parent
- If called with a parent URL where `has_chunks=1` → fetches all chunk rows ordered by `chunk_index`, concatenates `content_md`

**`--rechunk` flag:** Deletes all existing chunk rows for the specified sources, resets `has_chunks=0` on parents, re-runs `_chunk_pass()` and `_embed_pass()`. Use when the chunking strategy changes.

## Distribution Model

**GitHub Actions workflow** (`.github/workflows/crawl-and-release.yml`):

```
crawl job (matrix: 7 sources, parallel)
  └─ splunk-crawl --full --db data/{source}.db
  └─ upload artifact: db-{source}

crawl-derived job (matrix: 3 n-1 sources, after crawl)
  └─ download parent artifact
  └─ splunk-crawl --full --db data/{source}.db --derive-db data/{parent}.db
  └─ upload artifact: db-{source}

merge-and-release job (after both crawl jobs)
  └─ download all db-* artifacts
  └─ splunk-merge {all .db files} --output data/splunk_docs.db
  └─ splunk-merge --export-sources data/export/ --db data/splunk_docs.db
  └─ gh-release: splunk_docs.db + splunk_docs_*.db + manifest.json
```

Schedule: every Sunday 02:00 UTC + `workflow_dispatch`.

**Release assets:**
- `splunk_docs.db` — full merged DB (all sources)
- `splunk_docs_{source_id}.db` — per-source DB (one per source)
- `manifest.json` — list of sources with `display_name`, `version`, `pages`, `shared_pages`, `parent_source_id`, `file_name`, `size_bytes`

**User setup flow:**
```
git clone / uv sync
uv run splunk-setup
  → fetch manifest.json from latest release
  → interactive menu grouped by product (roots + indented n-1 children)
  → download selected per-source DBs
  → if > 1 selected: merge_dbs() locally → data/splunk_docs.db
  → configure MCP → done
```

**n-1 auto-include:** When a user selects an n-1 source (e.g. ES 8.4), `setup.py:_select_sources()` automatically adds the parent source (ES 8.5) so that shared pages (accessible via `version_tags`) are present in the merged DB.

## Entry Points

**`splunk-mcp`:**
- Location: `src/splunk_docs_mcp/server.py:run()`
- Triggers: MCP client connection (Claude Desktop / Claude Code)
- Responsibilities: Start FastMCP stdio server; all tool calls are handled in this process

**`splunk-crawl`:**
- Location: `src/splunk_docs_mcp/cli.py:main()`
- Triggers: Manual or GitHub Actions cron
- Responsibilities: Full write pipeline — crawl + chunk + embed + dedup; incremental by default

**`splunk-merge`:**
- Location: `src/splunk_docs_mcp/merge.py:main()`
- Triggers: GitHub Actions aggregation job; optionally manual
- Responsibilities: Merge per-source DBs; run version merge + dedup; export per-source files

**`splunk-setup`:**
- Location: `src/splunk_docs_mcp/setup.py:main()`
- Triggers: User onboarding
- Responsibilities: Download pre-built DBs from GitHub Releases; local merge; produce `data/splunk_docs.db`

## Architectural Constraints

- **Threading:** Single-threaded event loop in the crawler (asyncio). The MCP server is synchronous (FastMCP stdio); all tool handlers are called sequentially. No worker threads.
- **Global state:** Three module-level singletons in `server.py` — `_db`, `_embed_model`, `_embed_matrix`/`_embed_rows`. Initialised at import time. The embedding matrix is not refreshed after startup; a server restart is required to pick up new embeddings after a crawl.
- **DB connection sharing:** `crawler.py` uses a single `sqlite3.Connection` shared across async worker coroutines, protected by `asyncio.Lock`. WAL mode allows concurrent reads from the MCP server during a crawl.
- **Circular imports:** None. Dependency direction is strictly: `server/cli/merge/setup` → `db` + `config`; `crawler` → `db` + `extractor` + `config`; `extractor` → `config`.
- **No test coverage for DB helpers:** `tests/` only covers `extractor.parse_url_metadata()` and `crawler._normalise_url()`, `_is_target_url()`, `_section_from_url()`. DB write/read helpers and the MCP server tools are untested.

## Anti-Patterns

### Adding source-specific logic to crawler.py or db.py

**What happens:** A developer adds an `if source.source_id == "lantern"` branch inside `_process_url()` or a query function.
**Why it's wrong:** Breaks the source-agnostic contract. Every source difference must be expressible as a `CrawlSource` field. The crawler and DB code must stay ignorant of individual source IDs.
**Do this instead:** Add a new field to `CrawlSource` in `config.py` and read it in `crawler.py` via `source.new_field`. See `blocked_path_prefixes`, `crawl_delay`, `max_concurrency`, `derive_from` for examples.

### Searching without version filter when the user names a version

**What happens:** `search_docs('correlation search', source='enterprise-security')` is called when the user asked about ES 8.4.
**Why it's wrong:** Without `version='8.4'`, the `is_duplicate=0` filter hides all shared rows (which are stored under ES 8.5's source), returning only the small set of 8.4-unique pages or no results at all.
**Do this instead:** Always pass `version=` when the user names a specific product version. The server `instructions=` text encodes this requirement.

### Reading the embedding matrix from the DB on every search call

**What happens:** `db.search_docs_semantic()` (the legacy path) reloads all embeddings from SQLite on every call.
**Why it's wrong:** 8000+ embeddings × 384 dims = ~12MB of BLOB reads per query, adding 50–200ms latency.
**Do this instead:** Use `db.get_all_embeddings()` at startup to build the module-level matrix cache, then call `db.search_docs_semantic_from_matrix()` with the cached matrix. This is what `server.py` does.

## Error Handling

**Strategy:** Fail-fast on unrecoverable errors (missing DB, bad source ID); degrade gracefully on transient network failures.

**Patterns:**
- Crawler: per-request retry (3 attempts, exponential delay) + post-BFS retry pass; exit code 1 only if failure rate exceeds 5%
- MCP tools: return `[{"error": "..."}]` or `[{"message": "..."}]` dicts rather than raising exceptions; tool callers see structured error responses
- Setup: `sys.exit()` on fatal errors (no release, download failure); prints warning and continues on manifest-fetch failure (falls back to monolithic DB)

## Cross-Cutting Concerns

**Logging:** `logging.basicConfig()` to stderr in `cli.py` and `server.py`. Format: `HH:MM:SS  LEVEL  message`. Crawler uses `logger.info()` for page-level events and `logger.debug()` for skipped pages. Server logs each tool call with latency in ms.
**Validation:** Source IDs validated against `SOURCES_BY_ID` at tool call time; unknown source returns error dict immediately.
**Authentication:** None. All crawl targets are public documentation. The MCP server has no authentication; it runs as a local stdio process.

---

*Architecture analysis: 2026-05-03*
