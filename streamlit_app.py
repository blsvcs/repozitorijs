from __future__ import annotations

import html
import re
import sqlite3
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st


BUNDLED_DB_PATH = Path("pilot/pilot.sqlite")
DB_PATH = Path(tempfile.gettempdir()) / "repozitorijs-pilot.sqlite"
RELEASE_DATASET_URL = "https://github.com/blsvcs/repozitorijs/releases/download/pilot-dataset-latest/pilot-dataset.zip"
MIN_RELEASE_DOCUMENTS = 2000
MIN_RELEASE_TOPICS = 1000
EXAMPLE_QUERIES = [
    "kredīta parāds",
    "kredīta procentu piedziņu",
    "darba samaksa",
    "būvatļauja",
    "nodokļu parāds",
    "iepirkums",
]
TOKEN_RE = re.compile(r"[\wāčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ]+", flags=re.UNICODE)


st.set_page_config(page_title="Latvijas tiesu nolēmumu pilots", page_icon="⚖️", layout="wide")


def clean_query(value: str) -> str:
    return " ".join(TOKEN_RE.findall(value or ""))


def fts_query(value: str) -> str:
    terms = clean_query(value).split()
    return " ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()


def clean_snippet(value: str | None) -> str:
    text = html.escape(clean_text(value))
    return text.replace("[", "<mark>").replace("]", "</mark>")


def pct(part: int, total: int) -> float:
    return round((part / total) * 100, 1) if total else 0.0


