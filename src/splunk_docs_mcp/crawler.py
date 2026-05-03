"""
Async BFS web crawler.

Design
------
- One `crawl_source()` coroutine per CrawlSource, called sequentially from cli.py.
- Inside each crawl: N concurrent worker tasks share a single asyncio.Queue.
- `queue.join()` waits until every queued URL has been processed (including
  newly discovered links), so workers don't exit while pages are still in-flight.
- URLs are pre-added to `visited` when enqueued to prevent duplicate work.
- Incremental mode: pages whose raw-HTML SHA-256 hasn't changed are skipped for
  extraction and storage but their links are still followed (discovery continues).
- `--section` filter: when provided, only URLs whose section segment matches are
  enqueued.  Top-level seed URLs (no section yet) are always allowed through so
  link discovery can start.
"""

import asyncio
import hashlib
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse

import httpx
from bs4 import BeautifulSoup

from .config import CrawlSource, CRAWL_HEADERS, DOCS_DIR, DB_PATH
from .db import (
    get_connection,
    get_content_hash,
    get_crawl_timestamps,
    get_failed_urls,
    get_visited_urls,
    init_db,
    mark_crawl_state,
    upsert_document,
)
from .extractor import ExtractedPage, extract_page, parse_url_metadata, write_markdown_file

logger = logging.getLogger(__name__)

# Version-number path segments — same pattern as extractor.py
_VERSION_SEG_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")

