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


def plain_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def clean_snippet(value: str | None) -> str:
    value = plain_text(value)
    if not value:
        return ""
    value = html.escape(value)
    value = value.replace("[", "<mark>").replace("]", "</mark>")
    return value


def make_essence(row: dict, snippet: str, full_text: str) -> str:
    text = plain_text((full_text or snippet)[:3000]).lower()
    material_type = row.get("materialtype") or ""
    process = row.get("processtype") or ""

    if any(w in text for w in ["kredīt", "aizdev", "parād", "procent"]):
        topic = "Strīds par kredīta, aizdevuma, parāda vai procentu piedziņu."
    elif any(w in text for w in ["darba līgum", "darba samaks", "atlaišan"]):
        topic = "Strīds saistīts ar darba tiesiskajām attiecībām."
    elif any(w in text for w in ["būvniec", "būvatļauj", "būvvald", "teritorijas plānojum"]):
        topic = "Strīds saistīts ar būvniecību vai teritorijas plānošanu."
    elif any(w in text for w in ["iepirkum", "piedāvājum", "pretendent", "pasūtītāj"]):
        topic = "Strīds saistīts ar publisko iepirkumu."
    elif any(w in text for w in ["uzturlīdzek", "laulīb", "aizgādīb", "saskarsmes tiesīb"]):
        topic = "Strīds saistīts ar ģimenes tiesībām."
    elif process:
        topic = f"Lieta saistīta ar procesu: {process}."
    else:
        topic = "Lietas būtība automātiski nosakāma pēc fragmenta un pilnā teksta."

    if "prasība apmierināta" in text or "prasību apmierināt" in text:
        result = "Rezultāts: prasība apmierināta."
    elif "prasība noraidīta" in text or "prasību noraidīt" in text:
        result = "Rezultāts: prasība noraidīta."
    elif "daļēji apmierin" in text:
        result = "Rezultāts: prasība apmierināta daļēji."
    elif material_type:
        result = f"Nolēmuma veids: {material_type}."
    else:
        result = "Rezultāts automātiski nav droši nosakāms."

    return topic + " " + result


