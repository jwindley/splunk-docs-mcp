# TODO — splunk-docs-mcp

_Last updated: 2026-05-03_

---

## 🔴 Next session — Tier 2: automatic version discovery

### Goal
Eliminate manual edits to `versions.json`. The GHA `crawl-and-release` workflow detects the current and previous versions of each Splunk product automatically, rewrites `versions.json`, commits the change, then crawls. You maintain **n and n-1 at minimum; n-2 is kept when cheap** (it is cheap — version merge deduplicates shared content so n-2 storage cost is only unique pages, typically ~10-20% of n-1 size).

### Research findings (2026-05-03)
- **Version selectors are server-rendered static HTML** on help.splunk.com — no JavaScript required. BeautifulSoup (already a dependency via markdownify/BS4) can parse them directly.
- **Admin manual** (`/en/data-management/splunk-enterprise-admin-manual`): flat horizontal link list in static HTML — versions "10.2 10.0 9.4 9.3 9.2 9.1 9.0" are direct anchor elements. Straightforward to parse.
- **Enterprise Security** (`/en/splunk-enterprise-security-8`): versions appear as navigation links on the landing page (not a dropdown). The agent found language selectors on the root; version links may be on a section subpage like `/en/splunk-enterprise-security-8/administer/8.5`. **Needs a second look** at a section-level page to confirm where the version selector lives.
- **SOAR On-Premises** (`/en/splunk-soar/soar-on-premises`): no version selector on the hub page — versioning is on subpages. Need to identify which subpage exposes the version list.
- **Splunk Enterprise / Cloud / Lantern**: no version in URL prefix; version appears embedded in page content. Less critical to auto-detect since these are slower-moving.

### Implementation plan

#### Step 1: `splunk-discover-versions` CLI command (new entry point in `pyproject.toml`)
- For each source in `PHASE1_SOURCES` where version discovery is possible, fetch the appropriate discovery URL and parse the version list
- Output: updated `versions.json` written to project root
- If discovery fails for a source, keep the existing version in `versions.json` and log a warning (never fail silently)
- Dry-run mode (`--dry-run`): print what would change without writing

Per-product discovery URLs and parsing strategy:
| Source | Discovery URL | Parse strategy |
|--------|--------------|----------------|
| `enterprise-security` | `/en/splunk-enterprise-security-8/administer/8.5` (or similar section page) | Find version selector links in nav; pick highest semver as current |
| `admin-manual` | `/en/data-management/splunk-enterprise-admin-manual` | Parse flat link list; first = current |
| `soar-on-premises` | TBD — identify which subpage has version links | Same as above |
| `splunk-enterprise` | `/en/splunk-enterprise/` | May need to look at page content for version mentions |
| `splunk-cloud` | `/en/splunk-cloud-platform/` | Same |

For n1 and n2: once current version is known, n1 = second in the version list, n2 = third (if present).

#### Step 2: Add to `CrawlSource` (optional but clean)
- Add `version_discovery_url: str | None = None` field to `CrawlSource`
- `splunk-discover-versions` iterates sources that have this field set; others fall back to current `versions.json` value
- This keeps discovery config co-located with source definitions

#### Step 3: GHA integration
Add a step at the start of the `crawl` job (before any crawling) — OR as a separate pre-crawl job:
```yaml
- name: Discover current versions
  run: uv run splunk-discover-versions
  
- name: Commit updated versions.json
  run: |
    git config user.name "github-actions[bot]"
    git config user.email "github-actions[bot]@users.noreply.github.com"
    git add versions.json
    git diff --staged --quiet || git commit -m "chore: update versions.json [skip ci]"
    git push
```
`[skip ci]` prevents the commit triggering another workflow run.

