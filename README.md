# splunk-docs-mcp

An MCP server that gives Claude accurate, version-specific Splunk documentation without hallucination. It indexes Splunk docs in a local SQLite database and exposes full-text and semantic search via six MCP tools.

---

## What it is

Claude's built-in Splunk knowledge is inconsistent and often wrong on version-specific details. This server gives Claude access to the actual documentation — crawled directly from `help.splunk.com` and `lantern.splunk.com` — so it can answer questions like:

- "How do I configure correlation searches in ES 8.5?"
- "What fields does `transforms.conf` support?"
- "What's the difference between `notable` and `risk` in Enterprise Security?"

The database is rebuilt weekly by GitHub Actions and published as a release asset. You download it once with `splunk-setup` and the MCP server reads from it locally — no internet access required at query time.

---

## Limitations

- **Versions covered:** Splunk Enterprise Security 8.5, Splunk Enterprise 10.2, Splunk Cloud Platform 10.3.2512, Splunk Configuration File Reference 10.2, Splunk Lantern (current at crawl time). No other versions.
- **Products not covered:** ITSI, Observability Cloud, SOAR, Mission Control, or any other Splunk product not listed above.
- **Lantern content** reflects the state of `lantern.splunk.com` at the last crawl date, which may lag behind live updates.
- **Data freshness:** the database is rebuilt weekly. Answers reflect the documentation as of the last crawl date shown in the release tag (`data-YYYY-MM-DD`).
- **Not affiliated with or endorsed by Splunk or Cisco.**

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

This downloads `splunk_docs.db` (~200–400 MB) from the latest GitHub Release and writes it to `data/splunk_docs.db`. It takes a minute or two depending on your connection.

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

Replace `/absolute/path/to/splunk-docs-mcp` with the path where you cloned the repo. Restart Claude Desktop after saving.

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

Both search tools accept an optional `source` parameter to restrict results to a specific product:

| Source ID | Content |
|-----------|---------|
| `enterprise-security` | Splunk Enterprise Security 8.5 |
| `admin-manual` | Splunk Configuration File Reference 10.2 |
| `splunk-enterprise` | Splunk Enterprise 10.2 |
| `splunk-cloud` | Splunk Cloud Platform 10.3.2512 |
| `lantern` | Splunk Lantern |

---

## Data freshness

The database is rebuilt every Sunday at 02:00 UTC by a GitHub Actions workflow and published as a release asset tagged `data-YYYY-MM-DD`. `splunk-setup` always downloads the latest release.

To refresh your local database, re-run:

```bash
uv run splunk-setup
```

---

## Building locally (contributors)

If you want to crawl the docs yourself instead of downloading the pre-built database:

```bash
# Full crawl — all 5 sources (~9,000 pages; takes several hours)
uv run splunk-crawl

# Single source
uv run splunk-crawl --sources enterprise-security

# Single section (fast — useful during development, ~30 seconds)
uv run splunk-crawl --sources enterprise-security --section user-guide

# Force re-extract + re-chunk + re-embed everything
uv run splunk-crawl --full
```

Other flags:

```bash
--verbose          # debug output per page
--concurrency N    # parallel workers (default: 5; use 1 for Lantern)
--delay N          # per-request delay in seconds
--db PATH          # custom DB path
--docs-dir PATH    # custom markdown output directory
```

The crawl writes `data/splunk_docs.db` and Markdown files to `data/docs/`. Both are gitignored.