# Retry configuration for transient HTTP/network failures
_MAX_RETRIES = 3
_RETRY_DELAYS = (2.0, 4.0, 8.0)  # seconds between attempts


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@dataclass
class CrawlStats:
    source_id: str
    fetched: int = 0
    skipped: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.fetched + self.skipped + self.failed

    def summary(self) -> str:
        return (
            f"[{self.source_id}] "
            f"stored={self.fetched} skipped={self.skipped} failed={self.failed} "
            f"total={self.total}"
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def crawl_source(
    source: CrawlSource,
    db_path: Path = DB_PATH,
    docs_dir: Path = DOCS_DIR,
    concurrency: int = 3,
    delay: float = 0.5,
    delay_jitter: float = 0.0,
    full: bool = False,
    section_filter: str | None = None,
    extra_seeds: list[str] | None = None,
) -> CrawlStats:
    """
    Crawl all pages for *source*, store Markdown files and update the DB.

    Parameters
    ----------
    source:           Source definition (seeds, URL prefix, version, …).
    db_path:          Path to the SQLite database.
    docs_dir:         Root directory for Markdown files.
    concurrency:      Number of simultaneous HTTP requests.
    delay:            Seconds to wait after each fetch (rate limiting).
    full:             If True, re-extract and overwrite every page even if the
                      HTML hash hasn't changed.
    section_filter:   If set, only crawl pages in this section (e.g. 'user-guide').
                      Useful for fast pipeline testing during development.
    """
    # Per-source overrides: honour robots.txt Crawl-delay and Request-rate limits.
    # The CLI --delay arg is a floor; source.crawl_delay raises it if higher.
    effective_delay = max(delay, source.crawl_delay)
    # Cap concurrency at source.max_concurrency when set (e.g. 1 for Lantern).
    effective_concurrency = (
        min(concurrency, source.max_concurrency)
        if source.max_concurrency is not None
        else concurrency
    )

    stats = CrawlStats(source_id=source.source_id)
    conn = get_connection(db_path)
    init_db(conn)
    conn_lock = asyncio.Lock()

    # Pre-load visited URLs for incremental resume
    visited: set[str] = set() if full else get_visited_urls(conn, source.source_id)

    queue: asyncio.Queue[str] = asyncio.Queue()

    # Sitemap seeding: pre-populate queue with known URLs.
    # In --full mode, also compare <lastmod> against crawl_state timestamps
    # so unchanged pages are skipped without an HTTP fetch.
    if source.sitemap_url:
        sitemap_entries = await _fetch_sitemap_urls(source, section_filter)
        if sitemap_entries:
            crawl_ts = get_crawl_timestamps(conn, source.source_id) if full else {}
            queued = skipped = 0
            for url, lastmod in sitemap_entries:
                if url in visited:
                    continue
                if full and lastmod and url in crawl_ts:
                    if crawl_ts[url][:10] >= lastmod[:10]:
                        visited.add(url)   # prevent BFS from re-queuing
                        skipped += 1
                        continue
                visited.add(url)
                await queue.put(url)
                queued += 1
            logger.info(
                "[%s] Sitemap: %d URLs queued, %d unchanged (skipped lastmod check).",
                source.source_id, queued, skipped,
            )

    for seed in source.seed_urls:
        normalised = _normalise_url(seed)
        if normalised and normalised not in visited:
            visited.add(normalised)
            await queue.put(normalised)

    # Derived seeds: URLs from a parent source with the version segment substituted.
    # Bypasses the BFS discovery limitation where help.splunk.com nav always links
    # to the current version even when older-version content exists at predictable URLs.
    if extra_seeds:
        derived_queued = 0
        for seed in extra_seeds:
            normalised = _normalise_url(seed)
            if normalised and normalised not in visited and _is_target_url(normalised, source, section_filter):
                visited.add(normalised)
                await queue.put(normalised)
                derived_queued += 1
        logger.info(
            "[%s] Derived seeds: %d/%d queued (remainder already visited or filtered).",
            source.source_id, derived_queued, len(extra_seeds),
        )

    if queue.empty():
        logger.info(f"[{source.source_id}] Nothing to crawl — all seeds already visited.")
        conn.close()
        return stats

    logger.info(
        f"[{source.source_id}] Starting crawl "
        f"(concurrency={effective_concurrency}, delay={effective_delay}s"
        + (f", section={section_filter}" if section_filter else "")
        + ")"
    )

    # -----------------------------------------------------------------------
    # Worker coroutine
    # -----------------------------------------------------------------------

    async def worker() -> None:
        async with httpx.AsyncClient(
            headers=CRAWL_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        ) as client:
            while True:
                url = await queue.get()
                try:
                    await _process_url(
                        url=url,
                        client=client,
                        source=source,
                        conn=conn,
                        conn_lock=conn_lock,
                        visited=visited,
                        queue=queue,
                        docs_dir=docs_dir,
                        stats=stats,
                        full=full,
                        section_filter=section_filter,
                        delay=effective_delay,
                        delay_jitter=delay_jitter,
                    )
                finally:
                    queue.task_done()

    workers = [asyncio.create_task(worker()) for _ in range(effective_concurrency)]
    await queue.join()
    for w in workers:
        w.cancel()
    await asyncio.gather(*workers, return_exceptions=True)

    # Retry pass — re-attempt URLs that failed the main crawl.
    # Transient network errors (timeouts, 5xx) often resolve within minutes;
    # running one retry pass before closing catches most of them without
    # requiring a full re-crawl on the next run.
    failed = get_failed_urls(conn, source.source_id)
    if failed:
        logger.info(
            "[%s] Retry pass: re-attempting %d failed URL(s)…",
            source.source_id, len(failed),
        )
        for url in failed:
            queue.put_nowait(url)
        retry_workers = [
            asyncio.create_task(worker())
            for _ in range(min(effective_concurrency, len(failed)))
        ]
        await queue.join()
        for w in retry_workers:
            w.cancel()
        await asyncio.gather(*retry_workers, return_exceptions=True)

        # Update stats to reflect retry outcomes
        still_failed = get_failed_urls(conn, source.source_id)
        recovered = len(failed) - len(still_failed)
        if recovered:
            stats.failed -= recovered
            stats.fetched += recovered
            logger.info(
                "[%s] Retry pass recovered %d/%d URL(s).",
                source.source_id, recovered, len(failed),
            )

    conn.close()
    logger.info(f"[{source.source_id}] Crawl complete. {stats.summary()}")
    return stats


# ---------------------------------------------------------------------------
# Per-URL processing
# ---------------------------------------------------------------------------


async def _process_url(
    url: str,
    client: httpx.AsyncClient,
    source: CrawlSource,
    conn,
    conn_lock: asyncio.Lock,
    visited: set[str],
    queue: asyncio.Queue,
    docs_dir: Path,
    stats: CrawlStats,
    full: bool,
    section_filter: str | None,
    delay: float,
    delay_jitter: float = 0.0,
) -> None:
    last_exc: Exception | None = None
    resp = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            last_exc = None
            break
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            last_exc = exc
            logger.warning(f"  RETRY ({attempt + 1}/{_MAX_RETRIES}) {url}: {exc}")
            await asyncio.sleep(_RETRY_DELAYS[attempt])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and attempt < _MAX_RETRIES - 1:
                last_exc = exc
                logger.warning(f"  RETRY ({attempt + 1}/{_MAX_RETRIES}) {url}: {exc}")
                await asyncio.sleep(_RETRY_DELAYS[attempt])
            else:
                last_exc = exc
                break

    if last_exc is not None:
        # A 4xx after being redirected outside the source URL prefix means the
        # page is auth-gated (e.g. Lantern content pages that redirected to
        # /@app/auth/ → login.splunk.com and got a 403).  Count as skipped so
        # it doesn't inflate the failure rate.
        if (
            isinstance(last_exc, httpx.HTTPStatusError)
            and 400 <= last_exc.response.status_code < 500
            and not str(last_exc.response.url).startswith(source.url_prefix)
        ):
            logger.warning(f"  AUTH-SKIP {url}: redirected to auth endpoint, skipping")
            async with conn_lock:
                stats.skipped += 1
                mark_crawl_state(conn, url, source.source_id, "skipped")
            await asyncio.sleep(delay + (random.uniform(0, delay_jitter) if delay_jitter else 0))
            return
        # HTTP 404 means the page permanently doesn't exist (common for derived
        # URLs when an older version doesn't have an equivalent page).  Mark as
        # 'dead' so it is treated as visited and never retried.
        if (
            isinstance(last_exc, httpx.HTTPStatusError)
            and last_exc.response.status_code == 404
        ):
            logger.debug(f"  DEAD {url}: HTTP 404")
            async with conn_lock:
                stats.skipped += 1
                mark_crawl_state(conn, url, source.source_id, "dead")
            await asyncio.sleep(delay + (random.uniform(0, delay_jitter) if delay_jitter else 0))
            return
        logger.warning(f"  FAIL {url}: {last_exc}")
        async with conn_lock:
            stats.failed += 1
            mark_crawl_state(conn, url, source.source_id, "failed", str(last_exc))
        await asyncio.sleep(delay + (random.uniform(0, delay_jitter) if delay_jitter else 0))
        return

    html = resp.text

    # Capture the final URL after any redirects.  help.splunk.com section seed
    # URLs (e.g. .../administer/8.5) redirect to a deeper page, and the HTML
    # served there contains relative hrefs intended to be resolved against the
    # redirect destination's directory — not the original seed URL.  Using the
    # pre-redirect URL as the urljoin() base produces doubled/mangled paths that
    # return HTTP 404 and are never stored.
    final_url = _normalise_url(str(resp.url)) or url

    # Register the redirect destination as visited so no worker re-fetches it
    # when it appears as a link in other pages' HTML.
    if final_url != url:
        async with conn_lock:
            visited.add(final_url)
            mark_crawl_state(conn, final_url, source.source_id, "fetched")
        logger.debug(f"  REDIR {url} → {final_url}")

    new_hash = hashlib.sha256(html.encode()).hexdigest()

    async with conn_lock:
        existing_hash = get_content_hash(conn, url)

    if not full and existing_hash == new_hash:
        async with conn_lock:
            stats.skipped += 1
            mark_crawl_state(conn, url, source.source_id, "skipped")
        logger.debug(f"  SKIP {url} (unchanged)")
    else:
        page = extract_page(html, url, source)
        if page:
            file_path = write_markdown_file(page, docs_dir)
            doc = page.to_doc_dict(file_path)
            async with conn_lock:
                upsert_document(conn, doc)
                stats.fetched += 1
            logger.info(f"  + [{source.source_id}] {page.title[:70]}")
        async with conn_lock:
            mark_crawl_state(conn, url, source.source_id, "fetched")

    # Discover and enqueue links (even from skipped pages — content unchanged
    # but new pages may have been linked from this one since last crawl).
    # Use final_url (post-redirect) as the base for urljoin so relative hrefs
    # in the HTML resolve to the correct absolute paths.
    new_links = _extract_links(html, final_url, source, section_filter)
    async with conn_lock:
        for link in new_links:
            if link not in visited:
                visited.add(link)
                await queue.put(link)

    await asyncio.sleep(delay + (random.uniform(0, delay_jitter) if delay_jitter else 0))


# ---------------------------------------------------------------------------
# Sitemap discovery
# ---------------------------------------------------------------------------


async def _fetch_sitemap_urls(
    source: CrawlSource,
    section_filter: str | None,
) -> list[tuple[str, str | None]]:
    """
    Fetch and parse source.sitemap_url, returning (url, lastmod) pairs.

    Filters URLs through _normalise_url() and _is_target_url() so only
    in-scope pages are returned.  Returns an empty list on any fetch or
    parse failure so the caller can fall back to BFS-only discovery.

    lastmod is the raw <lastmod> string from the sitemap (typically YYYY-MM-DD
    or a full ISO-8601 datetime).  The caller compares only the date portion.
    """
    from xml.etree import ElementTree as ET

    if not source.sitemap_url:
        return []
    try:
        async with httpx.AsyncClient(
            headers=CRAWL_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        ) as client:
            resp = await client.get(source.sitemap_url)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("[%s] Sitemap fetch failed (%s): %s", source.source_id, source.sitemap_url, exc)
        return []

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("[%s] Sitemap parse error: %s", source.source_id, exc)
        return []

    # Handle the standard sitemap namespace transparently
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    results: list[tuple[str, str | None]] = []
    for url_el in root.findall(f"{ns}url"):
        loc_el = url_el.find(f"{ns}loc")
        if loc_el is None or not loc_el.text:
            continue
        url = _normalise_url(loc_el.text.strip())
        if not url or not _is_target_url(url, source, section_filter):
            continue
        lastmod_el = url_el.find(f"{ns}lastmod")
        lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None
        results.append((url, lastmod))

    return results


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------


def _extract_links(
    html: str,
    base_url: str,
    source: CrawlSource,
    section_filter: str | None,
) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "javascript:", "#")):
            continue
        absolute = urljoin(base_url, href)
        normalised = _normalise_url(absolute)
        if normalised and _is_target_url(normalised, source, section_filter):
            links.append(normalised)
    return links


