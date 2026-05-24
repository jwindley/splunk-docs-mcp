# splunk-docs-mcp

An MCP server that gives AI assistants accurate, version-specific Splunk documentation — AI assistants can hallucinate on Splunk specifics, and training data goes stale.

---

## Why this exists

AI assistants sometimes hallucinate on Splunk questions — giving confident answers that apply to a different version, or that are simply made up. Training data also goes stale quickly, so even correct answers may refer to old behaviour.

This server fixes that by giving Claude (or any MCP-compatible client) access to the **actual documentation**, crawled directly from `help.splunk.com` and `lantern.splunk.com`. With it connected, Claude looks up the real docs before answering instead of guessing from training data.

It can answer questions like:

- "How do I configure correlation searches in ES 8.5?"
- "What fields does `transforms.conf` support?"
- "What's the difference between `notable` and `risk` in Enterprise Security?"
- "What's the precise workflow for enabling cloud-to-enterprise federation?"

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

You can also target specific products or versions in your question — Claude will pass these as filters to the search tools:

- Ask about "ES 8.4" or "Splunk Cloud 10.3" and it will filter to that version automatically.
- Mention "Enterprise Security" or "admin manual" and it will search that source specifically.

---

## Limitations

- **Not affiliated with or endorsed by Splunk or Cisco.**
- **Products not yet covered:** ITSI, Observability Cloud, and Mission Control are not indexed yet — these are planned additions.
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

Add this to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS), your global Claude Code config (`~/.claude/settings.json`), or a per-project Claude Code config (`.claude/settings.json`):

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

The goal is to keep the **current released version plus the previous version (n−1)** for each major product. Source IDs use a stable `-n1` / `-n2` suffix so the identifier doesn't change when versions rotate — only the version metadata inside the source updates.

**Version granularity:** coverage is at the **minor version** level (e.g. 8.4, 8.5, 10.2). Patch releases (8.5.1, 10.2.3) are not tracked separately — use the nearest minor version when filtering.

Versions are detected automatically before each weekly crawl — the table below reflects the current `versions.json`; the actual crawled versions may be newer.

| Source ID | Product | Version |
|-----------|---------|---------|
| `enterprise-security` | Splunk Enterprise Security | 8.5 (current) |
| `enterprise-security-n1` | Splunk Enterprise Security | 8.4 (n−1) |
| `enterprise-security-n2` | Splunk Enterprise Security | 8.3 (n−2) |
| `splunk-enterprise` | Splunk Enterprise | 10.4 (current) |
| `splunk-enterprise-n1` | Splunk Enterprise | 10.2 (n−1) |
| `splunk-cloud` | Splunk Cloud Platform | 10.4.2604 (current) |
| `splunk-cloud-n1` | Splunk Cloud Platform | 10.3.2512 (n−1) |
| `admin-manual` | Splunk Configuration File Reference | 10.4 (current) |
| `admin-manual-n1` | Splunk Configuration File Reference | 10.2 (n−1) |
| `rest-api-reference` | Splunk Enterprise REST API Reference | 10.4 (current) |
| `rest-api-cloud` | Splunk Cloud Platform REST API Reference | 10.4.2604 (current) |
| `soar-on-premises` | Splunk SOAR On-Premises | 8.5.0 (current) |
| `soar-on-premises-n1` | Splunk SOAR On-Premises | 8.4.0 (n−1) |
| `soar-cloud` | Splunk SOAR Cloud | current |
| `lantern` | Splunk Lantern | current |

Note: Splunk skips certain minor versions in their release cycle (e.g. no 10.1 for Enterprise or Cloud). n−1 reflects the actual previous release, not necessarily the immediately preceding minor number.

---

## Refreshing the database

The database is rebuilt every Sunday at 02:00 UTC. To update your local copy:

```bash
uv run splunk-setup
```

After updating, restart the MCP server (restart Claude Desktop or reload the MCP connection in your editor). The semantic search index is loaded into memory at startup and won't reflect the new database until the server restarts.

---

## Building locally

If you want to crawl the docs yourself instead of downloading the pre-built database:

```bash
# Full crawl — all sources (takes several hours)
uv run splunk-crawl

# Single source
uv run splunk-crawl --sources enterprise-security

# Single section (fast — good for development)
uv run splunk-crawl --sources enterprise-security --section user-guide

# Force re-extract + re-chunk + re-embed everything
uv run splunk-crawl --full
```

The crawl writes `data/splunk_docs.db` and Markdown files to `data/docs/`. Both are gitignored.

---

## Contributing

This is a personal learning project — issues and PRs are welcome but I can't commit to a support timeline. If something is broken or a Splunk product version you care about isn't indexed, feel free to open an issue.
