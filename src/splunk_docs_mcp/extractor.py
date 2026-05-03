"""
HTML → Markdown extraction and URL metadata parsing.

Strategy
--------
1. trafilatura as primary extractor — text-density heuristics that don't rely on
   stable CSS class names (help.splunk.com has none).  `favor_recall=True` avoids
   discarding legitimate content in tables or short procedural steps.
2. BeautifulSoup + markdownify as fallback when trafilatura returns <100 chars —
   covers edge cases like pure-table pages or index pages.
3. URL metadata (section, subsection, slug) is derived purely from the URL path,
   with version-number segments (e.g. '8.5', '10.2') filtered out so they don't
   pollute directory names or section values.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify

from .config import CrawlSource, DOCS_DIR

# Version segments look like "8.5", "10.2", "10.2.0" — pure numeric with dots
_VERSION_SEG_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ExtractedPage:
    url: str
    title: str
    source: str
    version: str
    section: str | None
    subsection: str | None
    slug: str | None
    content_md: str
    content_hash: str   # SHA-256 of the *raw HTML* (used for incremental re-crawl)
    crawled_at: str     # ISO-8601 UTC

    def to_doc_dict(self, file_path: Path) -> dict:
        """Return a dict ready for db.upsert_document()."""
        return {
            "url": self.url,
            "title": self.title,
            "source": self.source,
            "version": self.version,
            "section": self.section,
            "subsection": self.subsection,
            "slug": self.slug,
            "file_path": str(file_path),
            "content_md": self.content_md,
            "content_hash": self.content_hash,
            "crawled_at": self.crawled_at,
        }


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------


def extract_page(html: str, url: str, source: CrawlSource) -> ExtractedPage | None:
    """
    Extract clean Markdown content and metadata from a raw HTML page.
    Returns None if the page contains less than 50 chars of usable content
    (typically navigation-only or error pages).
    """
    content = _extract_markdown(html)
    if not content:
        return None

    title = _extract_title(html, url)
    meta = parse_url_metadata(url, source)

    return ExtractedPage(
        url=url,
        title=title,
        source=source.source_id,
        version=source.version,
        section=meta["section"],
        subsection=meta["subsection"],
        slug=meta["slug"],
        content_md=content,
        content_hash=hashlib.sha256(html.encode()).hexdigest(),
        crawled_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def _extract_markdown(html: str) -> str | None:
    # Primary: trafilatura
    content = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_links=False,   # nav links add noise; not useful in search snippets
        include_images=False,
        favor_recall=True,     # prefer more content over precision (good for docs)
        no_fallback=False,
    )

    if content and len(content.strip()) >= 100:
        return content.strip()

    # Fallback: BS4 + markdownify
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.select("nav, header, footer, script, style, [class*='breadcrumb']"):
        tag.decompose()

    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", attrs={"role": "main"})
        or soup.body
    )
    if not main:
        return None

    content = markdownify(
        str(main),
        heading_style="ATX",
        strip=["a", "img"],
    ).strip()

    return content if len(content) >= 50 else None


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------


def _extract_title(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)

    title_tag = soup.find("title")
    if title_tag:
        # "Page Title | Splunk Documentation" — take the first part
        raw = title_tag.get_text(strip=True)
        return raw.split("|")[0].split(" - ")[0].strip()

    # Last resort: derive from URL slug (empty for root URLs like lantern.splunk.com/)
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title() or "Untitled"


# ---------------------------------------------------------------------------
# URL metadata parsing
# ---------------------------------------------------------------------------


def parse_url_metadata(url: str, source: CrawlSource) -> dict:
    """
    Derive section, subsection, and slug from the URL path.

    Version-number segments (e.g. '8.5' inside the ES path) are stripped so
    they don't end up as section or subsection values.

    Examples
    --------
    ES URL:  .../splunk-enterprise-security-8/user-guide/8.5/introduction/about-ses
             → section='user-guide', subsection='introduction', slug='about-ses'

    Config:  .../configuration-file-reference/transforms.conf
             → section=None, subsection=None, slug='transforms.conf'
    """
    parsed = urlparse(url)
    prefix_path = urlparse(source.url_prefix).path.rstrip("/")
    remainder = parsed.path[len(prefix_path):].strip("/")

    all_parts = [p for p in remainder.split("/") if p]
    # Filter out pure version-number segments
    parts = [p for p in all_parts if not _VERSION_SEG_RE.match(p)]

    return {
        "section": parts[0] if len(parts) > 0 else None,
        "subsection": parts[1] if len(parts) > 1 else None,
        "slug": parts[-1] if parts else None,
    }


# ---------------------------------------------------------------------------
# Markdown file writer
# ---------------------------------------------------------------------------


def write_markdown_file(page: ExtractedPage, docs_dir: Path = DOCS_DIR) -> Path:
    """
    Write the extracted page to a .md file with YAML frontmatter.

    File layout: docs_dir/{source}/{version}/{section}/{subsection}/{slug}.md
    Returns the absolute path of the written file.
    """
    path_parts = [page.source, page.version]
    if page.section:
        path_parts.append(_safe_name(page.section))
    if page.subsection:
        path_parts.append(_safe_name(page.subsection))

    dir_path = docs_dir.joinpath(*path_parts)
    dir_path.mkdir(parents=True, exist_ok=True)

    filename = _safe_name(page.slug or "index") + ".md"
    file_path = dir_path / filename

    frontmatter = (
        f"---\n"
        f"title: {_yaml_str(page.title)}\n"
        f"url: {_yaml_str(page.url)}\n"
        f"source: {_yaml_str(page.source)}\n"
        f"version: {_yaml_str(page.version)}\n"
        f"section: {_yaml_str(page.section or '')}\n"
        f"subsection: {_yaml_str(page.subsection or '')}\n"
        f"crawled: {_yaml_str(page.crawled_at)}\n"
        f"---\n\n"
    )

    file_path.write_text(frontmatter + page.content_md, encoding="utf-8")
    return file_path


def _safe_name(s: str) -> str:
    """Convert a URL segment to a safe filesystem name."""
    return re.sub(r"[^\w\-.]", "-", s).strip("-") or "page"


def _yaml_str(s: str) -> str:
    """Wrap a string in double quotes, escaping any embedded quotes."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
