# External Integrations

**Analysis Date:** 2026-05-03

## APIs & External Services

### Splunk Documentation Sites (crawled)

**help.splunk.com:**
- Role: Primary source of all product documentation; crawled by `src/splunk_docs_mcp/crawler.py`
- Access: Unauthenticated HTTP GET; custom `User-Agent` header set in `config.py`
  ```
  splunk-docs-mcp-crawler/0.1 (local knowledge base indexer; not for commercial use)
  ```
- Rate limiting: `crawl_delay=0.5` per source (default); configurable per `CrawlSource`
- Blocked paths per `robots.txt`: `https://help.splunk.com/api/`, `https://help.splunk.com/bundle/`
- Sources crawled from this host:
  - `enterprise-security` — `help.splunk.com/en/splunk-enterprise-security-8/` (ES 8.5)
  - `enterprise-security-8-4` — same prefix, version 8.4 (derived from ES 8.5 crawl)
  - `enterprise-security-8-3` — same prefix, version 8.3 (derived from ES 8.5 crawl)
  - `admin-manual` — `help.splunk.com/en/data-management/splunk-enterprise-admin-manual/10.2/configuration-file-reference/`
  - `splunk-enterprise` — `help.splunk.com/en/splunk-enterprise/`
  - `splunk-cloud` — `help.splunk.com/en/splunk-cloud-platform/`
  - `soar-on-premises` — `help.splunk.com/en/splunk-soar/soar-on-premises/` (SOAR 8.5.0)
  - `soar-on-premises-8-4-0` — same prefix, version 8.4.0 (derived from SOAR 8.5.0 crawl)
  - `soar-cloud` — `help.splunk.com/en/splunk-soar/soar-cloud/`
- What goes in: HTTP GET requests with `Accept: text/html`
- What comes out: HTML pages; extracted to Markdown via trafilatura/markdownify, stored in SQLite

**lantern.splunk.com:**
- Role: Splunk Lantern — use-case guidance and best-practice articles; crawled by `src/splunk_docs_mcp/crawler.py`
- Access: Unauthenticated HTTP GET; same `User-Agent` header
- Rate limiting: `crawl_delay=5.0, max_concurrency=1` (strict; per `robots.txt` `Crawl-delay: 5, Request-rate: 1/5`)
- Blocked paths per `robots.txt`: `/Special:*`, `/Template:*`, `/User:*`, `/deki/`, `/@*`, `/hc`
- Sitemap: `https://lantern.splunk.com/sitemap.xml` — pre-seeds BFS; covers ~800 of ~1,284 pages
- Source ID: `lantern`; version: `current`
- URL structure: PascalCase_with_underscores path segments; up to 4 levels deep
- What comes out: Markdown stored in SQLite; `section` = level-1 path segment, `subsection` = level-2

### GitHub Releases API (download)

- Role: Distribution of pre-built SQLite databases; consumed by `src/splunk_docs_mcp/setup.py`
- Endpoint: `https://api.github.com/repos/jwindley/splunk-docs-mcp/releases/latest`
- Auth: None (public repo; unauthenticated requests)
- Request header: `Accept: application/vnd.github+json`
- What goes in: GET request to releases API
- What comes out:
  - `tag_name` and `assets` list from the release JSON
  - `manifest.json` — lists per-source DB files with page counts, sizes, and parent relationships
  - `splunk_docs_<source>.db` — individual per-source SQLite databases (one per crawl source)
  - `splunk_docs.db` — monolithic merged DB (fallback when no manifest present)
- Download flow: `setup.py` fetches release metadata → fetches `manifest.json` → shows interactive menu → streams selected per-source DBs → merges with `merge.py` → renames to `data/splunk_docs.db`
- Timeout: 15s for API calls; 600s for file downloads (streaming)
- Fallback: if `manifest.json` absent (old release), downloads monolithic `splunk_docs.db` directly

### GitHub Actions (CI/CD — publish side)

