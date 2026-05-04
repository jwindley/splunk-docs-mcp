"""
SQLite database layer.

Schema
------
documents     — one row per crawled page (content + metadata + embedding BLOB)
documents_fts — FTS5 virtual table backed by `documents` (BM25 search)
crawl_state   — per-URL crawl progress (used by crawler; not read by server)

The FTS5 table uses the *content table* pattern: it holds a copy of the indexed
columns internally and INSERT/UPDATE/DELETE triggers on `documents` keep them in
sync automatically.  This means:
  - No text is duplicated in the Python layer
  - The index survives across server restarts (no rebuild on startup)
  - BM25 ranking and phrase search work out of the box via SQLite's porter stemmer

Embeddings
----------
Each document row stores a 384-dimensional float32 embedding (all-MiniLM-L6-v2)
as a BLOB in the `embedding` column.  Embeddings are generated at crawl time by
the post-crawl pass in cli.py.  The `search_docs_semantic` function loads all
embeddings into a NumPy matrix and computes cosine similarity in-process; this
is fast enough for the current corpus size (~9 000+ pages, ~20 000+ rows with chunks).

"""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec

from splunk_docs_mcp.config import PHASE1_SOURCES

# Heading and paragraph boundary patterns used by _split_content_smart
_HEADING_RE = re.compile(r'(?m)^(?=#{2,3} )')
_PARA_BREAK_RE = re.compile(r'\n{2,}')

