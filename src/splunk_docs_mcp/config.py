"""
Runtime constants and crawl-source definitions.

To add a new crawl source: add a CrawlSource entry to PHASE1_SOURCES.
To rotate product versions: edit versions.json — no Python changes needed.
"""

import json
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

    Adding a new source requires only a new CrawlSource entry in PHASE1_SOURCES.
    Rotating a product version requires only updating versions.json.
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

    derive_from: str | None = None
    """Source ID to derive seed URLs from by substituting version segments.

    Set this when the site's navigation always links to the current version
    (e.g. help.splunk.com always links to 8.5 even when viewing 8.4 pages),
    so BFS alone can't discover older-version pages.  The CLI loads all
    successfully fetched URLs from the named source, replaces the parent
    version string with this source's version, and adds the results as
    extra seeds before BFS starts."""

    version_discovery_url: str | None = None
    """URL to fetch for automatic version discovery (splunk-discover-versions).

    Set only on primary (current-version) sources, not on derived n-1/n-2 sources.
    The page at this URL must contain <select id="version-select"> — the first
    option is the current version, second is n-1, third is n-2.  splunk-discover-versions
    reads this selector and updates versions.json for the current, n-1, and n-2 keys."""


# ---------------------------------------------------------------------------
# Version data — loaded from versions.json at import time.
# To rotate versions, edit versions.json only.
# ---------------------------------------------------------------------------

def _load_versions() -> dict[str, str | None]:
    return json.loads((_PROJECT_ROOT / "versions.json").read_text())


_V = _load_versions()

# ---------------------------------------------------------------------------
# Shared URL constants
# ---------------------------------------------------------------------------

_ES_BASE = "https://help.splunk.com/en/splunk-enterprise-security-8"

_ES_SECTIONS = [
    "install",
    "administer",
    "user-guide",
    "troubleshoot",
    "release-notes-and-resources",
]

_ADMIN_BASE = (
    "https://help.splunk.com/en/data-management/splunk-enterprise-admin-manual"
)

# Paths that robots.txt blocks on help.splunk.com.
_HELP_BLOCKED = [
    "https://help.splunk.com/api/",
    "https://help.splunk.com/bundle/",
]

# Paths blocked by lantern.splunk.com robots.txt Disallow rules.
_LANTERN_BLOCKED = [
    "https://lantern.splunk.com/Special:",
    "https://lantern.splunk.com/Template:",
    "https://lantern.splunk.com/User:",
    "https://lantern.splunk.com/deki/",
    "https://lantern.splunk.com/@",
    "https://lantern.splunk.com/hc",  # auth-gated Help Center section
]

_SOAR_ONPREM_SEEDS = [
    "https://help.splunk.com/en/splunk-soar/soar-on-premises",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/install-and-upgrade-soar-on-premises",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/administer-soar-on-premises",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/use-splunk-soar-on-premises",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/build-playbooks",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/develop-apps",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/python-playbook-api-reference",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/python-playbook-tutorial",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/release-notes",
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/rest-api-reference",
]

# ---------------------------------------------------------------------------
# Source factories — called once at module load; version read from _V.
# ---------------------------------------------------------------------------


def _es_source(source_id: str, *, derive_from: str | None = None) -> CrawlSource | None:
    """Factory for Enterprise Security sources (current, n1, n2).

    Returns None if versions.json has a null value for source_id (version rotated out).
    Seed URLs include section-level entry points with the version segment so
    older versions are reachable even when the live nav only links to current.
    api-reference is seeded explicitly because it is not linked from the nav
    for non-current versions.
    """
    v = _V.get(source_id)
    if not v:
        return None
    return CrawlSource(
        source_id=source_id,
        display_name=f"Splunk Enterprise Security {v}",
        version=v,
        seed_urls=[
            _ES_BASE,
            *[f"{_ES_BASE}/{s}/{v}" for s in _ES_SECTIONS],
            f"{_ES_BASE}/api-reference/{v}",
        ],
        url_prefix=f"{_ES_BASE}/",
        blocked_path_prefixes=_HELP_BLOCKED,
        derive_from=derive_from,
        version_discovery_url=f"{_ES_BASE}/administer/{v}" if derive_from is None else None,
    )


def _admin_source(source_id: str, *, derive_from: str | None = None) -> CrawlSource | None:
    """Factory for Configuration File Reference sources (current, n1).

    Returns None if versions.json has a null value for source_id (version rotated out).
    The index page slug follows the pattern {version}.0-configuration-file-reference
    (e.g. 10.2 → 10.2.0-configuration-file-reference). derive_from provides the
    bulk of page seeds; the explicit seed covers the index page whose slug
    contains the version and won't be derived correctly from the parent.
    The hub URL _ADMIN_BASE redirects to the current version page, which has the
    version selector — so it works as a version-agnostic discovery URL.
    """
    v = _V.get(source_id)
    if not v:
        return None
    slug = f"{v}.0-configuration-file-reference"
    return CrawlSource(
        source_id=source_id,
        display_name=f"Splunk Configuration File Reference {v}",
        version=v,
        seed_urls=[f"{_ADMIN_BASE}/{v}/configuration-file-reference/{slug}"],
        url_prefix=f"{_ADMIN_BASE}/{v}/configuration-file-reference/",
        blocked_path_prefixes=_HELP_BLOCKED,
        derive_from=derive_from,
        version_discovery_url=_ADMIN_BASE if derive_from is None else None,
    )


