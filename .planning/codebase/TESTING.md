# Testing Patterns

**Analysis Date:** 2026-05-03

## Test Framework

**Runner:**
- pytest 8.0+ (dev dependency in `pyproject.toml`)
- Config: `pyproject.toml` under `[tool.pytest.ini_options]`

**Async support:**
- `pytest-asyncio>=0.23.0` installed; `asyncio_mode = "auto"` set globally
- No async tests currently exist (all tested functions are synchronous), but the config is ready

**Assertion Library:**
- pytest built-in assertions (no extra library)

**Run Commands:**
```bash
uv run pytest tests/         # Run all tests (36 total)
uv run pytest tests/test_extractor.py   # Run extractor tests only (18 tests)
uv run pytest tests/test_crawler.py     # Run crawler URL tests only (18 tests)
uv run pytest -v tests/      # Verbose output
```

No coverage tooling is configured. No watch-mode command is set up.

## Test File Organization

**Location:** `tests/` directory at project root — tests are NOT co-located with source files.

**Naming:**
- `test_<module>.py` — one test file per module under test
- Test classes: `Test<FunctionName><Scenario>` — e.g. `TestParseUrlMetadataES`, `TestNormaliseUrl`, `TestIsTargetUrlVersionFilter`
- Test methods: `test_<what_is_being_tested>` — e.g. `test_fragment_stripped`, `test_wrong_es_version_rejected`

**Structure:**
```
tests/
├── test_extractor.py   — 18 tests for parse_url_metadata()
└── test_crawler.py     — 18 tests for _normalise_url, _is_target_url, _section_from_url
```

## Test Structure

**Suite Organization:**
```python
# tests/test_crawler.py — exact pattern used throughout

ES = SOURCES_BY_ID["enterprise-security"]   # module-level source constants
LANTERN = SOURCES_BY_ID["lantern"]

class TestNormaliseUrl:
    def test_plain_url_unchanged(self):
        url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5"
        assert _normalise_url(url) == url

    def test_fragment_stripped(self):
        result = _normalise_url("https://help.splunk.com/en/page#section-1")
        assert result == "https://help.splunk.com/en/page"
        assert "#" not in result


class TestIsTargetUrlVersionFilter:
    def test_older_es_versions_rejected(self):
        for ver in ("8.1", "8.2", "8.3", "8.4"):
            url = f"https://help.splunk.com/en/splunk-enterprise-security-8/administer/{ver}/page"
            assert _is_target_url(url, ES, None) is False, f"Expected {ver} to be rejected"
```

**Patterns observed:**
- Classes group related tests; no shared `setUp`/`tearDown` or `@pytest.fixture`
- Module-level constants for source objects (`ES`, `ADMIN`, `ENTERPRISE`, `LANTERN`) reduce repetition
- Direct `assert x == y` — no helper assertion wrappers
- `is True` / `is False` used (not just truthiness) for boolean return tests
- Multi-assertion tests combine positive and negative assertions on the same result:
  ```python
  assert result == "https://help.splunk.com/en/page"
  assert "#" not in result
  ```
- Loop-based parametrisation used for related cases with a custom failure message:
  ```python
  for ver in ("8.1", "8.2", "8.3", "8.4"):
      assert _is_target_url(url, ES, None) is False, f"Expected {ver} to be rejected"
  ```

## Mocking

**None used.** All tested functions are pure (no I/O, no DB, no HTTP). Tests use real `CrawlSource` objects loaded from `SOURCES_BY_ID` — the live config is the fixture.

```python
from splunk_docs_mcp.config import SOURCES_BY_ID
ES = SOURCES_BY_ID["enterprise-security"]
```

This means tests break if `config.py` source definitions change, which is intentional — the URL-parsing logic is tightly coupled to real source URL structures.

## Fixtures and Factories

**No pytest fixtures defined.** Source objects imported at module level serve as the shared test data:
```python
# tests/test_extractor.py
ES = SOURCES_BY_ID["enterprise-security"]
ADMIN = SOURCES_BY_ID["admin-manual"]
ENTERPRISE = SOURCES_BY_ID["splunk-enterprise"]
CLOUD = SOURCES_BY_ID["splunk-cloud"]
LANTERN = SOURCES_BY_ID["lantern"]
```

**Test URLs:** Hardcoded inline in each test method — not extracted to shared fixtures. This makes each test fully self-documenting.

## Coverage

**Requirements:** None enforced — no coverage threshold, no `pytest-cov` in dev dependencies.

**View Coverage:**
```bash
# Not configured; add pytest-cov manually if needed:
uv add --dev pytest-cov
uv run pytest tests/ --cov=splunk_docs_mcp --cov-report=term-missing
```

## What IS Tested

### `tests/test_extractor.py` — `parse_url_metadata()` (18 tests)