# Chunking constants
CHUNK_THRESHOLD = 8_000   # characters; documents longer than this are split
CHUNK_SIZE      = 1_500   # characters per chunk
CHUNK_OVERLAP   = 200     # overlap between consecutive chunks


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode=WAL")   # readers don't block writers
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL") # safe with WAL; faster than FULL
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        -- Core document store ---------------------------------------------------
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            url          TEXT NOT NULL UNIQUE,
            title        TEXT NOT NULL,
            source       TEXT NOT NULL,   -- 'enterprise-security' | 'admin-manual' | …
            version      TEXT NOT NULL,   -- product version: '8.5', '10.2', …
            section      TEXT,            -- top-level section slug
            subsection   TEXT,            -- sub-section slug
            slug         TEXT,            -- final path segment
            file_path    TEXT NOT NULL,   -- relative path under data/docs/
            content_md   TEXT NOT NULL,   -- Markdown body (also in the .md file)
            content_hash TEXT NOT NULL,   -- SHA-256 of raw HTML for incremental re-crawl
            crawled_at   TEXT NOT NULL    -- ISO-8601 UTC
        );

        -- FTS5 index (content table — no text duplication) ----------------------
        CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
            title,
            content_md,
            content=documents,
            content_rowid=id,
            tokenize='porter unicode61'
        );

        -- Triggers to keep FTS5 in sync with documents --------------------------
        CREATE TRIGGER IF NOT EXISTS documents_ai
        AFTER INSERT ON documents BEGIN
            INSERT INTO documents_fts(rowid, title, content_md)
            VALUES (new.id, new.title, new.content_md);
        END;

        CREATE TRIGGER IF NOT EXISTS documents_ad
        AFTER DELETE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts, rowid, title, content_md)
            VALUES ('delete', old.id, old.title, old.content_md);
        END;

        CREATE TRIGGER IF NOT EXISTS documents_au
        AFTER UPDATE ON documents BEGIN
            INSERT INTO documents_fts(documents_fts, rowid, title, content_md)
            VALUES ('delete', old.id, old.title, old.content_md);
            INSERT INTO documents_fts(rowid, title, content_md)
            VALUES (new.id, new.title, new.content_md);
        END;

        -- Crawl progress (crawler-only; not read by MCP server) -----------------
        CREATE TABLE IF NOT EXISTS crawl_state (
            url          TEXT PRIMARY KEY,
            source       TEXT NOT NULL,
            status       TEXT NOT NULL,   -- 'fetched' | 'skipped' | 'failed' | 'dead'
            error        TEXT,
            attempted_at TEXT NOT NULL
        );

        -- Indexes ----------------------------------------------------------------
        CREATE INDEX IF NOT EXISTS idx_documents_source
            ON documents(source);
        CREATE INDEX IF NOT EXISTS idx_documents_version
            ON documents(version);
        CREATE INDEX IF NOT EXISTS idx_documents_section
            ON documents(section);
        CREATE INDEX IF NOT EXISTS idx_documents_source_section
            ON documents(source, section);

    """)
    conn.commit()

    # Add embedding column if it doesn't exist yet (migration for existing DBs).
    # SQLite ALTER TABLE ADD COLUMN is always safe: it adds a nullable column
    # with no default, so existing rows get NULL — correct for incremental embedding.
    try:
        conn.execute("ALTER TABLE documents ADD COLUMN embedding BLOB")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    # Chunking columns (migration for existing DBs) -------------------------
    # has_chunks=1  → this row has been split; exclude from FTS/embedding search
    # chunk_of      → non-NULL on chunk rows; points to the parent document URL
    # chunk_index   → 0-based position of this chunk within its parent
    for ddl in (
        "ALTER TABLE documents ADD COLUMN has_chunks INTEGER DEFAULT 0",
        "ALTER TABLE documents ADD COLUMN chunk_of TEXT",
        "ALTER TABLE documents ADD COLUMN chunk_index INTEGER",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_chunk_of ON documents(chunk_of)"
        )
    except sqlite3.OperationalError:
        pass
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash)"
    )
    # Deduplication column (migration for existing DBs) -------------------------
    # is_duplicate=1 → content_hash exists in a higher-priority source; excluded
    # from search unless the caller passes an explicit version= filter.
    try:
        conn.execute("ALTER TABLE documents ADD COLUMN is_duplicate INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Option B: content_md_hash + version_tags (migration for existing DBs) ----
    # content_md_hash — SHA-256 of extracted Markdown; used for cross-version
    # dedup (HTML differs but content is identical across Enterprise/Cloud).
    # version_tags — JSON array of versions this row's content applies to,
    # e.g. '["8.5","8.4"]' for a canonical row shared between two ES releases.
    for ddl in (
        "ALTER TABLE documents ADD COLUMN content_md_hash TEXT",
        "ALTER TABLE documents ADD COLUMN version_tags TEXT",
    ):
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_content_md_hash "
            "ON documents(content_md_hash)"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()

    # Vector index (sqlite-vec) — ANN search replacing the in-process numpy scan.
    # vec0 stores 384-dim float32 vectors; rowid matches documents.id.
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS vec_documents USING vec0(embedding float[384])"
    )
    conn.commit()

    # One-time migration: populate vec_documents from existing documents.embedding BLOBs.
    vec_count = conn.execute("SELECT COUNT(*) FROM vec_documents").fetchone()[0]
    if vec_count == 0:
        rows = conn.execute(
            "SELECT id, embedding FROM documents WHERE embedding IS NOT NULL"
        ).fetchall()
        if rows:
            conn.executemany(
                "INSERT INTO vec_documents(rowid, embedding) VALUES (?, ?)",
                [(r["id"], r["embedding"]) for r in rows],
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Crawler helpers (write path)
# ---------------------------------------------------------------------------


def get_content_hash(conn: sqlite3.Connection, url: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM documents WHERE url = ?", (url,)
    ).fetchone()
    return row["content_hash"] if row else None


def get_embedding_by_hash(conn: sqlite3.Connection, content_hash: str) -> bytes | None:
    """Return a stored embedding BLOB for any row sharing content_hash, or None.

    Used by the embed pass to copy an existing embedding rather than re-encoding
    identical content — works within a source (incremental re-crawl) and across
    sources/versions (once multi-version crawling is active).
    """
    row = conn.execute(
        "SELECT embedding FROM documents WHERE content_hash = ? AND embedding IS NOT NULL LIMIT 1",
        (content_hash,),
    ).fetchone()
    return row["embedding"] if row else None


def upsert_document(conn: sqlite3.Connection, doc: dict) -> None:
    """Insert or update a document row. The FTS5 triggers handle index sync."""
    doc["content_md_hash"] = hashlib.sha256(doc["content_md"].encode()).hexdigest()
    if "version_tags" not in doc:
        doc["version_tags"] = json.dumps([doc["version"]])
    conn.execute(
        """
        INSERT INTO documents
            (url, title, source, version, section, subsection, slug,
             file_path, content_md, content_hash, content_md_hash, version_tags, crawled_at)
        VALUES
            (:url, :title, :source, :version, :section, :subsection, :slug,
             :file_path, :content_md, :content_hash, :content_md_hash, :version_tags, :crawled_at)
        ON CONFLICT(url) DO UPDATE SET
            title            = excluded.title,
            content_md       = excluded.content_md,
            content_hash     = excluded.content_hash,
            content_md_hash  = excluded.content_md_hash,
            version_tags     = CASE
                                   WHEN excluded.content_md_hash != COALESCE(content_md_hash, '')
                                   THEN excluded.version_tags
                                   ELSE version_tags
                               END,
            crawled_at       = excluded.crawled_at,
            file_path        = excluded.file_path,
            section          = excluded.section,
            subsection       = excluded.subsection,
            slug             = excluded.slug,
            has_chunks       = CASE
                                   WHEN excluded.content_hash != content_hash THEN 0
                                   ELSE has_chunks
                               END
        """,
        doc,
    )
    # When content changes the ON CONFLICT resets has_chunks=0, leaving stale chunk
    # rows that chunk_document() will never overwrite (it only runs on has_chunks=0
    # rows longer than CHUNK_THRESHOLD).  Delete them now so they don't linger.
    conn.execute(
        "DELETE FROM documents WHERE chunk_of = ? "
        "AND NOT EXISTS (SELECT 1 FROM documents p WHERE p.url = ? AND p.has_chunks = 1)",
        (doc["url"], doc["url"]),
    )
    conn.commit()


def mark_crawl_state(
    conn: sqlite3.Connection,
    url: str,
    source_id: str,
    status: str,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO crawl_state (url, source, status, error, attempted_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            status = excluded.status,
            error  = excluded.error,
            attempted_at = excluded.attempted_at
        """,
        (url, source_id, status, error, now),
    )
    conn.commit()


