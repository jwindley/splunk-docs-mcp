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

    crawl_delay: float = 0.5
    """Minimum seconds to wait between requests. Overrides the CLI --delay floor
    when higher. Honour robots.txt Crawl-delay here."""

    max_concurrency: int | None = None
    """Cap on concurrent workers for this source. None = use CLI --concurrency as-is.
    Set to 1 for sources with a strict Request-rate (e.g. lantern.splunk.com)."""

    blocked_path_prefixes: list[str] = field(default_factory=list)
    """Full URL prefixes that must never be crawled (robots.txt Disallow rules,
    internal API paths, etc.). Checked in addition to url_prefix filtering."""

    sitemap_url: str | None = None
    """Optional sitemap.xml URL. When set, the crawler pre-seeds the BFS queue
    from the sitemap and uses <lastmod> dates to skip unchanged pages on --full
    runs. BFS link discovery still runs as fallback for pages missing from the
    sitemap."""


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

# Enterprise URL structure: /en/splunk-enterprise/{section}/{manual}/{version}/...
# Seeds use {section}/{version} which may redirect — the landing page is the
# primary reliable seed; section seeds are belt-and-braces extras.
_ENTERPRISE_SECTIONS = [
    "get-started",
    "administer",
    "search",
    "spl-search-reference",
    "manage-knowledge-objects",
    "forward-and-process-data",
    "get-data-in",
    "create-dashboards-and-reports",
    "alert-and-respond",
    "apply-machine-learning",
    "leverage-rest-apis",
    "connect-relational-databases",
]

# Cloud shares the same section slugs as Enterprise for most manuals.
# Cloud-exclusive sections (ACS API, edge processor, ingest processor) are
# discovered via BFS from the landing page.
_CLOUD_SECTIONS = [
    "get-started",
    "administer",
    "search",
    "spl-search-reference",
    "manage-knowledge-objects",
    "forward-and-process-data",
    "create-dashboards-and-reports",
    "alert-and-respond",
    "apply-machine-learning",
    "leverage-rest-apis",
    "connect-relational-databases",
]

# Paths that robots.txt blocks on help.splunk.com — carried on each source so
# the crawler stays source-agnostic (no hardcoded hostnames in crawler.py).
_HELP_BLOCKED = [
    "https://help.splunk.com/api/",
    "https://help.splunk.com/bundle/",
]

# Paths blocked by lantern.splunk.com robots.txt Disallow rules.
# Query-string variants (?action=, ?title=Special:…) are already neutralised by
# _normalise_url() which strips the query string before any URL is evaluated.
_LANTERN_BLOCKED = [
    "https://lantern.splunk.com/Special:",
    "https://lantern.splunk.com/Template:",
    "https://lantern.splunk.com/User:",
    "https://lantern.splunk.com/deki/",
    "https://lantern.splunk.com/@",
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
        blocked_path_prefixes=_HELP_BLOCKED,
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
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="splunk-enterprise",
        display_name="Splunk Enterprise 10.2",
        version="10.2",
        seed_urls=[
            # Landing page is the primary reliable entry point
            "https://help.splunk.com/en/splunk-enterprise/",
            # Section-level seeds — may redirect to a deeper page; the redirect
            # bug fix (2026-04-18) ensures relative links resolve correctly.
            *[
                f"https://help.splunk.com/en/splunk-enterprise/{s}/10.2"
                for s in _ENTERPRISE_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-enterprise/",
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="splunk-cloud",
        display_name="Splunk Cloud Platform 10.3.2512",
        version="10.3.2512",
        seed_urls=[
            # Landing page is the primary reliable entry point
            "https://help.splunk.com/en/splunk-cloud-platform/",
            # Section-level seeds
            *[
                f"https://help.splunk.com/en/splunk-cloud-platform/{s}/10.3.2512"
                for s in _CLOUD_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-cloud-platform/",
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="enterprise-security-8-4",
        display_name="Splunk Enterprise Security 8.4",
        version="8.4",
        seed_urls=[
            "https://help.splunk.com/en/splunk-enterprise-security-8",
            *[
                f"https://help.splunk.com/en/splunk-enterprise-security-8/{s}/8.4"
                for s in _ES_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-enterprise-security-8/",
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="enterprise-security-8-3",
        display_name="Splunk Enterprise Security 8.3",
        version="8.3",
        seed_urls=[
            "https://help.splunk.com/en/splunk-enterprise-security-8",
            *[
                f"https://help.splunk.com/en/splunk-enterprise-security-8/{s}/8.3"
                for s in _ES_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-enterprise-security-8/",
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="splunk-enterprise-10-1",
        display_name="Splunk Enterprise 10.1",
        version="10.1",
        seed_urls=[
            "https://help.splunk.com/en/splunk-enterprise/",
            *[
                f"https://help.splunk.com/en/splunk-enterprise/{s}/10.1"
                for s in _ENTERPRISE_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-enterprise/",
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="splunk-cloud-10-2",
        display_name="Splunk Cloud Platform 10.2",
        version="10.2",
        seed_urls=[
            "https://help.splunk.com/en/splunk-cloud-platform/",
            *[
                f"https://help.splunk.com/en/splunk-cloud-platform/{s}/10.2"
                for s in _CLOUD_SECTIONS
            ],
        ],
        url_prefix="https://help.splunk.com/en/splunk-cloud-platform/",
        blocked_path_prefixes=_HELP_BLOCKED,
    ),
    CrawlSource(
        source_id="lantern",
        display_name="Splunk Lantern",
        version="current",
        seed_urls=[
            # Root — discovers all top-level section links
            "https://lantern.splunk.com/",
            # Explicit section seeds as belt-and-braces
            "https://lantern.splunk.com/Security_Use_Cases",
            "https://lantern.splunk.com/Observability_Use_Cases",
            "https://lantern.splunk.com/Splunk_and_Cisco_Use_Cases",
            "https://lantern.splunk.com/Industry_Use_Cases",
            "https://lantern.splunk.com/Get_Started_with_Splunk_Software",
            "https://lantern.splunk.com/Splunk_Success_Framework",
            "https://lantern.splunk.com/Splunk_Cloud_Platform_Migration",
            "https://lantern.splunk.com/Manage_Performance_and_Health",
            "https://lantern.splunk.com/Platform_Data_Management",
            "https://lantern.splunk.com/Data_Sources",
            "https://lantern.splunk.com/Data_Types",
        ],
        url_prefix="https://lantern.splunk.com/",
        # robots.txt: Crawl-delay: 5, Request-rate: 1/5
        crawl_delay=5.0,
        max_concurrency=1,
        blocked_path_prefixes=_LANTERN_BLOCKED,
        # Sitemap covers ~800/1,284 pages; BFS discovers the remaining ~484.
        sitemap_url="https://lantern.splunk.com/sitemap.xml",
    ),
]

SOURCES_BY_ID: dict[str, CrawlSource] = {s.source_id: s for s in PHASE1_SOURCES}
