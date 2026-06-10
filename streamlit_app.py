from __future__ import annotations

import csv
import html
import io
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
MIN_RELEASE_AI = 50
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
            ai_count = safe_count(conn, "select count(*) from ai_summaries where error_message is null")
        return (
            documents < MIN_RELEASE_DOCUMENTS
            or with_text < MIN_RELEASE_DOCUMENTS
            or topics_count < MIN_RELEASE_TOPICS
            or ai_count < MIN_RELEASE_AI
        )
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
    return enrich_rows_with_ai(db, rows)


def result_title(row: dict) -> str:
    return row.get("casenumber") or row.get("materialfileid") or "Bez lietas numura"


def source_label(row: dict) -> str:
    return f"{result_title(row)} ({row.get('court') or '-'}, {row.get('registrationdate') or '-'})"


def enrich_rows_with_ai(db: str, rows: list[dict]) -> list[dict]:
    enriched = []
    for row in rows:
        item = dict(row)
        ai = ai_summary(db, item["materialfileid"])
        if ai:
            item.update(
                {
                    "ai_summary": ai.get("summary", ""),
                    "ai_legal_issue": ai.get("legal_issue", ""),
                    "ai_court_reasoning": ai.get("court_reasoning", ""),
                    "ai_outcome": ai.get("outcome", ""),
                }
            )
        item["answer_text"] = item.get("ai_summary") or clean_text(item.get("snippet"))
        enriched.append(item)
    return enriched


def rows_to_csv(rows: list[dict]) -> str:
    output = io.StringIO()
    fields = [
        "casenumber",
        "court",
        "registrationdate",
        "processtype",
        "materialtype",
        "topic",
        "ai_summary",
        "ai_legal_issue",
        "ai_outcome",
        "downloadurl",
        "materialfileid",
    ]
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: clean_text(row.get(field)) for field in fields})
    return "\ufeff" + output.getvalue()


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
            f"Juridiskais jautājums: {row.get('ai_legal_issue') or 'nav automātiski noteikts'}",
            f"Iznākums: {row.get('ai_outcome') or 'nav automātiski noteikts'}",
            "",
            row.get("answer_text") or clean_text(row.get("snippet")) or "Fragments nav pieejams.",
        ]
    return "\n".join(lines)


def build_html_report(question: str, rows: list[dict]) -> str:
    body = [
        "<!doctype html><html lang='lv'><head><meta charset='utf-8'>",
        "<title>Juridiskās prakses pārskats</title>",
        "<style>body{font-family:Arial,sans-serif;max-width:920px;margin:40px auto;line-height:1.55;color:#1f2937}"
        "h1{border-bottom:3px solid #2563eb;padding-bottom:12px}h2{color:#1d4ed8;margin-top:28px}"
        ".case{border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:16px 0}.meta{color:#6b7280}"
        ".label{font-weight:700;color:#374151}</style></head><body>",
        "<h1>Juridiskās prakses pārskata melnraksts</h1>",
        f"<p><span class='label'>Jautājums:</span> {html.escape(question)}</p>",
        f"<p><span class='label'>Atlasītie nolēmumi:</span> {len(rows)}</p>",
        "<h2>Avoti</h2>",
    ]
    for index, row in enumerate(rows, 1):
        body.extend(
            [
                "<section class='case'>",
                f"<h3>{index}. {html.escape(source_label(row))}</h3>",
                f"<p class='meta'>{html.escape(clean_text(row.get('processtype')) or '-')} · {html.escape(clean_text(row.get('materialtype')) or '-')}</p>",
                f"<p><span class='label'>Tēma:</span> {html.escape(clean_text(row.get('topic')) or 'Bez tēmas')}</p>",
                f"<p><span class='label'>Juridiskais jautājums:</span> {html.escape(clean_text(row.get('ai_legal_issue')) or 'nav automātiski noteikts')}</p>",
                f"<p><span class='label'>Iznākums:</span> {html.escape(clean_text(row.get('ai_outcome')) or 'nav automātiski noteikts')}</p>",
                f"<p>{html.escape(row.get('answer_text') or clean_text(row.get('snippet')) or 'Fragments nav pieejams.')}</p>",
            ]
        )
        if row.get("downloadurl"):
            body.append(f"<p><a href='{html.escape(row['downloadurl'])}'>Atvērt avota PDF</a></p>")
        body.append("</section>")
    body.append("</body></html>")
    return "\n".join(body)


