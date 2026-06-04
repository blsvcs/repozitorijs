#!/usr/bin/env python3
"""Build a GitHub-only pilot dataset in SQLite.

This script avoids PostgreSQL and Docker. It is designed for GitHub Actions.

What it does:
1. Reads the DAGR CSV metadata file.
2. Creates/updates pilot.sqlite.
3. Downloads the next batch of PDF files.
4. Extracts text with pypdf.
5. Stores metadata, document status, extracted text, and a simple FTS5 index.

Example:
  python scripts/build_pilot_sqlite.py data/archive/file.csv --target-total 10000 --batch-size 500
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader

DB_PATH = Path("pilot/pilot.sqlite")
PDF_DIR = Path("pilot/pdf")
USER_AGENT = "nolemumi-github-pilot/1.0"
TIMEOUT = 120


SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    materialfileid TEXT PRIMARY KEY,
    court TEXT,
    courtdepartment TEXT,
    eclicode TEXT,
    casenumber TEXT,
    applicationnumber TEXT,
    processtype TEXT,
    processsubtype TEXT,
    materialtype TEXT,
    registrationdate TEXT,
    status TEXT,
    courtinstance TEXT,
    downloadurl TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    materialfileid TEXT PRIMARY KEY,
    content_type TEXT,
    file_name TEXT,
    file_size INTEGER,
    sha256 TEXT,
    local_path TEXT,
    download_status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    downloaded_at TEXT,
    extracted_text TEXT,
    text_length INTEGER,
    FOREIGN KEY(materialfileid) REFERENCES decisions(materialfileid)
);

CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    materialfileid UNINDEXED,
    court UNINDEXED,
    casenumber UNINDEXED,
    text
);

CREATE INDEX IF NOT EXISTS idx_decisions_court ON decisions(court);
CREATE INDEX IF NOT EXISTS idx_decisions_registrationdate ON decisions(registrationdate);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(download_status);
"""


CSV_FIELD_ALIASES = {
    "materialfileid": ["materialfileid", "MaterialFileId", "material_file_id"],
    "court": ["court", "Court"],
    "courtdepartment": ["courtdepartment", "CourtDepartment"],
    "eclicode": ["eclicode", "EcliCode", "ECLICode"],
    "casenumber": ["casenumber", "CaseNumber"],
    "applicationnumber": ["applicationnumber", "ApplicationNumber"],
    "processtype": ["processtype", "ProcessType"],
    "processsubtype": ["processsubtype", "ProcessSubType"],
    "materialtype": ["materialtype", "MaterialType"],
    "registrationdate": ["registrationdate", "RegistrationDate"],
    "status": ["status", "Status"],
    "courtinstance": ["courtinstance", "CourtInstance"],
    "downloadurl": ["downloadurl", "DownloadUrl", "download_url"],
}


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def norm(row: dict[str, str], key: str) -> str | None:
    aliases = CSV_FIELD_ALIASES.get(key, [key])
    lower_map = {str(k).lower(): k for k in row.keys()}

    value = None
    for alias in aliases:
        if alias in row:
            value = row.get(alias)
            break
        actual_key = lower_map.get(alias.lower())
        if actual_key is not None:
            value = row.get(actual_key)
            break

    if value is None:
        return None
    value = str(value).strip()
    return value or None


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def import_metadata(conn: sqlite3.Connection, csv_path: Path, target_total: int) -> int:
    existing = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
    if existing >= target_total:
        return 0

    remaining = target_total - existing
    imported = 0
    skipped_missing_required = 0

    with csv_path.open("r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.DictReader(f)
        print(f"CSV columns: {reader.fieldnames}")
        for row in reader:
            materialfileid = norm(row, "materialfileid")
            downloadurl = norm(row, "downloadurl")
            if not materialfileid or not downloadurl:
                skipped_missing_required += 1
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO decisions (
                    materialfileid, court, courtdepartment, eclicode, casenumber,
                    applicationnumber, processtype, processsubtype, materialtype,
                    registrationdate, status, courtinstance, downloadurl
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    materialfileid,
                    norm(row, "court"),
                    norm(row, "courtdepartment"),
                    norm(row, "eclicode"),
                    norm(row, "casenumber"),
                    norm(row, "applicationnumber"),
                    norm(row, "processtype"),
                    norm(row, "processsubtype"),
                    norm(row, "materialtype"),
                    norm(row, "registrationdate"),
                    norm(row, "status"),
                    norm(row, "courtinstance"),
                    downloadurl,
                ),
            )
            if cur.rowcount == 1:
                imported += 1
            if imported >= remaining:
                break
    conn.commit()
    print(f"Skipped rows without materialfileid/downloadurl: {skipped_missing_required}")
    return imported


