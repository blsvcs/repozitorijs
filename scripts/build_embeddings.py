#!/usr/bin/env python3
"""Izveido lokalos BAAI/bge-m3 embeddingus nolēmumu tekstiem.

Piemers:
  python scripts/build_embeddings.py --limit-docs 100 --device cpu

Piezimes:
- Pirmaja palaisana sentence-transformers lejupielades modeli no Hugging Face.
- bge-m3 embedding dimensija ir 1024, tas atbilst db/schema.sql.
- Skripts ir resume-safe: tas neparraksta jau izveidotus chunkus tam pasam modelim.
"""

from __future__ import annotations

import argparse
import re
from typing import Iterable

from sentence_transformers import SentenceTransformer

from app.db import get_conn

MODEL_NAME = "BAAI/bge-m3"
DEFAULT_CHUNK_CHARS = 1800
DEFAULT_OVERLAP_CHARS = 250


def normalize_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + chunk_chars, text_len)
        chunk = text[start:end]

        # Try to end near a paragraph or sentence boundary.
        if end < text_len:
            paragraph_break = chunk.rfind("\n\n")
            sentence_break = max(chunk.rfind(". "), chunk.rfind("! "), chunk.rfind("? "))
            boundary = max(paragraph_break, sentence_break)
            if boundary > int(chunk_chars * 0.55):
                chunk = chunk[: boundary + 1]
                end = start + boundary + 1

        chunk = chunk.strip()
        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break
        start = max(0, end - overlap_chars)

    return chunks


def get_documents(limit_docs: int, model_name: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT doc.materialfileid::text AS materialfileid, doc.extracted_text
                FROM decision_documents doc
                WHERE doc.extracted_text IS NOT NULL
                  AND doc.extracted_text <> ''
                  AND NOT EXISTS (
                      SELECT 1
                      FROM decision_embeddings emb
                      WHERE emb.materialfileid = doc.materialfileid
                        AND emb.model_name = %s
                  )
                ORDER BY doc.materialfileid
                LIMIT %s
                """,
                (model_name, limit_docs),
            )
            return list(cur.fetchall())


def vector_to_pg(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def save_embeddings(materialfileid: str, chunks: list[str], embeddings, model_name: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            for index, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                cur.execute(
                    """
                    INSERT INTO decision_embeddings (
                        materialfileid, chunk_index, chunk_text, embedding, model_name
                    ) VALUES (%s, %s, %s, %s::vector, %s)
                    ON CONFLICT (materialfileid, chunk_index, model_name) DO NOTHING
                    """,
                    (materialfileid, index, chunk, vector_to_pg(embedding), model_name),
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-docs", type=int, default=100)
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--overlap-chars", type=int, default=DEFAULT_OVERLAP_CHARS)
    parser.add_argument("--device", default="cpu", help="cpu, cuda, mps")
    parser.add_argument("--model", default=MODEL_NAME)
    args = parser.parse_args()

    print(f"Loading embedding model: {args.model} on {args.device}")
    model = SentenceTransformer(args.model, device=args.device)

    docs = get_documents(args.limit_docs, args.model)
    print(f"Documents to embed: {len(docs)}")

    total_chunks = 0
    for i, row in enumerate(docs, start=1):
        materialfileid = row["materialfileid"]
        chunks = chunk_text(row["extracted_text"], args.chunk_chars, args.overlap_chars)
        if not chunks:
            print(f"[{i}/{len(docs)}] {materialfileid}: no chunks")
            continue

        print(f"[{i}/{len(docs)}] {materialfileid}: {len(chunks)} chunks")
        embeddings = model.encode(
            chunks,
            normalize_embeddings=True,
            batch_size=8,
            show_progress_bar=False,
        )
        save_embeddings(materialfileid, chunks, embeddings, args.model)
        total_chunks += len(chunks)

    print(f"Done. embedded_documents={len(docs)}, embedded_chunks={total_chunks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
