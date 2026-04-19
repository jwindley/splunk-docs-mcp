"""
CLI entry point for the documentation crawler.

Usage
-----
  uv run splunk-crawl                                   # crawl all Phase 1 sources
  uv run splunk-crawl --sources enterprise-security     # single source
  uv run splunk-crawl --sources enterprise-security --section user-guide   # dev/test
  uv run splunk-crawl --full                            # re-extract everything
  uv run splunk-crawl --help
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .config import DOCS_DIR, DB_PATH, PHASE1_SOURCES, SOURCES_BY_ID, CrawlSource
from .crawler import crawl_source


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
            "Only crawl pages in this section (e.g. 'user-guide'). "
            "Useful for fast development testing without a full crawl. "
            "Only meaningful for the 'enterprise-security' source."
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
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Enable DEBUG-level logging.",
    )
    return p


async def _run(args: argparse.Namespace) -> int:
    sources: list[CrawlSource] = [SOURCES_BY_ID[sid] for sid in args.sources]

    all_stats = []
    for source in sources:
        stats = await crawl_source(
            source=source,
            db_path=args.db,
            docs_dir=args.docs_dir,
            concurrency=args.concurrency,
            delay=args.delay,
            full=args.full,
            section_filter=args.section,
        )
        all_stats.append(stats)

    print("\n--- Crawl summary ---")
    for s in all_stats:
        print(f"  {s.summary()}")

    total_failures = sum(s.failed for s in all_stats)
    return 1 if total_failures > 0 else 0


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