- Role: Automated weekly crawl + release publication; defined in `.github/workflows/` (not inspected)
- Schedule: Sunday 02:00 UTC + `workflow_dispatch`
- Jobs: 10-source crawl matrix (`crawl` + `crawl-derived` jobs); aggregation job merges DBs, exports per-source files, publishes release
- Release assets published: `splunk_docs.db`, `splunk_docs_<source>.db` per source, `manifest.json`

## MCP Server (served)

- Protocol: Model Context Protocol over stdio transport
- Implementation: `mcp.server.fastmcp.FastMCP` in `src/splunk_docs_mcp/server.py`
- Server name: `splunk-docs`
- Launch: `uv run splunk-mcp` → calls `mcp.run()` which handles stdio framing

### MCP Tools Exposed

| Tool | Parameters | Purpose |
|------|-----------|---------|
| `search_docs` | `query`, `source?`, `version?`, `limit?` (1–20) | BM25 FTS5 keyword search; returns ranked results with snippet |
| `search_docs_semantic` | `query`, `source?`, `version?`, `limit?` (1–20) | Cosine-similarity vector search using `all-MiniLM-L6-v2` embeddings |
| `get_page` | `url` | Full Markdown content for a page by exact URL; reassembles chunked pages transparently |
| `list_sections` | `source?` | Lists all sections grouped by source with page counts |
| `browse_section` | `section`, `source`, `subsection?` | Lists all pages in a section (title + URL + char count) |
| `get_index_info` | _(none)_ | DB stats: total pages, embedded pages, sources, last crawl time, DB size |

**Version filter behaviour:** `version=` parameter on `search_docs` and `search_docs_semantic` bypasses the `is_duplicate=0` dedup filter and also matches `version_tags` JSON arrays via `json_each`, so shared/collapsed rows from older versions are returned.

**Source filter validation:** All tools with `source=` parameter validate against `SOURCES_BY_ID` from `config.py` and return an error dict with valid options if the source is unknown.

## Data Storage

**Databases:**
- SQLite — `data/splunk_docs.db`
- Connection opened once at module level in `server.py`; WAL mode allows concurrent reads

**File Storage:**
- `data/docs/{source_id}/{version}/{section}/{subsection}/{slug}.md` — Markdown files written at crawl time (gitignored)
- YAML frontmatter on each file: `title`, `url`, `source`, `version`, `section`, `subsection`, `crawled`

**Caching:**
- None — all data in SQLite; no Redis, Memcached, or CDN

## Authentication & Identity

All integrations are unauthenticated:
- Crawled sites: public documentation; no login required
- GitHub Releases API: public repository; no token needed
- MCP server: no auth layer; relies on Claude Desktop/Claude Code local process trust

## Environment Configuration

No `.env` file or environment variables are required for normal operation. All configuration is code-defined:

| Config | Location | Value |
|--------|----------|-------|
| DB path | `src/splunk_docs_mcp/config.py` `DB_PATH` | `<project_root>/data/splunk_docs.db` |
| Docs dir | `src/splunk_docs_mcp/config.py` `DOCS_DIR` | `<project_root>/data/docs/` |
| Crawler User-Agent | `src/splunk_docs_mcp/config.py` `CRAWL_HEADERS` | `splunk-docs-mcp-crawler/0.1 ...` |
| GitHub releases URL | `src/splunk_docs_mcp/setup.py` `_RELEASES_API` | `https://api.github.com/repos/jwindley/splunk-docs-mcp/releases/latest` |
| Crawl sources | `src/splunk_docs_mcp/config.py` `PHASE1_SOURCES` | List of 10 `CrawlSource` dataclasses |
| Embedding model | `src/splunk_docs_mcp/server.py` | `all-MiniLM-L6-v2` (downloaded from HuggingFace Hub on first use) |

## Webhooks & Callbacks

**Incoming:** None

**Outgoing:** None (crawler is pull-only; setup is pull-only)

---

*Integration audit: 2026-05-03*