# ---------------------------------------------------------------------------
# Phase 1 sources
# ---------------------------------------------------------------------------

_SOAR_ONPREM_DISCOVERY = (
    "https://help.splunk.com/en/splunk-soar/soar-on-premises/release-notes"
)

# Sources where version is null in versions.json are excluded from PHASE1_SOURCES.
# Factories (_es_source, _admin_source) return None for null versions; filter removes them.
PHASE1_SOURCES: list[CrawlSource] = [
    s
    for s in [
        _es_source("enterprise-security"),
        _admin_source("admin-manual"),
        CrawlSource(
            source_id="splunk-enterprise",
            display_name=f"Splunk Enterprise {_V['splunk-enterprise']}",
            version=_V["splunk-enterprise"],  # type: ignore[arg-type]
            seed_urls=[
                # Landing page only — BFS discovers all section pages from here.
                # Section-level seeds return HTTP 404 on help.splunk.com and
                # accumulate dead entries in crawl_state if added explicitly.
                "https://help.splunk.com/en/splunk-enterprise/",
            ],
            url_prefix="https://help.splunk.com/en/splunk-enterprise/",
            blocked_path_prefixes=_HELP_BLOCKED,
        ),
        CrawlSource(
            source_id="splunk-cloud",
            display_name=f"Splunk Cloud Platform {_V['splunk-cloud']}",
            version=_V["splunk-cloud"],  # type: ignore[arg-type]
            seed_urls=[
                "https://help.splunk.com/en/splunk-cloud-platform/",
            ],
            url_prefix="https://help.splunk.com/en/splunk-cloud-platform/",
            blocked_path_prefixes=_HELP_BLOCKED,
        ),
        _es_source("enterprise-security-n1", derive_from="enterprise-security"),
        _admin_source("admin-manual-n1", derive_from="admin-manual"),
        _es_source("enterprise-security-n2", derive_from="enterprise-security"),
        CrawlSource(
            source_id="soar-on-premises",
            display_name=f"Splunk SOAR On-Premises {_V['soar-on-premises']}",
            version=_V["soar-on-premises"],  # type: ignore[arg-type]
            seed_urls=_SOAR_ONPREM_SEEDS,
            url_prefix="https://help.splunk.com/en/splunk-soar/soar-on-premises/",
            blocked_path_prefixes=_HELP_BLOCKED,
            version_discovery_url=_SOAR_ONPREM_DISCOVERY,
        ),
        CrawlSource(
            source_id="soar-on-premises-n1",
            display_name=f"Splunk SOAR On-Premises {_V['soar-on-premises-n1']}",
            version=_V["soar-on-premises-n1"],  # type: ignore[arg-type]
            seed_urls=_SOAR_ONPREM_SEEDS,
            url_prefix="https://help.splunk.com/en/splunk-soar/soar-on-premises/",
            blocked_path_prefixes=_HELP_BLOCKED,
            derive_from="soar-on-premises",
        ),
        CrawlSource(
            source_id="soar-cloud",
            display_name="Splunk SOAR Cloud",
            version=_V["soar-cloud"],  # type: ignore[arg-type]
            seed_urls=[
                "https://help.splunk.com/en/splunk-soar/soar-cloud",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/administer-soar-cloud",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/use-soar-cloud",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/build-playbooks",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/develop-apps",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/python-playbook-api-reference",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/python-playbook-tutorial",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/release-notes",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/rest-api-reference",
                "https://help.splunk.com/en/splunk-soar/soar-cloud/migrate-from-splunk-soar-on-premises-to-splunk-soar-cloud",
            ],
            url_prefix="https://help.splunk.com/en/splunk-soar/soar-cloud/",
            blocked_path_prefixes=_HELP_BLOCKED,
        ),
        CrawlSource(
            source_id="lantern",
            display_name="Splunk Lantern",
            version=_V["lantern"],  # type: ignore[arg-type]
            seed_urls=[
                "https://lantern.splunk.com/",
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
    if s is not None
]

SOURCES_BY_ID: dict[str, CrawlSource] = {s.source_id: s for s in PHASE1_SOURCES}


def get_source_version_pairs() -> list[tuple[str, str]]:
    """Return (derived_source_id, parent_source_id) pairs for the version merge pass."""
    return [
        (s.source_id, s.derive_from)
        for s in PHASE1_SOURCES
        if s.derive_from
    ]
