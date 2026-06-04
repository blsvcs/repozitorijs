#!/usr/bin/env python3
"""Search the pilot SQLite database.

Usage:
  python search_pilot.py "kredīta parāds"
  python search_pilot.py "būvniecība" --db pilot/pilot.sqlite --limit 10

The script searches the FTS5 index created by scripts/build_pilot_sqlite.py.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

DEFAULT_DB = Path("pilot/pilot.sqlite")


def clean_fts_query(query: str) -> str:
    words = re.findall(r"[\wāčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ]+", query, flags=re.UNICODE)
    return " ".join(words)


def search(db_path: Path, query: str, limit: int):
    fts_query = clean_fts_query(query)
    if not fts_query:
        raise SystemExit("Meklēšanas frāze ir tukša.")

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                d.materialfileid,
                d.court,
                d.casenumber,
                d.processtype,
                d.materialtype,
                d.registrationdate,
                snippet(documents_fts, 3, '[', ']', ' ... ', 24) AS snippet,
                bm25(documents_fts) AS rank
            FROM documents_fts
            JOIN decisions d ON d.materialfileid = documents_fts.materialfileid
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()

    return rows


def print_results(rows, query: str) -> None:
    print(f"Meklēšana: {query}")
    print(f"Atrasti rezultāti: {len(rows)}")
    print()

    if not rows:
        print("Nav rezultātu. Pamēģini citu atslēgvārdu vai īsāku frāzi.")
        return

    for i, row in enumerate(rows, start=1):
        print("=" * 80)
        print(f"#{i}")
        print(f"Lieta: {row['casenumber'] or '-'}")
        print(f"Tiesa: {row['court'] or '-'}")
        print(f"Datums: {row['registrationdate'] or '-'}")
        print(f"Process: {row['processtype'] or '-'}")
        print(f"Nolēmuma veids: {row['materialtype'] or '-'}")
        print(f"MaterialFileId: {row['materialfileid']}")
        print()
        print((row['snippet'] or '').replace('\n', ' '))
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Meklēšanas frāze, piemēram: kredīta parāds")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Ceļš uz pilot.sqlite")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Datubāze nav atrasta: {args.db}")

    rows = search(args.db, args.query, args.limit)
    print_results(rows, args.query)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
