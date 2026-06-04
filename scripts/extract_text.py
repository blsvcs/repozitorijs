#!/usr/bin/env python3
"""Izvelk tekstu no lejupieladetajiem PDF failiem un saglaba PostgreSQL.

Piemers:
  python scripts/extract_text.py --limit 1000

Skripts apstrada tikai tos dokumentus, kuri ir download_status='downloaded'
un kuriem extracted_text vel nav aizpildits.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from pypdf import PdfReader

from app.db import get_conn


def get_pending(limit: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT materialfileid::text AS materialfileid, local_path
                FROM decision_documents
                WHERE download_status = 'downloaded'
                  AND local_path IS NOT NULL
                  AND (extracted_text IS NULL OR extracted_text = '')
                ORDER BY downloaded_at NULLS LAST, materialfileid
                LIMIT %s
                """,
                (limit,),
            )
            return list(cur.fetchall())


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            text = f"\n[PAGE {page_no} TEXT EXTRACTION ERROR: {type(exc).__name__}: {exc}]\n"
        if text.strip():
            parts.append(text)
    return "\n\n".join(parts).strip()


def save_text(materialfileid: str, text: str, error_message: str | None = None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if error_message:
                cur.execute(
                    """
                    UPDATE decision_documents
                    SET error_message = %s
                    WHERE materialfileid = %s
                    """,
                    (error_message[:1000], materialfileid),
                )
            else:
                cur.execute(
                    """
                    UPDATE decision_documents
                    SET extracted_text = %s,
                        error_message = NULL
                    WHERE materialfileid = %s
                    """,
                    (text, materialfileid),
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    pending = get_pending(args.limit)
    print(f"Pending extracted_text documents: {len(pending)}")

    ok = 0
    failed = 0
    empty = 0

    for idx, row in enumerate(pending, start=1):
        materialfileid = row["materialfileid"]
        path = Path(row["local_path"])
        print(f"[{idx}/{len(pending)}] extracting {materialfileid}: {path}")
        try:
            if not path.exists():
                raise FileNotFoundError(str(path))
            text = extract_pdf_text(path)
            if not text:
                empty += 1
                save_text(materialfileid, "", "No text extracted. OCR may be required.")
            else:
                save_text(materialfileid, text)
                ok += 1
        except Exception as exc:
            failed += 1
            message = f"{type(exc).__name__}: {exc}"
            print(f"FAILED {materialfileid}: {message}")
            save_text(materialfileid, "", message)

    print(f"Done. extracted={ok}, empty={empty}, failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
