from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path

import streamlit as st

DB_PATH = Path("pilot/pilot.sqlite")

st.set_page_config(
    page_title="Latvijas tiesu nolēmumu pilots",
    page_icon="⚖️",
    layout="wide",
)


def clean_fts_query(query: str) -> str:
    words = re.findall(r"[\wāčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ]+", query, flags=re.UNICODE)
    return " ".join(words)


def clean_snippet(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = html.escape(value)
    value = value.replace("[", "<mark>").replace("]", "</mark>")
    return value


@st.cache_data(show_spinner=False)
def get_stats(db_path: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            "metadata": conn.execute("SELECT count(*) FROM decisions").fetchone()[0],
            "documents": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
            "downloaded": conn.execute("SELECT count(*) FROM documents WHERE download_status = 'downloaded'").fetchone()[0],
            "with_text": conn.execute("SELECT count(*) FROM documents WHERE extracted_text IS NOT NULL AND extracted_text <> ''").fetchone()[0],
        }


@st.cache_data(show_spinner=False)
def get_courts(db_path: str) -> list[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT court
            FROM decisions
            WHERE court IS NOT NULL AND court <> ''
            ORDER BY court
            """
        ).fetchall()
    return [row[0] for row in rows]


@st.cache_data(show_spinner=False)
def search_decisions(db_path: str, query: str, court: str | None, limit: int):
    fts_query = clean_fts_query(query)
    if not fts_query:
        return []

    params: list[object] = [fts_query]
    where = ["documents_fts MATCH ?"]
    if court:
        where.append("d.court = ?")
        params.append(court)
    params.append(limit)

    sql = f"""
        SELECT
            d.materialfileid,
            d.court,
            d.casenumber,
            d.processtype,
            d.materialtype,
            d.registrationdate,
            snippet(documents_fts, 3, '[', ']', ' ... ', 42) AS snippet,
            bm25(documents_fts) AS rank
        FROM documents_fts
        JOIN decisions d ON d.materialfileid = documents_fts.materialfileid
        WHERE {' AND '.join(where)}
        ORDER BY rank
        LIMIT ?
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


@st.cache_data(show_spinner=False)
def get_full_text(db_path: str, materialfileid: str) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT extracted_text FROM documents WHERE materialfileid = ?",
            (materialfileid,),
        ).fetchone()
    return row[0] if row and row[0] else ""


st.markdown(
    """
    <style>
    mark {
        background-color: #fff3a3;
        padding: 0.05rem 0.18rem;
        border-radius: 0.2rem;
    }
    .result-card {
        border: 1px solid #e6e8ef;
        border-radius: 0.75rem;
        padding: 1rem 1.1rem;
        margin-bottom: 0.85rem;
        background: #ffffff;
        box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }
    .result-title {
        font-size: 1.05rem;
        font-weight: 700;
        margin-bottom: 0.25rem;
    }
    .result-meta {
        color: #5f6673;
        font-size: 0.92rem;
        margin-bottom: 0.65rem;
    }
    .result-snippet {
        font-size: 1rem;
        line-height: 1.55;
        color: #242936;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚖️ Latvijas tiesu nolēmumu pilots")
st.caption("GitHub-only MVP: SQLite datubāze, PDF teksta ekstrakcija un pilnteksta meklēšana.")

if not DB_PATH.exists():
    st.error(f"Datubāze nav atrasta: `{DB_PATH}`")
    st.info("Lejupielādē GitHub Actions artefaktu `pilot-dataset` un novieto `pilot.sqlite` mapē `pilot/`.")
    st.stop()

stats = get_stats(str(DB_PATH))
col1, col2, col3, col4 = st.columns(4)
col1.metric("Metadati", stats["metadata"])
col2.metric("Dokumenti", stats["documents"])
col3.metric("Lejupielādēti", stats["downloaded"])
col4.metric("Ar tekstu", stats["with_text"])

with st.sidebar:
    st.header("Meklēšana")
    query = st.text_input("Meklējamā frāze", placeholder="piemēram: kredīta parāds")
    limit = st.slider("Rezultātu skaits", min_value=5, max_value=50, value=10, step=5)
    courts = get_courts(str(DB_PATH))
    court = st.selectbox("Tiesa", ["Visas"] + courts)
    selected_court = None if court == "Visas" else court
    st.divider()
    st.caption("Padoms: sākumā izmanto vienu vai divus vārdus, piemēram, `parāds`, `būvniecība`, `darba līgums`.")

if not query:
    st.info("Ieraksti meklējamo frāzi kreisajā pusē, lai sāktu.")
    st.stop()

rows = search_decisions(str(DB_PATH), query, selected_court, limit)
st.subheader(f"Atrasti rezultāti: {len(rows)}")

if not rows:
    st.warning("Nav rezultātu. Pamēģini īsāku vai citu meklēšanas frāzi.")
    st.stop()

for index, row in enumerate(rows, start=1):
    case_number = row.get("casenumber") or "Bez lietas numura"
    court_name = row.get("court") or "-"
    date = row.get("registrationdate") or "-"
    process = row.get("processtype") or "-"
    material_type = row.get("materialtype") or "-"
    snippet = clean_snippet(row.get("snippet")) or "Fragments nav pieejams."

    st.markdown(
        f"""
        <div class="result-card">
            <div class="result-title">#{index} · Lieta {html.escape(case_number)}</div>
            <div class="result-meta">
                {html.escape(court_name)} · {html.escape(str(date))} · {html.escape(process)} · {html.escape(material_type)}
            </div>
            <div class="result-snippet">“{snippet}”</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.expander("Atvērt pilnu nolēmuma tekstu"):
        st.write(f"**MaterialFileId:** `{row.get('materialfileid')}`")
        full_text = get_full_text(str(DB_PATH), row["materialfileid"])
        if full_text:
            st.text_area("Pilns nolēmuma teksts", full_text[:100000], height=500)
        else:
            st.warning("Šim ierakstam pilns teksts nav pieejams.")
