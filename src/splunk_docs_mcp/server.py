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
search_docs_hybrid   — BM25 + semantic fused via RRF (default first choice)
search_docs          — BM25 full-text search (exact keywords / config keys)
search_docs_semantic — cosine-similarity vector search (natural language / concepts)
get_page             — retrieve full Markdown for a URL
list_sections        — browse the index structure by source and section
browse_section       — list all pages in a section with titles and URLs
get_index_info       — stats: total pages, sources indexed, last crawl time
"""

import concurrent.futures
import functools
import logging
import sqlite3
import sys
import threading
import time
from typing import Annotated

import numpy as np
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from sentence_transformers import SentenceTransformer

from .config import DB_PATH, PHASE1_SOURCES, SOURCES_BY_ID
from .db import (
    get_connection,
    init_db,
    search_docs as db_search,
    search_docs_semantic_vec as db_search_semantic_vec,
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
# DB — opened synchronously (fast; just opens a file).
# ---------------------------------------------------------------------------

logger.info("Opening database…")
_db: sqlite3.Connection = get_connection(DB_PATH)
init_db(_db)
logger.info("Database ready.")

# ---------------------------------------------------------------------------
# Embedding model — loaded in a background thread so the server is available
# immediately on startup. search_docs_semantic and search_docs_hybrid wait on
# _model_ready before encoding queries; the wait returns instantly once set.
# ---------------------------------------------------------------------------

_model_ready = threading.Event()
_embed_model: SentenceTransformer | None = None


def _load_model() -> None:
    global _embed_model
    logger.info("Loading sentence-transformers model (all-MiniLM-L6-v2)…")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    # Pre-warm PyTorch JIT — batch of representative-length strings compiles
    # all JIT paths so the first real query is ~50ms, not ~450ms.
    model.encode(
        [
            "warmup",
            "how to configure correlation searches in enterprise security",
            "transforms.conf configuration file reference lookup table",
            "search head cluster replication factor peer nodes troubleshooting",
            "alert actions notable events threat intelligence lookup dashboard",
        ],
        normalize_embeddings=True,
    )
    _embed_model = model
    _model_ready.set()
    logger.info("Embedding model ready.")


threading.Thread(target=_load_model, daemon=True, name="model-loader").start()


def _get_db() -> sqlite3.Connection:
    return _db


# ---------------------------------------------------------------------------
# LRU caches for search tools — keyed on (query, source, version, limit).
# The DB connection is read-only during server lifetime so staleness is not
# a concern within a session.
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=128)
def _search_docs_cached(
    query: str, source: str | None, version: str | None, limit: int
) -> list[dict]:
    return db_search(_get_db(), query, source=source, version=version, limit=limit)


@functools.lru_cache(maxsize=128)
def _search_docs_semantic_cached(
    query: str, source: str | None, version: str | None, limit: int
) -> list[dict]:
    _model_ready.wait()  # no-op once model is loaded; blocks only during startup
    q_vec = _embed_model.encode(query, normalize_embeddings=True).astype(np.float32)
    return db_search_semantic_vec(_get_db(), q_vec, source=source, version=version, limit=limit)


@functools.lru_cache(maxsize=128)
def _search_docs_hybrid_cached(
    query: str, source: str | None, version: str | None, limit: int
) -> list[dict]:
    fetch_n = min(limit * 2, 20)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        bm25_future = executor.submit(_search_docs_cached, query, source, version, fetch_n)
        sem_future = executor.submit(_search_docs_semantic_cached, query, source, version, fetch_n)
        bm25_results = bm25_future.result()
        sem_results = sem_future.result()

    k = 60
    rrf_scores: dict[str, float] = {}
    docs: dict[str, dict] = {}

    for rank, doc in enumerate(bm25_results, start=1):
        url = doc.get("url", "")
        if url:
            rrf_scores[url] = rrf_scores.get(url, 0.0) + 1.0 / (k + rank)
            docs[url] = doc  # BM25 result has snippet — prefer it

    for rank, doc in enumerate(sem_results, start=1):
        url = doc.get("url", "")
        if url:
            rrf_scores[url] = rrf_scores.get(url, 0.0) + 1.0 / (k + rank)
            if url not in docs:
                docs[url] = doc

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    results = []
    for url, rrf_score in ranked:
        doc = {**docs[url], "rrf_score": round(rrf_score, 6)}
        doc.pop("score", None)
        results.append(doc)
    return results


# ---------------------------------------------------------------------------
# MCP server — source list and valid versions built from config at startup.
# ---------------------------------------------------------------------------

_LANTERN_NOTE = " (use-case guidance, best practices)"
_source_list = "\n".join(
    f"  {s.source_id:<26} — {s.display_name}"
    + (_LANTERN_NOTE if s.source_id == "lantern" else "")
    for s in PHASE1_SOURCES
)

# Unique version strings in PHASE1_SOURCES order (current versions first).
_seen: set[str] = set()
_valid_versions: list[str] = []
for _s in PHASE1_SOURCES:
    if _s.version not in _seen:
        _seen.add(_s.version)
        _valid_versions.append(f"'{_s.version}'")
_valid_versions_str = ", ".join(_valid_versions)

_source_options = ", ".join(f"'{s.source_id}'" for s in PHASE1_SOURCES)

mcp = FastMCP(
    name="splunk-docs",
    instructions=(
        "ALWAYS consult this server before answering any Splunk-related question. "
        "Never answer from training data alone — training data is outdated and error-prone "
        "for Splunk specifics. If the user asks anything about Splunk configuration, "
        "administration, search, dashboards, alerts, data ingestion, Enterprise Security, "
        "SOAR, Observability, or any other Splunk product or feature, use the tools below "
        "to retrieve the current documentation first, then answer from that.\n\n"

        f"Available sources (use source= to target one; combine with version= to pinpoint):\n"
        f"{_source_list}\n\n"

        "CALL LIMIT — 4 tool calls maximum per question. Read this before choosing any tool.\n"
        "  Count every search_docs*, browse_section, list_sections, and get_page call.\n"
        "  After your 4th call, write your answer immediately. Never make a 5th call.\n"
        "  Most questions need only 2 calls. At call 3, stop searching — read what you have.\n"
        "  Never call get_page for a URL you have already fetched in this conversation.\n\n"

        "VERSION FILTER — apply on the FIRST call, every call, no exceptions:\n"
        "  User mentions a version → version= goes on every single search call.\n"
        "  WRONG: search_docs_hybrid('event-based detection', source='enterprise-security')\n"
        "  RIGHT: search_docs('event-based detection', source='enterprise-security', version='8.4')\n"
        "  Omitting version= returns mostly current-version results. You will then wrongly\n"
        "  conclude the older version is not indexed. It is indexed — use version=.\n"
        f"  Valid values: {_valid_versions_str}.\n\n"
        "  READING version_tags IN RESULTS: every result includes a version_tags list.\n"
        "  version='8.5' + version_tags=['8.5','8.4'] → authoritative for both versions.\n"
        "  Do NOT say a topic is missing from 8.4 when version_tags contains '8.4'.\n\n"

        "DECISION TREE — choose one path per question:\n\n"

        "A. CONF FILE STANZA LOOKUP (.conf file question)\n"
        "   → search_docs(stanza_name, source='admin-manual')  [BM25 — finds exact stanza fast]\n"
        "   → get_page(chunk_url from result)                  [read that 1,500-char section]\n"
        "   DONE. 2 calls. Never use browse_section for conf files — it returns 288 rows.\n\n"

        "B. SEARCH FIRST (DEFAULT — unknown topic or section)\n"
        "   → search_docs_hybrid(query, source=...)  [BM25 + semantic, RRF-fused]\n"
        "   → get_page(url or chunk_url)             [read the top result]\n"
        "   DONE. 2 calls.\n\n"

        "C. KNOWN SECTION (ES feature, product area — not conf files)\n"
        "   → browse_section(section, source)   [lists pages in that section]\n"
        "   → get_page(url)                      [read the most relevant page]\n"
        "   DONE. 2 calls.\n\n"

        "D. COMPARISON QUESTION (A vs B, differences between X and Y, pros/cons)\n"
        "   → search_docs_hybrid(name_of_A, source=...)   [call 1 — A-specific pages]\n"
        "   → search_docs_hybrid(name_of_B, source=...)   [call 2 — B-specific pages]\n"
        "   → get_page(best result for A)                 [call 3]\n"
        "   → get_page(best result for B)                 [call 4]\n"
        "   DONE. 4 calls. Search each concept separately — never combine A and B in one query.\n\n"

        "E. POOR RESULTS (first search returned nothing useful)\n"
        "   → Try one alternative: search_docs(exact term) OR search_docs_semantic(concept)\n"
        "   → get_page(url) if something useful appears\n"
        "   STOP. Never search a third time. Report what was found and acknowledge the gap.\n\n"

        "CONFIDENCE AND UNCERTAINTY — mandatory:\n"
        "  • If retrieved pages do not directly address the question, say so explicitly "
        "before attempting to synthesise an answer from partial information.\n"
        "  • If you are uncertain whether the retrieved content is correct or complete "
        "for the user's specific version or configuration, state that uncertainty.\n"
        "  • Never present a synthesised answer as if it came from authoritative docs "
        "when the retrieved content only partially matches the question.\n"
        "  • Prefer 'The documentation does not cover this directly' over a confident "
        "answer inferred from loosely related content.\n\n"

        "TOOL SELECTION GUIDE:\n"
        "  search_docs          — FIRST CHOICE for conf file stanzas, exact setting names, "
        "quoted phrases; also use when version= is set (BM25 is faster and more precise)\n"
        "  search_docs_hybrid   — first choice for unknown topics without an exact term; "
        "handles natural-language and keyword queries via RRF fusion\n"
        "  search_docs_semantic — fallback for concept questions when keyword search returns nothing\n"
        "  browse_section       — use ONLY when the topic area (not a conf file) is already "
        "known and you need the page list; never for conf files\n"
        "  list_sections        — orientation only; call once max\n"
        "  get_page             — read a page before answering; pass chunk_url not parent URL "
        "for conf files; never answer from snippet text alone; never repeat a URL"
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
            f"Options: {_source_options}. "
            "Omit to search across all indexed sources."
        )),
    ] = None,
    version: Annotated[
        str | None,
        Field(description=(
            "Filter by product version. "
            "Valid values: '8.3', '8.4', '8.5', '10.2', '10.3.2512', 'current'. "
            "Combine with source= for precise targeting, or use alone to search "
            "a specific release across all sources that have it."
        )),
    ] = None,
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

    Large documents (e.g. server.conf, transforms.conf) are split into
    overlapping 1,500-character chunks. When a result came from a chunk, the
    result includes a 'chunk_url' field alongside the parent 'url'. Pass the
    chunk_url to get_page() to retrieve just that section (~1,500 chars) with
    navigation pointers to adjacent chunks — much more efficient than fetching
    the truncated full page.

    If the returned snippets do not directly address the question, state that
    explicitly rather than synthesising an answer from partially relevant content.
    """
    t0 = time.perf_counter()
    try:
        if source and source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        results = _search_docs_cached(query, source, version, limit)
        if not results:
            return [{"message": "No results found. Try broader keywords or check get_index_info() to confirm the index is populated."}]
        return results
    finally:
        logger.info("search_docs(query=%r, source=%r, version=%r, limit=%d) — %.1f ms", query, source, version, limit, (time.perf_counter() - t0) * 1000)


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
            f"Options: {_source_options}. "
            "Omit to search across all indexed sources."
        )),
    ] = None,
    version: Annotated[
        str | None,
        Field(description=(
            "Filter by product version. "
            "Valid values: '8.3', '8.4', '8.5', '10.2', '10.3.2512', 'current'. "
            "Combine with source= for precise targeting."
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
    Large documents are embedded as overlapping chunks for finer-grained retrieval.
    Returns results ranked by cosine similarity score (1.0 = most similar).
    Returns a message if no embeddings exist — run 'uv run splunk-crawl' first.

    Cosine similarity scores do not indicate factual relevance — a high score means
    the document is topically similar, not that it answers the question directly.
    If the retrieved pages do not address the question, say so rather than
    synthesising an answer from loosely related content.
    """
    t0 = time.perf_counter()
    try:
        if source and source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        results = _search_docs_semantic_cached(query, source, version, limit)
        if not results:
            return [{
                "message": (
                    "No results found. Run 'uv run splunk-crawl' to generate embeddings, "
                    "or try search_docs() for keyword-based search."
                )
            }]
        return results
    finally:
        logger.info(
            "search_docs_semantic(query=%r, source=%r, version=%r, limit=%d) — %.1f ms",
            query, source, version, limit, (time.perf_counter() - t0) * 1000,
        )


# ---------------------------------------------------------------------------
# Tool: search_docs_hybrid
# ---------------------------------------------------------------------------

@mcp.tool()
def search_docs_hybrid(
    query: Annotated[
        str,
        Field(description=(
            "Search query — any form: exact term, config key, natural-language question, "
            "or concept description. Combines BM25 and semantic search for best coverage."
        )),
    ],
    source: Annotated[
        str | None,
        Field(description=(
            "Limit search to a specific source. "
            f"Options: {_source_options}. "
            "Omit to search across all indexed sources."
        )),
    ] = None,
    version: Annotated[
        str | None,
        Field(description=(
            "Filter by product version. "
            "Valid values: '8.3', '8.4', '8.5', '10.2', '10.3.2512', 'current'. "
            "Combine with source= for precise targeting."
        )),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Maximum number of results to return (1–20).", ge=1, le=20),
    ] = 5,
) -> list[dict]:
    """
    Hybrid search combining BM25 keyword matching and semantic vector search,
    fused via Reciprocal Rank Fusion (RRF).

    Use this as the default first search for any unknown topic — it handles both
    exact-term queries (like config key names) and natural-language questions
    equally well, without needing to guess which search mode is more appropriate.

    RRF score combines rank positions from both search methods; higher rrf_score
    indicates a result that ranked well in one or both component searches.
    Individual 'score' fields are omitted; use rrf_score for ranking comparison.

    If results are poor, follow up with search_docs() for exact terms or
    search_docs_semantic() for concept queries before giving up.
    """
    t0 = time.perf_counter()
    try:
        if source and source not in SOURCES_BY_ID:
            valid = ", ".join(SOURCES_BY_ID.keys())
            return [{"error": f"Unknown source '{source}'. Valid options: {valid}"}]

        results = _search_docs_hybrid_cached(query, source, version, limit)
        if not results:
            return [{"message": "No results found. Try search_docs() or search_docs_semantic() with different terms, or check get_index_info() to confirm the index is populated."}]
        return results
    finally:
        logger.info(
            "search_docs_hybrid(query=%r, source=%r, version=%r, limit=%d) — %.1f ms",
            query, source, version, limit, (time.perf_counter() - t0) * 1000,
        )


# ---------------------------------------------------------------------------
# Tool: get_page
# ---------------------------------------------------------------------------

_GET_PAGE_MAX_CHARS = 40_000  # ~10K tokens; server.conf is 330K — cap it

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
    Retrieve the Markdown content of a documentation page or chunk by URL.

    Pass a plain page URL to get the full document (truncated at 40,000 chars
    for very large pages like server.conf). When truncated, a 'truncated' flag
    and 'total_chars' are included.

    Pass a chunk_url from search_docs() results to get just that 1,500-char
    section. The response includes chunk_index, total_chunks, prev_chunk_url,
    and next_chunk_url to navigate adjacent sections.

    CHUNK NAVIGATION LIMIT: follow at most 1 next_chunk_url hop per question.
    If the answer is not in the initial chunk or its immediate neighbour, do NOT
    continue walking — call search_docs() again with more specific keywords to
    jump directly to the right section. Walking all chunks of a large conf file
    (server.conf has ~220 chunks) takes minutes and wastes the context window.
    """
    t0 = time.perf_counter()
    try:
        page = db_get_page(_get_db(), url)
        if page is None:
            return {"error": f"Page not found in index: {url}"}
        content = page.get("content_md") or ""
        if len(content) > _GET_PAGE_MAX_CHARS:
            page["content_md"] = content[:_GET_PAGE_MAX_CHARS]
            page["truncated"] = True
            page["total_chars"] = len(content)
            page["truncation_note"] = (
                f"Content truncated at {_GET_PAGE_MAX_CHARS:,} chars "
                f"(full page is {len(content):,} chars). "
                "Use search_docs() with specific keywords to locate a particular stanza or section."
            )
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
            "Filter by source: 'enterprise-security', 'admin-manual', "
            "'splunk-enterprise', 'splunk-cloud', or 'lantern'. "
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
            "Source the section belongs to: 'enterprise-security', 'admin-manual', "
            "'splunk-enterprise', 'splunk-cloud', or 'lantern'."
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
