from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from app.db import get_conn

app = FastAPI(title="Anonimizeto nolemumu API", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


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