def safe_count(conn: sqlite3.Connection, sql: str) -> int:
    try:
        return int(conn.execute(sql).fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def database_needs_refresh(path: Path) -> bool:
    if not path.exists():
        return True
    try:
        with sqlite3.connect(path) as conn:
            documents = safe_count(conn, "select count(*) from documents")
            with_text = safe_count(conn, "select count(*) from documents where extracted_text is not null and extracted_text<>''")
            topics_count = safe_count(conn, "select count(*) from case_topics")
        return documents < MIN_RELEASE_DOCUMENTS or with_text < MIN_RELEASE_DOCUMENTS or topics_count < MIN_RELEASE_TOPICS
    except sqlite3.Error:
        return True


def install_database_from_release(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        archive_path = tmp / "pilot-dataset.zip"
        extracted_path = tmp / "pilot.sqlite"
        with urllib.request.urlopen(RELEASE_DATASET_URL, timeout=180) as response:
            archive_path.write_bytes(response.read())
        with zipfile.ZipFile(archive_path) as archive:
            archive.extract("pilot.sqlite", tmp)
        extracted_path.replace(path)


def prepare_database() -> tuple[Path, str]:
    if DB_PATH.exists() and not database_needs_refresh(DB_PATH):
        return DB_PATH, "runtime"

    if BUNDLED_DB_PATH.exists() and not database_needs_refresh(BUNDLED_DB_PATH):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BUNDLED_DB_PATH, DB_PATH)
        st.cache_data.clear()
        return DB_PATH, "bundled"

    try:
        install_database_from_release(DB_PATH)
        st.cache_data.clear()
        return DB_PATH, "release"
    except Exception:
        if DB_PATH.exists():
            return DB_PATH, "stale-runtime"
        if BUNDLED_DB_PATH.exists():
            return BUNDLED_DB_PATH, "stale-bundled"
        raise


@st.cache_data(show_spinner=False)
def stats(db: str) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        return {
            "metadata": safe_count(conn, "select count(*) from decisions"),
            "documents": safe_count(conn, "select count(*) from documents"),
            "downloaded": safe_count(conn, "select count(*) from documents where download_status='downloaded'"),
            "with_text": safe_count(conn, "select count(*) from documents where extracted_text is not null and extracted_text<>''"),
            "ai": safe_count(conn, "select count(*) from ai_summaries where error_message is null"),
            "semantic": safe_count(conn, "select count(*) from semantic_embeddings"),
            "topics": safe_count(conn, "select count(*) from case_topics"),
            "fts": safe_count(conn, "select count(*) from documents_fts"),
        }


@st.cache_data(show_spinner=False)
def courts(db: str) -> list[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "select distinct court from decisions where court is not null and court<>'' order by court"
        ).fetchall()
    return [row[0] for row in rows]


@st.cache_data(show_spinner=False)
def topics(db: str) -> list[str]:
    try:
        with sqlite3.connect(db) as conn:
            rows = conn.execute(
                "select topic, count(*) as n from case_topics group by topic order by n desc, topic"
            ).fetchall()
        return [row[0] for row in rows]
    except sqlite3.OperationalError:
        return []


@st.cache_data(show_spinner=False)
def topic_overview(db: str) -> pd.DataFrame:
    try:
        with sqlite3.connect(db) as conn:
            return pd.read_sql_query(
                """
                select topic as Tēma, count(*) as Skaits
                from case_topics
                group by topic
                order by Skaits desc, Tēma
                limit 20
                """,
                conn,
            )
    except Exception:
        return pd.DataFrame(columns=["Tēma", "Skaits"])


def fallback_search(
    conn: sqlite3.Connection,
    query: str,
    court: str | None,
    topic: str | None,
    limit: int,
) -> list[dict]:
    terms = clean_query(query).split()
    if not terms:
        return []
    joins = ["join decisions d on d.materialfileid = docs.materialfileid"]
    where = ["docs.extracted_text is not null", "docs.extracted_text <> ''"]
    params: list[object] = []
    for term in terms:
        where.append("docs.extracted_text like ?")
        params.append(f"%{term}%")
    if court:
        where.append("d.court = ?")
        params.append(court)
    if topic:
        joins.append("join case_topics t on t.materialfileid = d.materialfileid")
        where.append("t.topic = ?")
        params.append(topic)
    topic_select = (
        "t.topic as topic, t.score as topic_score, coalesce(t.matched_keywords, '') as matched_keywords,"
        if topic
        else "'' as topic, 0 as topic_score, '' as matched_keywords,"
    )
    params.append(limit)
    sql = f"""
    select d.materialfileid, d.court, d.casenumber, d.processtype, d.materialtype,
           d.registrationdate, d.downloadurl,
           {topic_select}
           substr(docs.extracted_text, 1, 700) as snippet,
           0 as rank
    from documents docs
    {' '.join(joins)}
    where {' and '.join(where)}
    limit ?
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


@st.cache_data(show_spinner=False)
def search(db: str, query: str, court: str | None, topic: str | None, limit: int) -> list[dict]:
    q = fts_query(query)
    if not q:
        return []
    joins = ["join decisions d on d.materialfileid = documents_fts.materialfileid"]
    where = ["documents_fts match ?"]
    params: list[object] = [q]
    if court:
        where.append("d.court = ?")
        params.append(court)
    if topic:
        joins.append("join case_topics t on t.materialfileid = d.materialfileid")
        where.append("t.topic = ?")
        params.append(topic)
    topic_select = (
        "t.topic as topic, t.score as topic_score, coalesce(t.matched_keywords, '') as matched_keywords,"
        if topic
        else "'' as topic, 0 as topic_score, '' as matched_keywords,"
    )
    params.append(limit)
    sql = f"""
    select d.materialfileid, d.court, d.casenumber, d.processtype, d.materialtype,
           d.registrationdate, d.downloadurl,
           {topic_select}
           snippet(documents_fts, 3, '[', ']', ' ... ', 42) as snippet,
           bm25(documents_fts) as rank
    from documents_fts
    {' '.join(joins)}
    where {' and '.join(where)}
    order by rank
    limit ?
    """
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return fallback_search(conn, query, court, topic, limit)


@st.cache_data(show_spinner=False)
def full_text(db: str, materialfileid: str) -> str:
    with sqlite3.connect(db) as conn:
        row = conn.execute("select extracted_text from documents where materialfileid=?", (materialfileid,)).fetchone()
    return row[0] if row and row[0] else ""


@st.cache_data(show_spinner=False)
def ai_summary(db: str, materialfileid: str) -> dict | None:
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                select summary, legal_issue, court_reasoning, outcome
                from ai_summaries
                where materialfileid=? and error_message is null
                """,
                (materialfileid,),
            ).fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


@st.cache_data(show_spinner=False)
def answer_question(db: str, question: str, topic: str | None, limit: int = 8) -> list[dict]:
    rows = search(db, question, None, topic, limit)
    for row in rows:
        ai = ai_summary(db, row["materialfileid"])
        row["answer_text"] = ai["summary"] if ai and ai.get("summary") else clean_text(row.get("snippet"))
    return rows


def result_title(row: dict) -> str:
    return row.get("casenumber") or row.get("materialfileid") or "Bez lietas numura"


