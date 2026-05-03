# Coding Conventions

**Analysis Date:** 2026-05-03

## Naming Patterns

**Files:**
- `snake_case.py` throughout: `crawler.py`, `extractor.py`, `cli.py`, `db.py`, `server.py`, `config.py`, `merge.py`, `setup.py`
- Test files prefixed `test_`: `tests/test_extractor.py`, `tests/test_crawler.py`

**Functions:**
- `snake_case` for all functions
- Public/exported helpers: plain names — `crawl_source`, `get_connection`, `upsert_document`, `search_docs`
- Private helpers: single leading underscore — `_normalise_url`, `_is_target_url`, `_extract_markdown`, `_build_parser`, `_chunk_pass`, `_embed_pass`, `_dedup_pass`
- DB query helpers follow a consistent pattern: `get_<thing>`, `mark_<thing>`, `run_<thing>_pass`

**Variables:**
- `snake_case` throughout
- Module-level singletons prefixed with underscore: `_db`, `_embed_model`, `_embed_matrix`, `_embed_rows`
- Module-level regex compiled as constants: `_VERSION_SEG_RE`, `_HEADING_RE`, `_PARA_BREAK_RE`
- Module-level config lists: `_ES_SECTIONS`, `_CLOUD_SECTIONS`, `_HELP_BLOCKED`, `_LANTERN_BLOCKED`

**Types/Classes:**
- `PascalCase` for dataclasses: `CrawlSource`, `CrawlStats`, `ExtractedPage`
- Constants in `UPPER_SNAKE_CASE`: `CHUNK_THRESHOLD`, `CHUNK_SIZE`, `CHUNK_OVERLAP`, `PHASE1_SOURCES`, `SOURCES_BY_ID`, `DB_PATH`, `DOCS_DIR`, `CRAWL_HEADERS`

## Code Style

**Formatter/Linter:**
- Ruff with rules `E`, `F`, `I` (pycodestyle errors, pyflakes, isort) — configured in `pyproject.toml`
- No explicit line-length override observed; default Ruff 88-char limit applies

**String formatting:**
- f-strings used consistently for interpolation
- `%`-style used exclusively for `logging.*()` calls (avoids eager string construction)
  ```python
  logger.info("[%s] Retry pass: re-attempting %d failed URL(s)…", source.source_id, len(failed))
  logger.warning(f"  FAIL {url}: {last_exc}")  # f-string used for short one-liners
  ```

**Multi-line SQL:**
- Triple-quoted strings for all SQL — never single-line for queries with multiple clauses
- Named parameters (`:name`) used in INSERT/UPDATE statements; positional `?` used in SELECT filters

**Blank lines:**
- Module-level section dividers use a consistent banner comment style:
  ```python
  # ---------------------------------------------------------------------------
  # Section name
  # ---------------------------------------------------------------------------
  ```

## Import Organization

**Order (enforced by Ruff `I` rule):**
1. Standard library (`asyncio`, `hashlib`, `re`, `json`, `sqlite3`, `pathlib`, etc.)
2. Third-party (`httpx`, `bs4`, `trafilatura`, `mcp`, `numpy`, `sentence_transformers`)
3. Local package (`from .config import ...`, `from .db import ...`)

**Lazy imports for heavy optional deps:**
- `sentence_transformers` is imported inside `_embed_pass()` in `cli.py` to avoid paying model-load cost when only crawling
- `numpy` imported at top of `server.py` (needed at startup) but also imported lazily with `import numpy as np` inside `db.py` functions that are not called at import time
- `xml.etree.ElementTree` imported inside `_fetch_sitemap_urls()` in `crawler.py`

**Aliasing:**
```python
from .db import search_docs as db_search
from .db import search_docs_semantic_from_matrix as db_search_semantic
from .db import get_page as db_get_page
```
Used in `server.py` to avoid shadowing the MCP tool functions that share the same names.

## Adding a New Crawl Source

The codebase is deliberately source-agnostic. Only `src/splunk_docs_mcp/config.py` ever needs editing:

1. Add a new `CrawlSource(...)` entry to `PHASE1_SOURCES`
2. Set `crawl_delay` to match `robots.txt` `Crawl-delay` (default 0.5)
3. Set `max_concurrency=1` if `robots.txt` specifies `Request-rate: 1/N`
4. Set `blocked_path_prefixes` to match `robots.txt` `Disallow` rules
5. Set `derive_from` if the site always links to current version and older URLs must be derived