def build_rtf_report(question: str, rows: list[dict]) -> str:
    def rtf_escape(value: object) -> str:
        text = clean_text(str(value or ""))
        escaped = []
        for char in text:
            code = ord(char)
            if char == "\\":
                escaped.append("\\\\")
            elif char == "{":
                escaped.append("\\{")
            elif char == "}":
                escaped.append("\\}")
            elif char == "\n":
                escaped.append("\\par ")
            elif code > 127:
                if code > 32767:
                    code -= 65536
                escaped.append(rf"\u{code}?")
            else:
                escaped.append(char)
        return "".join(escaped)

    parts = [
        r"{\rtf1\ansi\deff0",
        r"{\fonttbl{\f0 Arial;}}",
        r"\fs28\b Juridiskās prakses pārskata melnraksts\b0\par",
        rf"\fs22 Jautājums: {rtf_escape(question)}\par",
        rf"Atlasītie nolēmumi: {len(rows)}\par\par",
    ]
    for index, row in enumerate(rows, 1):
        parts.extend(
            [
                rf"\b {index}. {rtf_escape(source_label(row))}\b0\par",
                rf"Tēma: {rtf_escape(row.get('topic') or 'Bez tēmas')}\par",
                rf"Juridiskais jautājums: {rtf_escape(row.get('ai_legal_issue') or 'nav automātiski noteikts')}\par",
                rf"Iznākums: {rtf_escape(row.get('ai_outcome') or 'nav automātiski noteikts')}\par",
                rf"{rtf_escape(row.get('answer_text') or row.get('snippet') or 'Fragments nav pieejams.')}\par\par",
            ]
        )
    parts.append("}")
    return "\n".join(parts)


def render_report_downloads(question: str, rows: list[dict], filename_base: str) -> str:
    markdown_report = build_report(question, rows)
    html_report = build_html_report(question, rows)
    rtf_report = build_rtf_report(question, rows)
    csv_report = rows_to_csv(rows)
    cols = st.columns(4)
    cols[0].download_button(
        "Markdown",
        markdown_report,
        file_name=f"{filename_base}.md",
        mime="text/markdown",
        width="stretch",
    )
    cols[1].download_button(
        "HTML / PDF",
        html_report,
        file_name=f"{filename_base}.html",
        mime="text/html",
        width="stretch",
    )
    cols[2].download_button(
        "Word / RTF",
        rtf_report,
        file_name=f"{filename_base}.rtf",
        mime="application/rtf",
        width="stretch",
    )
    cols[3].download_button(
        "CSV",
        csv_report,
        file_name=f"{filename_base}.csv",
        mime="text/csv",
        width="stretch",
    )
    return markdown_report


def render_status_strip(data: dict[str, int]) -> None:
    items = [
        ("Dokumenti", f"{data['documents']:,}".replace(",", " ")),
        ("Teksts", f"{data['with_text']:,}".replace(",", " ")),
        ("AI", f"{data['ai']:,}".replace(",", " ")),
        ("Indekss", f"{data['fts']:,}".replace(",", " ")),
    ]
    cards = "".join(
        f"<div class='status-card'><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in items
    )
    st.markdown(f"<div class='status-grid'>{cards}</div>", unsafe_allow_html=True)


def render_quick_queries() -> None:
    st.markdown("<div class='section-label'>Ātrie vaicājumi</div>", unsafe_allow_html=True)
    cols = st.columns(3)
    for index, example in enumerate(EXAMPLE_QUERIES):
        if cols[index % 3].button(example, key=f"example-{example}", width="stretch"):
            st.session_state["search_query"] = example
            st.rerun()


