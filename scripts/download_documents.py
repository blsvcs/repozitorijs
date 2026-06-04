#!/usr/bin/env python3
"""Lejupielade nolemumu PDF failus un saglaba statusu PostgreSQL.

Piemers:
  python scripts/download_documents.py --limit 1000

Skripts ir resume-safe: tas izlaiž dokumentus ar download_status = 'downloaded'.
"""

from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.db import get_conn

STORAGE_DIR = Path("storage/pdf")
TIMEOUT = 120
USER_AGENT = "nolemumi-mvp-downloader/1.0"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extension_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return ".pdf"
    value = content_type.lower()
    if "pdf" in value:
        return ".pdf"
    if "word" in value or "officedocument" in value:
        return ".docx"
    if "html" in value:
        return ".html"
    return ".bin"


def choose_file_name(materialfileid: str, content_type: str | None, url: str) -> str:
    path = urlparse(url).path
    guessed = Path(path).name
    if guessed and "." in guessed and len(guessed) < 120:
        return guessed
    return materialfileid + extension_from_content_type(content_type)


def get_pending(limit: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.materialfileid::text AS materialfileid, d.downloadurl
                FROM decisions d
                LEFT JOIN decision_documents doc ON doc.materialfileid = d.materialfileid
                WHERE d.downloadurl IS NOT NULL
                  AND COALESCE(doc.download_status, 'pending') IN ('pending', 'failed')
                ORDER BY d.registrationdate NULLS LAST, d.materialfileid
                LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())


def mark_status(materialfileid: str, status: str, error_message: str | None = None, **extra):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO decision_documents (
                    materialfileid, content_type, file_name, file_size, sha256,
                    local_path, download_status, error_message, downloaded_at
                ) VALUES (
                    %(materialfileid)s, %(content_type)s, %(file_name)s, %(file_size)s,
                    %(sha256)s, %(local_path)s, %(download_status)s, %(error_message)s,
                    CASE WHEN %(download_status)s = 'downloaded' THEN now() ELSE NULL END
                )
                ON CONFLICT (materialfileid) DO UPDATE SET
                    content_type = COALESCE(EXCLUDED.content_type, decision_documents.content_type),
                    file_name = COALESCE(EXCLUDED.file_name, decision_documents.file_name),
                    file_size = COALESCE(EXCLUDED.file_size, decision_documents.file_size),
                    sha256 = COALESCE(EXCLUDED.sha256, decision_documents.sha256),
                    local_path = COALESCE(EXCLUDED.local_path, decision_documents.local_path),
                    download_status = EXCLUDED.download_status,
                    error_message = EXCLUDED.error_message,
                    downloaded_at = CASE WHEN EXCLUDED.download_status = 'downloaded' THEN now() ELSE decision_documents.downloaded_at END
                """,
                {
                    "materialfileid": materialfileid,
                    "download_status": status,
                    "error_message": error_message,
                    "content_type": extra.get("content_type"),
                    "file_name": extra.get("file_name"),
                    "file_size": extra.get("file_size"),
                    "sha256": extra.get("sha256"),
                    "local_path": extra.get("local_path"),
                },
            )


def download_one(session: requests.Session, materialfileid: str, url: str) -> None:
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    content = response.content
    if not content:
        raise RuntimeError("empty response")

    content_type = response.headers.get("content-type", "")
    file_name = choose_file_name(materialfileid, content_type, url)
    local_path = STORAGE_DIR / file_name
    local_path.write_bytes(content)

    mark_status(
        materialfileid,
        "downloaded",
        content_type=content_type,
        file_name=file_name,
        file_size=len(content),
        sha256=sha256_bytes(content),
        local_path=str(local_path),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--sleep", type=float, default=0.2, help="Delay between requests in seconds")
    args = parser.parse_args()

    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    pending = get_pending(args.limit)
    print(f"Pending documents: {len(pending)}")

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    ok = 0
    failed = 0
    for idx, row in enumerate(pending, start=1):
        materialfileid = row["materialfileid"]
        url = row["downloadurl"]
        try:
            print(f"[{idx}/{len(pending)}] downloading {materialfileid}")
            download_one(session, materialfileid, url)
            ok += 1
        except Exception as exc:
            failed += 1
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {materialfileid}: {message}")
            mark_status(materialfileid, "failed", error_message=message[:1000])
        time.sleep(args.sleep)

    print(f"Done. downloaded={ok}, failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