def source_label(row: dict) -> str:
    return f"{result_title(row)} ({row.get('court') or '-'}, {row.get('registrationdate') or '-'})"


def build_report(question: str, rows: list[dict]) -> str:
    lines = [
        "# Juridiskās prakses pārskata melnraksts",
        "",
        f"Jautājums: {question}",
        f"Atlasītie nolēmumi: {len(rows)}",
        "",
        "## Īss kopsavilkums",
    ]
    if not rows:
        lines.append("Nav atrasti nolēmumi, no kuriem veidot pārskatu.")
    else:
        lines.append("Zemāk ir automātiski atlasīti avoti un īsi fragmenti. Pirms izmantošanas jāpārbauda pilnie nolēmumi.")
    lines += ["", "## Avoti"]
    for index, row in enumerate(rows, 1):
        lines += [
            "",
            f"### {index}. {source_label(row)}",
            f"Tēma: {row.get('topic') or 'Bez tēmas'}",
            "",
            row.get("answer_text") or clean_text(row.get("snippet")) or "Fragments nav pieejams.",
        ]
    return "\n".join(lines)


def render_result(row: dict, index: int, query: str) -> bool:
    title = html.escape(result_title(row))
    meta = " · ".join(
        clean_text(value) or "-"
        for value in [row.get("court"), row.get("registrationdate"), row.get("processtype"), row.get("materialtype")]
    )
    topic = clean_text(row.get("topic")) or "Bez tēmas"
    snippet = clean_snippet(row.get("snippet")) or "Fragments nav pieejams."
    st.markdown(
        f"""
        <div class="card">
          <strong>#{index} · Lieta {title}</strong>
          <span class="topic">{html.escape(topic)}</span>
          <div class="meta">{html.escape(meta)}</div>
          <div class="why">Atrasts pēc vaicājuma: “{html.escape(query)}”</div>
          <strong>Fragments:</strong><br>{snippet}
        </div>
        """,
        unsafe_allow_html=True,
    )
    left, right = st.columns([1, 3])
    with left:
        if row.get("downloadurl"):
            st.link_button("📄 Atvērt PDF", row["downloadurl"])
        selected = st.checkbox("Atlasīt pārskatam", key=f"select-report-{row['materialfileid']}")
    with right:
        with st.expander("Pilns teksts"):
            text = full_text(str(DB_PATH), row["materialfileid"])
            st.text_area("Teksts", text[:100000], height=420) if text else st.info("Pilns teksts nav pieejams.")
    return selected