def render_result(row: dict, index: int, query: str) -> bool:
    title = html.escape(result_title(row))
    meta = " · ".join(
        clean_text(value) or "-"
        for value in [row.get("court"), row.get("registrationdate"), row.get("processtype"), row.get("materialtype")]
    )
    topic = clean_text(row.get("topic")) or "Bez tēmas"
    ai = ai_summary(str(DB_PATH), row["materialfileid"])
    has_ai = bool(ai and ai.get("summary"))
    summary = clean_text(ai.get("summary")) if ai else ""
    legal_issue = clean_text(ai.get("legal_issue")) if ai else ""
    outcome = clean_text(ai.get("outcome")) if ai else ""
    reasoning = clean_text(ai.get("court_reasoning")) if ai else ""
    snippet = clean_snippet(row.get("snippet")) or "Fragments nav pieejams."
    badge = "<span class='ai-badge'>AI kopsavilkums</span>" if has_ai else "<span class='plain-badge'>Fragments</span>"
    main_text = html.escape(summary) if summary else snippet
    detail_rows = ""
    if legal_issue:
        detail_rows += f"<div><strong>Juridiskais jautājums:</strong> {html.escape(legal_issue)}</div>"
    if outcome:
        detail_rows += f"<div><strong>Iznākums:</strong> {html.escape(outcome)}</div>"
    if reasoning:
        detail_rows += f"<div><strong>Tiesas pamatojums:</strong> {html.escape(reasoning)}</div>"
    with st.container():
        st.markdown(
            f"""
            <article class="case-card">
              <div class="case-topline">
                <div>
                  <div class="case-index">Rezultāts {index}</div>
                  <h3>Lieta {title}</h3>
                </div>
                <div class="badges">{badge}<span class="topic">{html.escape(topic)}</span></div>
              </div>
              <div class="meta">{html.escape(meta)}</div>
              <div class="why">Vaicājums: “{html.escape(query)}”</div>
              <div class="summary">{main_text}</div>
              <div class="details">{detail_rows}</div>
              <details><summary>Avota fragments</summary><div class="snippet">{snippet}</div></details>
            </article>
            """,
            unsafe_allow_html=True,
        )
        actions = st.columns([1, 1, 2])
        with actions[0]:
            selected = st.checkbox("Pārskatam", key=f"select-report-{row['materialfileid']}")
        with actions[1]:
            if row.get("downloadurl"):
                st.link_button("Atvērt PDF", row["downloadurl"], width="stretch")
        with actions[2]:
            with st.expander("Pilns teksts"):
                text = full_text(str(DB_PATH), row["materialfileid"])
                st.text_area("Teksts", text[:100000], height=420) if text else st.info("Pilns teksts nav pieejams.")
    return selected


st.markdown(
    """
    <style>
    :root{
      --ink:#172033;--muted:#667085;--line:#e6e9ef;--paper:#ffffff;--soft:#f6f7f9;
      --blue:#1d4ed8;--green:#047857;--amber:#a16207;
    }
    .block-container{max-width:1120px;padding-top:1.4rem;padding-bottom:4rem}
    [data-testid="stSidebar"]{display:none}
    h1,h2,h3{letter-spacing:0}
    mark{background:#fff3a3;padding:.05rem .18rem;border-radius:.2rem}
    .app-masthead{border-bottom:1px solid var(--line);padding:.4rem 0 1rem;margin-bottom:1rem}
    .eyebrow{font-size:.78rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:700}
    .app-masthead h1{font-size:clamp(1.7rem,3vw,2.45rem);line-height:1.1;margin:.25rem 0;color:var(--ink)}
    .app-masthead p{color:var(--muted);font-size:1rem;margin:.15rem 0 0;max-width:720px}
    .status-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.65rem;margin:.9rem 0 1.1rem}
    .status-card{border:1px solid var(--line);background:var(--paper);border-radius:8px;padding:.75rem .85rem}
    .status-card span{display:block;color:var(--muted);font-size:.78rem;margin-bottom:.1rem}
    .status-card strong{color:var(--ink);font-size:1.15rem}
    .work-panel{border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,#fff,#fbfcfe);padding:1rem;margin:.8rem 0 1rem}
    .section-label{font-size:.82rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700;margin:.25rem 0 .45rem}
    .case-card{border:1px solid var(--line);border-radius:8px;background:var(--paper);padding:1rem;margin:1rem 0 .5rem;box-shadow:0 1px 2px rgba(16,24,40,.04)}
    .case-topline{display:flex;gap:.8rem;align-items:flex-start;justify-content:space-between}
    .case-index{color:var(--muted);font-size:.78rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em}
    .case-card h3{font-size:1.06rem;line-height:1.25;margin:.12rem 0;color:var(--ink)}
    .badges{display:flex;gap:.35rem;flex-wrap:wrap;justify-content:flex-end}
    .meta{color:var(--muted);font-size:.9rem;margin:.45rem 0 .55rem}
    .topic{background:#eef4ff;color:#2457a6;border:1px solid #c9d8ff;border-radius:999px;padding:.1rem .55rem;font-size:.78rem}
    .why{color:var(--muted);font-size:.86rem;margin-bottom:.65rem}
    .summary{background:#f8fafc;border-left:4px solid var(--blue);border-radius:6px;padding:.78rem;margin:.6rem 0;color:#243044}
    .details{font-size:.92rem;color:#374151;display:grid;gap:.35rem;margin:.55rem 0}
    .snippet{margin-top:.45rem;color:#374151}
    .ai-badge{background:#ecfdf5;color:var(--green);border:1px solid #a7f3d0;border-radius:999px;padding:.1rem .55rem;font-size:.78rem}
    .plain-badge{background:#f3f4f6;color:#4b5563;border:1px solid #d1d5db;border-radius:999px;padding:.1rem .55rem;font-size:.78rem}
    .empty-state{border:1px dashed #cbd5e1;border-radius:8px;background:#fafafa;padding:1rem;color:var(--muted);margin-top:1rem}
    div[data-testid="stTextInput"] input{border-radius:8px}
    div[data-testid="stButton"] button, div[data-testid="stDownloadButton"] button, a[data-testid="stLinkButton"]{
      border-radius:8px!important;font-weight:650
    }
    @media (max-width: 760px){
      .block-container{padding-left:.85rem;padding-right:.85rem;padding-top:.9rem}
      .status-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
      .work-panel{padding:.85rem}
      .case-topline{display:block}
      .badges{justify-content:flex-start;margin-top:.45rem}
      .case-card{padding:.9rem}
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <header class="app-masthead">
      <div class="eyebrow">Tiesu nolēmumu darba vide</div>
      <h1>Latvijas tiesu nolēmumi</h1>
      <p>Meklēšana, avotu pārbaude un pārskata melnraksts vienā jurista darba plūsmā.</p>
    </header>
    """,
    unsafe_allow_html=True,
)

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
render_status_strip(s)
with st.expander("Datu statuss", expanded=False):
    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Metadati", s["metadata"])
    c2.metric("Dokumenti", s["documents"])
    c3.metric("PDF", s["downloaded"])
    c4.metric("Teksts", s["with_text"])
    c5.metric("AI", s["ai"])
    c6.metric("Semantika", s["semantic"])
    c7.metric("Tēmas", s["topics"])
    p1, p2 = st.columns(2)
    p1.progress(pct(s["with_text"], s["documents"]) / 100, text=f"Teksts: {s['with_text']} no {s['documents']}")
    p2.progress(pct(s["fts"], s["with_text"]) / 100, text=f"Meklēšanas indekss: {s['fts']} no {s['with_text']}")

