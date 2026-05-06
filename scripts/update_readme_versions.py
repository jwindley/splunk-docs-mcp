"""Update the versions table in README.md from versions.json.

Run after splunk-discover-versions so the README stays in sync with
the versions that will actually be crawled.
"""

import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).parent.parent

# Label suffix by source-ID ending — "current" versions get no suffix.
_LABELS = {"-n2": "n−2", "-n1": "n−1"}  # n−2, n−1 (unicode minus sign)


def _version_cell(source_id: str, version: str) -> str:
    if version == "current":
        return "current"
    for suffix, label in _LABELS.items():
        if source_id.endswith(suffix):
            return f"{version} ({label})"
    return f"{version} (current)"


def update(versions_path: pathlib.Path, readme_path: pathlib.Path) -> bool:
    versions: dict[str, str] = json.loads(versions_path.read_text())
    lines = readme_path.read_text().splitlines(keepends=True)

    changed = False
    for i, line in enumerate(lines):
        m = re.match(r"\| `([^`]+)` \|", line)
        if not m:
            continue
        source_id = m.group(1)
        if source_id not in versions:
            continue
        new_cell = _version_cell(source_id, versions[source_id])
        # Replace the last column (everything after the second pipe group up to trailing |)
        new_line = re.sub(
            r"(\| `[^`]+` \|[^|]+\|) [^|]+ \|$",
            rf"\1 {new_cell} |",
            line.rstrip("\n"),
        ) + "\n"
        if new_line != line:
            lines[i] = new_line
            changed = True

    if changed:
        readme_path.write_text("".join(lines))
    return changed


if __name__ == "__main__":
    changed = update(REPO_ROOT / "versions.json", REPO_ROOT / "README.md")
    print("README.md updated" if changed else "README.md unchanged")
    sys.exit(0)
