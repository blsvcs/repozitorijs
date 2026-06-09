from __future__ import annotations

import subprocess
import sys
from pathlib import Path


DB_PATH = Path("pilot/pilot.sqlite")
APP_PATH = Path("streamlit_app.py")


def ensure_database() -> None:
    if DB_PATH.exists():
        return
    result = subprocess.run(
        [sys.executable, "scripts/download_pilot_artifact.py", "--source", "release", "--force"],
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"Neizdevās lejupielādēt pilotdatubāzi: {details}")


def ensure_sqlite_search_guard() -> None:
    source = APP_PATH.read_text(encoding="utf-8")
    if "def fallback_search(" in source and "q = fts_query(query)" in source:
        return

    if "def fts_query(q: str) -> str:" not in source:
        source = source.replace(
            "\n\ndef clean_text(value: str | None) -> str:\n",
            "\n\ndef fts_query(q: str) -> str:\n"
            "    terms = clean_query(q).split()\n"
            "    return \" \".join(f'\"{term.replace(chr(34), chr(34) * 2)}\"' for term in terms)\n"
            "\n\n"
            "def clean_text(value: str | None) -> str:\n",
            1,
        )

    source = source.replace("    q = clean_query(query)\n", "    q = fts_query(query)\n", 1)
    source = source.replace(
        "        return [dict(r) for r in conn.execute(sql, params).fetchall()]\n"
        "\n\n@st.cache_data(show_spinner=False)\n"
        "def answer_question",
        "        try:\n"
        "            return [dict(r) for r in conn.execute(sql, params).fetchall()]\n"
        "        except sqlite3.OperationalError:\n"
        "            return fallback_search(conn, query, court, topic, limit)\n"
        "\n\n"
        "def fallback_search(\n"
        "    conn: sqlite3.Connection,\n"
        "    query: str,\n"
        "    court: str | None,\n"
        "    topic: str | None,\n"
        "    limit: int,\n"
        ") -> list[dict]:\n"
        "    terms = clean_query(query).split()\n"
        "    if not terms:\n"
        "        return []\n"
        "    joins = [\"join decisions d on d.materialfileid = docs.materialfileid\"]\n"
        "    where = [\"docs.extracted_text is not null\", \"docs.extracted_text <> ''\"]\n"
        "    params: list[object] = []\n"
        "    for term in terms:\n"
        "        where.append(\"docs.extracted_text like ?\")\n"
        "        params.append(f\"%{term}%\")\n"
        "    if court:\n"
        "        where.append(\"d.court = ?\")\n"
        "        params.append(court)\n"
        "    if topic:\n"
        "        joins.append(\"join case_topics t on t.materialfileid = d.materialfileid\")\n"
        "        where.append(\"t.topic = ?\")\n"
        "        params.append(topic)\n"
        "    params.append(limit)\n"
        "    topic_select = (\n"
        "        \"t.topic as topic, t.score as topic_score, coalesce(t.matched_keywords, '') as matched_keywords,\"\n"
        "        if topic\n"
        "        else \"coalesce(t2.topic, '') as topic, coalesce(t2.score, 0) as topic_score, coalesce(t2.matched_keywords, '') as matched_keywords,\"\n"
        "    )\n"
        "    sql = f\"\"\"\n"
        "    select d.materialfileid, d.court, d.casenumber, d.processtype, d.materialtype,\n"
        "           d.registrationdate, d.downloadurl,\n"
        "           {topic_select}\n"
        "           substr(docs.extracted_text, 1, 700) as snippet,\n"
        "           0 as rank\n"
        "    from documents docs\n"
        "    {' '.join(joins)}\n"
        "    {\"left join case_topics t2 on t2.materialfileid = d.materialfileid\" if not topic else \"\"}\n"
        "    where {' and '.join(where)}\n"
        "    limit ?\n"
        "    \"\"\"\n"
        "    return [dict(r) for r in conn.execute(sql, params).fetchall()]\n"
        "\n\n@st.cache_data(show_spinner=False)\n"
        "def answer_question",
        1,
    )
    APP_PATH.write_text(source, encoding="utf-8")


ensure_database()
ensure_sqlite_search_guard()

import streamlit_app  # noqa: E402,F401