@st.cache_data(show_spinner=False)
def get_stats(db_path: str) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        stats = {
            "metadata": conn.execute("SELECT count(*) FROM decisions").fetchone()[0],
            "documents": conn.execute("SELECT count(*) FROM documents").fetchone()[0],
            "downloaded": conn.execute("SELECT count(*) FROM documents WHERE download_status = 'downloaded'").fetchone()[0],
            "with_text": conn.execute("SELECT count(*) FROM documents WHERE extracted_text IS NOT NULL AND extracted_text <> ''").fetchone()[0],
            "ai_summaries": 0,
        }
        try:
            stats["ai_summaries"] = conn.execute(
                "SELECT count(*) FROM ai_summaries WHERE error_message IS NULL"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            stats["ai_summaries"] = 0
        return stats


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
            d.downloadurl,
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


@st.cache_data(show_spinner=False)
def get_ai_summary(db_path: str, materialfileid: str) -> dict[str, str] | None:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT summary, legal_issue, court_reasoning, outcome, model_name, created_at
                FROM ai_summaries
                WHERE materialfileid = ? AND error_message IS NULL
                """,
                (materialfileid,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None

    return dict(row) if row else None


def render_ai_summary(ai_summary: dict[str, str] | None, fallback: str) -> str:
    if not ai_summary:
        return f"<strong>Būtība:</strong><br>{html.escape(fallback)}"

    parts = []
    if ai_summary.get("summary"):
        parts.append(f"<strong>AI kopsavilkums:</strong><br>{html.escape(ai_summary['summary'])}")
    if ai_summary.get("legal_issue"):
        parts.append(f"<strong>Juridiskais jautājums:</strong><br>{html.escape(ai_summary['legal_issue'])}")
    if ai_summary.get("court_reasoning"):
        parts.append(f"<strong>Tiesas secinājums:</strong><br>{html.escape(ai_summary['court_reasoning'])}")
    if ai_summary.get("outcome"):
        parts.append(f"<strong>Rezultāts:</strong><br>{html.escape(ai_summary['outcome'])}")

    if not parts:
        return f"<strong>Būtība:</strong><br>{html.escape(fallback)}"
    return "<br><br>".join(parts)


st.markdown(
    """
    <style>
    mark { background-color: #fff3a3; padding: 0.05rem 0.18rem; border-radius: 0.2rem; }
    .result-card { border: 1px solid #e6e8ef; border-radius: 0.75rem; padding: 1rem 1.1rem; margin-bottom: 0.85rem; background: #ffffff; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
    .result-title { font-size: 1.05rem; font-weight: 700; margin-bottom: 0.25rem; }
    .result-meta { color: #5f6673; font-size: 0.92rem; margin-bottom: 0.65rem; }
    .result-essence { background: #f6f8fb; border-left: 4px solid #7c9cff; border-radius: 0.45rem; padding: 0.75rem 0.85rem; margin-bottom: 0.75rem; line-height: 1.45; }
    .ai-badge { display: inline-block; background: #edf7ed; color: #1f7a1f; border: 1px solid #b7e0b7; border-radius: 999px; padding: 0.1rem 0.5rem; font-size: 0.82rem; margin-left: 0.4rem; }
    .rule-badge { display: inline-block; background: #f6f6f6; color: #666; border: 1px solid #ddd; border-radius: 999px; padding: 0.1rem 0.5rem; font-size: 0.82rem; margin-left: 0.4rem; }
    .result-snippet { font-size: 1rem; line-height: 1.55; color: #242936; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚖️ Latvijas tiesu nolēmumu pilots")
st.caption("GitHub-only MVP: SQLite datubāze, PDF teksta ekstrakcija, pilnteksta meklēšana un kešoti AI kopsavilkumi.")

if not DB_PATH.exists():
    st.error(f"Datubāze nav atrasta: `{DB_PATH}`")
    st.info("Lejupielādē GitHub Actions artefaktu `pilot-dataset` un novieto `pilot.sqlite` mapē `pilot/`.")
    st.stop()

stats = get_stats(str(DB_PATH))
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Metadati", stats["metadata"])
col2.metric("Dokumenti", stats["documents"])
col3.metric("Lejupielādēti", stats["downloaded"])
col4.metric("Ar tekstu", stats["with_text"])
col5.metric("AI kopsavilkumi", stats["ai_summaries"])

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
    download_url = row.get("downloadurl") or ""
    raw_snippet = row.get("snippet") or ""
    snippet = clean_snippet(raw_snippet) or "Fragments nav pieejams."
    full_text = get_full_text(str(DB_PATH), row["materialfileid"])
    fallback_essence = make_essence(row, raw_snippet, full_text)
    ai_summary = get_ai_summary(str(DB_PATH), row["materialfileid"])
    summary_html = render_ai_summary(ai_summary, fallback_essence)
    badge = "<span class='ai-badge'>AI</span>" if ai_summary else "<span class='rule-badge'>noteikumi</span>"

    st.markdown(
        f"""
        <div class="result-card">
            <div class="result-title">#{index} · Lieta {html.escape(case_number)} {badge}</div>
            <div class="result-meta">{html.escape(court_name)} · {html.escape(str(date))} · {html.escape(process)} · {html.escape(material_type)}</div>
            <div class="result-essence">{summary_html}</div>
            <div class="result-snippet"><strong>Fragments:</strong><br>“{snippet}”</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col_pdf, col_text = st.columns([1, 3])
    with col_pdf:
        if download_url:
            st.link_button("📄 Atvērt PDF", download_url)
        else:
            st.caption("PDF saite nav pieejama")

    with col_text:
        with st.expander("Atvērt pilnu nolēmuma tekstu"):
            st.write(f"**MaterialFileId:** `{row.get('materialfileid')}`")
            if full_text:
                st.text_area("Pilns nolēmuma teksts", full_text[:100000], height=500)
            else:
                st.warning("Šim ierakstam pilns teksts nav pieejams.")
