"""
SQLite database layer.

Schema
------
documents     — one row per crawled page (content + metadata)
documents_fts — FTS5 virtual table backed by `documents` (BM25 search)
crawl_state   — per-URL crawl progress (used by crawler; not read by server)

The FTS5 table uses the *content table* pattern: it holds a copy of the indexed
columns internally and INSERT/UPDATE/DELETE triggers on `documents` keep them in
sync automatically.  This means:
  - No text is duplicated in the Python layer
  - The index survives across server restarts (no rebuild on startup)
  - BM25 ranking and phrase search work out of the box via SQLite's porter stemmer

Future extensibility
--------------------
SPL examples library (Phase 2+): add the `spl_examples` table below and a
matching FTS5 table.  The rest of the codebase is unaffected.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
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
            status       TEXT NOT NULL,   -- 'fetched' | 'skipped' | 'failed'
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

        -- Future: SPL examples library (Phase 2+) --------------------------------
        -- CREATE TABLE IF NOT EXISTS spl_examples (
        --     id          INTEGER PRIMARY KEY AUTOINCREMENT,
        --     title       TEXT NOT NULL,
        --     spl_query   TEXT NOT NULL,
        --     explanation TEXT,
        --     tags        TEXT,          -- JSON array of strings
        --     use_case    TEXT,
        --     source_file TEXT
        -- );
        -- CREATE VIRTUAL TABLE IF NOT EXISTS spl_examples_fts USING fts5(
        --     title, spl_query, explanation,
        --     content=spl_examples, content_rowid=id,
        --     tokenize='porter unicode61'
        -- );
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Crawler helpers (write path)
# ---------------------------------------------------------------------------


def get_content_hash(conn: sqlite3.Connection, url: str) -> str | None:
    row = conn.execute(
        "SELECT content_hash FROM documents WHERE url = ?", (url,)
    ).fetchone()
    return row["content_hash"] if row else None


def upsert_document(conn: sqlite3.Connection, doc: dict) -> None:
    """Insert or update a document row. The FTS5 triggers handle index sync."""
    conn.execute(
        """
        INSERT INTO documents
            (url, title, source, version, section, subsection, slug,
             file_path, content_md, content_hash, crawled_at)
        VALUES
            (:url, :title, :source, :version, :section, :subsection, :slug,
             :file_path, :content_md, :content_hash, :crawled_at)
        ON CONFLICT(url) DO UPDATE SET
            title        = excluded.title,
            content_md   = excluded.content_md,
            content_hash = excluded.content_hash,
            crawled_at   = excluded.crawled_at,
            file_path    = excluded.file_path,
            section      = excluded.section,
            subsection   = excluded.subsection,
            slug         = excluded.slug
        """,
        doc,
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
    """Return all URLs already attempted for this source (for crawl resume)."""
    rows = conn.execute(
        "SELECT url FROM crawl_state WHERE source = ?", (source_id,)
    ).fetchall()
    return {row["url"] for row in rows}


# ---------------------------------------------------------------------------
# Server query helpers (read path)
# ---------------------------------------------------------------------------


def search_docs(
    conn: sqlite3.Connection,
    query: str,
    source: str | None = None,
    limit: int = 5,
) -> list[dict]:
    """
    BM25 full-text search across all indexed documents.

    Title matches are weighted 10x higher than body matches via the bm25()
    column weights argument.  Lower (more negative) score = better match.

    Future: add a `version` parameter here when multi-version support is added.
    """
    params: list = [query]
    source_filter = ""
    if source:
        source_filter = "AND d.source = ?"
        params.append(source)
    params.extend([limit])

    rows = conn.execute(
        f"""
        SELECT
            d.url,
            d.title,
            d.source,
            d.version,
            d.section,
            d.subsection,
            snippet(documents_fts, 1, '**', '**', '…', 32) AS snippet,
            bm25(documents_fts, 10.0, 1.0) AS score
        FROM documents_fts
        JOIN documents d ON d.id = documents_fts.rowid
        WHERE documents_fts MATCH ?
          {source_filter}
        ORDER BY score
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_page(conn: sqlite3.Connection, url: str) -> dict | None:
    row = conn.execute(
        """
        SELECT url, title, source, version, section, subsection,
               content_md, crawled_at, length(content_md) AS char_count
        FROM documents
        WHERE url = ?
        """,
        (url,),
    ).fetchone()
    return dict(row) if row else None


def list_sections(
    conn: sqlite3.Connection, source: str | None = None
) -> list[dict]:
    params: list = []
    source_filter = ""
    if source:
        source_filter = "WHERE source = ?"
        params.append(source)

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
          {sub_filter}
        ORDER BY subsection, title
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def get_index_info(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    last_crawled = conn.execute(
        "SELECT MAX(crawled_at) FROM documents"
    ).fetchone()[0]

    sources = conn.execute(
        "SELECT source, version, COUNT(*) AS page_count FROM documents GROUP BY source, version ORDER BY source"
    ).fetchall()

    db_size = conn.execute("PRAGMA page_count").fetchone()[0] * conn.execute(
        "PRAGMA page_size"
    ).fetchone()[0]

    return {
        "total_pages": total,
        "last_crawled_at": last_crawled,
        "sources": [dict(r) for r in sources],
        "db_size_bytes": db_size,
    }
