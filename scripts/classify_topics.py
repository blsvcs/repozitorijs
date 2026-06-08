#!/usr/bin/env python3
"""Classify Latvian court decisions into simple legal topics.

Free, local, rule-based classifier. Stores results in pilot.sqlite.

Usage:
  python scripts/classify_topics.py --limit 1500
  python scripts/classify_topics.py --refresh --limit 1500
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("pilot/pilot.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS case_topics (
    materialfileid TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    score INTEGER NOT NULL,
    matched_keywords TEXT,
    created_at TEXT,
    FOREIGN KEY(materialfileid) REFERENCES decisions(materialfileid)
);

CREATE INDEX IF NOT EXISTS idx_case_topics_topic ON case_topics(topic);
"""

TOPICS: list[tuple[str, list[str]]] = [
    ("Kredīti un parādi", ["kredīt", "aizdev", "parād", "parāda piedziņ", "parāda atmaksa", "piedziņ", "procent", "līgumsod", "galvojum", "cesij"]),
    ("Darba tiesības", ["darba līgum", "darba samaks", "atlaišan", "uzteikum", "darba devēj", "darbiniek", "disciplinār", "darba tiesisk", "atjaunošanu darbā", "vidējās izpeļņas"]),
    ("Ģimenes tiesības", ["uzturlīdzek", "laulīb", "šķiršan", "aizgādīb", "saskarsmes tiesīb", "bērna", "bērnu", "paternitāt"]),
    ("Būvniecība un plānošana", ["būvniec", "būvatļauj", "būvvald", "detālplānojum", "teritorijas plānojum", "būvprojekt", "ekspluatācijā"]),
    ("Nekustamais īpašums", ["nekustam", "īpašum", "zemesgrāmat", "servitūt", "kopīpaš", "valdījum", "īres līgum", "dzīvokļ"]),
    ("Publiskie iepirkumi", ["iepirkum", "pretendent", "piedāvājum", "pasūtītāj", "iepirkuma komis", "publisko iepirkumu"]),
    ("Nodokļi", ["nodok", "valsts ieņēmumu dienest", "vid", "pievienotās vērtības", "ienākuma nodok", "akcīzes"]),
    ("Administratīvo pārkāpumu lietas", ["administratīvā pārkāpuma", "administratīvo pārkāpumu", "administratīvais sods", "administratīvā soda", "pie administratīvās atbildības", "lapk"]),
    ("Administratīvās lietas", ["administratīv", "iestādes lēm", "pārsūdz", "administratīvā akta", "publisko tiesību", "valsts pārvald"]),
    ("Krimināllietas", ["krimināl", "apsūdz", "sods", "noziedz", "cietuš", "probācij", "brīvības atņem"]),
    ("Maksātnespēja", ["maksātnespēj", "maksātnespējas process", "administratora", "kreditora prasījum", "parādnieka maksātnespēj"]),
    ("Apdrošināšana", ["apdrošin", "apdrošināšanas atlīdz", "apdrošināšanas gadīj", "transportlīdzekļu apdrošin"]),
    ("Izglītība", ["skola", "vidusskol", "augstskol", "izglītīb", "audzēkn", "student", "mācību"]),
    ("Patērētāju tiesības", ["patērētāj", "prece", "pakalpojum", "garantij", "distances līgum", "netaisnīgi līguma noteikumi"]),
]

PROCESS_FALLBACKS: list[tuple[str, str]] = [
    ("administratīvo pārkāpumu", "Administratīvo pārkāpumu lietas"),
    ("administratīvā pārkāpuma", "Administratīvo pārkāpumu lietas"),
    ("administratīv", "Administratīvās lietas"),
    ("krimināl", "Krimināllietas"),
    ("civil", "Civillietas"),
]

LOW_CONFIDENCE_SCORE = 1


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.lower()).strip()


def classify(text: str, process: str = "", material_type: str = "") -> tuple[str, int, list[str]]:
    value = normalize(f"{process} {material_type} {text[:10000]}")
    process_value = normalize(process)
    best_topic = "Citi"
    best_score = 0
    best_matches: list[str] = []

    for topic, keywords in TOPICS:
        matches = [kw for kw in keywords if kw in value]
        score = len(matches)
        if score > best_score:
            best_topic = topic
            best_score = score
            best_matches = matches

    if best_score <= LOW_CONFIDENCE_SCORE:
        for process_key, process_topic in PROCESS_FALLBACKS:
            if process_key in process_value:
                return process_topic, max(best_score, 1), ["process"] + best_matches

    return best_topic, best_score, best_matches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--refresh", action="store_true", help="Reclassify existing rows as well as new rows.")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        if args.refresh:
            conn.execute("DELETE FROM case_topics")
            conn.commit()
        rows = conn.execute(
            """
            SELECT d.materialfileid, d.processtype, d.materialtype, doc.extracted_text
            FROM documents doc
            JOIN decisions d ON d.materialfileid = doc.materialfileid
            LEFT JOIN case_topics t ON t.materialfileid = d.materialfileid
            WHERE doc.extracted_text IS NOT NULL
              AND doc.extracted_text <> ''
              AND t.materialfileid IS NULL
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
        print(f"Pending topic classifications: {len(rows)}")

        for idx, row in enumerate(rows, 1):
            topic, score, matches = classify(row["extracted_text"], row["processtype"] or "", row["materialtype"] or "")
            conn.execute(
                """
                INSERT OR REPLACE INTO case_topics(materialfileid, topic, score, matched_keywords, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row["materialfileid"], topic, score, ", ".join(matches), now_iso()),
            )
            if idx % 100 == 0:
                conn.commit()
                print(f"Classified {idx}/{len(rows)}")
        conn.commit()

        print("Topic counts:")
        for topic, count in conn.execute("SELECT topic, count(*) FROM case_topics GROUP BY topic ORDER BY count(*) DESC"):
            print(f"- {topic}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
