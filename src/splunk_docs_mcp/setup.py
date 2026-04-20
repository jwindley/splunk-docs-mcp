"""
splunk-setup: download the latest pre-built splunk_docs.db from GitHub Releases.

Usage:
    uv run splunk-setup
"""

import sys

import httpx

from splunk_docs_mcp.config import DATA_DIR, DB_PATH

_RELEASES_API = (
    "https://api.github.com/repos/jwindley/splunk-docs-mcp/releases/latest"
)
_ASSET_NAME = "splunk_docs.db"


def main() -> None:
    print("Fetching latest release info from GitHub...")

    try:
        resp = httpx.get(
            _RELEASES_API,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
            timeout=15,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            sys.exit(
                "Error: no releases found at jwindley/splunk-docs-mcp. "
                "The first automated crawl may not have run yet."
            )
        sys.exit(f"Error fetching release info: {exc}")
    except httpx.RequestError as exc:
        sys.exit(f"Network error fetching release info: {exc}")

    release = resp.json()
    tag = release.get("tag_name", "unknown")
    assets = release.get("assets", [])

    asset = next((a for a in assets if a["name"] == _ASSET_NAME), None)
    if asset is None:
        sys.exit(
            f"Error: release '{tag}' exists but does not contain an asset "
            f"named '{_ASSET_NAME}'. The crawl workflow may still be running."
        )

    download_url = asset["browser_download_url"]
    total_bytes = asset.get("size", 0)
    print(f"Release:  {tag}")
    print(f"Asset:    {_ASSET_NAME}  ({total_bytes / 1_048_576:.1f} MB)")
    print(f"Saving to: {DB_PATH}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = DB_PATH.parent / (DB_PATH.name + ".tmp")

    try:
        with httpx.stream("GET", download_url, follow_redirects=True, timeout=300) as dl:
            dl.raise_for_status()
            downloaded = 0
            with tmp_path.open("wb") as fh:
                for chunk in dl.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes:
                        pct = downloaded / total_bytes * 100
                        mb = downloaded / 1_048_576
                        total_mb = total_bytes / 1_048_576
                        print(
                            f"\r  {mb:.1f} / {total_mb:.1f} MB  ({pct:.0f}%)",
                            end="",
                            flush=True,
                        )
        print()  # newline after progress
    except httpx.RequestError as exc:
        tmp_path.unlink(missing_ok=True)
        sys.exit(f"Download failed: {exc}")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    tmp_path.rename(DB_PATH)
    print(f"Done. Database written to {DB_PATH}")
