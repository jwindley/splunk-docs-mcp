"""
CLI for discovering current Splunk product versions from help.splunk.com.

Usage
-----
  uv run splunk-discover-versions           # update versions.json
  uv run splunk-discover-versions --dry-run # show what would change, don't write

For each source that has version_discovery_url set, fetches the page and parses
the <select id="version-select"> dropdown. The first option is the current version,
second is n-1, third is n-2. Updates versions.json keys accordingly.

On failure for any source, keeps the existing value and logs a warning. The tool
exits 0 so that GHA crawl jobs continue with the last-known-good version.
"""

import argparse
import json
import sys

import httpx
from bs4 import BeautifulSoup

from .config import PHASE1_SOURCES, _PROJECT_ROOT

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _fetch_versions(client: httpx.Client, url: str) -> list[str]:
    """Return ordered version list from <select id="version-select"> on `url`."""
    resp = client.get(url, follow_redirects=True, timeout=30.0)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    select = soup.find("select", id="version-select")
    if not select:
        return []
    return [
        opt.get_text(strip=True)
        for opt in select.find_all("option")
        if opt.get_text(strip=True)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="splunk-discover-versions",
        description="Discover current Splunk product versions and update versions.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without writing versions.json.",
    )
    args = parser.parse_args()

    versions_path = _PROJECT_ROOT / "versions.json"
    current: dict[str, str | None] = json.loads(versions_path.read_text())
    updated = dict(current)

    primary_sources = [s for s in PHASE1_SOURCES if s.version_discovery_url]
    if not primary_sources:
        print("No sources with version_discovery_url configured. Nothing to do.")
        sys.exit(0)

    with httpx.Client(headers=_HEADERS) as client:
        for source in primary_sources:
            print(f"Discovering {source.source_id}...", end=" ", flush=True)
            try:
                versions = _fetch_versions(client, source.version_discovery_url)  # type: ignore[arg-type]
                if not versions:
                    print(
                        f"WARNING: no version selector at {source.version_discovery_url}"
                    )
                    continue
                preview = versions[:5] + (["..."] if len(versions) > 5 else [])
                print(f"found: {preview}")

                src_id = source.source_id
                n1_key = f"{src_id}-n1"
                n2_key = f"{src_id}-n2"

                updated[src_id] = versions[0]
                if n1_key in updated:
                    updated[n1_key] = versions[1] if len(versions) >= 2 else None
                if n2_key in updated:
                    updated[n2_key] = versions[2] if len(versions) >= 3 else None

            except Exception as exc:
                # Keep existing value; never fail silently.
                print(f"WARNING: discovery failed — {exc} — keeping {current.get(source.source_id)!r}")

    changes = [
        (k, current.get(k), v)
        for k, v in updated.items()
        if current.get(k) != v
    ]
    if changes:
        print("\nVersion changes detected:")
        for k, old, new in changes:
            print(f"  {k}: {old!r} -> {new!r}")
    else:
        print("\nNo version changes.")

    if not args.dry_run:
        versions_path.write_text(json.dumps(updated, indent=2) + "\n")
        print(f"Updated {versions_path}")
    else:
        print("(dry-run — versions.json not modified)")
