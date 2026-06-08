#!/usr/bin/env python3
"""Build a free local semantic index for similar-case search.

This stores sentence-transformer embeddings in pilot.sqlite, without any paid API.

Usage:
  python scripts/build_semantic_index.py --limit 200
  python scripts/build_semantic_index.py --limit 1500
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

DB_PATH = Path("pilot/pilot.sqlite")
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def configure_output() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_embeddings (
    materialfileid TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    embedding BLOB NOT NULL,
    text_length INTEGER,
    created_at TEXT,
    FOREIGN KEY(materialfileid) REFERENCES decisions(materialfileid)
);

CREATE INDEX IF NOT EXISTS idx_semantic_embeddings_model
ON semantic_embeddings(model_name);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_pending(conn: sqlite3.Connection, model_name: str, limit: int):
    return conn.execute(
        """
        SELECT
            d.materialfileid,
            d.court,
            d.casenumber,
            d.processtype,
            d.materialtype,
            doc.extracted_text
        FROM documents doc
        JOIN decisions d ON d.materialfileid = doc.materialfileid
        LEFT JOIN semantic_embeddings emb
          ON emb.materialfileid = doc.materialfileid
         AND emb.model_name = ?
        WHERE doc.download_status = 'downloaded'
          AND doc.extracted_text IS NOT NULL
          AND doc.extracted_text <> ''
          AND emb.materialfileid IS NULL
        ORDER BY d.registrationdate, d.materialfileid
        LIMIT ?
        """,
        (model_name, limit),
    ).fetchall()


def build_embedding_text(row: sqlite3.Row) -> str:
    text = row["extracted_text"] or ""
    meta = "\n".join(
        [
            f"Tiesa: {row['court'] or ''}",
            f"Lieta: {row['casenumber'] or ''}",
            f"Process: {row['processtype'] or ''}",
            f"Veids: {row['materialtype'] or ''}",
        ]
    )
    # First part of the decision is usually enough for a useful pilot index.
    return (meta + "\n\n" + text[:7000]).strip()


def save_embedding(conn: sqlite3.Connection, materialfileid: str, model_name: str, vector: np.ndarray, text_length: int) -> None:
    vector = vector.astype("float32")
    conn.execute(
        """
        INSERT INTO semantic_embeddings(materialfileid, model_name, embedding, text_length, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(materialfileid) DO UPDATE SET
            model_name = excluded.model_name,
            embedding = excluded.embedding,
            text_length = excluded.text_length,
            created_at = excluded.created_at
        """,
        (materialfileid, model_name, vector.tobytes(), text_length, now_iso()),
    )


def main() -> int:
    configure_output()

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="Show pending rows without loading the embedding model.")
    parser.add_argument("--status", action="store_true", help="Print current semantic index status and exit.")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        init_db(conn)
        total_ready = conn.execute("SELECT count(*) FROM semantic_embeddings WHERE model_name = ?", (args.model,)).fetchone()[0]
        if args.status:
            pending_total = conn.execute(
                """
                SELECT count(*)
                FROM documents doc
                LEFT JOIN semantic_embeddings emb
                  ON emb.materialfileid = doc.materialfileid
                 AND emb.model_name = ?
                WHERE doc.download_status = 'downloaded'
                  AND doc.extracted_text IS NOT NULL
                  AND doc.extracted_text <> ''
                  AND emb.materialfileid IS NULL
                """,
                (args.model,),
            ).fetchone()[0]
            print(f"Semantic embeddings ready: {total_ready}")
            print(f"Pending semantic embeddings: {pending_total}")
            print(f"Model: {args.model}")
            return 0

        rows = get_pending(conn, args.model, args.limit)
        print(f"Pending semantic embeddings: {len(rows)}")
        if args.dry_run:
            if rows:
                preview = build_embedding_text(rows[0])
                print("\nEmbedding text preview:")
                print(preview[:3000])
            return 0
        if not rows:
            return 0

        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise SystemExit("sentence-transformers package is not installed. Run: pip install -r requirements.txt") from exc

        model = SentenceTransformer(args.model)

        for start in range(0, len(rows), args.batch_size):
            batch = rows[start : start + args.batch_size]
            texts = [build_embedding_text(row) for row in batch]
            vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

            for row, vector, text in zip(batch, vectors, texts):
                save_embedding(conn, row["materialfileid"], args.model, np.asarray(vector), len(text))
            conn.commit()
            print(f"Saved {min(start + len(batch), len(rows))}/{len(rows)} embeddings")

        total = conn.execute("SELECT count(*) FROM semantic_embeddings WHERE model_name = ?", (args.model,)).fetchone()[0]
        print(f"Semantic embeddings ready: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