st.markdown("<section class='work-panel'>", unsafe_allow_html=True)
render_quick_queries()
search_col, limit_col = st.columns([4, 1])
with search_col:
    query = st.text_input(
        "Darba jautājums vai frāze",
        placeholder="piemēram: kredīta parāds vai kā tiesas vērtē kredīta procentu piedziņu?",
        key="search_query",
    )
with limit_col:
    limit = st.slider("Skaits", 5, 50, 10, 5)
with st.expander("Filtri", expanded=False):
    filter_col1, filter_col2 = st.columns(2)
    with filter_col1:
        court_choice = st.selectbox("Tiesa", ["Visas"] + courts(str(DB_PATH)))
    with filter_col2:
        topic_choice = st.selectbox("Tēma", ["Visas"] + topics(str(DB_PATH)))
st.markdown("</section>", unsafe_allow_html=True)

selected_court = None if court_choice == "Visas" else court_choice
selected_topic = None if topic_choice == "Visas" else topic_choice

if not query:
    st.markdown(
        "<div class='empty-state'>Ievadi jautājumu vai frāzi. Zemāk uzreiz parādīsies avoti un pārskata melnraksts.</div>",
        unsafe_allow_html=True,
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

report_rows = enrich_rows_with_ai(str(DB_PATH), rows[:8])
with st.expander("Gudrais ziņojums no atrastajiem avotiem", expanded=False):
    report = render_report_downloads(query, report_rows, "gudrais_zinojums")
    st.text_area("Ziņojuma teksts", report, height=360)

selected_rows = []
for index, row in enumerate(rows, 1):
    if render_result(row, index, query):
        selected_rows.append(row)

if selected_rows:
    selected_rows = enrich_rows_with_ai(str(DB_PATH), selected_rows)
    st.divider()
    st.subheader(f"Pārskats no atlasītajiem nolēmumiem: {len(selected_rows)}")
    selected_report = render_report_downloads(query, selected_rows, "atlasitie_nolemumi")
    st.text_area("Pārskata teksts", selected_report, height=420)
