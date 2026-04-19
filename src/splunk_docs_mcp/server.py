"""
MCP server — exposes the Splunk docs index via 5 tools.

Run with:   uv run splunk-mcp
Configure in Claude Desktop / Claude Code:
  {
    "mcpServers": {
      "splunk-docs": {
        "command": "uv",
        "args": ["run", "--project", "/path/to/splunk-docs-mcp", "splunk-mcp"]
      }
    }
  }

Tools
-----
search_docs      — BM25 full-text search (primary tool)
get_page         — retrieve full Markdown for a URL
list_sections    — browse the index structure by source and section
browse_section   — list all pages in a section with titles and URLs
get_index_info   — stats: total pages, sources indexed, last crawl time
"""

import sqlite3
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .config import DB_PATH, SOURCES_BY_ID
from .db import (
    get_connection,
    init_db,
    search_docs as db_search,
    get_page as db_get_page,
    list_sections as db_list_sections,
    browse_section as db_browse_section,
    get_index_info as db_get_index_info,
)

# ---------------------------------------------------------------------------
# DB singleton — opened once on first use, reused across all tool calls
# ---------------------------------------------------------------------------

_db: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = get_connection(DB_PATH)
        init_db(_db)
    return _db


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="splunk-docs",
    instructions=(
        "Search and retrieve Splunk documentation. "
        "Phase 1 covers: Splunk Enterprise Security 8.5 and the "
        "Splunk Configuration File Reference 10.2.\n\n"
        "Typical workflow:\n"
        "  1. search_docs('your keywords') — find relevant pages\n"
        "  2. get_page(url) — read the full content of a result\n"
        "  3. list_sections() / browse_section() — explore the doc tree\n"
        "  4. get_index_info() — check what's indexed and when it was last crawled"
    ),
)


# ---------------------------------------------------------------------------
# Tool: search_docs
# ---------------------------------------------------------------------------

@mcp.tool()
def search_docs(
    query: Annotated[
        str,
        Field(description=(
            "Search keywords or a quoted phrase. "
            "Examples: 'correlation rule', 'risk score threshold', '\"notable event\"'"
        )),
    ],
    source: Annotated[
        str | None,
        Field(description=(
            "Limit search to a specific source. "
            "Options: 'enterprise-security', 'admin-manual'. "
            "Omit to search across all indexed sources."
        )),
    ] = None,
    # Future: add `version: str | None = None` filter here when multi-version
    # support is added (Phase 2+). For now, one version per source is indexed.
    limit: Annotated[
        int,
        Field(description="Maximum number of results to return (1–20).", ge=1, le=20),
    ] = 5,
) -> list[dict]:
    """
    Full-text BM25 search across all indexed Splunk documentation.

    Returns ranked results with title, URL, source, version, section, and a
    ~30-token snippet showing where the query terms appear in the content.
    Lower score values indicate better matches (SQLite BM25 convention).
    """
    if source and source not in SOURCES_BY_ID:
        valid = ", ".join(SOURCES_BY_ID.keys())
        return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

    results = db_search(_get_db(), query, source=source, limit=limit)
    if not results:
        return [{"message": "No results found. Try broader keywords or check get_index_info() to confirm the index is populated."}]
    return results


# ---------------------------------------------------------------------------
# Tool: get_page
# ---------------------------------------------------------------------------

@mcp.tool()
def get_page(
    url: Annotated[
        str,
        Field(description=(
            "Exact URL of the documentation page to retrieve. "
            "Get URLs from search_docs() results or browse_section()."
        )),
    ],
) -> dict:
    """
    Retrieve the full Markdown content of a specific documentation page by URL.

    Returns title, source, version, section, full Markdown body, crawl timestamp,
    and character count. Returns an error dict if the URL is not in the index.
    """
    page = db_get_page(_get_db(), url)
    if page is None:
        return {"error": f"Page not found in index: {url}"}
    return page


# ---------------------------------------------------------------------------
# Tool: list_sections
# ---------------------------------------------------------------------------

@mcp.tool()
def list_sections(
    source: Annotated[
        str | None,
        Field(description=(
            "Filter by source: 'enterprise-security' or 'admin-manual'. "
            "Omit to list sections for all sources."
        )),
    ] = None,
) -> list[dict]:
    """
    List all indexed sections grouped by source and version, with page counts.

    Use this to understand what documentation is available before searching,
    or to pick a section name for browse_section().
    """
    if source and source not in SOURCES_BY_ID:
        valid = ", ".join(SOURCES_BY_ID.keys())
        return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

    rows = db_list_sections(_get_db(), source=source)
    if not rows:
        return [{"message": "Index is empty. Run 'uv run splunk-crawl' to populate it."}]
    return rows


# ---------------------------------------------------------------------------
# Tool: browse_section
# ---------------------------------------------------------------------------

@mcp.tool()
def browse_section(
    section: Annotated[
        str,
        Field(description=(
            "Section slug to browse, e.g. 'user-guide', 'administer', "
            "'install'. Get valid names from list_sections()."
        )),
    ],
    source: Annotated[
        str,
        Field(description=(
            "Source the section belongs to: 'enterprise-security' or 'admin-manual'."
        )),
    ],
    subsection: Annotated[
        str | None,
        Field(description="Optional subsection slug to narrow results further."),
    ] = None,
) -> list[dict]:
    """
    List all pages in a section with their titles, URLs, and character counts.

    Useful for enumerating available pages before deciding which to read in full
    with get_page(), or for building a reading list on a topic.
    """
    if source not in SOURCES_BY_ID:
        valid = ", ".join(SOURCES_BY_ID.keys())
        return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

    rows = db_browse_section(_get_db(), section=section, source=source, subsection=subsection)
    if not rows:
        return [{"message": f"No pages found for section='{section}' source='{source}'. Check list_sections() for valid names."}]
    return rows


# ---------------------------------------------------------------------------
# Tool: get_index_info
# ---------------------------------------------------------------------------

@mcp.tool()
def get_index_info() -> dict:
    """
    Return database statistics: total pages, sources indexed, last crawl
    timestamp, and database size.

    Use this to verify the index is populated before searching, or to check
    when documentation was last updated.
    """
    return db_get_index_info(_get_db())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    mcp.run()
