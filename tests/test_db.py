"""Tests for the database layer (db.py).

Uses in-memory SQLite so tests are fast and isolated.
"""

import hashlib
import json
import sqlite3

import pytest
import sqlite_vec

from splunk_docs_mcp.config import PHASE1_SOURCES
from splunk_docs_mcp.db import (
    _DEDUP_PRIORITY,
    browse_section,
    chunk_document,
    get_page,
    init_db,
    run_dedup_pass,
    run_version_merge_pass,
    search_docs,
    upsert_document,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.enable_load_extension(True)
    sqlite_vec.load(c)
    c.enable_load_extension(False)
    init_db(c)
    return c


def _doc(url, source, version, content, title="Test Page", section="test-section", subsection=None):
    return {
        "url": url,
        "title": title,
        "source": source,
        "version": version,
        "section": section,
        "subsection": subsection,
        "slug": url.rstrip("/").split("/")[-1],
        "file_path": f"data/docs/{source}/{version}/test.md",
        "content_md": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "crawled_at": "2024-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# _DEDUP_PRIORITY stays in sync with PHASE1_SOURCES
# ---------------------------------------------------------------------------


def test_dedup_priority_contains_all_sources():
    source_ids = {s.source_id for s in PHASE1_SOURCES}
    priority_ids = set(_DEDUP_PRIORITY)
    assert priority_ids == source_ids, (
        f"_DEDUP_PRIORITY is out of sync with PHASE1_SOURCES.\n"
        f"Missing: {source_ids - priority_ids}\n"
        f"Extra:   {priority_ids - source_ids}"
    )


def test_dedup_priority_parents_before_derived():
    parent_ids = {s.source_id for s in PHASE1_SOURCES if s.derive_from is None}
    derived_ids = {s.source_id for s in PHASE1_SOURCES if s.derive_from is not None}
    last_parent_index = max(_DEDUP_PRIORITY.index(sid) for sid in parent_ids)
    first_derived_index = min(_DEDUP_PRIORITY.index(sid) for sid in derived_ids)
    assert last_parent_index < first_derived_index, (
        "All parent sources must appear before derived sources in _DEDUP_PRIORITY"
    )


# ---------------------------------------------------------------------------
# run_dedup_pass
# ---------------------------------------------------------------------------


def test_dedup_pass_marks_lower_priority_duplicate(conn):
    # enterprise-security is higher priority than lantern in _DEDUP_PRIORITY
    shared_content = "Identical content shared between two sources."
    upsert_document(conn, _doc("https://es.example.com/page", "enterprise-security", "8.5", shared_content))
    upsert_document(conn, _doc("https://lantern.example.com/page", "lantern", "current", shared_content))

    marked = run_dedup_pass(conn)

    assert marked == 1
    es_row = conn.execute("SELECT is_duplicate FROM documents WHERE source='enterprise-security'").fetchone()
    lantern_row = conn.execute("SELECT is_duplicate FROM documents WHERE source='lantern'").fetchone()
    assert es_row["is_duplicate"] == 0
    assert lantern_row["is_duplicate"] == 1


def test_dedup_pass_unique_content_not_marked(conn):
    upsert_document(conn, _doc("https://es.example.com/page1", "enterprise-security", "8.5", "Unique ES content."))
    upsert_document(conn, _doc("https://lantern.example.com/page2", "lantern", "current", "Unique Lantern content."))

    marked = run_dedup_pass(conn)

    assert marked == 0
    rows = conn.execute("SELECT is_duplicate FROM documents").fetchall()
    assert all(r["is_duplicate"] == 0 for r in rows)


def test_dedup_pass_chunks_inherit_parent_flag(conn):
    shared_content = "x" * 10_000  # large enough to chunk
    upsert_document(conn, _doc("https://es.example.com/big", "enterprise-security", "8.5", shared_content))
    upsert_document(conn, _doc("https://lantern.example.com/big", "lantern", "current", shared_content))

    # Chunk the lantern parent so chunk rows exist
    lantern_row = dict(conn.execute(
        "SELECT * FROM documents WHERE source='lantern'"
    ).fetchone())
    chunk_document(conn, lantern_row)

    run_dedup_pass(conn)

    # All lantern rows (parent + chunks) should be is_duplicate=1
    lantern_rows = conn.execute(
        "SELECT is_duplicate FROM documents WHERE source='lantern'"
    ).fetchall()
    assert all(r["is_duplicate"] == 1 for r in lantern_rows)


def test_dedup_pass_is_idempotent(conn):
    shared = "Same content."
    upsert_document(conn, _doc("https://es.example.com/a", "enterprise-security", "8.5", shared))
    upsert_document(conn, _doc("https://lantern.example.com/a", "lantern", "current", shared))

    run_dedup_pass(conn)
    run_dedup_pass(conn)

    marked = conn.execute("SELECT COUNT(*) FROM documents WHERE is_duplicate=1").fetchone()[0]
    assert marked == 1


# ---------------------------------------------------------------------------
# run_version_merge_pass
# ---------------------------------------------------------------------------


def test_version_merge_pass_deletes_matching_derived_row(conn):
    shared_content = "Identical page content."
    upsert_document(conn, _doc("https://es.example.com/page/8.5", "enterprise-security", "8.5", shared_content))
    upsert_document(conn, _doc("https://es.example.com/page/8.4", "enterprise-security-8-4", "8.4", shared_content))

    merged = run_version_merge_pass(conn, [("enterprise-security-8-4", "enterprise-security")])

    assert merged == 1
    # Derived row should be deleted
    derived = conn.execute("SELECT * FROM documents WHERE source='enterprise-security-8-4'").fetchone()
    assert derived is None


def test_version_merge_pass_updates_parent_version_tags(conn):
    shared_content = "Shared page."
    upsert_document(conn, _doc("https://es.example.com/page/8.5", "enterprise-security", "8.5", shared_content))
    upsert_document(conn, _doc("https://es.example.com/page/8.4", "enterprise-security-8-4", "8.4", shared_content))

    run_version_merge_pass(conn, [("enterprise-security-8-4", "enterprise-security")])

    parent = conn.execute(
        "SELECT version_tags FROM documents WHERE source='enterprise-security'"
    ).fetchone()
    tags = json.loads(parent["version_tags"])
    assert "8.5" in tags
    assert "8.4" in tags


def test_version_merge_pass_keeps_unique_derived_row(conn):
    upsert_document(conn, _doc("https://es.example.com/a/8.5", "enterprise-security", "8.5", "Parent only content."))
    upsert_document(conn, _doc("https://es.example.com/b/8.4", "enterprise-security-8-4", "8.4", "Derived unique content."))

    merged = run_version_merge_pass(conn, [("enterprise-security-8-4", "enterprise-security")])

    assert merged == 0
    derived = conn.execute("SELECT * FROM documents WHERE source='enterprise-security-8-4'").fetchone()
    assert derived is not None


def test_version_merge_pass_graceful_when_derived_source_absent(conn):
    upsert_document(conn, _doc("https://es.example.com/a", "enterprise-security", "8.5", "Some content."))
    # No enterprise-security-8-4 rows inserted
    merged = run_version_merge_pass(conn, [("enterprise-security-8-4", "enterprise-security")])
    assert merged == 0


# ---------------------------------------------------------------------------
# search_docs
# ---------------------------------------------------------------------------


def test_search_docs_excludes_duplicates_by_default(conn):
    upsert_document(conn, _doc("https://es.example.com/page", "enterprise-security", "8.5", "correlation search rule"))
    upsert_document(conn, _doc("https://lantern.example.com/page", "lantern", "current", "correlation search rule"))
    run_dedup_pass(conn)

    results = search_docs(conn, "correlation search", limit=10)

    urls = [r["url"] for r in results]
    # lantern is lower priority — should be suppressed
    assert "https://lantern.example.com/page" not in urls
    assert "https://es.example.com/page" in urls


def test_search_docs_with_version_sees_duplicates(conn):
    content = "notable event threshold configuration"
    upsert_document(conn, _doc("https://es.example.com/page", "enterprise-security", "8.5", content))
    upsert_document(conn, _doc("https://lantern.example.com/page", "lantern", "current", content))
    run_dedup_pass(conn)

    # Version filter bypasses the is_duplicate check
    results = search_docs(conn, "notable event threshold", version="current", limit=10)
    urls = [r["url"] for r in results]
    assert "https://lantern.example.com/page" in urls


def test_search_docs_source_filter(conn):
    upsert_document(conn, _doc("https://es.example.com/page", "enterprise-security", "8.5", "correlation search"))
    upsert_document(conn, _doc("https://admin.example.com/page", "admin-manual", "10.2", "correlation search"))

    results = search_docs(conn, "correlation search", source="admin-manual", limit=10)
    assert all(r["source"] == "admin-manual" for r in results)


# ---------------------------------------------------------------------------
# get_page
# ---------------------------------------------------------------------------


def test_get_page_returns_content(conn):
    upsert_document(conn, _doc("https://es.example.com/guide", "enterprise-security", "8.5", "Full page content."))
    page = get_page(conn, "https://es.example.com/guide")
    assert page is not None
    assert page["content_md"] == "Full page content."


def test_get_page_reassembles_chunks(conn):
    long_content = "A" * 10_000
    upsert_document(conn, _doc("https://es.example.com/big", "enterprise-security", "8.5", long_content))
    parent_row = dict(conn.execute("SELECT * FROM documents WHERE url='https://es.example.com/big'").fetchone())
    chunk_document(conn, parent_row)

    page = get_page(conn, "https://es.example.com/big")
    assert page is not None
    # Reassembled content should roughly match (chunks overlap, so length >= original)
    assert len(page["content_md"]) >= len(long_content) - 100


def test_get_page_chunk_url_returns_chunk_with_navigation(conn):
    long_content = "B" * 10_000
    upsert_document(conn, _doc("https://es.example.com/chunked", "enterprise-security", "8.5", long_content))
    parent_row = dict(conn.execute("SELECT * FROM documents WHERE url='https://es.example.com/chunked'").fetchone())
    chunk_document(conn, parent_row)

    # Chunk URL returns the chunk directly, not the reassembled parent
    page = get_page(conn, "https://es.example.com/chunked#chunk-0")
    assert page is not None
    assert page["url"] == "https://es.example.com/chunked#chunk-0"
    assert page["parent_url"] == "https://es.example.com/chunked"
    assert page["chunk_index"] == 0
    assert page["total_chunks"] > 1
    assert "next_chunk_url" in page
    assert "prev_chunk_url" not in page  # first chunk has no prev
    assert "chunk_note" in page


def test_get_page_missing_url_returns_none(conn):
    assert get_page(conn, "https://es.example.com/nonexistent") is None


# ---------------------------------------------------------------------------
# browse_section
# ---------------------------------------------------------------------------


def test_browse_section_excludes_duplicates(conn):
    upsert_document(conn, _doc(
        "https://es.example.com/section/pageA", "enterprise-security", "8.5",
        "ES content", section="administer"
    ))
    upsert_document(conn, _doc(
        "https://lantern.example.com/section/pageA", "lantern", "current",
        "ES content", section="administer"
    ))
    run_dedup_pass(conn)

    # browse_section for lantern — the duplicate should be hidden
    results = browse_section(conn, section="administer", source="lantern")
    assert len(results) == 0


def test_browse_section_shows_non_duplicates(conn):
    upsert_document(conn, _doc(
        "https://es.example.com/administer/pageA", "enterprise-security", "8.5",
        "Unique ES admin content", section="administer"
    ))
    results = browse_section(conn, section="administer", source="enterprise-security")
    assert len(results) == 1
    assert results[0]["url"] == "https://es.example.com/administer/pageA"


def test_browse_section_excludes_chunk_rows(conn):
    long_content = "C" * 10_000
    upsert_document(conn, _doc(
        "https://es.example.com/administer/big", "enterprise-security", "8.5",
        long_content, section="administer"
    ))
    parent_row = dict(conn.execute(
        "SELECT * FROM documents WHERE url='https://es.example.com/administer/big'"
    ).fetchone())
    chunk_document(conn, parent_row)

    results = browse_section(conn, section="administer", source="enterprise-security")
    # Only the parent URL should appear, not chunk URLs
    assert len(results) == 1
    assert "#chunk-" not in results[0]["url"]


# ---------------------------------------------------------------------------
# chunk_document
# ---------------------------------------------------------------------------


def test_chunk_document_creates_multiple_chunks(conn):
    long_content = "Word " * 5000  # well over 8000 chars
    upsert_document(conn, _doc("https://es.example.com/long", "enterprise-security", "8.5", long_content))
    parent_row = dict(conn.execute("SELECT * FROM documents WHERE url='https://es.example.com/long'").fetchone())

    n = chunk_document(conn, parent_row)

    assert n > 1
    chunks = conn.execute(
        "SELECT * FROM documents WHERE chunk_of='https://es.example.com/long' ORDER BY chunk_index"
    ).fetchall()
    assert len(chunks) == n
    assert all(c["chunk_index"] == i for i, c in enumerate(chunks))


def test_chunk_document_marks_parent_has_chunks(conn):
    long_content = "Z" * 10_000
    upsert_document(conn, _doc("https://es.example.com/parent", "enterprise-security", "8.5", long_content))
    parent_row = dict(conn.execute("SELECT * FROM documents WHERE url='https://es.example.com/parent'").fetchone())

    chunk_document(conn, parent_row)

    parent = conn.execute(
        "SELECT has_chunks FROM documents WHERE url='https://es.example.com/parent'"
    ).fetchone()
    assert parent["has_chunks"] == 1


def test_chunk_document_is_idempotent(conn):
    long_content = "Y" * 10_000
    upsert_document(conn, _doc("https://es.example.com/idem", "enterprise-security", "8.5", long_content))
    parent_row = dict(conn.execute("SELECT * FROM documents WHERE url='https://es.example.com/idem'").fetchone())

    n1 = chunk_document(conn, parent_row)
    # Re-fetch updated parent row (has_chunks is now set)
    parent_row2 = dict(conn.execute("SELECT * FROM documents WHERE url='https://es.example.com/idem'").fetchone())
    n2 = chunk_document(conn, parent_row2)

    assert n1 == n2
    chunk_count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE chunk_of='https://es.example.com/idem'"
    ).fetchone()[0]
    assert chunk_count == n1
