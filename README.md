# splunk-docs-mcp

An MCP server that gives AI assistants accurate, version-specific Splunk documentation. It indexes Splunk docs in a local SQLite database and exposes full-text and semantic search via six MCP tools.

---

## Why this exists

This started as a fun side project to learn how MCP servers work — and to solve a real annoyance: Claude's built-in Splunk knowledge is inconsistent and often wrong on version-specific details. The whole thing was vibe-coded.

The server gives Claude (or any MCP-compatible client) access to the actual documentation — crawled directly from `help.splunk.com` and `lantern.splunk.com` — so it can answer questions like:

- "How do I configure correlation searches in ES 8.5?"
- "What fields does `transforms.conf` support?"
- "What's the difference between `notable` and `risk` in Enterprise Security?"
- "What changed between ES 8.3 and 8.5?"

The database is rebuilt weekly by GitHub Actions and published as a release asset. You download it once with `splunk-setup` and the MCP server reads from it locally — no internet access required at query time.

---

## MCP client compatibility

This server uses the standard MCP protocol over stdio and works with any MCP-compatible client — not just Claude. Examples include:

- **Claude Desktop** (macOS/Windows)
- **Claude Code** (CLI)
- **Cursor**, **Windsurf**, or any editor with MCP support
- Any other client that supports stdio MCP servers

Configuration snippets throughout this README use the Claude Desktop format, but the `command` + `args` values are the same regardless of client.

---

## Getting the best results

Claude will not always consult the MCP server automatically for Splunk questions. To ensure it uses the documentation rather than its training data, start each chat with:

> "You have a splunk-docs MCP server connected with indexed Splunk documentation. Use it for all Splunk-related questions before answering from your training data."

You can save this as a custom instruction so it applies automatically to every session: in Claude Desktop go to **Settings → Custom Instructions**.

---

## Limitations

- **Not affiliated with or endorsed by Splunk or Cisco.**
- **Products not covered:** ITSI, Observability Cloud, SOAR, Mission Control, or any Splunk product not listed in the sources table below.
- **Data freshness:** rebuilt weekly. Answers reflect the documentation as of the last crawl date shown in the release tag (`data-YYYY-MM-DD`).
- **Lantern content** reflects `lantern.splunk.com` at the last crawl date.

---

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)

---

## Setup

**1. Clone the repo**

```bash
git clone https://github.com/jwindley/splunk-docs-mcp
cd splunk-docs-mcp
```

**2. Install dependencies**

```bash
uv sync
```

**3. Download the pre-built database**

```bash
uv run splunk-setup
```

This downloads `splunk_docs.db` from the latest GitHub Release and writes it to `data/splunk_docs.db`. It takes a minute or two depending on your connection.

**4. Configure your MCP client**

Add this to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS) or Claude Code project settings (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "splunk-docs": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/splunk-docs-mcp", "splunk-mcp"]
    }
  }
}
```

Replace `/absolute/path/to/splunk-docs-mcp` with the path where you cloned the repo. Restart your MCP client after saving.

---

## MCP tools

| Tool | What it does |
|------|-------------|
| `search_docs` | BM25 keyword search — best for exact terms, config key names, quoted phrases |
| `search_docs_semantic` | Semantic/vector search — best for natural-language or concept queries |
| `get_page` | Full Markdown content of a page by exact URL |
| `list_sections` | Lists all sources, sections, and page counts in the index |
| `browse_section` | All pages in a section (titles and URLs) |
| `get_index_info` | DB stats: total pages, embedded pages, sources, last crawl time, DB size |

### Filtering by source and version

Both `search_docs` and `search_docs_semantic` accept optional `source` and `version` parameters:

```
search_docs("correlation search", source="enterprise-security", version="8.5")
search_docs("transforms.conf", source="admin-manual")
search_docs_semantic("reduce false positives", version="8.4")
```

| Source ID | Content | Version |
|-----------|---------|---------|
| `enterprise-security` | Splunk Enterprise Security | 8.5 |
| `enterprise-security-8-4` | Splunk Enterprise Security | 8.4 |
| `enterprise-security-8-3` | Splunk Enterprise Security | 8.3 |
| `admin-manual` | Splunk Configuration File Reference | 10.2 |
| `splunk-enterprise` | Splunk Enterprise | 10.2 |
| `splunk-enterprise-10-1` | Splunk Enterprise | 10.1 |
| `splunk-cloud` | Splunk Cloud Platform | 10.3.2512 |
| `splunk-cloud-10-2` | Splunk Cloud Platform | 10.2 |
| `lantern` | Splunk Lantern | current |

Use `source=` to target a specific product, `version=` to target a specific release, or both together for precision. Omit both to search across all sources.

**Note:** when no `version=` filter is specified, cross-source duplicates (identical content appearing in multiple products) are automatically suppressed so you don't see the same page twice.

---

## Data freshness

The database is rebuilt every Sunday at 02:00 UTC by a GitHub Actions workflow and published as a release asset tagged `data-YYYY-MM-DD`. `splunk-setup` always downloads the latest release.

To refresh your local database:

```bash
uv run splunk-setup
```

After updating, **restart the MCP server** (restart Claude Desktop or reload the MCP connection). The semantic search index is loaded into memory at startup and won't reflect the new database until the server restarts.

---

## Building locally

If you want to crawl the docs yourself instead of downloading the pre-built database:

```bash
# Full crawl — all 9 sources (~14,000+ pages; takes several hours)
uv run splunk-crawl

# Single source
uv run splunk-crawl --sources enterprise-security

# Single section (fast — good for development, ~30 seconds)
uv run splunk-crawl --sources enterprise-security --section user-guide

# Rebuild chunks only (no re-crawl)
uv run splunk-crawl --rechunk

# Force re-extract + re-chunk + re-embed everything
uv run splunk-crawl --full
```

Other flags:

```
--verbose              debug output per page
--concurrency N        parallel workers (default: 3)
--delay N              per-request delay in seconds (default: 0.5)
--delay-jitter N       add random jitter up to N seconds per request
--db PATH              custom DB path
--docs-dir PATH        custom markdown output directory
```

### Merging per-source databases

If you crawl sources into separate databases (as the GitHub Actions workflow does), use `splunk-merge` to combine them:

```bash
# Merge per-source DBs into one
uv run splunk-merge data/enterprise-security.db data/admin-manual.db --output data/splunk_docs.db

# Export per-source DBs + manifest.json from a merged DB
uv run splunk-merge --export-sources data/export/ --db data/splunk_docs.db
```

The crawl writes `data/splunk_docs.db` and Markdown files to `data/docs/`. Both are gitignored.

---

## Contributing

This is a personal learning project — issues and PRs are welcome but I can't commit to a support timeline. If something is broken or a Splunk product version you care about isn't indexed, feel free to open an issue.
