#!/usr/bin/env python3
"""
Automatizēta anonimizēto nolēmumu izgūšana no DAGR CSV resursa.

Ko dara:
1) Lejupielādē CSV no DAGR.
2) Aprēķina faila SHA-256 hash un izlaiž importu, ja izmaiņu nav.
3) Saglabā oriģinālo CSV arhīvā.
4) Importē datus SQLite datubāzē.
5) Izveido atsevišķu metadatu tabulu ar pēdējās sinhronizācijas informāciju.

Palaist:
    python sync_anon_nolemumi.py

Pēc noklusējuma rezultāti būs mapē ./data
"""

from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

DAGR_CSV_URL = "https://dagr.gov.lv/public/kkp_prod_anon_nolemumi?filename=kkp_prod_anon_nolemumi.csv"
DATA_DIR = Path(os.getenv("ANON_NOLEMUMI_DATA_DIR", "data"))
DB_PATH = DATA_DIR / "anon_nolemumi.sqlite"
ARCHIVE_DIR = DATA_DIR / "archive"
TABLE_NAME = "anon_nolemumi"
CHUNK_SIZE = 1024 * 1024


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def download_file(url: str, dest: Path) -> str:
    """Download URL to dest and return SHA-256 hash."""
    sha = hashlib.sha256()
    req = Request(url, headers={"User-Agent": "anon-nolemumi-sync/1.0"})
    with urlopen(req, timeout=120) as response, dest.open("wb") as f:
        while True:
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
            f.write(chunk)
    return sha.hexdigest()


def get_last_hash(conn: sqlite3.Connection) -> str | None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    row = conn.execute("SELECT value FROM sync_meta WHERE key = 'last_sha256'").fetchone()
    return row[0] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def detect_dialect(path: Path) -> csv.Dialect:
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:65536]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        return csv.excel


def normalize_column(name: str, index: int) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in (name or "").strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    if not cleaned:
        cleaned = f"column_{index + 1}"
    return cleaned


def unique_columns(columns: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: dict[str, int] = {}
    for i, col in enumerate(columns):
        base = normalize_column(col, i)
        count = seen.get(base, 0)
        seen[base] = count + 1
        result.append(base if count == 0 else f"{base}_{count + 1}")
    return result


def import_csv_to_sqlite(csv_path: Path, conn: sqlite3.Connection) -> int:
    dialect = detect_dialect(csv_path)
    with csv_path.open("r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.reader(f, dialect)
        try:
            raw_header = next(reader)
        except StopIteration:
            raise RuntimeError("CSV fails ir tukšs")

        columns = unique_columns(raw_header)
        quoted_cols = ", ".join(f'"{c}" TEXT' for c in columns)
        conn.execute(f'DROP TABLE IF EXISTS "{TABLE_NAME}"')
        conn.execute(f'CREATE TABLE "{TABLE_NAME}" ({quoted_cols})')

        placeholders = ", ".join("?" for _ in columns)
        col_list = ", ".join(f'"{c}"' for c in columns)
        sql = f'INSERT INTO "{TABLE_NAME}" ({col_list}) VALUES ({placeholders})'

        count = 0
        batch = []
        width = len(columns)
        for row in reader:
            if len(row) < width:
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                row = row[:width]
            batch.append(row)
            if len(batch) >= 5000:
                conn.executemany(sql, batch)
                count += len(batch)
                batch.clear()
        if batch:
            conn.executemany(sql, batch)
            count += len(batch)

    conn.execute(f'CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_rowid ON "{TABLE_NAME}" (rowid)')
    return count


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        last_hash = get_last_hash(conn)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
            tmp_path = Path(tmp.name)

        try:
            print(f"Lejupielādēju: {DAGR_CSV_URL}")
            new_hash = download_file(DAGR_CSV_URL, tmp_path)

            if new_hash == last_hash:
                set_meta(conn, "last_checked_at", utc_now_iso())
                conn.commit()
                print("Izmaiņu nav. Imports izlaists.")
                return 0

            archive_name = f"kkp_prod_anon_nolemumi_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            archive_path = ARCHIVE_DIR / archive_name
            tmp_path.replace(archive_path)

            print("Importēju CSV SQLite datubāzē...")
            row_count = import_csv_to_sqlite(archive_path, conn)

            set_meta(conn, "last_sha256", new_hash)
            set_meta(conn, "last_imported_at", utc_now_iso())
            set_meta(conn, "last_archive_file", str(archive_path))
            set_meta(conn, "last_row_count", str(row_count))
            set_meta(conn, "source_url", DAGR_CSV_URL)
            conn.commit()

            print(f"Gatavs. Importēti {row_count} ieraksti datubāzē: {DB_PATH}")
            print(f"Arhīva fails: {archive_path}")
            return 0
        finally:
            if tmp_path.exists():
                tmp_path.unlink()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Kļūda: {exc}", file=sys.stderr)
        raise SystemExit(1)
