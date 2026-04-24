"""
CLI and library for merging per-source SQLite databases into one.

Two modes
---------
Merge mode (default)
    Combines multiple per-source DBs produced by ``splunk-crawl --db`` into a
    single output DB, then rebuilds the FTS5 index for consistency.

    uv run splunk-merge data/enterprise-security.db data/admin-manual.db \\
        data/splunk-enterprise.db data/splunk-cloud.db data/lantern.db \\
        --output data/splunk_docs.db

Export mode (--export-sources)
    Extracts per-source DBs and a ``manifest.json`` from an existing merged DB.
    Used by the GHA aggregation job to publish individual source downloads.

    uv run splunk-merge --export-sources data/export/ --db data/splunk_docs.db
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH, SOURCES_BY_ID
from . import db as db_module


# ---------------------------------------------------------------------------
# Library functions
# ---------------------------------------------------------------------------


def merge_dbs(source_db_paths: list[Path], output_path: Path) -> None:
    """Merge per-source DBs into a single output DB and rebuild FTS5."""
    conn = db_module.get_connection(output_path)
    db_module.init_db(conn)

    total = 0
    for src_path in source_db_paths:
        count = db_module.merge_source_db(conn, src_path)
        print(f"  {src_path.name}: {count} rows inserted")
        total += count

    print("Rebuilding FTS5 index…")
    conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
    conn.commit()

    print("Running cross-source deduplication…")
    n = db_module.run_dedup_pass(conn)
    print(f"Dedup complete — {n} duplicate rows suppressed")

    print(f"Merge complete — {total} rows total in {output_path}")


def export_sources(merged_db_path: Path, export_dir: Path) -> None:
    """Export per-source DBs and manifest.json from a merged DB."""
    export_dir.mkdir(parents=True, exist_ok=True)
    conn = db_module.get_connection(merged_db_path)

    source_ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source FROM documents WHERE chunk_of IS NULL ORDER BY source"
        ).fetchall()
    ]

    manifest_sources = []
    total_pages = 0

    for source_id in source_ids:
        out_path = export_dir / f"splunk_docs_{source_id}.db"
        _export_source_db(conn, source_id, out_path)
        size = out_path.stat().st_size

        # Count real pages (unchunked leaves + chunked parents)
        pages = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source = ? AND chunk_of IS NULL",
            (source_id,),
        ).fetchone()[0]
        chunks = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE source = ? AND chunk_of IS NOT NULL",
            (source_id,),
        ).fetchone()[0]
        total_pages += pages

        src_cfg = SOURCES_BY_ID.get(source_id)
        manifest_sources.append(
            {
                "source_id": source_id,
                "display_name": src_cfg.display_name if src_cfg else source_id,
                "version": src_cfg.version if src_cfg else "unknown",
                "pages": pages,
                "chunks": chunks,
                "file_name": out_path.name,
                "size_bytes": size,
            }
        )
        print(
            f"  {source_id}: {pages} pages, {chunks} chunks, "
            f"{size:,} bytes → {out_path.name}"
        )

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_pages": total_pages,
        "sources": manifest_sources,
    }
    manifest_path = export_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"manifest.json written ({len(manifest_sources)} sources, {total_pages} pages)")


def _export_source_db(conn, source_id: str, output_path: Path) -> None:
    """Write a fresh DB containing only documents belonging to source_id."""
    out = db_module.get_connection(output_path)
    db_module.init_db(out)

    rows = conn.execute(
        """
        SELECT url, title, source, version, section, subsection, slug,
               file_path, content_md, content_hash, crawled_at,
               embedding, has_chunks, chunk_of, chunk_index
        FROM documents WHERE source = ?
        """,
        (source_id,),
    ).fetchall()

    for row in rows:
        out.execute(
            """
            INSERT OR IGNORE INTO documents
                (url, title, source, version, section, subsection, slug,
                 file_path, content_md, content_hash, crawled_at,
                 embedding, has_chunks, chunk_of, chunk_index)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["url"], row["title"], row["source"], row["version"],
                row["section"], row["subsection"], row["slug"],
                row["file_path"], row["content_md"], row["content_hash"],
                row["crawled_at"], row["embedding"], row["has_chunks"],
                row["chunk_of"], row["chunk_index"],
            ),
        )

    cs_rows = conn.execute(
        "SELECT url, source, status, error, attempted_at FROM crawl_state WHERE source = ?",
        (source_id,),
    ).fetchall()
    for cs in cs_rows:
        out.execute(
            "INSERT OR IGNORE INTO crawl_state (url, source, status, error, attempted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cs["url"], cs["source"], cs["status"], cs["error"], cs["attempted_at"]),
        )

    out.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
    out.commit()
    out.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="splunk-merge",
        description="Merge per-source Splunk docs DBs or export per-source files.",
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--export-sources",
        metavar="DIR",
        default=None,
        help=(
            "Export mode: extract per-source DBs and manifest.json from the "
            "merged DB (--db) into DIR."
        ),
    )

    p.add_argument(
        "source_dbs",
        nargs="*",
        type=Path,
        metavar="SOURCE_DB",
        help="Per-source DB files to merge (merge mode only).",
    )
    p.add_argument(
        "--output", "-o",
        type=Path,
        default=DB_PATH,
        metavar="PATH",
        help=f"Output merged DB path (merge mode). Default: {DB_PATH}",
    )
    p.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        metavar="PATH",
        help=f"Merged DB to read from (export mode). Default: {DB_PATH}",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.export_sources:
        export_dir = Path(args.export_sources)
        print(f"Exporting per-source DBs to {export_dir} …")
        export_sources(args.db, export_dir)
    else:
        if not args.source_dbs:
            parser.error("Provide at least one SOURCE_DB in merge mode.")
        missing = [p for p in args.source_dbs if not p.exists()]
        if missing:
            for p in missing:
                print(f"ERROR: {p} not found", file=sys.stderr)
            sys.exit(1)
        print(f"Merging {len(args.source_dbs)} source DB(s) → {args.output} …")
        merge_dbs(args.source_dbs, args.output)