def get_pending(conn: sqlite3.Connection, batch_size: int):
    cur = conn.execute(
        """
        SELECT d.materialfileid, d.downloadurl, d.court, d.casenumber
        FROM decisions d
        LEFT JOIN documents doc ON doc.materialfileid = d.materialfileid
        WHERE COALESCE(doc.download_status, 'pending') IN ('pending', 'failed')
        ORDER BY d.registrationdate, d.materialfileid
        LIMIT ?
        """,
        (batch_size,),
    )
    return cur.fetchall()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extension_from_content_type(content_type: str | None) -> str:
    value = (content_type or "").lower()
    if "pdf" in value:
        return ".pdf"
    if "word" in value or "officedocument" in value:
        return ".docx"
    if "html" in value:
        return ".html"
    return ".bin"


def choose_file_name(materialfileid: str, content_type: str | None, url: str) -> str:
    guessed = Path(urlparse(url).path).name
    if guessed and "." in guessed and len(guessed) < 120:
        return guessed
    return materialfileid + extension_from_content_type(content_type)


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts).strip()


def upsert_failed(conn: sqlite3.Connection, materialfileid: str, message: str) -> None:
    conn.execute(
        """
        INSERT INTO documents(materialfileid, download_status, error_message)
        VALUES (?, 'failed', ?)
        ON CONFLICT(materialfileid) DO UPDATE SET
            download_status = 'failed',
            error_message = excluded.error_message
        """,
        (materialfileid, message[:1000]),
    )


def upsert_document(
    conn: sqlite3.Connection,
    materialfileid: str,
    content_type: str,
    file_name: str,
    file_size: int,
    file_hash: str,
    local_path: str,
    extracted_text: str,
) -> None:
    conn.execute(
        """
        INSERT INTO documents(
            materialfileid, content_type, file_name, file_size, sha256,
            local_path, download_status, error_message, downloaded_at,
            extracted_text, text_length
        ) VALUES (?, ?, ?, ?, ?, ?, 'downloaded', NULL, ?, ?, ?)
        ON CONFLICT(materialfileid) DO UPDATE SET
            content_type = excluded.content_type,
            file_name = excluded.file_name,
            file_size = excluded.file_size,
            sha256 = excluded.sha256,
            local_path = excluded.local_path,
            download_status = 'downloaded',
            error_message = NULL,
            downloaded_at = excluded.downloaded_at,
            extracted_text = excluded.extracted_text,
            text_length = excluded.text_length
        """,
        (
            materialfileid,
            content_type,
            file_name,
            file_size,
            file_hash,
            local_path,
            now_iso(),
            extracted_text,
            len(extracted_text),
        ),
    )

    row = conn.execute(
        "SELECT court, casenumber FROM decisions WHERE materialfileid = ?",
        (materialfileid,),
    ).fetchone()
    court = row[0] if row else None
    casenumber = row[1] if row else None
    conn.execute("DELETE FROM documents_fts WHERE materialfileid = ?", (materialfileid,))
    if extracted_text:
        conn.execute(
            "INSERT INTO documents_fts(materialfileid, court, casenumber, text) VALUES (?, ?, ?, ?)",
            (materialfileid, court, casenumber, extracted_text),
        )


def download_and_extract(conn: sqlite3.Connection, batch_size: int, sleep: float) -> tuple[int, int]:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    pending = get_pending(conn, batch_size)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    ok = 0
    failed = 0
    for idx, row in enumerate(pending, start=1):
        materialfileid, url, _court, _casenumber = row
        print(f"[{idx}/{len(pending)}] {materialfileid}")
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            data = response.content
            if not data:
                raise RuntimeError("empty response")
            content_type = response.headers.get("content-type", "")
            file_name = choose_file_name(materialfileid, content_type, url)
            local_path = PDF_DIR / file_name
            local_path.write_bytes(data)

            extracted_text = ""
            if local_path.suffix.lower() == ".pdf" or "pdf" in content_type.lower():
                extracted_text = extract_pdf_text(local_path)

            upsert_document(
                conn,
                materialfileid,
                content_type,
                file_name,
                len(data),
                sha256_bytes(data),
                str(local_path),
                extracted_text,
            )
            conn.commit()
            ok += 1
        except Exception as exc:
            failed += 1
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {materialfileid}: {message}")
            upsert_failed(conn, materialfileid, message)
            conn.commit()
        time.sleep(sleep)
    return ok, failed


def print_stats(conn: sqlite3.Connection) -> None:
    stats = {}
    stats["decisions"] = conn.execute("SELECT count(*) FROM decisions").fetchone()[0]
    stats["documents"] = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    stats["downloaded"] = conn.execute("SELECT count(*) FROM documents WHERE download_status = 'downloaded'").fetchone()[0]
    stats["with_text"] = conn.execute("SELECT count(*) FROM documents WHERE extracted_text IS NOT NULL AND extracted_text <> ''").fetchone()[0]
    print("Stats:", stats)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--target-total", type=int, default=10000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    args = parser.parse_args()

    args.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(args.db_path) as conn:
        init_db(conn)
        imported = import_metadata(conn, args.csv_path, args.target_total)
        print(f"Imported metadata rows: {imported}")
        ok, failed = download_and_extract(conn, args.batch_size, args.sleep)
        print(f"Batch done. downloaded={ok}, failed={failed}")
        print_stats(conn)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
