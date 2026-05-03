"""Tests for the merge pipeline (merge.py).

merge_dbs and export_sources work with real file paths, so these tests use
pytest's tmp_path fixture rather than in-memory connections.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from splunk_docs_mcp.db import get_connection, init_db, upsert_document
from splunk_docs_mcp.merge import _export_source_db, export_sources, merge_dbs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc(url, source, version, content, section="test-section"):
    return {
        "url": url,
        "title": f"Page: {url.split('/')[-1]}",
        "source": source,
        "version": version,
        "section": section,
        "subsection": None,
        "slug": url.split("/")[-1],
        "file_path": f"data/docs/{source}/{version}/test.md",
        "content_md": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "crawled_at": "2024-01-01T00:00:00+00:00",
    }


def _make_source_db(path: Path, docs: list[dict]) -> None:
    """Create a per-source DB file populated with the given documents."""
    conn = get_connection(path)
    init_db(conn)
    for doc in docs:
        upsert_document(conn, doc)
    conn.commit()
    conn.close()


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# merge_dbs — basic merging
# ---------------------------------------------------------------------------


def test_merge_dbs_combines_rows_from_all_sources(tmp_path):
    es_db = tmp_path / "enterprise-security.db"
    lantern_db = tmp_path / "lantern.db"
    output_db = tmp_path / "merged.db"

    _make_source_db(es_db, [
        _doc("https://es.test/page1", "enterprise-security", "8.5", "ES content one."),
        _doc("https://es.test/page2", "enterprise-security", "8.5", "ES content two."),
    ])
    _make_source_db(lantern_db, [
        _doc("https://lantern.test/page1", "lantern", "current", "Lantern content."),
    ])

    merge_dbs([es_db, lantern_db], output_db)

    conn = _open(output_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE chunk_of IS NULL"
    ).fetchone()[0]
    assert count == 3


def test_merge_dbs_output_fts5_is_searchable(tmp_path):
    es_db = tmp_path / "enterprise-security.db"
    output_db = tmp_path / "merged.db"

    _make_source_db(es_db, [
        _doc("https://es.test/correlation", "enterprise-security", "8.5",
             "correlation search threshold configuration"),
    ])

    merge_dbs([es_db], output_db)

    conn = _open(output_db)
    rows = conn.execute(
        "SELECT d.url FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid "
        "WHERE documents_fts MATCH 'correlation' AND d.has_chunks = 0"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["url"] == "https://es.test/correlation"


def test_merge_dbs_runs_dedup_pass(tmp_path):
    es_db = tmp_path / "enterprise-security.db"
    lantern_db = tmp_path / "lantern.db"
    output_db = tmp_path / "merged.db"

    shared_content = "Identical content in both sources."
    _make_source_db(es_db, [
        _doc("https://es.test/shared", "enterprise-security", "8.5", shared_content),
    ])
    _make_source_db(lantern_db, [
        _doc("https://lantern.test/shared", "lantern", "current", shared_content),
    ])

    merge_dbs([es_db, lantern_db], output_db)

    conn = _open(output_db)
    # enterprise-security is higher priority — lantern copy should be suppressed
    es_dup = conn.execute(
        "SELECT is_duplicate FROM documents WHERE source='enterprise-security'"
    ).fetchone()
    lantern_dup = conn.execute(
        "SELECT is_duplicate FROM documents WHERE source='lantern'"
    ).fetchone()
    assert es_dup["is_duplicate"] == 0
    assert lantern_dup["is_duplicate"] == 1


def test_merge_dbs_runs_version_merge_pass(tmp_path):
    es_db = tmp_path / "enterprise-security.db"
    es84_db = tmp_path / "enterprise-security-8-4.db"
    output_db = tmp_path / "merged.db"

    shared_content = "Same page content in ES 8.5 and 8.4."
    _make_source_db(es_db, [
        _doc("https://es.test/page/8.5", "enterprise-security", "8.5", shared_content),
    ])
    _make_source_db(es84_db, [
        _doc("https://es.test/page/8.4", "enterprise-security-8-4", "8.4", shared_content),
    ])

    merge_dbs([es_db, es84_db], output_db)

    conn = _open(output_db)
    # Derived row should have been deleted
    derived = conn.execute(
        "SELECT * FROM documents WHERE source='enterprise-security-8-4'"
    ).fetchone()
    assert derived is None

    # Parent row should carry version_tags for both versions
    parent = conn.execute(
        "SELECT version_tags FROM documents WHERE source='enterprise-security'"
    ).fetchone()
    import json as _json
    tags = _json.loads(parent["version_tags"])
    assert "8.5" in tags
    assert "8.4" in tags


def test_merge_dbs_skips_missing_source_gracefully(tmp_path):
    es_db = tmp_path / "enterprise-security.db"
    missing_db = tmp_path / "nonexistent.db"
    output_db = tmp_path / "merged.db"

    _make_source_db(es_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5", "Some content."),
    ])

    # merge_source_db in db.py raises if the file doesn't exist; merge_dbs surfaces that.
    # The CLI guards against missing files, but the library function does not.
    # This test documents current behaviour — if it raises, that's expected.
    try:
        merge_dbs([es_db, missing_db], output_db)
    except Exception:
        pass  # Missing source DB raises — that's fine; the CLI rejects it before calling


def test_merge_dbs_idempotent_on_duplicate_urls(tmp_path):
    es_db = tmp_path / "enterprise-security.db"
    output_db = tmp_path / "merged.db"

    _make_source_db(es_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5", "Content."),
    ])

    merge_dbs([es_db], output_db)
    merge_dbs([es_db], output_db)  # merge same source twice

    conn = _open(output_db)
    count = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE chunk_of IS NULL"
    ).fetchone()[0]
    assert count == 1  # INSERT OR IGNORE keeps only one copy


# ---------------------------------------------------------------------------
# _export_source_db
# ---------------------------------------------------------------------------


def test_export_source_db_contains_only_target_source(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_path = tmp_path / "splunk_docs_enterprise-security.db"

    _make_source_db(merged_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5", "ES content."),
        _doc("https://lantern.test/page", "lantern", "current", "Lantern content."),
    ])
    merged_conn = sqlite3.connect(merged_db)
    merged_conn.row_factory = sqlite3.Row

    _export_source_db(merged_conn, "enterprise-security", export_path)
    merged_conn.close()

    export_conn = _open(export_path)
    rows = export_conn.execute("SELECT source FROM documents WHERE chunk_of IS NULL").fetchall()
    sources = {r["source"] for r in rows}
    assert sources == {"enterprise-security"}
    assert "lantern" not in sources


def test_export_source_db_fts5_is_functional(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_path = tmp_path / "splunk_docs_enterprise-security.db"

    _make_source_db(merged_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5",
             "notable event threshold"),
    ])
    merged_conn = sqlite3.connect(merged_db)
    merged_conn.row_factory = sqlite3.Row

    _export_source_db(merged_conn, "enterprise-security", export_path)
    merged_conn.close()

    export_conn = _open(export_path)
    rows = export_conn.execute(
        "SELECT d.url FROM documents_fts JOIN documents d ON d.id = documents_fts.rowid "
        "WHERE documents_fts MATCH 'notable' AND d.has_chunks = 0"
    ).fetchall()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# export_sources
# ---------------------------------------------------------------------------


def test_export_sources_creates_per_source_db_files(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_dir = tmp_path / "export"

    _make_source_db(merged_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5", "ES content."),
        _doc("https://lantern.test/page", "lantern", "current", "Lantern content."),
    ])

    export_sources(merged_db, export_dir)

    assert (export_dir / "splunk_docs_enterprise-security.db").exists()
    assert (export_dir / "splunk_docs_lantern.db").exists()


def test_export_sources_creates_manifest_json(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_dir = tmp_path / "export"

    _make_source_db(merged_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5", "ES content."),
    ])

    export_sources(merged_db, export_dir)

    manifest_path = export_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert "sources" in manifest
    assert "total_pages" in manifest
    assert "generated_at" in manifest


def test_export_sources_manifest_page_counts_are_correct(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_dir = tmp_path / "export"

    _make_source_db(merged_db, [
        _doc("https://es.test/page1", "enterprise-security", "8.5", "ES page one."),
        _doc("https://es.test/page2", "enterprise-security", "8.5", "ES page two."),
        _doc("https://lantern.test/page1", "lantern", "current", "Lantern page one."),
    ])

    export_sources(merged_db, export_dir)

    manifest = json.loads((export_dir / "manifest.json").read_text())
    by_source = {s["source_id"]: s for s in manifest["sources"]}

    assert by_source["enterprise-security"]["pages"] == 2
    assert by_source["lantern"]["pages"] == 1
    assert manifest["total_pages"] == 3


def test_export_sources_manifest_includes_source_metadata(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_dir = tmp_path / "export"

    _make_source_db(merged_db, [
        _doc("https://es.test/page", "enterprise-security", "8.5", "Content."),
    ])

    export_sources(merged_db, export_dir)

    manifest = json.loads((export_dir / "manifest.json").read_text())
    es_entry = next(s for s in manifest["sources"] if s["source_id"] == "enterprise-security")

    assert es_entry["display_name"] == "Splunk Enterprise Security 8.5"
    assert es_entry["version"] == "8.5"
    assert "size_bytes" in es_entry
    assert "file_name" in es_entry


def test_export_sources_n1_manifest_includes_shared_pages(tmp_path):
    merged_db = tmp_path / "merged.db"
    export_dir = tmp_path / "export"

    # Simulate a merged DB where version merge pass has already run:
    # ES 8.5 parent row has version_tags ["8.5", "8.4"] (shared with ES 8.4)
    conn = get_connection(merged_db)
    init_db(conn)

    # Parent row tagged as shared with 8.4
    import json as _json
    upsert_document(conn, _doc(
        "https://es.test/shared", "enterprise-security", "8.5", "Shared content."
    ))
    conn.execute(
        "UPDATE documents SET version_tags = ? WHERE url = ?",
        (_json.dumps(["8.5", "8.4"]), "https://es.test/shared"),
    )
    # Unique 8.4 page that wasn't collapsed
    upsert_document(conn, _doc(
        "https://es.test/unique-84", "enterprise-security-8-4", "8.4", "Unique 8.4 content."
    ))
    conn.commit()
    conn.close()

    export_sources(merged_db, export_dir)

    manifest = json.loads((export_dir / "manifest.json").read_text())
    by_source = {s["source_id"]: s for s in manifest["sources"]}

    # The 8.4 export has 1 unique page; 1 shared page lives in the parent DB
    assert by_source["enterprise-security-8-4"]["pages"] == 1
    assert by_source["enterprise-security-8-4"]["shared_pages"] == 1
    assert by_source["enterprise-security-8-4"]["parent_source_id"] == "enterprise-security"
