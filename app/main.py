from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable

from fastapi import FastAPI, HTTPException, Query
from sentence_transformers import SentenceTransformer

from app.db import get_conn

MODEL_NAME = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
MODEL_DEVICE = os.getenv("EMBEDDING_DEVICE", "cpu")

app = FastAPI(title="Anonimizeto nolemumu API", version="0.2.0")


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    return SentenceTransformer(MODEL_NAME, device=MODEL_DEVICE)


def vector_to_pg(values: Iterable[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def embed_query(text: str) -> str:
    model = get_embedding_model()
    embedding = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
    return vector_to_pg(embedding)


@app.get("/health")
def health():
    return {"status": "ok", "embedding_model": MODEL_NAME, "embedding_device": MODEL_DEVICE}


@app.get("/stats")
def stats():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) AS total FROM decisions")
            total = cur.fetchone()["total"]
            cur.execute("SELECT count(*) AS downloaded FROM decision_documents WHERE download_status = 'downloaded'")
            downloaded = cur.fetchone()["downloaded"]
            cur.execute("SELECT count(*) AS extracted FROM decision_documents WHERE extracted_text IS NOT NULL AND extracted_text <> ''")
            extracted = cur.fetchone()["extracted"]
            cur.execute("SELECT count(*) AS embeddings FROM decision_embeddings")
            embeddings = cur.fetchone()["embeddings"]
            cur.execute(
                """
                SELECT court, count(*) AS count
                FROM decisions
                WHERE court IS NOT NULL
                GROUP BY court
                ORDER BY count DESC
                LIMIT 10
                """
            )
            top_courts = cur.fetchall()
    return {
        "decisions": total,
        "downloaded_documents": downloaded,
        "extracted_texts": extracted,
        "embedding_chunks": embeddings,
        "top_courts": top_courts,
    }


@app.get("/search")
def search(
    q: str = Query(..., min_length=2),
    court: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    filters = ["doc.extracted_text IS NOT NULL", "doc.extracted_text <> ''"]
    params: dict[str, object] = {"q": q, "limit": limit}

    if court:
        filters.append("d.court ILIKE %(court)s")
        params["court"] = f"%{court}%"
    if year_from:
        filters.append("d.registrationdate >= make_date(%(year_from)s, 1, 1)")
        params["year_from"] = year_from
    if year_to:
        filters.append("d.registrationdate <= make_date(%(year_to)s, 12, 31)")
        params["year_to"] = year_to

    where_sql = " AND ".join(filters)
    sql = f"""
        SELECT
            d.materialfileid::text AS materialfileid,
            d.court,
            d.casenumber,
            d.processtype,
            d.materialtype,
            d.registrationdate,
            d.status,
            ts_rank(doc.search_vector, plainto_tsquery('simple', %(q)s)) AS rank,
            ts_headline('simple', doc.extracted_text, plainto_tsquery('simple', %(q)s),
                'MaxWords=45, MinWords=15, ShortWord=3') AS snippet
        FROM decisions d
        JOIN decision_documents doc ON doc.materialfileid = d.materialfileid
        WHERE {where_sql}
          AND doc.search_vector @@ plainto_tsquery('simple', %(q)s)
        ORDER BY rank DESC, d.registrationdate DESC NULLS LAST
        LIMIT %(limit)s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return {"query": q, "count": len(rows), "results": rows}


@app.get("/semantic-search")
def semantic_search(
    q: str = Query(..., min_length=3),
    court: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = Query(10, ge=1, le=50),
):
    query_vector = embed_query(q)
    filters = ["emb.model_name = %(model_name)s"]
    params: dict[str, object] = {
        "qvec": query_vector,
        "model_name": MODEL_NAME,
        "limit": limit,
    }

    if court:
        filters.append("d.court ILIKE %(court)s")
        params["court"] = f"%{court}%"
    if year_from:
        filters.append("d.registrationdate >= make_date(%(year_from)s, 1, 1)")
        params["year_from"] = year_from
    if year_to:
        filters.append("d.registrationdate <= make_date(%(year_to)s, 12, 31)")
        params["year_to"] = year_to

    where_sql = " AND ".join(filters)
    sql = f"""
        SELECT
            d.materialfileid::text AS materialfileid,
            d.court,
            d.casenumber,
            d.processtype,
            d.materialtype,
            d.registrationdate,
            emb.chunk_index,
            1 - (emb.embedding <=> %(qvec)s::vector) AS similarity,
            left(emb.chunk_text, 1200) AS snippet
        FROM decision_embeddings emb
        JOIN decisions d ON d.materialfileid = emb.materialfileid
        WHERE {where_sql}
        ORDER BY emb.embedding <=> %(qvec)s::vector
        LIMIT %(limit)s
    """

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return {"query": q, "model": MODEL_NAME, "count": len(rows), "results": rows}


@app.get("/similar/{materialfileid}")
def similar_decisions(
    materialfileid: str,
    limit: int = Query(10, ge=1, le=50),
):
    sql = """
        WITH source AS (
            SELECT embedding
            FROM decision_embeddings
            WHERE materialfileid = %s
              AND model_name = %s
            ORDER BY chunk_index
            LIMIT 1
        )
        SELECT
            d.materialfileid::text AS materialfileid,
            d.court,
            d.casenumber,
            d.processtype,
            d.materialtype,
            d.registrationdate,
            emb.chunk_index,
            1 - (emb.embedding <=> source.embedding) AS similarity,
            left(emb.chunk_text, 1200) AS snippet
        FROM source
        JOIN decision_embeddings emb ON emb.model_name = %s
        JOIN decisions d ON d.materialfileid = emb.materialfileid
        WHERE emb.materialfileid <> %s
        ORDER BY emb.embedding <=> source.embedding
        LIMIT %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (materialfileid, MODEL_NAME, MODEL_NAME, materialfileid, limit))
            rows = cur.fetchall()
    return {"materialfileid": materialfileid, "model": MODEL_NAME, "count": len(rows), "results": rows}


@app.get("/decision/{materialfileid}")
def get_decision(materialfileid: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.materialfileid::text AS materialfileid,
                    d.court,
                    d.courtdepartment,
                    d.eclicode,
                    d.casenumber,
                    d.applicationnumber,
                    d.processtype,
                    d.processsubtype,
                    d.materialtype,
                    d.registrationdate,
                    d.status,
                    d.courtinstance,
                    d.downloadurl,
                    doc.content_type,
                    doc.file_name,
                    doc.file_size,
                    doc.local_path,
                    doc.download_status,
                    doc.downloaded_at,
                    doc.text_length,
                    doc.extracted_text
                FROM decisions d
                LEFT JOIN decision_documents doc ON doc.materialfileid = d.materialfileid
                WHERE d.materialfileid = %s
                """,
                (materialfileid,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Decision not found")
    return row


@app.get("/metadata")
def metadata(
    court: str | None = None,
    casenumber: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    filters = []
    params: dict[str, object] = {"limit": limit}
    if court:
        filters.append("court ILIKE %(court)s")
        params["court"] = f"%{court}%"
    if casenumber:
        filters.append("casenumber ILIKE %(casenumber)s")
        params["casenumber"] = f"%{casenumber}%"
    where_sql = "WHERE " + " AND ".join(filters) if filters else ""
    sql = f"""
        SELECT materialfileid::text AS materialfileid, court, casenumber, processtype,
               materialtype, registrationdate, status, downloadurl
        FROM decisions
        {where_sql}
        ORDER BY registrationdate DESC NULLS LAST
        LIMIT %(limit)s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
    return {"count": len(rows), "results": rows}
