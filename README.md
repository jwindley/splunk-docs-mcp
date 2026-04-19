# splunk-docs-mcp

A local [MCP](https://modelcontextprotocol.io) server that gives Claude Desktop and Claude Code full-text search over Splunk documentation. It crawls [help.splunk.com](https://help.splunk.com), indexes pages in a SQLite FTS5 database, and exposes the index through 5 MCP tools — so Claude can answer version-specific Splunk questions accurately without hallucinating.

**Phase 1 covers:**
- Splunk Enterprise Security 8.5
- Splunk Configuration File Reference 10.2 (`transforms.conf`, `inputs.conf`, etc.)

---

## Prerequisites

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)

---

## Setup

```bash
git clone https://github.com/jwindley/splunk-docs-mcp
cd splunk-docs-mcp
uv sync
uv run splunk-crawl    # crawls ~960 pages; takes ~15–20 minutes
```

The crawl writes `data/splunk_docs.db`. This is a one-time step; re-run it whenever you want to refresh the index.

---

## MCP configuration

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS) or Claude Code project settings (`.claude/settings.json`):

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

Replace `/absolute/path/to/splunk-docs-mcp` with the actual path where you cloned the repo.

---

## Available tools

| Tool | Description | Key parameters |
|------|-------------|----------------|
| `search_docs` | BM25 full-text search across all indexed pages | `query` (required), `source` (optional filter), `limit` (default 5) |
| `get_page` | Full Markdown content of a page by exact URL | `url` — use URLs from `search_docs` results |
| `list_sections` | Index structure: sources, sections, and page counts | `source` (optional filter) |
| `browse_section` | All pages in a section with titles and URLs | `section`, `source` (both required) |
| `get_index_info` | DB stats: total pages, sources, last crawl time, DB size | — |

---

## Development

Crawl a single section for fast pipeline testing (~30 seconds instead of 20 minutes):

```bash
uv run splunk-crawl --sources enterprise-security --section user-guide
```

Other useful flags:

```bash
uv run splunk-crawl --verbose                  # debug output per page
uv run splunk-crawl --full                     # re-extract all pages, ignoring content hashes
uv run splunk-crawl --sources admin-manual     # single source
uv run splunk-crawl --concurrency 5            # parallel workers (default: 5)
```