No changes needed in `crawler.py`, `db.py`, `server.py`, `cli.py`, or `merge.py`.

Example of a minimal new source:
```python
CrawlSource(
    source_id="itsi",
    display_name="Splunk IT Service Intelligence 4.19",
    version="4.19",
    seed_urls=["https://help.splunk.com/en/splunk-it-service-intelligence/"],
    url_prefix="https://help.splunk.com/en/splunk-it-service-intelligence/",
    blocked_path_prefixes=_HELP_BLOCKED,
)
```

## Error Handling Patterns

**HTTP retry logic in `crawler.py`:**
- 3 attempts (`_MAX_RETRIES = 3`) with exponential delays `_RETRY_DELAYS = (2.0, 4.0, 8.0)`
- Retried exceptions: `httpx.TimeoutException`, `httpx.ConnectError`, `httpx.ReadError`, and `5xx` HTTP status codes
- `4xx` errors (except 404) outside the source URL prefix are treated as auth-gate skips, not failures
- `404` responses are marked `status='dead'` in `crawl_state` and permanently excluded from retries

**Post-BFS retry pass:**
- After the main BFS completes, all URLs still marked `status='failed'` are re-queued once
- Implemented in `crawl_source()` in `crawler.py` — runs before `conn.close()`

**Exit code policy in `cli.py`:**
- Exit 1 only if `failure_rate > 0.05` AND `total_processed > 0`
- A handful of permanent 404s or transient failures exits 0
- Logged at `WARNING` level when the threshold is exceeded:
  ```python
  if failure_rate > 0.05 and total_processed > 0:
      logger.warning("Failure rate %.1f%% (%d/%d) exceeds 5%% threshold — exiting with code 1.", ...)
      return 1
  ```

**SQLite migration pattern:**
- Schema additions use `ALTER TABLE ADD COLUMN` wrapped in `try/except sqlite3.OperationalError: pass`
- This makes `init_db()` safe to call on both new and existing databases:
  ```python
  try:
      conn.execute("ALTER TABLE documents ADD COLUMN embedding BLOB")
      conn.commit()
  except sqlite3.OperationalError:
      pass  # column already exists
  ```

**MCP tool error returns:**
- Tools return `[{"error": "message"}]` for invalid input (unknown source)
- Tools return `[{"message": "..."}]` for empty results or missing prerequisites (no embeddings)
- Never raise exceptions to the MCP layer — always return structured dicts

## Logging Patterns

**Framework:** Python stdlib `logging`

**Configuration:**
- `cli.py` sets up logging in `main()` via `logging.basicConfig()` with `%(asctime)s  %(levelname)-8s  %(message)s` format, `%H:%M:%S` timestamp, to `sys.stderr`
- `server.py` sets up logging at module level (before FastMCP init) with `[splunk-mcp]` prefix, to `sys.stderr`
- Every module calls `logger = logging.getLogger(__name__)` at module level

**Level discipline:**
- `DEBUG`: per-URL events that would be noisy in production — `"  SKIP {url}"`, `"  REDIR {url} → {final_url}"`
- `INFO`: crawl lifecycle events, post-crawl pass summaries, tool call timings
- `WARNING`: transient failures, retry attempts, exceeded failure thresholds, skipped auth-gated URLs

**Crawler log prefixes:**
- `[{source_id}]` prefix on all source-level log lines
- Per-URL action prefixes: `+ [source]` (stored), `SKIP`, `REDIR`, `FAIL`, `DEAD`, `AUTH-SKIP`

**MCP server timing:**
- Every tool wraps its body in `try/finally` and logs duration via `time.perf_counter()`:
  ```python
  t0 = time.perf_counter()
  try:
      ...
  finally:
      logger.info("search_docs(query=%r, ...) — %.1f ms", query, ..., (time.perf_counter() - t0) * 1000)
  ```

## Type Annotations

**Usage:** Full type annotations on all public function signatures; less strict on private helpers.

**Style:**
- `str | None` union syntax (Python 3.10+), not `Optional[str]`
- `list[str]`, `dict[str, CrawlSource]` etc. — built-in generics, not `typing.List`
- `Annotated[T, Field(...)]` used for MCP tool parameters (required by FastMCP/pydantic)
- Return type on all public functions; often omitted on private helpers with obvious returns
- Forward-reference strings for numpy types to avoid import at module level:
  ```python
  def get_all_embeddings(conn) -> "tuple[numpy.ndarray, list[dict]]":  # noqa: F821
  ```

