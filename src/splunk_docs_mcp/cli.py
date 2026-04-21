"""
CLI entry point for the documentation crawler.

Usage
-----
  uv run splunk-crawl                                   # crawl all Phase 1 sources
  uv run splunk-crawl --sources enterprise-security     # single source
  uv run splunk-crawl --sources enterprise-security --section user-guide   # dev/test
  uv run splunk-crawl --full                            # re-extract everything
  uv run splunk-crawl --rechunk                         # rebuild chunks only (no crawl)
  uv run splunk-crawl --help

After each crawl the CLI runs a post-crawl embedding pass that generates a
384-dimensional sentence embedding (all-MiniLM-L6-v2) for every document that
doesn't have one yet and stores it as a BLOB in the documents table.  Use
--full to force re-embedding of all documents.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from .config import DOCS_DIR, DB_PATH, PHASE1_SOURCES, SOURCES_BY_ID, CrawlSource
from .crawler import crawl_source
from . import db as db_module


def _build_parser() -> argparse.ArgumentParser:
    source_ids = [s.source_id for s in PHASE1_SOURCES]

    p = argparse.ArgumentParser(
        prog="splunk-crawl",
        description="Crawl Splunk documentation and build the local search index.",
    )
    p.add_argument(
        "--sources",
        nargs="+",
        metavar="SOURCE_ID",
        default=source_ids,
        choices=source_ids,
        help=(
            f"Source(s) to crawl. Choices: {', '.join(source_ids)}. "
            f"Default: all sources."
        ),
    )
    p.add_argument(
        "--section",
        metavar="SECTION",
        default=None,
        help=(
            "Only crawl pages in this section. "
            "For enterprise-security use e.g. 'user-guide'; "
            "for lantern use e.g. 'Splunk_Success_Framework'. "
            "Useful for fast development testing without a full crawl."
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Number of simultaneous HTTP requests per source. Default: 3.",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECONDS",
        help="Delay between requests in seconds (rate limiting). Default: 0.5.",
    )
    p.add_argument(
        "--full",
        action="store_true",
        default=False,
        help=(
            "Re-extract and overwrite all pages even if their HTML hash hasn't "
            "changed. Default: incremental (skip unchanged pages)."
        ),
    )
    p.add_argument(
        "--rechunk",
        action="store_true",
        default=False,
        help=(
            "Delete and rebuild all chunks for the specified sources using the "
            "current chunking strategy, without re-crawling. "
            "The embed pass runs automatically for the newly created chunks."
        ),
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        metavar="PATH",
        help=f"Path to the SQLite database. Default: {DB_PATH}",
    )
    p.add_argument(
        "--docs-dir",
        type=Path,
        default=DOCS_DIR,
        metavar="PATH",
        help=f"Root directory for Markdown output files. Default: {DOCS_DIR}",
    )
    p.add_argument(
        "--delay-jitter",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Maximum random jitter added to each request delay "
            "(uniform distribution over [0, JITTER]). Default: 0."
        ),
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )
    return p


async def _run(args: argparse.Namespace) -> int:
    sources: list[CrawlSource] = [SOURCES_BY_ID[sid] for sid in args.sources]

    if args.rechunk:
        # Skip crawl — only rebuild chunks and embed new chunk rows
        print(f"\nRechunking {len(sources)} source(s): {', '.join(s.source_id for s in sources)}")
        _chunk_pass(args, sources)
        _embed_pass(args, sources)
        return 0

    all_stats = []
    for source in sources:
        stats = await crawl_source(
            source=source,
            db_path=args.db,
            docs_dir=args.docs_dir,
            concurrency=args.concurrency,
            delay=args.delay,
            delay_jitter=args.delay_jitter,
            full=args.full,
            section_filter=args.section,
        )
        all_stats.append(stats)

    print("\n--- Crawl summary ---")
    for s in all_stats:
        print(f"  {s.summary()}")

    # Post-crawl pass order: chunk → embed → dedup
    _chunk_pass(args, sources)
    _embed_pass(args, sources)
    _dedup_pass(args)

    total_failures = sum(s.failed for s in all_stats)
    total_attempted = sum(s.total for s in all_stats)
    failure_rate = total_failures / total_attempted if total_attempted else 0
    if failure_rate > 0.05:
        logger.warning(
            "Failure rate %.1f%% (%d/%d) exceeds 5%% threshold — exiting with code 1.",
            failure_rate * 100, total_failures, total_attempted,
        )
        return 1
    if total_failures:
        logger.info(
            "%d page(s) failed (%.1f%%) — within acceptable threshold, exiting 0.",
            total_failures, failure_rate * 100,
        )
    return 0


def _chunk_pass(args: argparse.Namespace, sources: list[CrawlSource]) -> None:
    """
    Split documents over CHUNK_THRESHOLD characters into overlapping chunk rows.

    Chunk rows get their own FTS5 entries and embeddings so search finds the
    relevant section rather than scoring the whole document.  The parent row
    is marked has_chunks=1 and excluded from search; get_page() reassembles
    chunks transparently when called with the original URL.

    With --full, all existing chunks for the crawled sources are deleted and
    rebuilt from scratch.
    """
    logger = logging.getLogger(__name__)

    conn = db_module.get_connection(args.db)
    db_module.init_db(conn)

    if args.full or args.rechunk:
        for source in sources:
            conn.execute(
                "DELETE FROM documents WHERE chunk_of IN "
                "(SELECT url FROM documents WHERE source = ?)",
                (source.source_id,),
            )
            conn.execute(
                "UPDATE documents SET has_chunks = 0 WHERE source = ?",
                (source.source_id,),
            )
        conn.commit()
        flag = "--full" if args.full else "--rechunk"
        logger.info("Cleared existing chunks for re-chunking (%s).", flag)

    total = 0
    for source in sources:
        docs = db_module.get_documents_needing_chunking(conn, source_id=source.source_id)
        for doc in docs:
            n = db_module.chunk_document(conn, dict(doc))
            total += 1
            logger.debug("Chunked %s into %d parts.", doc["url"], n)

    if total:
        logger.info("Chunk pass complete — %d documents split into chunks.", total)
    else:
        logger.info("No documents needed chunking.")


def _embed_pass(args: argparse.Namespace, sources: list[CrawlSource]) -> None:
    """
    Generate and store sentence embeddings for documents that don't have one.

    Loads all-MiniLM-L6-v2 via sentence-transformers (lazy — model is only
    downloaded on first run, then cached in ~/.cache/torch/sentence_transformers).
    Encodes the full title + content_md text, stores the 384-dim float32 vector
    as a BLOB in documents.embedding.

    With --full, clears existing embeddings for the given sources first so every
    document is re-embedded from scratch.
    """
    logger = logging.getLogger(__name__)

    conn = db_module.get_connection(args.db)
    db_module.init_db(conn)

    # --full: clear embeddings for the crawled sources so all docs are re-embedded
    if args.full:
        for source in sources:
            conn.execute(
                "UPDATE documents SET embedding = NULL WHERE source = ?",
                (source.source_id,),
            )
        conn.commit()
        logger.info("Cleared existing embeddings for re-embedding (--full).")

    # Collect docs that need embedding across all crawled sources
    docs: list = []
    for source in sources:
        docs.extend(
            db_module.get_documents_without_embeddings(conn, source.source_id)
        )

    if not docs:
        logger.info("All documents already have embeddings — skipping embed pass.")
        return

    # Reuse embeddings for documents whose content matches an already-encoded row.
    # This avoids re-encoding identical pages on incremental re-crawls and across
    # sources/versions once multi-version crawling is active.
    to_encode: list = []
    reused = 0
    for doc in docs:
        existing = db_module.get_embedding_by_hash(conn, doc["content_hash"])
        if existing is not None:
            db_module.update_embedding(conn, doc["id"], existing)
            reused += 1
        else:
            to_encode.append(doc)
    if reused:
        conn.commit()
        logger.info("Reused %d embedding(s) from matching content_hash.", reused)

    if not to_encode:
        logger.info("Embedding pass complete — all %d via hash reuse.", reused)
        return

    logger.info("Loading embedding model (all-MiniLM-L6-v2)…")
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415
    model = SentenceTransformer("all-MiniLM-L6-v2")

    logger.info("Generating embeddings for %d documents…", len(to_encode))
    texts = [f"{doc['title']}\n\n{doc['content_md']}" for doc in to_encode]

    # Batch encode — sentence-transformers handles padding/truncation internally.
    # show_progress_bar gives a tqdm bar for longer runs.
    embeddings = model.encode(
        texts,
        normalize_embeddings=True,
        batch_size=32,
        show_progress_bar=True,
    )

    for doc, emb in zip(to_encode, embeddings):
        db_module.update_embedding(conn, doc["id"], emb.astype("float32").tobytes())

    conn.commit()
    logger.info(
        "Embedding pass complete — %d encoded, %d reused from hash.",
        len(to_encode), reused,
    )


def _dedup_pass(args: argparse.Namespace) -> None:
    """
    Mark cross-source duplicate documents so they are excluded from search results.

    Runs across ALL sources in the DB (not just the currently crawled subset) so
    that newly crawled pages are correctly evaluated against the full corpus.
    Idempotent — always resets and rebuilds from scratch.
    """
    logger = logging.getLogger(__name__)
    conn = db_module.get_connection(args.db)
    db_module.init_db(conn)
    n = db_module.run_dedup_pass(conn)
    if n:
        logger.info("Dedup pass complete — %d duplicate rows suppressed.", n)
    else:
        logger.info("Dedup pass complete — no cross-source duplicates found.")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)