| Test Class | What it covers |
|------------|----------------|
| `TestParseUrlMetadataES` | ES URL parsing: full depth, version segment stripped, section-only, landing page |
| `TestParseUrlMetadataAdminManual` | Admin manual: conf file slug, hyphenated version-like segment NOT stripped |
| `TestParseUrlMetadataLantern` | Lantern: 3-level paths, 4-level paths (level 3 dropped), root URL |

Key edge cases explicitly tested:
- Version segment `8.5` stripped from section/subsection/slug
- `10.2.0-configuration-file-reference` is NOT stripped (hyphen disqualifies it as a version)
- Lantern 4-level paths: `section`=level1, `subsection`=level2, `slug`=last segment (level 3 group dropped)
- Landing page returns `section=None, subsection=None, slug=None`

### `tests/test_crawler.py` — URL utilities (18 tests)

**`TestNormaliseUrl` (8 tests):**
- Fragment stripped (`#section-1` removed)
- Query string stripped (`?action=edit` removed)
- Both stripped together
- `mailto:` → `None`
- `javascript:` → `None`
- Relative URL (`/en/path`) → `None`
- `http://` accepted
- Plain URL unchanged

**`TestIsTargetUrlVersionFilter` (9 tests):**
- Correct ES version accepted
- Wrong ES version rejected
- All ES versions 8.1–8.4 rejected when source.version is 8.5
- URL with no version segment accepted
- Admin manual (version baked into prefix) accepted
- Lantern (no numeric segments) always accepted
- Wrong URL prefix rejected
- Blocked prefix rejected (`https://help.splunk.com/api/`)
- Lantern blocked special page rejected (`Special:Search`)

**`TestIsTargetUrlSectionFilter` (4 tests):**
- Matching section accepted
- Non-matching section rejected
- Section-index URL (no sub-path) passes filter
- Lantern section filter (both positive and negative in one test)

**`TestSectionFromUrl` (6 tests):**
- ES URL with full path returns section
- ES URL with only version returns `None`
- Landing page returns `None`
- Enterprise URL returns section
- Lantern URL returns section
- Lantern root returns `None`

## What is NOT Tested

**No tests exist for:**

| Area | Risk |
|------|------|
| `db.py` — all DB functions | Schema changes could silently break reads/writes; FTS5 trigger correctness untested |
| `db.py` — `search_docs()` query | BM25 filter logic (version bypass, dedup filter) entirely untested |
| `db.py` — `upsert_document()` | ON CONFLICT logic, `has_chunks` reset, `version_tags` update condition |
| `db.py` — `chunk_document()` / `_split_content_smart()` | Chunking boundary logic for heading splits, paragraph fallback, character fallback |
| `db.py` — `run_dedup_pass()` / `run_version_merge_pass()` | Cross-source dedup and version tag collapsing are untested |
| `crawler.py` — `crawl_source()` | BFS integration, retry pass, sitemap seeding (require HTTP mocking) |
| `crawler.py` — `_extract_links()` | Link extraction from real HTML untested |
| `extractor.py` — `extract_page()` | trafilatura + BS4/markdownify extraction untested |
| `extractor.py` — `_extract_title()` | Title fallback chain untested |
| `server.py` — MCP tools | Tool input validation, error paths, semantic search routing |
| `cli.py` — exit code policy | `failure_rate > 0.05` threshold never exercised |
| `merge.py` | DB merge, export-sources, manifest generation |
| `setup.py` | GitHub release download flow |

**Highest-risk untested areas:**
1. `db.py` — the version filter bypass (`is_duplicate=0` skipped when `version=` set) is a critical correctness invariant with no tests
2. `db.py` — `run_version_merge_pass()` modifies data in-place and is irreversible; bugs could corrupt the merged DB silently
3. `crawler.py` — `_extract_links()` is called millions of times in production but never unit-tested
4. `cli.py` — the 5% failure-rate exit-code threshold is the CI gate and is never tested

## Common Patterns

**All URL tests — inline URL construction:**
```python
def test_correct_es_version_accepted(self):
    url = "https://help.splunk.com/en/splunk-enterprise-security-8/user-guide/8.5/intro"
    assert _is_target_url(url, ES, None) is True
```

**None-return tests:**
```python
def test_mailto_returns_none(self):
    assert _normalise_url("mailto:user@example.com") is None
```

**Multi-case loop (used for related version variants):**
```python
def test_older_es_versions_rejected(self):
    for ver in ("8.1", "8.2", "8.3", "8.4"):
        url = f"https://help.splunk.com/en/splunk-enterprise-security-8/administer/{ver}/page"
        assert _is_target_url(url, ES, None) is False, f"Expected {ver} to be rejected"
```

**Positive + negative in one test (for filter functions):**
```python
def test_lantern_section_filter(self):
    url = "https://lantern.splunk.com/Splunk_Success_Framework/something"
    assert _is_target_url(url, LANTERN, "Splunk_Success_Framework") is True
    assert _is_target_url(url, LANTERN, "Security_Use_Cases") is False
```

---

*Testing analysis: 2026-05-03*
