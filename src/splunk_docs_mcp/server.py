"""
MCP server — exposes the Splunk docs index via 6 tools.

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
search_docs          — BM25 full-text search (exact keywords / config keys)
search_docs_semantic — cosine-similarity vector search (natural language / concepts)
get_page             — retrieve full Markdown for a URL
list_sections        — browse the index structure by source and section
browse_section       — list all pages in a section with titles and URLs
get_index_info       — stats: total pages, sources indexed, last crawl time
"""

import logging
import sqlite3
import sys
import time
from typing import Annotated

import numpy as np
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from sentence_transformers import SentenceTransformer

from .config import DB_PATH, SOURCES_BY_ID
from .db import (
    get_connection,
    init_db,
    search_docs as db_search,
    search_docs_semantic as db_search_semantic,
    get_page as db_get_page,
    list_sections as db_list_sections,
    browse_section as db_browse_section,
    get_index_info as db_get_index_info,
)

# ---------------------------------------------------------------------------
# Logging — goes to stderr so it doesn't interfere with the stdio MCP protocol
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [splunk-mcp] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedding model — loaded once at startup so the first search_docs_semantic
# call is not penalised by a 6-second model-load delay.
# ---------------------------------------------------------------------------

logger.info("Loading sentence-transformers model (all-MiniLM-L6-v2)…")
_embed_model = SentenceTransformer("all-MiniLM-L6-v2")
logger.info("Embedding model ready.")

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
        "Splunk documentation index. Sources: Splunk Enterprise Security 8.5 "
        "(source='enterprise-security') and Splunk Configuration File Reference 10.2 "
        "(source='admin-manual').\n\n"

        "DECISION TREE — apply before every question:\n\n"

        "1. TOPIC KNOWN, SECTION KNOWN\n"
        "   (you recognise the ES feature, conf file, or doc area)\n"
        "   → browse_section(section, source)   [lists pages in that section]\n"
        "   → get_page(url)                      [read the 1–2 most relevant pages]\n"
        "   DONE. Target: 2 calls.\n\n"

        "2. TOPIC KNOWN, SECTION UNCERTAIN\n"
        "   → list_sections(source)              [see available sections — call once]\n"
        "   → browse_section(section, source)\n"
        "   → get_page(url)\n"
        "   DONE. Target: 3 calls.\n\n"

        "3. TOPIC UNKNOWN — exact term, config key, or quoted phrase\n"
        "   → search_docs(query, source=...)     [BM25 keyword search]\n"
        "   → get_page(url)                      [read top 1–2 results]\n"
        "   DONE. Target: 2–3 calls.\n\n"

        "4. TOPIC UNKNOWN — concept question or natural-language description\n"
        "   → search_docs_semantic(query, source=...)\n"
        "   → get_page(url)                      [read top 1–2 results]\n"
        "   DONE. Target: 2–3 calls.\n\n"

        "5. SEARCH RETURNED POOR RESULTS\n"
        "   → Switch tools once: keyword→semantic or semantic→keyword\n"
        "   → get_page(url) if anything useful appears\n"
        "   STOP. Do not reformulate and search again. Report what was found.\n\n"

        "HARD LIMITS — never exceed these per question:\n"
        "  • 2 search calls total (not 2 of each tool — 2 across both)\n"
        "  • 1 list_sections call\n"
        "  • 3 get_page calls (unless the user explicitly requests a full survey)\n"
        "  • 0 get_index_info calls (only call if the user asks about index status)\n\n"

        "TOOL SELECTION GUIDE:\n"
        "  search_docs          — exact config key (inputs.conf), quoted phrase "
        "(\"notable event\"), specific setting name\n"
        "  search_docs_semantic — concept questions (how do I reduce false positives), "
        "natural language, or when keyword search returns nothing relevant\n"
        "  browse_section       — preferred entry point when the topic area is already "
        "known; faster and more precise than searching\n"
        "  list_sections        — orientation only; use once to pick a section, then "
        "switch to browse_section\n"
        "  get_page             — always read the actual page before answering; "
        "never answer from snippet text alone"
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
    t0 = time.perf_counter()
    try:
        if source and source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        results = db_search(_get_db(), query, source=source, limit=limit)
        if not results:
            return [{"message": "No results found. Try broader keywords or check get_index_info() to confirm the index is populated."}]
        return results
    finally:
        logger.info("search_docs(query=%r, source=%r, limit=%d) — %.1f ms", query, source, limit, (time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------------------
# Tool: search_docs_semantic
# ---------------------------------------------------------------------------

@mcp.tool()
def search_docs_semantic(
    query: Annotated[
        str,
        Field(description=(
            "Natural-language question or concept description. "
            "Examples: 'how do I configure risk scoring thresholds', "
            "'lateral movement detection', 'suppress duplicate notable events'."
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
    limit: Annotated[
        int,
        Field(description="Maximum number of results to return (1–20).", ge=1, le=20),
    ] = 5,
) -> list[dict]:
    """
    Semantic vector search across all indexed Splunk documentation.

    Use this tool when search_docs (keyword search) returns poor results because
    the query uses different terminology than the documentation — e.g. concept
    questions, 'how do I…' queries, or natural-language descriptions of a feature.

    Embeddings are generated at crawl time (all-MiniLM-L6-v2, 384 dims).
    Returns results ranked by cosine similarity score (1.0 = most similar).
    Returns a message if no embeddings exist — run 'uv run splunk-crawl' first.
    """
    t0 = time.perf_counter()
    try:
        if source and source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        q_vec = _embed_model.encode(query, normalize_embeddings=True).astype(np.float32)

        results = db_search_semantic(_get_db(), q_vec, source=source, limit=limit)
        if not results:
            return [{
                "message": (
                    "No embeddings found. Run 'uv run splunk-crawl' to generate them. "
                    "You can also try search_docs() for keyword-based search."
                )
            }]
        return results
    finally:
        logger.info(
            "search_docs_semantic(query=%r, source=%r, limit=%d) — %.1f ms",
            query, source, limit, (time.perf_counter() - t0) * 1000,
        )


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
    t0 = time.perf_counter()
    try:
        page = db_get_page(_get_db(), url)
        if page is None:
            return {"error": f"Page not found in index: {url}"}
        return page
    finally:
        logger.info("get_page(url=%r) — %.1f ms", url, (time.perf_counter() - t0) * 1000)


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
    t0 = time.perf_counter()
    try:
        if source and source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        rows = db_list_sections(_get_db(), source=source)
        if not rows:
            return [{"message": "Index is empty. Run 'uv run splunk-crawl' to populate it."}]
        return rows
    finally:
        logger.info("list_sections(source=%r) — %.1f ms", source, (time.perf_counter() - t0) * 1000)


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
    t0 = time.perf_counter()
    try:
        if source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        rows = db_browse_section(_get_db(), section=section, source=source, subsection=subsection)
        if not rows:
            return [{"message": f"No pages found for section='{section}' source='{source}'. Check list_sections() for valid names."}]
        return rows
    finally:
        logger.info("browse_section(section=%r, source=%r, subsection=%r) — %.1f ms", section, source, subsection, (time.perf_counter() - t0) * 1000)


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
    t0 = time.perf_counter()
    try:
        return db_get_index_info(_get_db())
    finally:
        logger.info("get_index_info() — %.1f ms", (time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    mcp.run()