#### Step 4: n/n-1/n-2 policy
- `config.py` factories (`_es_source`, `_admin_source`) already support current, n1, n2 via `_V[source_id]`
- Policy: always maintain current + n1; keep n2 as long as n2 exists in the version list (it's cheap due to version merge dedup)
- When n2 ages out (e.g. Splunk drops 8.3 from their version selector), `splunk-discover-versions` sets n2 to `null` or removes it from `versions.json`; `config.py` skips sources whose version is null

#### Step 5: `versions.json` schema extension for nullability
```json
{
  "enterprise-security": "8.6",
  "enterprise-security-n1": "8.5",
  "enterprise-security-n2": "8.4",
  "admin-manual": "10.3",
  "admin-manual-n1": "10.2",
  "admin-manual-n2": null
}
```
Sources with `null` version are skipped by `config.py` (no CrawlSource created) and by the GHA matrix.

### Order of implementation
1. Manually fetch the ES section page and SOAR subpage to confirm where the version selector HTML lives (5 min research)
2. Write `splunk-discover-versions` with `--dry-run`; test against each product
3. Add `version_discovery_url` to CrawlSource and wire into factories
4. Handle `null` versions in config.py and GHA
5. Add GHA step + push

---

## 🟢 Content expansion (Phase 5)

### Splunk REST API docs
- Splunk Enterprise and Cloud both have a REST API reference at a separate URL tree on help.splunk.com
- Identify the seed URL and `url_prefix` for the REST API reference (e.g. `help.splunk.com/en/splunk-enterprise/rest-api-reference/`)
- Add as a new `CrawlSource` — no other code changes needed
- Large and valuable: covers every endpoint, request/response params, auth methods
- Check robots.txt for `Crawl-delay` before adding

### Splunk SDK docs
- Splunk provides Python, JavaScript, and Java SDKs — docs live at `dev.splunk.com` or similar
- Investigate URL structure and robots.txt compliance before adding
- Python SDK is highest priority (most common for automation and custom apps)
- May need a separate `CrawlSource` with different `url_prefix` and `blocked_path_prefixes`
- Worth checking if `dev.splunk.com` allows crawling — may need to fall back to GitHub-hosted docs

### n-1 for Enterprise and Cloud (needs investigation, not blocked)
- Enterprise URLs DO contain an isolated version segment: e.g.
  `help.splunk.com/en/splunk-enterprise/get-started/install-and-upgrade/10.2/page`
  so URL derivation (`/10.2/` → `/10.1/`) should work the same as ES
- Enterprise 10.1 and Cloud 10.2 were previously attempted and dropped with a note
  "seeding problem unsolvable without major rework" — but the exact failure cause was
  never documented. Likely candidates: mass 404s on derived seeds if section structure
  changed between versions, or the base seed URL not linking into version-specific paths
- **Action:** check `git log` for the commit that dropped these sources and read the
  context; then attempt a test crawl with `--section` on a small section to see if
  derived URLs resolve correctly
- If it works: standard `CrawlSource` entry with `derive_from`, same as ES 8.4
- Could unlock ~5,000+ unique pages; skip until admin-manual n-1 is done first

---

## 🟡 Operational improvements

### Version rotation command (`splunk-rotate-versions`) — Tier 2
- versions.json is now the single source of truth (Tier 1 ✅ done)
- Tier 2: add `splunk-discover-versions` CLI that fetches Splunk docs landing pages,
  parses the version selector HTML, and rewrites versions.json automatically
- GHA would run discovery before each crawl and auto-commit versions.json if changed
- Blocker: need to confirm help.splunk.com version selector is in static HTML (not JS-rendered)
- Related: decide the policy on ES 8.3 (enterprise-security-n2) — it's n-2 and was added during
  development. Keep it (low cost due to version merge) or drop it for cleanliness?

---

## ⚫ Priority — Future / optional

- [ ] **Add ITSI, Observability** — most-requested missing products

---

## ✅ Done (2026-05-03) — Phase 5: admin-manual n-1

### n-1 for admin-manual (config file reference) ✅
- Previous version is 10.0 (versions available: 10.2, 10.0, 9.4, 9.3… — no 10.1 published)
- Added `admin-manual-n1` CrawlSource in `config.py` with `derive_from="admin-manual"`
- Added to `crawl-derived` GHA matrix; merge step and release notes updated

---

## ✅ Done (2026-05-03) — Phase 4: Performance & UX improvements

### LRU result cache
- `@functools.lru_cache(maxsize=128)` on `_search_docs_cached`, `_search_docs_semantic_cached`, `_search_docs_hybrid_cached` in `server.py`; keyed on `(query, source, version, limit)`

### Hybrid search tool — `search_docs_hybrid`
- Runs BM25 + semantic in parallel via `ThreadPoolExecutor(max_workers=2)`; fuses results via RRF (k=60)
- Prefers BM25 result dict (has snippet) when a URL appears in both; strips individual `score`, returns `rrf_score`
- Added to decision tree as default first call for unknown topics; `search_docs`/`search_docs_semantic` demoted to targeted fallbacks

### Background model loading
- `SentenceTransformer` loaded in a daemon thread; `_model_ready = threading.Event()` signals readiness
- `search_docs_semantic` and `search_docs_hybrid` call `_model_ready.wait()` (no-op once set); server available immediately on startup
- Embedding matrix loading eliminated entirely (replaced by sqlite-vec)

### sqlite-vec ANN vector search
- `sqlite-vec` 0.1.9 added to dependencies; `vec0` virtual table (`vec_documents`) created in `init_db`
- `upsert_vec_embedding()` added to `db.py`; `_embed_pass` in `cli.py` populates `vec_documents` alongside `documents.embedding`
- `search_docs_semantic_vec()` replaces numpy matrix scan: fetches `limit*4` candidates via ANN, joins to `documents` for metadata + version/source filtering, deduplicates chunks→parent, returns cosine similarity (L2→cos conversion)
- One-time auto-migration in `init_db` copies existing `documents.embedding` BLOBs to `vec_documents`
- Eliminates 23-38MB in-process RAM matrix; `get_all_embeddings()` and `_embed_matrix` global removed from server.py

---

## ✅ Done (2026-04-30)

### Setup menu redesign
- Grouped hierarchical display: current version first, n-1 versions indented beneath
- n-1 per-source exports contain only unique pages (shared pages live in parent DB via version_tags)
- Selecting an n-1 version auto-adds the parent so shared pages are available
- Total download size shown per entry (unique DB + parent DB)
- Export order follows PHASE1_SOURCES order (logical grouping, not alphabetical)

### Option B: cross-version content deduplication (version_tags)
- `content_md_hash` column added (SHA-256 of extracted Markdown) — fixes Enterprise/Cloud overlap (~2,006 pages)
- `version_tags` JSON column added — canonical rows tagged with all versions they cover
- `run_version_merge_pass()` collapses same-content derived rows into parent rows
- `run_dedup_pass()` updated to use `COALESCE(content_md_hash, content_hash)`
- `search_docs` and semantic search version filter now matches via `json_each(version_tags)`
- `merge_dbs()` calls version merge pass before FTS5 rebuild
- `export_sources()` includes shared rows in n-1 per-source exports

### SOAR indexing
- `soar-on-premises` 8.5.0 (current) and `soar-on-premises-n1` 8.4.0 (n-1) added
- `soar-cloud` (current) added
- URL derivation for SOAR 8.4.0 via `derive_from="soar-on-premises"`

### 'Dead' URL status for permanent 404s
- `crawler.py`: HTTP 404 responses now stored as `status='dead'` not `status='failed'`
- Dead URLs are not retried on subsequent crawls (excluded from `get_failed_urls`)

---

## ✅ Done (2026-04-26)

### ES 8.4 coverage — confirmed correct
- Sitemap ground truth: ES 8.5 = 566, ES 8.4 = 308, ES 8.3 = 290 (our BFS exceeds these in all cases)
- `common-information-model` and `splunk-app-for-pci-compliance` are 8.5-only sections (404 on 8.4) — not missing pages, just new sections added in 8.5
- Added `api-reference/8.4` seed; 8.4 now crawls 431 pages (sitemap has 308, we exceed it)
- ES 8.4 page count is lower than 8.5 because 8.5 genuinely added ~330 pages across three new sections

---

## ✅ Done (2026-04-25)

### Lantern crawl fix
- `config.py`: added `https://lantern.splunk.com/hc` to `_LANTERN_BLOCKED` (auth-gated Help Center section)
- `crawler.py`: added auth-redirect detection — 4xx after off-domain redirect counts as skipped, not failed

### ES 8.5 page count investigation
- Confirmed 738 is correct and complete: all 14 sections on the ES 8.5 landing page are found via BFS
- The ~1,275 expected figure was a bad estimate; 738 is the real page count

### ES 8.4 page count investigation
- Root cause identified: `api-reference`, `common-information-model`, and most of `pci-compliance` are missing because they're only linked with `/8.5/` version segments on the live site
- See Priority 1 fix above

---

## ✅ Done (2026-04-23)

### README overhaul
- Hallucination motivation prominent at top
- uv install instructions (Homebrew, curl, PowerShell)
- Removed "Merging per-source databases" section (CI-only, confuses users)
- Simplified sources table with n−1 coverage model
- ITSI/SOAR/Observability listed as planned additions

### Bug fixes
- `setup.py`: clean up stale WAL/SHM files after merge temp rename
- `.gitignore`: added `*.tmp`, `*.tmp-wal`, `*.tmp-shm` patterns for merge temp files
- `crawl-and-release.yml`: merge step now skips missing per-source DBs instead of failing

### GHA re-run
- Triggered and verified: all sources except Lantern succeeded; Lantern fixed 2026-04-25
- ES 8.3 confirmed working (351 pages) — prior "0 pages" bug fixed in earlier session
- Enterprise 10.1 and Cloud 10.2 dropped from crawl matrix (seeding problem unsolvable without major rework; n−1 coverage maintained via ES 8.3/8.4 and Cloud/Enterprise current+1 back version)

---

## ✅ Done (Phase 3, 2026-04-20/21)

### Item 1: GHA matrix parallelisation + `merge_dbs()` ✅
### Item 7: Multi-version crawling ✅
### Item 5: Cross-source deduplication ✅
### Item 9: splunk-setup version selection UI ✅
### Priority 5 items ✅ (jitter, tests)
### GHA fixes (2026-04-21) ✅

---

## ✅ Done (Phase 1 + 2, earlier sessions)

- [x] **Phase 2 — Public release distribution** — `setup.py`, GHA workflow skeleton, README
- [x] **Item 10** — `crawled_at` in search result metadata
- [x] **Item 4** — Exponential backoff retry (3 attempts, 2/4/8 s)
- [x] **Item 3** — Embedding matrix cache at server startup
- [x] **Item 8** — Smart chunking + `--rechunk` flag
- [x] **Item 2** — Lantern sitemap-based URL discovery
- [x] **Item 6** — Embedding reuse via `content_hash`
- [x] All core files: `config.py`, `db.py`, `extractor.py`, `crawler.py`, `cli.py`, `server.py`
- [x] Full crawls of all original 5 sources