def get_visited_urls(conn: sqlite3.Connection, source_id: str) -> set[str]:
    """Return all non-failed URLs already attempted for this source (for crawl resume).

    Failed URLs are excluded so they are automatically retried on the next
    incremental crawl run rather than being permanently skipped.
    """
    rows = conn.execute(
        "SELECT url FROM crawl_state WHERE source = ? AND status != 'failed'",
        (source_id,),
    ).fetchall()
    return {row["url"] for row in rows}


def get_failed_urls(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """Return URLs that failed on the most recent crawl of source_id."""
    rows = conn.execute(
        "SELECT url FROM crawl_state WHERE source = ? AND status = 'failed'",
        (source_id,),
    ).fetchall()
    return [row["url"] for row in rows]


def get_crawled_urls_for_source(conn: sqlite3.Connection, source_id: str) -> list[str]:
    """Return all successfully fetched URLs for source_id.

    Used by the URL derivation pass: takes 8.5 URLs and substitutes the version
    segment to generate candidate 8.4 / 8.3 seed URLs.
    """
    rows = conn.execute(
        "SELECT url FROM crawl_state WHERE source = ? AND status = 'fetched'",
        (source_id,),
    ).fetchall()
    return [row["url"] for row in rows]


def merge_source_db(conn: sqlite3.Connection, source_db_path: Path) -> int:
    """Merge all documents and crawl_state rows from source_db_path into conn.

    Uses ATTACH + INSERT OR IGNORE so existing URLs are skipped silently.
    The id column is excluded from the INSERT so SQLite auto-assigns new IDs
    in the target DB; INSERT triggers on documents keep documents_fts in sync.

    Returns the number of document rows successfully inserted.
    """
    conn.execute("ATTACH DATABASE ? AS src", (str(source_db_path),))
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO documents
            (url, title, source, version, section, subsection, slug,
             file_path, content_md, content_hash, content_md_hash, version_tags,
             crawled_at, embedding, has_chunks, chunk_of, chunk_index)
        SELECT
            url, title, source, version, section, subsection, slug,
            file_path, content_md, content_hash, content_md_hash, version_tags,
            crawled_at, embedding, has_chunks, chunk_of, chunk_index
        FROM src.documents
        """
    )
    merged = cursor.rowcount
    conn.execute(
        """
        INSERT OR IGNORE INTO crawl_state (url, source, status, error, attempted_at)
        SELECT url, source, status, error, attempted_at
        FROM src.crawl_state
        """
    )
    conn.commit()
    conn.execute("DETACH DATABASE src")
    return merged


# Source priority for dedup: lower index = higher priority (wins the dedup).
# Derived from PHASE1_SOURCES so it stays in sync automatically when sources
# are added or removed. Parent sources (current versions) come before derived
# sources (n-1 versions) so current docs always win over older ones.
_DEDUP_PRIORITY: list[str] = [
    s.source_id for s in PHASE1_SOURCES if s.derive_from is None
] + [
    s.source_id for s in PHASE1_SOURCES if s.derive_from is not None
]


def run_dedup_pass(conn: sqlite3.Connection) -> int:
    """
    Mark cross-source duplicates: rows sharing a content_hash with a
    higher-priority source get is_duplicate=1 and are excluded from search.

    Chunk rows inherit the is_duplicate value of their parent.
    Idempotent — resets all flags before re-running.
    Returns the number of parent rows marked as duplicates.
    """
    conn.execute("UPDATE documents SET is_duplicate = 0")

    # Find content-md hashes present in more than one source (parent rows only).
    # content_md_hash compares extracted Markdown, catching cases where the raw
    # HTML differs (e.g. different URLs) but the page content is identical —
    # the main Enterprise/Cloud overlap scenario.  Fall back to content_hash for
    # rows that pre-date Option B and have no content_md_hash yet.
    dup_rows = conn.execute(
        """
        SELECT COALESCE(content_md_hash, content_hash) AS dedup_hash
        FROM documents
        WHERE chunk_of IS NULL
        GROUP BY COALESCE(content_md_hash, content_hash)
        HAVING COUNT(DISTINCT source) > 1
        """
    ).fetchall()

    priority = {s: i for i, s in enumerate(_DEDUP_PRIORITY)}
    total = 0

    for row in dup_rows:
        ch = row["dedup_hash"]
        parents = conn.execute(
            """
            SELECT id, url, source FROM documents
            WHERE COALESCE(content_md_hash, content_hash) = ? AND chunk_of IS NULL
            """,
            (ch,),
        ).fetchall()
        winner = min(parents, key=lambda r: priority.get(r["source"], 999))
        for p in parents:
            if p["id"] != winner["id"]:
                conn.execute("UPDATE documents SET is_duplicate = 1 WHERE id = ?", (p["id"],))
                conn.execute(
                    "UPDATE documents SET is_duplicate = 1 WHERE chunk_of = ?", (p["url"],)
                )
                total += 1

    conn.commit()
    return total


def run_version_merge_pass(
    conn: sqlite3.Connection,
    source_pairs: list[tuple[str, str]],
) -> int:
    """
    Collapse identical cross-version content into a single canonical row.

    For each (derived_source_id, parent_source_id) pair (e.g. ES 8.4 → ES 8.5):
    - If a derived row's content_md_hash matches a parent row, the derived row is
      deleted and the parent row's version_tags is updated to include the derived
      version (e.g. ["8.5", "8.4"]).
    - If there is no match (unique content), the derived row is kept as-is.

    Must be called BEFORE the FTS5 rebuild in merge_dbs so the rebuild reflects
    the final row set.  The is_duplicate dedup pass should run after this.

    Returns the number of derived rows merged (deleted).
    """
    total = 0

    for derived_source, parent_source in source_pairs:
        derived_ver_row = conn.execute(
            "SELECT DISTINCT version FROM documents "
            "WHERE source = ? AND chunk_of IS NULL LIMIT 1",
            (derived_source,),
        ).fetchone()
        if not derived_ver_row:
            continue
        derived_version = derived_ver_row["version"]

        parent_rows = conn.execute(
            "SELECT id, url, version, content_md_hash, version_tags "
            "FROM documents WHERE source = ? AND chunk_of IS NULL AND content_md_hash IS NOT NULL",
            (parent_source,),
        ).fetchall()
        parent_map: dict[str, dict] = {
            r["content_md_hash"]: dict(r) for r in parent_rows
        }

        derived_rows = conn.execute(
            "SELECT id, url, content_md_hash "
            "FROM documents WHERE source = ? AND chunk_of IS NULL AND content_md_hash IS NOT NULL",
            (derived_source,),
        ).fetchall()

        for derived in derived_rows:
            cmh = derived["content_md_hash"]
            if cmh not in parent_map:
                continue
            parent = parent_map[cmh]

            try:
                tags = json.loads(parent["version_tags"] or "[]")
            except (json.JSONDecodeError, TypeError):
                tags = [parent["version"]]
            if derived_version not in tags:
                tags.append(derived_version)
            new_tags = json.dumps(tags)
            conn.execute(
                "UPDATE documents SET version_tags = ? WHERE id = ? OR chunk_of = ?",
                (new_tags, parent["id"], parent["url"]),
            )
            parent_map[cmh]["version_tags"] = new_tags

            # Delete derived row and its chunks
            conn.execute("DELETE FROM documents WHERE chunk_of = ?", (derived["url"],))
            conn.execute("DELETE FROM documents WHERE id = ?", (derived["id"],))
            total += 1

        conn.commit()

    return total


def get_crawl_timestamps(conn: sqlite3.Connection, source_id: str) -> dict[str, str]:
    """Return {url: attempted_at} for all crawled URLs of a given source.

    Used by sitemap-based discovery to compare <lastmod> dates against the
    last crawl timestamp and skip pages that have not changed.
    """
    rows = conn.execute(
        "SELECT url, attempted_at FROM crawl_state WHERE source = ?", (source_id,)
    ).fetchall()
    return {row["url"]: row["attempted_at"] for row in rows}


# ---------------------------------------------------------------------------
# Chunking helpers (write path — called by cli.py chunk pass)
# ---------------------------------------------------------------------------


def _split_content(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping fixed-size chunks."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def _accumulate_with_overlap(
    units: list[str], chunk_size: int, overlap: int
) -> list[str]:
    """Greedily pack units into chunks up to chunk_size, carrying an overlap tail."""
    chunks: list[str] = []
    current = ""
    for unit in units:
        if not current:
            current = unit
        elif len(current) + 2 + len(unit) <= chunk_size:
            current = current + "\n\n" + unit
        else:
            chunks.append(current)
            tail = current[-overlap:] if len(current) > overlap else current
            current = (tail + "\n\n" + unit) if tail else unit
    if current.strip():
        chunks.append(current)
    return chunks


def _split_content_smart(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """
    Split text into overlapping chunks using structural boundaries.

    Strategy (in priority order):
      1. ## / ### heading boundaries — keeps config stanzas and table sections intact
      2. Paragraph breaks — applied to heading-sections that exceed chunk_size * 2
      3. Character-based fallback — for content with no structural markers

    Heading sections up to chunk_size * 2 chars are kept whole so that config
    key descriptions stay grouped with their stanza heading.
    """
    segments = [s for s in _HEADING_RE.split(text) if s.strip()]
    if not segments:
        return _split_content(text, chunk_size, overlap)

    # Pack heading-sections into chunks up to chunk_size
    base_chunks = _accumulate_with_overlap(segments, chunk_size, overlap)

    # Second pass: paragraph-split any chunk still larger than chunk_size * 2
    result: list[str] = []
    for chunk in base_chunks:
        if len(chunk) <= chunk_size * 2:
            result.append(chunk)
            continue
        paras = [p for p in _PARA_BREAK_RE.split(chunk) if p.strip()]
        if len(paras) > 1:
            result.extend(_accumulate_with_overlap(paras, chunk_size, overlap))
        else:
            result.extend(_split_content(chunk, chunk_size, overlap))

    return result or _split_content(text, chunk_size, overlap)


def get_documents_needing_chunking(
    conn: sqlite3.Connection,
    threshold: int = CHUNK_THRESHOLD,
    source_id: str | None = None,
) -> list[sqlite3.Row]:
    """Return original (non-chunk) rows with content_md longer than threshold that haven't been chunked yet."""
    params: list = [threshold]
    source_filter = ""
    if source_id:
        source_filter = "AND source = ?"
        params.append(source_id)
    return conn.execute(
        f"""
        SELECT id, url, title, source, version, section, subsection, slug,
               file_path, content_md, content_hash, content_md_hash, version_tags, crawled_at
        FROM documents
        WHERE has_chunks = 0
          AND chunk_of IS NULL
          AND length(content_md) > ?
          {source_filter}
        """,
        params,
    ).fetchall()


def chunk_document(
    conn: sqlite3.Connection,
    parent: dict,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
) -> int:
    """
    Split a large document into overlapping chunk rows and mark the parent as
    has_chunks=1 so it is excluded from FTS/embedding search.

    Chunk rows use synthetic URLs of the form ``{parent_url}#chunk-{i}`` and
    store ``chunk_of = parent_url`` so get_page() can reassemble them.

    Deletes any existing stale chunks before inserting new ones (idempotent).
    Returns the number of chunks created.
    """
    chunks = _split_content_smart(parent["content_md"], chunk_size, overlap)
    n = len(chunks)

    # Remove stale chunks from a previous pass
    conn.execute("DELETE FROM documents WHERE chunk_of = ?", (parent["url"],))

    for i, chunk_text in enumerate(chunks):
        chunk_url = f"{parent['url']}#chunk-{i}"
        chunk_md_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
        conn.execute(
            """
            INSERT INTO documents
                (url, title, source, version, section, subsection, slug,
                 file_path, content_md, content_hash, content_md_hash, version_tags,
                 crawled_at, has_chunks, chunk_of, chunk_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                content_md      = excluded.content_md,
                content_hash    = excluded.content_hash,
                content_md_hash = excluded.content_md_hash,
                version_tags    = excluded.version_tags,
                chunk_of        = excluded.chunk_of,
                chunk_index     = excluded.chunk_index
            """,
            (
                chunk_url,
                f"{parent['title']} [{i + 1}/{n}]",
                parent["source"],
                parent["version"],
                parent["section"],
                parent["subsection"],
                parent["slug"],
                parent["file_path"],
                chunk_text,
                parent["content_hash"],  # inherits parent's raw-HTML hash; content_hash
                                         # on chunks has no raw-HTML meaning, but using
                                         # the parent hash enables correct embedding reuse
                                         # across re-crawls via get_embedding_by_hash()
                chunk_md_hash,
                parent.get("version_tags") or json.dumps([parent["version"]]),
                parent["crawled_at"],
                parent["url"],
                i,
            ),
        )

    conn.execute("UPDATE documents SET has_chunks = 1 WHERE url = ?", (parent["url"],))
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Server query helpers (read path)
# ---------------------------------------------------------------------------


def search_docs(
    conn: sqlite3.Connection,
    query: str,
    source: str | None = None,
    version: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    BM25 full-text search across all indexed documents.

    Title matches are weighted 10x higher than body matches via the bm25()
    column weights argument.  Lower (more negative) score = better match.
    """
    # FTS5's unicode61 tokenizer splits on non-word characters anyway, so
    # strip them before the first execute rather than catching the syntax error.
    # Hyphens must also be removed: FTS5 parses "risk-based" as "risk NOT based",
    # then throws "no such column: based" when it tries to validate the column filter.
    query = re.sub(r"[^\w\s]", " ", query)
    params: list = [query]
    filters = ""
    if source:
        filters += " AND d.source = ?"
        params.append(source)
    if version:
        # Match rows that are either the canonical version or have been tagged
        # (version_tags) as also applying to this version via the merge pass.
        # is_duplicate is intentionally NOT filtered here: n-1 rows collapsed by
        # run_version_merge_pass are deleted (not flagged), but any remaining
        # version-specific rows must be visible regardless of their dedup status.
        filters += (
            " AND (d.version = ? OR EXISTS ("
            "SELECT 1 FROM json_each(d.version_tags) jt WHERE jt.value = ?))"
        )
        params.append(version)
        params.append(version)
    else:
        # Without a version filter the caller wants general results — suppress
        # cross-source duplicates so the same content doesn't appear twice.
        filters += " AND d.is_duplicate = 0"
    # Fetch extra rows to allow deduplication across chunks of the same parent
    params.append(limit * 4)

    _sql = f"""
        SELECT
            d.url,
            d.title,
            d.source,
            d.version,
            d.version_tags,
            d.section,
            d.subsection,
            d.crawled_at,
            d.chunk_of,
            snippet(documents_fts, 1, '**', '**', '…', 32) AS snippet,
            bm25(documents_fts, 10.0, 1.0) AS score
        FROM documents_fts
        JOIN documents d ON d.id = documents_fts.rowid
        WHERE documents_fts MATCH ?
          AND d.has_chunks = 0
          {filters}
        ORDER BY score
        LIMIT ?
        """
    rows = conn.execute(_sql, params).fetchall()

    # Deduplicate: multiple chunks of the same parent may match; keep the
    # best-scoring chunk per canonical (parent) URL.  BM25 score is negative
    # so ORDER BY score ASC gives best-first — first occurrence wins.
    seen: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        chunk_of = d.pop("chunk_of")
        canonical = chunk_of if chunk_of else d["url"]
        if chunk_of:
            # Expose the chunk URL so callers can pass it to get_page() to read
            # just that 1,500-char slice instead of the full reassembled page.
            d["chunk_url"] = d["url"]
        d["url"] = canonical
        d["crawled"] = (d.pop("crawled_at") or "")[:10]
        # Parse version_tags JSON so callers see a list, not a raw string.
        # Important when version= is used: result row may show version='8.5'
        # but version_tags=['8.5','8.4'], meaning it was found via tag match.
        d["version_tags"] = json.loads(d.get("version_tags") or "[]")
        if canonical not in seen:
            seen[canonical] = d

    return list(seen.values())[:limit]


def get_page(conn: sqlite3.Connection, url: str) -> dict | None:
    row = conn.execute(
        """
        SELECT url, title, source, version, section, subsection,
               content_md, crawled_at, has_chunks, chunk_of, chunk_index,
               length(content_md) AS char_count
        FROM documents
        WHERE url = ?
        """,
        (url,),
    ).fetchone()
    if row is None:
        return None

    page = dict(row)

    if page.get("chunk_of"):
        # Called with a chunk URL — return the chunk content directly so the
        # model can read specific sections of large pages without getting the
        # truncated full document.  Include navigation info for adjacent chunks.
        parent_url: str = page["chunk_of"]
        idx: int = page.get("chunk_index") or 0
        total: int = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE chunk_of = ?", (parent_url,)
        ).fetchone()[0]
        page["parent_url"] = parent_url
        page["chunk_index"] = idx
        page["total_chunks"] = total
        if idx > 0:
            page["prev_chunk_url"] = f"{parent_url}#chunk-{idx - 1}"
        if idx < total - 1:
            page["next_chunk_url"] = f"{parent_url}#chunk-{idx + 1}"
        page["chunk_note"] = (
            f"Chunk {idx + 1} of {total} for this page. "
            "Follow at most 1 next_chunk_url hop — if the answer is not here or "
            "in the next chunk, call search_docs() with more specific keywords instead. "
            "Do NOT walk all chunks sequentially."
        )
        page.pop("has_chunks", None)
        page.pop("chunk_of", None)
        return page

    if page.get("has_chunks"):
        # Reassemble the full document from its ordered chunks
        chunks = conn.execute(
            "SELECT content_md FROM documents WHERE chunk_of = ? ORDER BY chunk_index",
            (url,),
        ).fetchall()
        if chunks:
            page["content_md"] = "".join(c["content_md"] for c in chunks)
            page["char_count"] = len(page["content_md"])

    # Strip internal-only columns from the returned dict
    page.pop("has_chunks", None)
    page.pop("chunk_of", None)
    return page


def list_sections(
    conn: sqlite3.Connection, source: str | None = None
) -> list[dict]:
    params: list = []
    source_filter = ""
    if source:
        source_filter = "WHERE source = ?"
        params.append(source)

    # Always exclude chunk rows so page counts reflect real documents only
    if source_filter:
        source_filter += " AND chunk_of IS NULL"
    else:
        source_filter = "WHERE chunk_of IS NULL"

    rows = conn.execute(
        f"""
        SELECT source, version, section, COUNT(*) AS page_count
        FROM documents
        {source_filter}
        GROUP BY source, version, section
        ORDER BY source, section
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def browse_section(
    conn: sqlite3.Connection,
    section: str,
    source: str,
    subsection: str | None = None,
) -> list[dict]:
    params: list = [section, source]
    sub_filter = ""
    if subsection:
        sub_filter = "AND subsection = ?"
        params.append(subsection)

    rows = conn.execute(
        f"""
        SELECT url, title, subsection, length(content_md) AS char_count
        FROM documents
        WHERE section = ?
          AND source = ?
          AND chunk_of IS NULL
          AND is_duplicate = 0
          {sub_filter}
        ORDER BY subsection, title
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_index_info(conn: sqlite3.Connection) -> dict:
    total = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE chunk_of IS NULL"
    ).fetchone()[0]
    last_crawled = conn.execute(
        "SELECT MAX(crawled_at) FROM documents"
    ).fetchone()[0]

    sources = conn.execute(
        "SELECT source, version, COUNT(*) AS page_count FROM documents GROUP BY source, version ORDER BY source"
    ).fetchall()

    db_size = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute(
        "PRAGMA page_size"
    ).fetchone()[0]

    embedded = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE embedding IS NOT NULL"
    ).fetchone()[0]

    return {
        "total_pages": total,
        "embedded_pages": embedded,
        "last_crawled_at": last_crawled,
        "sources": [dict(r) for r in sources],
        "db_size_bytes": db_size,
    }


# ---------------------------------------------------------------------------
# Embedding helpers (write path — called by cli.py post-crawl pass)
# ---------------------------------------------------------------------------


def update_embedding(
    conn: sqlite3.Connection, doc_id: int, embedding_bytes: bytes
) -> None:
    """Store a serialised float32 embedding BLOB for a document row."""
    conn.execute(
        "UPDATE documents SET embedding = ? WHERE id = ?",
        (embedding_bytes, doc_id),
    )


def upsert_vec_embedding(
    conn: sqlite3.Connection, doc_id: int, embedding_bytes: bytes
) -> None:
    """Insert or replace a vector in vec_documents (vec0 requires delete+insert for updates)."""
    conn.execute("DELETE FROM vec_documents WHERE rowid = ?", [doc_id])
    conn.execute(
        "INSERT INTO vec_documents(rowid, embedding) VALUES (?, ?)",
        [doc_id, embedding_bytes],
    )


def get_documents_without_embeddings(
    conn: sqlite3.Connection, source_id: str | None = None
) -> list[sqlite3.Row]:
    """
    Return rows that have no embedding yet (or all rows if source_id is None).

    Each row exposes: id, title, content_md.
    Used by the post-crawl embedding pass in cli.py.
    """
    # Exclude parent rows that have been chunked — their content is indexed via
    # chunk rows; embedding the parent again would waste compute and add noise.
    if source_id:
        return conn.execute(
            "SELECT id, title, content_md, content_hash FROM documents "
            "WHERE embedding IS NULL AND has_chunks = 0 AND source = ?",
            (source_id,),
        ).fetchall()
    return conn.execute(
        "SELECT id, title, content_md, content_hash FROM documents "
        "WHERE embedding IS NULL AND has_chunks = 0"
    ).fetchall()


# ---------------------------------------------------------------------------
# Semantic search — matrix helpers (read path — called by server.py)
# ---------------------------------------------------------------------------


def get_all_embeddings(
    conn: sqlite3.Connection,
) -> "tuple[numpy.ndarray, list[dict]]":  # noqa: F821
    """
    Load all embeddings into a NumPy matrix alongside lightweight row metadata.

    Returns (matrix, rows) where matrix is shape (N, 384) float32 and rows[i]
    contains the metadata dict for matrix row i.  Used by server.py to build a
    module-level cache that is queried on every search_docs_semantic call without
    hitting the database.
    """
    import numpy as np

    db_rows = conn.execute(
        "SELECT id, url, title, source, version, section, chunk_of, crawled_at, "
        "is_duplicate, version_tags, embedding "
        "FROM documents WHERE embedding IS NOT NULL AND has_chunks = 0"
    ).fetchall()

    if not db_rows:
        return np.empty((0, 384), dtype=np.float32), []

    meta = []
    for r in db_rows:
        try:
            vtags: list[str] = json.loads(r["version_tags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            vtags = [r["version"]]
        if not vtags:
            vtags = [r["version"]]
        meta.append({
            "url": r["url"],
            "title": r["title"],
            "source": r["source"],
            "version": r["version"],
            "section": r["section"],
            "chunk_of": r["chunk_of"],
            "crawled_at": r["crawled_at"],
            "is_duplicate": bool(r["is_duplicate"]),
            "version_tags": vtags,
        })
    matrix = np.stack(
        [np.frombuffer(r["embedding"], dtype=np.float32) for r in db_rows]
    )
    return matrix, meta


def search_docs_semantic_from_matrix(
    matrix: "numpy.ndarray",  # noqa: F821
    rows: list[dict],
    query_vec: "numpy.ndarray",  # noqa: F821
    source: str | None = None,
    version: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Cosine-similarity search against a pre-loaded embedding matrix.

    Accepts the (matrix, rows) tuple returned by get_all_embeddings() so the
    database is not touched at query time.  Applies optional source and version
    pre-filters via numpy boolean indexing before computing the dot product.
    """
    import numpy as np

    if matrix.shape[0] == 0:
        return []

    mask = np.ones(len(rows), dtype=bool)
    if source:
        mask &= np.array([r["source"] == source for r in rows])
    if version:
        # Match canonical rows for this version OR rows whose version_tags include it.
        mask &= np.array([
            r["version"] == version or version in r.get("version_tags", [])
            for r in rows
        ])
    else:
        # No version filter → suppress cross-source duplicates (same logic as FTS search)
        mask &= np.array([not r["is_duplicate"] for r in rows])
    if not mask.any():
        return []
    mat = matrix[mask]
    filtered = [r for r, m in zip(rows, mask) if m]

    scores = mat @ query_vec
    ranked = np.argsort(scores)[::-1]

    seen: dict[str, dict] = {}
    for i in ranked:
        if len(seen) >= limit:
            break
        r = filtered[i]
        canonical = r["chunk_of"] if r["chunk_of"] else r["url"]
        if canonical not in seen:
            seen[canonical] = {
                "url": canonical,
                "title": r["title"].split(" [")[0],  # strip chunk suffix
                "source": r["source"],
                "version": r["version"],
                "section": r["section"],
                "score": round(float(scores[i]), 4),
                "crawled": (r["crawled_at"] or "")[:10],
            }

    return list(seen.values())


def search_docs_semantic_vec(
    conn: sqlite3.Connection,
    query_vec: "numpy.ndarray",  # noqa: F821
    source: str | None = None,
    version: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    ANN vector search via sqlite-vec (replaces the in-process numpy matrix scan).

    Fetches limit*4 candidates from vec_documents, joins to documents for
    metadata and source/version filtering, deduplicates chunk→parent, returns
    top limit results ordered by cosine similarity (higher = better).

    When version is set, ANN post-filtering is unreliable for minority versions
    (n-1 rows are a small fraction of the corpus, so top-k ANN candidates are
    unlikely to include them).  In that case we fall back to a targeted numpy
    scan over just the version-filtered rows, which is small and fast.
    """
    import numpy as np  # lazy import

    if version:
        # Targeted scan: load embeddings only for this version (small set).
        src_filter = " AND source = ?" if source else ""
        params: list = [version, version]
        if source:
            params.append(source)
        rows = conn.execute(
            "SELECT id, url, title, source, version, section, chunk_of, crawled_at, embedding "
            "FROM documents "
            "WHERE embedding IS NOT NULL AND has_chunks = 0 "
            "AND (version = ? OR EXISTS "
            "  (SELECT 1 FROM json_each(version_tags) jt WHERE jt.value = ?))"
            f"{src_filter}",
            params,
        ).fetchall()
        if not rows:
            return []
        mat = np.stack(
            [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
        )
        scores = mat @ query_vec
        ranked = np.argsort(scores)[::-1]
        seen: dict[str, dict] = {}
        for i in ranked:
            if len(seen) >= limit:
                break
            r = rows[i]
            canonical = r["chunk_of"] if r["chunk_of"] else r["url"]
            if canonical not in seen:
                seen[canonical] = {
                    "url": canonical,
                    "title": r["title"].split(" [")[0],
                    "source": r["source"],
                    "version": r["version"],
                    "section": r["section"],
                    "score": round(float(scores[i]), 4),
                    "crawled": (r["crawled_at"] or "")[:10],
                }
        return list(seen.values())

    fetch_k = limit * 4

    candidates = conn.execute(
        "SELECT rowid, distance FROM vec_documents WHERE embedding MATCH ? AND k = ?",
        [query_vec.tobytes(), fetch_k],
    ).fetchall()
    if not candidates:
        return []

    rowids = [r[0] for r in candidates]
    dist_map = {r[0]: r[1] for r in candidates}

    placeholders = ",".join("?" * len(rowids))
    ann_params: list = list(rowids)
    filters = ""
    if source:
        filters += " AND source = ?"
        ann_params.append(source)
    filters += " AND is_duplicate = 0"

    ann_rows = conn.execute(
        f"SELECT id, url, title, source, version, section, chunk_of, crawled_at "
        f"FROM documents WHERE id IN ({placeholders}){filters}",
        ann_params,
    ).fetchall()

    # Sort by distance ascending (closest = most similar)
    sorted_rows = sorted([dict(r) for r in ann_rows], key=lambda r: dist_map[r["id"]])

    seen2: dict[str, dict] = {}
    for r in sorted_rows:
        if len(seen2) >= limit:
            break
        canonical = r["chunk_of"] if r["chunk_of"] else r["url"]
        if canonical not in seen2:
            d = dist_map[r["id"]]
            seen2[canonical] = {
                "url": canonical,
                "title": r["title"].split(" [")[0],
                "source": r["source"],
                "version": r["version"],
                "section": r["section"],
                # L2 distance on unit vectors → cosine similarity: cos = 1 - d²/2
                "score": round(1.0 - d * d / 2.0, 4),
                "crawled": (r["crawled_at"] or "")[:10],
            }

    return list(seen2.values())


def search_docs_semantic(
    conn: sqlite3.Connection,
    query_vec: "numpy.ndarray",  # noqa: F821 — numpy imported lazily below
    source: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    Cosine-similarity search using pre-computed document embeddings.

    All embeddings are loaded into a NumPy matrix and the dot product with the
    (unit-norm) query vector is computed in-process.  This is O(n) in corpus
    size but fast enough for ~9 000+ pages / ~20 000+ rows with chunks.

    Returns results sorted by descending similarity score (1.0 = identical).
    Returns an empty list if no embeddings have been generated yet.
    """
    import numpy as np  # lazy import — only needed when this function is called

    # Exclude chunked parents — embeddings are on chunk rows instead
    where = "WHERE embedding IS NOT NULL AND has_chunks = 0"
    params: list = []
    if source:
        where += " AND source = ?"
        params.append(source)

    rows = conn.execute(
        f"SELECT id, url, title, source, version, section, crawled_at, chunk_of, embedding "
        f"FROM documents {where}",
        params,
    ).fetchall()

    if not rows:
        return []

    # Stack embeddings into a (N, 384) float32 matrix
    mat = np.stack(
        [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    )

    # Dot product of unit-norm vectors == cosine similarity
    scores = mat @ query_vec

    # Gather all ranked indices and deduplicate by canonical (parent) URL,
    # keeping the highest-scoring chunk per document.
    ranked_all = np.argsort(scores)[::-1]
    seen: dict[str, dict] = {}
    for i in ranked_all:
        if len(seen) >= limit:
            break
        chunk_of = rows[i]["chunk_of"]
        canonical = chunk_of if chunk_of else rows[i]["url"]
        if canonical not in seen:
            seen[canonical] = {
                "url": canonical,
                "title": rows[i]["title"].split(" [")[0],  # strip chunk suffix
                "source": rows[i]["source"],
                "version": rows[i]["version"],
                "section": rows[i]["section"],
                "score": round(float(scores[i]), 4),
                "crawled": (rows[i]["crawled_at"] or "")[:10],
            }

    return list(seen.values())