## Async Patterns

**BFS crawler (`crawler.py`):**
- `asyncio.Queue` shared across N worker coroutines; `queue.join()` for correct termination
- `asyncio.create_task(worker())` to spawn workers; cancelled with `w.cancel()` after `join()`
- `asyncio.gather(*workers, return_exceptions=True)` to suppress `CancelledError` from cancelled workers
- `asyncio.Lock` (`conn_lock`) protects all SQLite writes and `visited` set mutations
- `await asyncio.sleep(delay + jitter)` for rate limiting after each fetch
- Workers are async closures that capture enclosing scope; defined inside `crawl_source()`

**Entry point:**
- `asyncio.run(_run(args))` in `cli.py main()` — single event loop per process

**httpx usage:**
- `httpx.AsyncClient` created once per worker (not per-request) inside `async with` block
- `follow_redirects=True` and `httpx.Timeout(15.0)` set at client construction

## SQL Query Patterns

**Connection factory (`db.py`):**
```python
conn = sqlite3.connect(db_path, check_same_thread=False)
conn.row_factory = sqlite3.Row       # all rows returned as sqlite3.Row (dict-accessible)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA synchronous=NORMAL")
```

**Parameter binding:**
- Named parameters (`:name`) for INSERT/UPDATE with dict arguments
- Positional `?` for SELECT filter conditions, appended to a `params: list`
- Dynamic WHERE clause built by string concatenation with empty `filters = ""` or `source_filter = ""` pattern:
  ```python
  params: list = [query]
  filters = ""
  if source:
      filters += " AND d.source = ?"
      params.append(source)
  ```

**Row access:**
- `row["column_name"]` throughout (sqlite3.Row acts like a dict)
- `dict(row)` to convert to a plain dict for return from helper functions
- `.fetchone()` for single-row lookups; `.fetchall()` for list results

**Upsert pattern:**
```sql
INSERT INTO documents (...)
VALUES (...)
ON CONFLICT(url) DO UPDATE SET
    col = excluded.col, ...
```

**FTS5 queries:**
```sql
SELECT ... FROM documents_fts
JOIN documents d ON d.id = documents_fts.rowid
WHERE documents_fts MATCH ?
  AND d.has_chunks = 0
  {filters}
ORDER BY bm25(documents_fts, 10.0, 1.0)
```

**Commits:**
- `conn.commit()` called after every write operation — no explicit transaction management beyond implicit autocommit on commit

## Constants and Configuration

**Location:** All project-wide constants live in `src/splunk_docs_mcp/config.py` or at the top of the module that owns them.

**Chunking constants (top of `db.py`):**
```python
CHUNK_THRESHOLD = 8_000   # characters; documents longer than this are split
CHUNK_SIZE      = 1_500   # characters per chunk
CHUNK_OVERLAP   = 200     # overlap between consecutive chunks
```

**Retry constants (top of `crawler.py`):**
```python
_MAX_RETRIES = 3
_RETRY_DELAYS = (2.0, 4.0, 8.0)  # seconds between attempts
```

**Path constants (`config.py`):**
```python
_PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = _PROJECT_ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
DB_PATH  = DATA_DIR / "splunk_docs.db"
```

**Numeric literal style:** Underscore separators used for readability: `8_000` not `8000`.

## Comment Style

**Module docstrings:** All modules have a top-level docstring explaining purpose, design decisions, and key patterns (not just "what" but "why"). These are the primary documentation for each module.

**Section banners:** Used to organise modules into logical sections:
```python
# ---------------------------------------------------------------------------
# Section name
# ---------------------------------------------------------------------------
```

**Inline comments:** Used when a decision is non-obvious or could be misread as a bug:
```python
# Pre-load visited URLs for incremental resume
# Cap concurrency at source.max_concurrency when set (e.g. 1 for Lantern).
# Don't exit 1 when total_processed == 0: this means every URL attempted...
```

**SQL inline comments:** Schema DDL uses trailing `--` comments to document column semantics directly in the SQL string.

**`# noqa:` suppressions:** Used sparingly for intentional lazy imports (`# noqa: PLC0415`) and forward-reference type strings (`# noqa: F821`).

**What is NOT commented:**
- Obvious variable assignments
- Standard library calls with self-documenting names
- Loop bodies where intent is clear from context

---

*Convention analysis: 2026-05-03*
