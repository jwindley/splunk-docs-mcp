"""
splunk-setup: download the latest pre-built splunk_docs.db from GitHub Releases.

Usage:
    uv run splunk-setup          # interactive source selection menu
    uv run splunk-setup --all    # download all sources (skips menu)
"""

import argparse
import sys
from pathlib import Path

import httpx

from splunk_docs_mcp.config import DATA_DIR, DB_PATH

_RELEASES_API = (
    "https://api.github.com/repos/jwindley/splunk-docs-mcp/releases/latest"
)
_MANIFEST_NAME = "manifest.json"
_MONOLITHIC_ASSET = "splunk_docs.db"
_GITHUB_HEADERS = {"Accept": "application/vnd.github+json"}


def _fetch_release() -> tuple[str, list[dict]]:
    """Return (tag_name, assets_list) for the latest release."""
    try:
        resp = httpx.get(
            _RELEASES_API,
            headers=_GITHUB_HEADERS,
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
    return release.get("tag_name", "unknown"), release.get("assets", [])


def _fetch_manifest(assets: list[dict]) -> dict | None:
    """Download and parse manifest.json from release assets, or None if absent."""
    asset = next((a for a in assets if a["name"] == _MANIFEST_NAME), None)
    if asset is None:
        return None
    try:
        resp = httpx.get(
            asset["browser_download_url"],
            follow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"Warning: could not fetch manifest.json ({exc}); falling back to monolithic DB.")
        return None


def _download_file(url: str, dest: Path, label: str, total_bytes: int) -> None:
    """Stream-download url to dest with a simple progress indicator."""
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as dl:
            dl.raise_for_status()
            downloaded = 0
            with dest.open("wb") as fh:
                for chunk in dl.iter_bytes(chunk_size=65536):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total_bytes:
                        pct = downloaded / total_bytes * 100
                        mb = downloaded / 1_048_576
                        total_mb = total_bytes / 1_048_576
                        print(
                            f"\r  {label}  {mb:.1f} / {total_mb:.1f} MB  ({pct:.0f}%)",
                            end="",
                            flush=True,
                        )
        print()
    except httpx.RequestError as exc:
        dest.unlink(missing_ok=True)
        sys.exit(f"Download failed: {exc}")
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def _select_sources(sources: list[dict]) -> list[dict]:
    """Display numbered menu and return user-selected sources."""
    print("\nAvailable sources:\n")
    for i, src in enumerate(sources, 1):
        size_mb = src["size_bytes"] / 1_048_576
        print(
            f"  [{i}] {src['display_name']}"
            f"  ({src['pages']} pages, {size_mb:.1f} MB)"
        )

    total_mb = sum(s["size_bytes"] for s in sources) / 1_048_576
    print(f"\n  [all] Download all sources  ({total_mb:.1f} MB total)\n")

    while True:
        raw = input("Select sources (e.g. 1,3,5  or  all): ").strip().lower()
        if not raw:
            continue
        if raw == "all":
            return sources
        try:
            indices = [int(x.strip()) for x in raw.split(",")]
        except ValueError:
            print("  Enter numbers separated by commas, or 'all'.")
            continue
        invalid = [i for i in indices if i < 1 or i > len(sources)]
        if invalid:
            print(f"  Invalid number(s): {invalid}. Choose between 1 and {len(sources)}.")
            continue
        selected = [sources[i - 1] for i in indices]
        return selected


def _confirm_all(sources: list[dict]) -> None:
    """Print size warning for --all and ask for confirmation."""
    total_mb = sum(s["size_bytes"] for s in sources) / 1_048_576
    print(f"\nDownloading all {len(sources)} sources ({total_mb:.1f} MB total).")
    raw = input("Continue? [y/N] ").strip().lower()
    if raw != "y":
        sys.exit("Aborted.")


def _fallback_monolithic(tag: str, assets: list[dict]) -> None:
    """Download the monolithic splunk_docs.db when no manifest is available."""
    asset = next((a for a in assets if a["name"] == _MONOLITHIC_ASSET), None)
    if asset is None:
        sys.exit(
            f"Error: release '{tag}' has neither manifest.json nor {_MONOLITHIC_ASSET}. "
            "The crawl workflow may still be running."
        )

    total_bytes = asset.get("size", 0)
    print(f"Release:   {tag}")
    print(f"Asset:     {_MONOLITHIC_ASSET}  ({total_bytes / 1_048_576:.1f} MB)")
    print(f"Saving to: {DB_PATH}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.parent / (DB_PATH.name + ".tmp")
    _download_file(asset["browser_download_url"], tmp, _MONOLITHIC_ASSET, total_bytes)
    tmp.rename(DB_PATH)
    print(f"Done. Database written to {DB_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="splunk-setup",
        description="Download the latest Splunk docs index from GitHub Releases.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Skip the source selection menu and download all sources.",
    )
    args = parser.parse_args()

    print("Fetching latest release info from GitHub...")
    tag, assets = _fetch_release()
    print(f"Release: {tag}")

    manifest = _fetch_manifest(assets)

    if manifest is None:
        _fallback_monolithic(tag, assets)
        return

    sources = manifest["sources"]
    asset_by_name = {a["name"]: a for a in assets}

    if args.all:
        _confirm_all(sources)
        selected = sources
    else:
        selected = _select_sources(sources)

    total_bytes = sum(s["size_bytes"] for s in selected)
    total_mb = total_bytes / 1_048_576
    print(f"\nDownloading {len(selected)} source(s) ({total_mb:.1f} MB)...")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    tmp_paths: list[Path] = []
    for src in selected:
        file_name = src["file_name"]
        asset = asset_by_name.get(file_name)
        if asset is None:
            sys.exit(f"Error: asset '{file_name}' not found in release '{tag}'.")
        tmp = DATA_DIR / (file_name + ".tmp")
        _download_file(
            asset["browser_download_url"],
            tmp,
            file_name,
            asset.get("size", 0),
        )
        tmp_paths.append(tmp)

    if len(tmp_paths) == 1:
        # Single source — skip merge, just rename
        tmp_paths[0].rename(DB_PATH)
    else:
        print(f"\nMerging {len(tmp_paths)} source DB(s)...")
        from splunk_docs_mcp.merge import merge_dbs  # noqa: PLC0415
        merge_tmp = DB_PATH.parent / (DB_PATH.name + ".merge.tmp")
        merge_dbs(tmp_paths, merge_tmp)
        for p in tmp_paths:
            p.unlink(missing_ok=True)
        merge_tmp.rename(DB_PATH)

    print(f"\nDone. Database written to {DB_PATH}")
