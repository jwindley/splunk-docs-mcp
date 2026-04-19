"""
Runtime constants and crawl-source definitions.

To add a new crawl source (e.g. Lantern, core Splunk Enterprise docs):
  1. Add a CrawlSource entry to PHASE1_SOURCES.
  2. No other files need changing.
"""

from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Project root is two levels up from this file (src/splunk_docs_mcp/config.py)
_PROJECT_ROOT = Path(__file__).parent.parent.parent

DATA_DIR = _PROJECT_ROOT / "data"
DOCS_DIR = DATA_DIR / "docs"
DB_PATH = DATA_DIR / "splunk_docs.db"

# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

CRAWL_HEADERS = {
    "User-Agent": (
        "splunk-docs-mcp-crawler/0.1 "
        "(local knowledge base indexer; not for commercial use)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ---------------------------------------------------------------------------
# CrawlSource
# ---------------------------------------------------------------------------


@dataclass
class CrawlSource:
    """Defines a single documentation source to crawl.

    Adding a new source (e.g. lantern.splunk.com) requires only a new
    CrawlSource entry here — crawler, DB, and server code are source-agnostic.
    """

    source_id: str
    """Stable identifier stored in the DB: 'enterprise-security', 'admin-manual'."""

    display_name: str
    """Human-readable label shown in tool output."""

    version: str
    """Product version stored in the DB: '8.5', '10.2', etc."""

    seed_urls: list[str]
    """BFS starting points. Should cover all top-level entry points for this source."""

    url_prefix: str
    """Only follow links whose full URL starts with this string."""


# ---------------------------------------------------------------------------
# Phase 1 sources
# ---------------------------------------------------------------------------

_ES_SECTIONS = [
    "install",
    "administer",
    "user-guide",
    "troubleshoot",
    "release-notes-and-resources",
    "enterprise-security-editions",
]

PHASE1_SOURCES: list[CrawlSource] = [
    CrawlSource(
        source_id="enterprise-security",
        display_name="Splunk Enterprise Security 8.5",
        version="8.5",
        seed_urls=[
            # Top-level landing page (discovers cross-section nav links)
            "https://help.splunk.com/en/splunk-enterprise-security-8",
            # Section-specific entry points ensure full coverage even if the
            # landing page nav doesn't link to every section directly.
            *[
                f"https://help.splunk.com/en/splunk-enterprise-security-8/{s}/8.5"
                for s in _ES_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-enterprise-security-8/",
    ),
    CrawlSource(
        source_id="admin-manual",
        display_name="Splunk Configuration File Reference 10.2",
        version="10.2",
        seed_urls=[
            # Index page for the config file reference section
            (
                "https://help.splunk.com/en/data-management/splunk-enterprise-admin-manual"
                "/10.2/configuration-file-reference/10.2.0-configuration-file-reference"
            ),
        ],
        url_prefix=(
            "https://help.splunk.com/en/data-management/splunk-enterprise-admin-manual"
            "/10.2/configuration-file-reference/"
        ),
    ),
    # Future sources (not yet active):
    #
    # CrawlSource(
    #     source_id="lantern",
    #     display_name="Splunk Lantern",
    #     version="current",
    #     seed_urls=["https://lantern.splunk.com/"],
    #     url_prefix="https://lantern.splunk.com/",
    # ),
    #
    # CrawlSource(
    #     source_id="splunk-enterprise",
    #     display_name="Splunk Enterprise Documentation",
    #     version="9.x",
    #     seed_urls=["https://help.splunk.com/en/splunk-enterprise/..."],
    #     url_prefix="https://help.splunk.com/en/splunk-enterprise/",
    # ),
]

SOURCES_BY_ID: dict[str, CrawlSource] = {s.source_id: s for s in PHASE1_SOURCES}