st.markdown(
    """
    <style>
    mark{background:#fff3a3;padding:.05rem .18rem;border-radius:.2rem}
    .card{border:1px solid #e5e7eb;border-radius:8px;padding:1rem;margin:.75rem 0;background:#fff}
    .meta{color:#5f6673;font-size:.92rem;margin:.35rem 0 .55rem}
    .topic{background:#eef4ff;color:#2457a6;border:1px solid #c9d8ff;border-radius:999px;padding:.08rem .5rem;font-size:.82rem;margin-left:.35rem}
    .why{color:#6b7280;font-size:.88rem;margin-bottom:.6rem}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("⚖️ Latvijas tiesu nolēmumu pilots")
st.caption("Pilots tiesu nolēmumu pilnteksta meklēšanai, avotu pārbaudei un pārskatu melnrakstiem.")

try:
    with st.spinner("Pārbaudu pilotdatubāzi..."):
        active_db_path, db_source = prepare_database()
        DB_PATH = active_db_path
        if db_source == "release":
            st.success("Pilotdatubāze atjaunota no jaunākās publiskās datu kopas.")
        elif db_source in {"stale-runtime", "stale-bundled"}:
            st.warning("Neizdevās atjaunot jaunāko datubāzi, turpinu ar vecāku lokālo kopiju.")
except Exception as exc:
    if not DB_PATH.exists():
        st.error(f"Neizdevās lejupielādēt pilotdatubāzi: {exc}")
        st.stop()
    st.warning("Neizdevās pārbaudīt jaunāko datubāzi, turpinu ar lokālo kopiju.")

if not DB_PATH.exists():
    st.error(f"Datubāze nav atrasta: `{DB_PATH}`")
    st.stop()

s = stats(str(DB_PATH))
with st.expander("📌 Vadības panelis", expanded=True):
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Metadati", s["metadata"])
    c2.metric("Dokumenti", s["documents"])
    c3.metric("Lejupielādēti", s["downloaded"])
    c4.metric("Ar tekstu", s["with_text"])
    c5.metric("AI", s["ai"])
    c6.metric("Semantika", s["semantic"])
    c7.metric("Tēmas", s["topics"])
    p1, p2 = st.columns(2)
    p1.progress(pct(s["with_text"], s["documents"]) / 100, text=f"Teksts: {s['with_text']} no {s['documents']}")
    p2.progress(pct(s["fts"], s["with_text"]) / 100, text=f"Meklēšanas indekss: {s['fts']} no {s['with_text']}")

with st.expander("💬 Jautājums nolēmumu datubāzei", expanded=True):
    qa_col1, qa_col2 = st.columns([3, 1])
    with qa_col1:
        question = st.text_input("Jautājums", placeholder="piemēram: kredīta procentu piedziņu")
    with qa_col2:
        qa_topic_choice = st.selectbox("Jautājuma tēma", ["Visas"] + topics(str(DB_PATH)), key="qa_topic")
    if question:
        qa_topic = None if qa_topic_choice == "Visas" else qa_topic_choice
        answers = answer_question(str(DB_PATH), question, qa_topic, 8)
        if not answers:
            st.warning("Nav atrasti pietiekami atbilstoši nolēmumi. Pamēģini īsāku jautājumu vai noņem tēmas filtru.")
        else:
            st.markdown("**Īsa sintēze no atrastajiem avotiem:**")
            for number, answer in enumerate(answers[:5], 1):
                st.markdown(f"{number}. {answer.get('answer_text') or 'Avotā ir fragments, bet kopsavilkums vēl nav sagatavots.'}")
            report = build_report(question, answers)
            with st.expander("🧾 Gudrais ziņojums"):
                st.download_button("⬇️ Lejupielādēt Markdown", report, file_name="gudrais_zinojums.md", mime="text/markdown")
                st.text_area("Ziņojuma teksts", report, height=420)
            st.markdown("**Avoti:**")
            for answer in answers:
                st.markdown(f"**{source_label(answer)}** · {answer.get('topic') or 'Bez tēmas'}")
                if answer.get("downloadurl"):
                    st.link_button("📄 Atvērt avota PDF", answer["downloadurl"], key=f"qa-pdf-{answer['materialfileid']}")

with st.sidebar:
    st.header("Meklēšana")
    st.caption("Ātrie vaicājumi")
    for example in EXAMPLE_QUERIES:
        if st.button(example, key=f"example-{example}", width="stretch"):
            st.session_state["search_query"] = example
    query = st.text_input("Meklējamā frāze", placeholder="piemēram: kredīta parāds", key="search_query")
    limit = st.slider("Rezultātu skaits", 5, 50, 10, 5)
    court_choice = st.selectbox("Tiesa", ["Visas"] + courts(str(DB_PATH)))
    topic_choice = st.selectbox("Tēma", ["Visas"] + topics(str(DB_PATH)))
    selected_court = None if court_choice == "Visas" else court_choice
    selected_topic = None if topic_choice == "Visas" else topic_choice

if not query:
    st.subheader("Demo scenāriji")
    st.dataframe(
        pd.DataFrame({"Vaicājums": EXAMPLE_QUERIES, "Ko pārbaudīt": ["rezultāti un PDF avoti"] * len(EXAMPLE_QUERIES)}),
        width="stretch",
        hide_index=True,
    )
    overview = topic_overview(str(DB_PATH))
    if not overview.empty:
        st.subheader("Tēmu pārskats")
        st.bar_chart(overview.set_index("Tēma"))
    st.stop()

rows = search(str(DB_PATH), query, selected_court, selected_topic, limit)
st.subheader(f"Atrasti rezultāti: {len(rows)}")
if not rows:
    st.warning("Nav rezultātu ar pašreizējiem filtriem.")
    st.stop()

selected_rows = []
for index, row in enumerate(rows, 1):
    if render_result(row, index, query):
        row = dict(row)
        row["answer_text"] = clean_text(row.get("snippet"))
        selected_rows.append(row)

if selected_rows:
    st.divider()
    st.subheader(f"Pārskats no atlasītajiem nolēmumiem: {len(selected_rows)}")
    selected_report = build_report(query, selected_rows)
    st.download_button("⬇️ Lejupielādēt Markdown", selected_report, file_name="atlasitie_nolemumi.md", mime="text/markdown")
    st.text_area("Pārskata teksts", selected_report, height=420)
