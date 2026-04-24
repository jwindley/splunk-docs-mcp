# splunk-docs-mcp

An MCP server that gives AI assistants accurate, version-specific Splunk documentation — because Claude hallucinates on Splunk questions.

---

## Why this exists

Claude's training data for Splunk is outdated, incomplete, and wrong on version-specific details. Ask it something like "how do I configure correlation searches in ES 8.5?" and it will give you a confident answer that applies to a completely different version, or just makes things up.

This server fixes that by giving Claude (or any MCP-compatible client) access to the **actual documentation**, crawled directly from `help.splunk.com` and `lantern.splunk.com`. With it connected, Claude looks up the real docs before answering instead of guessing from training data.

It can answer questions like:

- "How do I configure correlation searches in ES 8.5?"
- "What fields does `transforms.conf` support?"
- "What's the difference between `notable` and `risk` in Enterprise Security?"
- "What changed between ES 8.3 and 8.5?"

The database is rebuilt weekly by GitHub Actions and published as a release asset. You download it once with `splunk-setup` and the MCP server reads it locally — no internet access needed at query time.

The whole thing was vibe-coded as a side project to learn how MCP servers work.

---

## MCP client compatibility

Works with any MCP-compatible client — not just Claude. Examples:

- **Claude Desktop** (macOS/Windows)
- **Claude Code** (CLI)
- **Cursor**, **Windsurf**, or any editor with MCP support

Configuration snippets throughout this README use the Claude Desktop format, but the `command` + `args` values are the same for any client.

---

## Getting the best results

Claude will not always consult the MCP server automatically. To ensure it uses the documentation rather than its training data, start each chat with:

> "You have a splunk-docs MCP server connected with indexed Splunk documentation. Use it for all Splunk-related questions before answering from your training data."

Save this as a custom instruction so it applies to every session: in Claude Desktop go to **Settings → Custom Instructions**.

---

## Limitations

- **Not affiliated with or endorsed by Splunk or Cisco.**
- **Products not yet covered:** ITSI, Observability Cloud, SOAR, and Mission Control are not indexed yet — these are planned additions.
- **Data freshness:** rebuilt weekly. Answers reflect docs as of the last crawl shown in the release tag (`data-YYYY-MM-DD`).

---

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — fast Python package manager

**Install uv:**

```bash
# macOS (Homebrew)
brew install uv

# macOS / Linux (installer script)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

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

This shows a menu of available sources so you can download only what you need, or choose **all** for everything. The database is saved to `data/splunk_docs.db`. Downloading all sources takes a few minutes depending on your connection.

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

## What's indexed

The goal is to keep the **current released version plus the previous version (n−1)** for each major product. ITSI, SOAR, Observability, and Mission Control are planned additions.

| Source ID | Product | Version |
|-----------|---------|---------|
| `enterprise-security` | Splunk Enterprise Security | 8.5 (current) |
| `enterprise-security-8-4` | Splunk Enterprise Security | 8.4 (n−1) |
| `enterprise-security-8-3` | Splunk Enterprise Security | 8.3 (n−2) |
| `splunk-enterprise` | Splunk Enterprise | 10.2 (current) |
| `splunk-cloud` | Splunk Cloud Platform | 10.3.2512 (current) |
| `admin-manual` | Splunk Configuration File Reference | 10.2 |
| `lantern` | Splunk Lantern | current |

---

## MCP tools

| Tool | What it does |
|------|-------------|
| `search_docs` | BM25 keyword search — best for exact terms, config key names, quoted phrases |
| `search_docs_semantic` | Semantic/vector search — best for natural-language or concept queries |
| `get_page` | Full Markdown content of a page by exact URL |
| `list_sections` | Lists all sources, sections, and page counts in the index |
| `browse_section` | All pages in a section (titles and URLs) |
| `get_index_info` | DB stats: total pages, sources indexed, last crawl time |

### Filtering by source and version

Both `search_docs` and `search_docs_semantic` accept optional `source` and `version` parameters:

```
search_docs("correlation search", source="enterprise-security", version="8.5")
search_docs("transforms.conf", source="admin-manual")
search_docs_semantic("reduce false positives", version="8.4")
```

Use `source=` to target a specific product, `version=` to target a specific release, or both together for precision. Omit both to search across all sources.

**Note:** when no `version=` filter is specified, pages with identical content across sources are automatically de-duplicated so you don't see the same article twice.

---

## Data freshness

The database is rebuilt every Sunday at 02:00 UTC and published as a release tagged `data-YYYY-MM-DD`. `splunk-setup` always downloads the latest release.

To refresh your local database:

```bash
uv run splunk-setup
```

After updating, **restart the MCP server** (restart Claude Desktop or reload the MCP connection in your editor). The semantic search index is loaded into memory at startup and won't reflect the new database until the server restarts.

---

## Building locally

If you want to crawl the docs yourself instead of downloading the pre-built database:

```bash
# Full crawl — all sources (takes several hours)
uv run splunk-crawl

# Single source
uv run splunk-crawl --sources enterprise-security

# Single section (fast — ~30 seconds, good for development)
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

The crawl writes `data/splunk_docs.db` and Markdown files to `data/docs/`. Both are gitignored.

---

## Contributing

This is a personal learning project — issues and PRs are welcome but I can't commit to a support timeline. If something is broken or a Splunk product version you care about isn't indexed, feel free to open an issue.
