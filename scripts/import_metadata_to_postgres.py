#!/usr/bin/env python3
"""Import DAGR anonimizeto nolemumu CSV metadatus PostgreSQL tabula decisions.

Piemers:
  python scripts/import_metadata_to_postgres.py data/archive/kkp_prod_anon_nolemumi_20260604_113746.csv --limit 1000
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from app.db import get_conn


def parse_date(value: str | None):
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt).date()
        except ValueError:
            pass
    return None


def norm(row: dict[str, str], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    inserted = 0
    with args.csv_path.open("r", encoding="utf-8-sig", newline="", errors="replace") as f, get_conn() as conn:
        reader = csv.DictReader(f)
        with conn.cursor() as cur:
            for row in reader:
                if args.limit and inserted >= args.limit:
                    break
                materialfileid = norm(row, "materialfileid")
                downloadurl = norm(row, "downloadurl")
                if not materialfileid or not downloadurl:
                    continue
                cur.execute(
                    """
                    INSERT INTO decisions (
                        materialfileid, court, courtdepartment, eclicode, casenumber,
                        applicationnumber, processtype, processsubtype, materialtype,
                        registrationdate, status, courtinstance, downloadurl, updated_at
                    ) VALUES (
                        %(materialfileid)s, %(court)s, %(courtdepartment)s, %(eclicode)s,
                        %(casenumber)s, %(applicationnumber)s, %(processtype)s,
                        %(processsubtype)s, %(materialtype)s, %(registrationdate)s,
                        %(status)s, %(courtinstance)s, %(downloadurl)s, now()
                    )
                    ON CONFLICT (materialfileid) DO UPDATE SET
                        court = EXCLUDED.court,
                        courtdepartment = EXCLUDED.courtdepartment,
                        eclicode = EXCLUDED.eclicode,
                        casenumber = EXCLUDED.casenumber,
                        applicationnumber = EXCLUDED.applicationnumber,
                        processtype = EXCLUDED.processtype,
                        processsubtype = EXCLUDED.processsubtype,
                        materialtype = EXCLUDED.materialtype,
                        registrationdate = EXCLUDED.registrationdate,
                        status = EXCLUDED.status,
                        courtinstance = EXCLUDED.courtinstance,
                        downloadurl = EXCLUDED.downloadurl,
                        updated_at = now()
                    """,
                    {
                        "materialfileid": materialfileid,
                        "court": norm(row, "court"),
                        "courtdepartment": norm(row, "courtdepartment"),
                        "eclicode": norm(row, "eclicode"),
                        "casenumber": norm(row, "casenumber"),
                        "applicationnumber": norm(row, "applicationnumber"),
                        "processtype": norm(row, "processtype"),
                        "processsubtype": norm(row, "processsubtype"),
                        "materialtype": norm(row, "materialtype"),
                        "registrationdate": parse_date(norm(row, "registrationdate")),
                        "status": norm(row, "status"),
                        "courtinstance": norm(row, "courtinstance"),
                        "downloadurl": downloadurl,
                    },
                )
                inserted += 1

    print(f"Imported {inserted} metadata rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