def _is_target_url(
    url: str,
    source: CrawlSource,
    section_filter: str | None,
) -> bool:
    if not url.startswith(source.url_prefix):
        return False
    if source.blocked_path_prefixes and any(
        url.startswith(p) for p in source.blocked_path_prefixes
    ):
        return False

    # Reject pages from the wrong product version.
    # ES docs embed the version in the path: .../administer/8.0/... vs .../8.5/...
    # Without this check the crawler follows cross-version nav links and indexes
    # 8.0, 8.1, 8.2, 8.3, 8.4 pages alongside 8.5.
    # Strategy: collect all version-like segments (e.g. "8.0", "8.5") from the
    # path after the source prefix. If any are present and none match
    # source.version, this URL belongs to a different version — skip it.
    prefix_path = urlparse(source.url_prefix).path.rstrip("/")
    remainder = urlparse(url).path[len(prefix_path):].strip("/")
    version_segs = [p for p in remainder.split("/") if _VERSION_SEG_RE.match(p)]
    if version_segs and source.version not in version_segs:
        return False

    if section_filter:
        section = _section_from_url(url, source)
        # Allow URLs with no section yet (top-level / landing pages)
        if section is not None and section != section_filter:
            return False

    return True


def _section_from_url(url: str, source: CrawlSource) -> str | None:
    """Return the section segment for a URL, or None if at/above the section level."""
    prefix_path = urlparse(source.url_prefix).path.rstrip("/")
    remainder = urlparse(url).path[len(prefix_path):].strip("/")
    parts = [p for p in remainder.split("/") if p and not _VERSION_SEG_RE.match(p)]
    return parts[0] if parts else None


def _normalise_url(url: str) -> str | None:
    """Strip fragments and query strings; return None for non-HTTP URLs."""
    try:
        no_frag, _ = urldefrag(url)
        parsed = urlparse(no_frag)
        if parsed.scheme not in ("http", "https"):
            return None
        # Drop query string — doc pages don't need them
        return parsed._replace(query="").geturl()
    except Exception:
        return None
