"""Tests for the MCP server tool functions (server.py).

server.py runs model loading and DB connection at module level. We intercept
those calls before the module loads by patching the targets in db/ST before
the first import.
"""

import hashlib
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Remove any prior import so the patches below take effect on a clean load.
for _key in list(sys.modules):
    if _key == "splunk_docs_mcp.server":
        del sys.modules[_key]

# Build an in-memory DB that the server will use for the whole test session.
from splunk_docs_mcp.db import init_db, upsert_document  # noqa: E402

_test_conn = sqlite3.connect(":memory:")
_test_conn.row_factory = sqlite3.Row
init_db(_test_conn)

# Minimal mock for the embedding model.
_mock_model = MagicMock()
_mock_model.encode.return_value = np.zeros(384, dtype=np.float32)

# Empty embedding matrix — signals "no embeddings generated yet".
_empty_matrix = np.zeros((0, 384), dtype=np.float32)
_empty_rows: list = []

# Patch module-level initializations so importing server.py is fast + safe.
with (
    patch("splunk_docs_mcp.db.get_connection", return_value=_test_conn),
    patch("splunk_docs_mcp.db.get_all_embeddings", return_value=(_empty_matrix, _empty_rows)),
    patch("sentence_transformers.SentenceTransformer", return_value=_mock_model),
):
    import splunk_docs_mcp.server as server  # noqa: E402


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


# ---------------------------------------------------------------------------
# search_docs
# ---------------------------------------------------------------------------


def test_search_docs_invalid_source_returns_error():
    result = server.search_docs("anything", source="not-a-real-source")
    assert isinstance(result, list)
    assert "error" in result[0]
    assert "not-a-real-source" in result[0]["error"]


def test_search_docs_empty_index_returns_message():
    # Empty DB: no docs match this unique string.
    result = server.search_docs("zzz_unlikely_token_xyz")
    assert isinstance(result, list)
    assert "message" in result[0]


def test_search_docs_returns_result_for_indexed_content():
    upsert_document(_test_conn, _doc(
        "https://es.test/search-target",
        "enterprise-security", "8.5",
        "correlation search rule threshold configuration",
        section="administer",
    ))
    result = server.search_docs("correlation search rule threshold")
    assert isinstance(result, list)
    assert len(result) >= 1
    # The result should not be an error or no-results message
    assert "error" not in result[0]
    assert "message" not in result[0]


def test_search_docs_source_filter_limits_results():
    upsert_document(_test_conn, _doc(
        "https://admin.test/source-filter-page",
        "admin-manual", "10.2",
        "inputs.conf source filter documentation",
        section="configuration-reference",
    ))
    result = server.search_docs("inputs conf source filter documentation", source="admin-manual")
    assert isinstance(result, list)
    assert "error" not in result[0]
    if "message" not in result[0]:
        assert all(r["source"] == "admin-manual" for r in result)


# ---------------------------------------------------------------------------
# search_docs_semantic
# ---------------------------------------------------------------------------


def test_search_docs_semantic_invalid_source_returns_error():
    result = server.search_docs_semantic("configure risk scores", source="bad-source")
    assert isinstance(result, list)
    assert "error" in result[0]
    assert "bad-source" in result[0]["error"]


def test_search_docs_semantic_no_embeddings_returns_message():
    # _embed_matrix has 0 rows so this should return the "no embeddings" message.
    result = server.search_docs_semantic("configure risk score thresholds")
    assert isinstance(result, list)
    assert "message" in result[0]


# ---------------------------------------------------------------------------
# get_page
# ---------------------------------------------------------------------------


def test_get_page_missing_url_returns_error():
    result = server.get_page("https://es.test/page-that-does-not-exist-xyz")
    assert isinstance(result, dict)
    assert "error" in result


def test_get_page_valid_url_returns_content():
    upsert_document(_test_conn, _doc(
        "https://es.test/get-page-target",
        "enterprise-security", "8.5",
        "Full page markdown content here.",
    ))
    result = server.get_page("https://es.test/get-page-target")
    assert isinstance(result, dict)
    assert "error" not in result
    assert result["content_md"] == "Full page markdown content here."
    assert result["source"] == "enterprise-security"
    assert result["version"] == "8.5"


# ---------------------------------------------------------------------------
# list_sections
# ---------------------------------------------------------------------------


def test_list_sections_invalid_source_returns_error():
    result = server.list_sections(source="completely-fake-source")
    assert isinstance(result, list)
    assert "error" in result[0]
    assert "completely-fake-source" in result[0]["error"]


def test_list_sections_returns_section_data():
    upsert_document(_test_conn, _doc(
        "https://es.test/administer/list-sections-page",
        "enterprise-security", "8.5",
        "Administration guide content.",
        section="administer",
    ))
    result = server.list_sections(source="enterprise-security")
    assert isinstance(result, list)
    assert "error" not in result[0]
    if "message" not in result[0]:
        sources = [r["source"] for r in result]
        assert "enterprise-security" in sources


def test_list_sections_no_source_filter_returns_all():
    result = server.list_sections()
    assert isinstance(result, list)
    # Should not be an error
    assert "error" not in result[0]


# ---------------------------------------------------------------------------
# browse_section
# ---------------------------------------------------------------------------


def test_browse_section_invalid_source_returns_error():
    result = server.browse_section(section="administer", source="not-a-valid-source")
    assert isinstance(result, list)
    assert "error" in result[0]
    assert "not-a-valid-source" in result[0]["error"]


def test_browse_section_unknown_section_returns_message():
    result = server.browse_section(section="zzz-nonexistent-section", source="enterprise-security")
    assert isinstance(result, list)
    assert "message" in result[0]


def test_browse_section_returns_pages_in_section():
    upsert_document(_test_conn, _doc(
        "https://es.test/user-guide/browse-target",
        "enterprise-security", "8.5",
        "User guide content here.",
        section="user-guide",
    ))
    result = server.browse_section(section="user-guide", source="enterprise-security")
    assert isinstance(result, list)
    assert "error" not in result[0]
    if "message" not in result[0]:
        urls = [r["url"] for r in result]
        assert "https://es.test/user-guide/browse-target" in urls


# ---------------------------------------------------------------------------
# get_index_info
# ---------------------------------------------------------------------------


def test_get_index_info_returns_stats_dict():
    result = server.get_index_info()
    assert isinstance(result, dict)
    assert "total_pages" in result
    assert "db_size_bytes" in result
    assert isinstance(result["total_pages"], int)
