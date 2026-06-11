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
DOCUMENT_SORTS = ["Atbilstība", "Datums", "Tiesa"]
PUBLICATION_STATUSES = ["Visi", "Publiski pieejams", "Nepublicēts", "Nepieciešama pārbaude"]
ANONYMIZATION_STATUSES = ["Visi", "Anonimizēts", "Daļēji anonimizēts", "Nav publiski pieejams", "Nepieciešama pārbaude"]
LANGUAGES = ["Visas", "Latviešu", "Angļu", "Franču", "Cita"]
VALUE_COLUMNS = {
    "courtinstance": "courtinstance",
    "materialtype": "materialtype",
    "processtype": "processtype",
    "status": "status",
}
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
def distinct_values(db: str, column: str) -> list[str]:
    db_column = VALUE_COLUMNS.get(column)
    if not db_column:
        return []
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            f"select distinct {db_column} from decisions where {db_column} is not null and {db_column}<>'' order by {db_column}"
        ).fetchall()
    return [row[0] for row in rows]


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


def metadata_search(
    conn: sqlite3.Connection,
    query: str,
    court: str | None,
    topic: str | None,
    limit: int,
    instance: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    case_number: str | None = None,
    identifier: str | None = None,
    sort_by: str = "Atbilstība",
) -> list[dict]:
    joins = [
        "join decisions d on d.materialfileid = docs.materialfileid",
        """
        left join (
            select materialfileid, max(topic) as topic, max(score) as topic_score,
                   coalesce(group_concat(matched_keywords, ', '), '') as matched_keywords
            from case_topics
            group by materialfileid
        ) topic_info on topic_info.materialfileid = d.materialfileid
        """,
    ]
    where = ["docs.extracted_text is not null", "docs.extracted_text <> ''"]
    params: list[object] = []
    if query:
        like = f"%{clean_text(query)}%"
        where.append(
            """(
                d.casenumber like ? or d.eclicode like ? or d.materialfileid like ? or
                d.applicationnumber like ? or d.court like ? or d.materialtype like ? or
                d.processtype like ? or docs.extracted_text like ?
            )"""
        )
        params.extend([like, like, like, like, like, like, like, like])
    if court:
        where.append("d.court = ?")
        params.append(court)
    if topic:
        where.append("exists (select 1 from case_topics tx where tx.materialfileid = d.materialfileid and tx.topic = ?)")
        params.append(topic)
    if instance:
        where.append("d.courtinstance = ?")
        params.append(instance)
    if document_type:
        where.append("d.materialtype = ?")
        params.append(document_type)
    if date_from:
        where.append("d.registrationdate >= ?")
        params.append(date_from)
    if date_to:
        where.append("d.registrationdate <= ?")
        params.append(date_to)
    if case_number:
        where.append("d.casenumber like ?")
        params.append(f"%{case_number}%")
    if identifier:
        where.append("(d.eclicode like ? or d.materialfileid like ? or d.applicationnumber like ?)")
        ident = f"%{identifier}%"
        params.extend([ident, ident, ident])
    order_by = {
        "Datums": "d.registrationdate desc, d.materialfileid",
        "Tiesa": "d.court collate nocase, d.registrationdate desc",
    }.get(sort_by, "d.registrationdate desc, d.materialfileid")
    params.append(limit)
    sql = f"""
    select d.materialfileid, d.court, d.courtdepartment, d.eclicode, d.casenumber,
           d.applicationnumber, d.processtype, d.processsubtype, d.materialtype,
           d.registrationdate, d.status, d.courtinstance, d.downloadurl,
           coalesce(topic_info.topic, '') as topic,
           coalesce(topic_info.topic_score, 0) as topic_score,
           coalesce(topic_info.matched_keywords, '') as matched_keywords,
           substr(docs.extracted_text, 1, 700) as snippet,
           0 as rank
    from documents docs
    {' '.join(joins)}
    where {' and '.join(where)}
    order by {order_by}
    limit ?
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def fallback_search(
    conn: sqlite3.Connection,
    query: str,
    court: str | None,
    topic: str | None,
    limit: int,
    instance: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    case_number: str | None = None,
    identifier: str | None = None,
    sort_by: str = "Atbilstība",
) -> list[dict]:
    terms = clean_query(query).split()
    if not terms:
        return metadata_search(
            conn,
            query,
            court,
            topic,
            limit,
            instance,
            document_type,
            date_from,
            date_to,
            case_number,
            identifier,
            sort_by,
        )
    joins = [
        "join decisions d on d.materialfileid = docs.materialfileid",
        """
        left join (
            select materialfileid, max(topic) as topic, max(score) as topic_score,
                   coalesce(group_concat(matched_keywords, ', '), '') as matched_keywords
            from case_topics
            group by materialfileid
        ) topic_info on topic_info.materialfileid = d.materialfileid
        """,
    ]
    where = ["docs.extracted_text is not null", "docs.extracted_text <> ''"]
    params: list[object] = []
    for term in terms:
        where.append("docs.extracted_text like ?")
        params.append(f"%{term}%")
    if court:
        where.append("d.court = ?")
        params.append(court)
    if topic:
        where.append("exists (select 1 from case_topics tx where tx.materialfileid = d.materialfileid and tx.topic = ?)")
        params.append(topic)
    if instance:
        where.append("d.courtinstance = ?")
        params.append(instance)
    if document_type:
        where.append("d.materialtype = ?")
        params.append(document_type)
    if date_from:
        where.append("d.registrationdate >= ?")
        params.append(date_from)
    if date_to:
        where.append("d.registrationdate <= ?")
        params.append(date_to)
    if case_number:
        where.append("d.casenumber like ?")
        params.append(f"%{case_number}%")
    if identifier:
        where.append("(d.eclicode like ? or d.materialfileid like ? or d.applicationnumber like ?)")
        ident = f"%{identifier}%"
        params.extend([ident, ident, ident])
    order_by = {
        "Datums": "d.registrationdate desc, d.materialfileid",
        "Tiesa": "d.court collate nocase, d.registrationdate desc",
    }.get(sort_by, "d.materialfileid")
    params.append(limit)
    sql = f"""
    select d.materialfileid, d.court, d.courtdepartment, d.eclicode, d.casenumber,
           d.applicationnumber, d.processtype, d.processsubtype, d.materialtype,
           d.registrationdate, d.status, d.courtinstance, d.downloadurl,
           coalesce(topic_info.topic, '') as topic,
           coalesce(topic_info.topic_score, 0) as topic_score,
           coalesce(topic_info.matched_keywords, '') as matched_keywords,
           substr(docs.extracted_text, 1, 700) as snippet,
           0 as rank
    from documents docs
    {' '.join(joins)}
    where {' and '.join(where)}
    order by {order_by}
    limit ?
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


@st.cache_data(show_spinner=False)
def search(
    db: str,
    query: str,
    court: str | None,
    topic: str | None,
    limit: int,
    instance: str | None = None,
    document_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    case_number: str | None = None,
    identifier: str | None = None,
    sort_by: str = "Atbilstība",
) -> list[dict]:
    q = fts_query(query)
    if not q:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return metadata_search(
                conn,
                query,
                court,
                topic,
                limit,
                instance,
                document_type,
                date_from,
                date_to,
                case_number,
                identifier,
                sort_by,
            )
    joins = [
        "join decisions d on d.materialfileid = documents_fts.materialfileid",
        """
        left join (
            select materialfileid, max(topic) as topic, max(score) as topic_score,
                   coalesce(group_concat(matched_keywords, ', '), '') as matched_keywords
            from case_topics
            group by materialfileid
        ) topic_info on topic_info.materialfileid = d.materialfileid
        """,
    ]
    where = ["documents_fts match ?"]
    params: list[object] = [q]
    if court:
        where.append("d.court = ?")
        params.append(court)
    if topic:
        where.append("exists (select 1 from case_topics tx where tx.materialfileid = d.materialfileid and tx.topic = ?)")
        params.append(topic)
    if instance:
        where.append("d.courtinstance = ?")
        params.append(instance)
    if document_type:
        where.append("d.materialtype = ?")
        params.append(document_type)
    if date_from:
        where.append("d.registrationdate >= ?")
        params.append(date_from)
    if date_to:
        where.append("d.registrationdate <= ?")
        params.append(date_to)
    if case_number:
        where.append("d.casenumber like ?")
        params.append(f"%{case_number}%")
    if identifier:
        where.append("(d.eclicode like ? or d.materialfileid like ? or d.applicationnumber like ?)")
        ident = f"%{identifier}%"
        params.extend([ident, ident, ident])
    order_by = {
        "Datums": "d.registrationdate desc, rank",
        "Tiesa": "d.court collate nocase, rank",
    }.get(sort_by, "rank")
    params.append(limit)
    sql = f"""
    select d.materialfileid, d.court, d.courtdepartment, d.eclicode, d.casenumber,
           d.applicationnumber, d.processtype, d.processsubtype, d.materialtype,
           d.registrationdate, d.status, d.courtinstance, d.downloadurl,
           coalesce(topic_info.topic, '') as topic,
           coalesce(topic_info.topic_score, 0) as topic_score,
           coalesce(topic_info.matched_keywords, '') as matched_keywords,
           snippet(documents_fts, 3, '[', ']', ' ... ', 42) as snippet,
           bm25(documents_fts) as rank
    from documents_fts
    {' '.join(joins)}
    where {' and '.join(where)}
    order by {order_by}
    limit ?
    """
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
            if rows:
                return rows
            return metadata_search(
                conn,
                query,
                court,
                topic,
                limit,
                instance,
                document_type,
                date_from,
                date_to,
                case_number,
                identifier,
                sort_by,
            )
        except sqlite3.OperationalError:
            return fallback_search(
                conn,
                query,
                court,
                topic,
                limit,
                instance,
                document_type,
                date_from,
                date_to,
                case_number,
                identifier,
                sort_by,
            )


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


def judgment_identifier(row: dict) -> str:
    return clean_text(row.get("eclicode")) or clean_text(row.get("materialfileid")) or "Identifikators nav norādīts"


def case_category(row: dict) -> str:
    return clean_text(row.get("topic")) or clean_text(row.get("processtype")) or "Nav norādīta"


def publication_status(row: dict) -> str:
    if row.get("downloadurl"):
        return "Publiski pieejams"
    return clean_text(row.get("status")) or "Nepieciešama pārbaude"


def anonymization_status(row: dict) -> str:
    if row.get("downloadurl"):
        return "Anonimizēts"
    return "Nepieciešama pārbaude"


def legal_norms(row: dict) -> list[str]:
    # TODO: sasaistīt nolēmumos minētās tiesību normas ar normatīvo aktu datubāzi.
    return []


def citation_text(row: dict) -> str:
    parts = [
        clean_text(row.get("court")) or "Tiesa nav norādīta",
        clean_text(row.get("materialtype")) or "nolēmums",
        clean_text(row.get("registrationdate")) or "datums nav norādīts",
        f"lieta Nr. {result_title(row)}",
        judgment_identifier(row),
    ]
    return ", ".join(parts)


def source_reference(row: dict) -> str:
    reference = citation_text(row)
    if row.get("downloadurl"):
        reference += f". Avots: {row['downloadurl']}"
    return reference


def source_excerpt(row: dict) -> str:
    return clean_text(row.get("snippet")) or "Avota fragments nav pieejams."


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
        item["source_reference"] = source_reference(item)
        item["source_excerpt"] = source_excerpt(item)
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
        "source_reference",
        "source_excerpt",
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
            f"Atsauce: {row.get('source_reference') or source_reference(row)}",
            "",
            "Kopsavilkums:",
            row.get("answer_text") or clean_text(row.get("snippet")) or "Fragments nav pieejams.",
            "",
            "Pārbaudāmais avota fragments:",
            row.get("source_excerpt") or source_excerpt(row),
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
                f"<p><span class='label'>Atsauce:</span> {html.escape(row.get('source_reference') or source_reference(row))}</p>",
                f"<p>{html.escape(row.get('answer_text') or clean_text(row.get('snippet')) or 'Fragments nav pieejams.')}</p>",
                f"<details><summary>Pārbaudāmais avota fragments</summary><p>{html.escape(row.get('source_excerpt') or source_excerpt(row))}</p></details>",
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
                rf"Atsauce: {rtf_escape(row.get('source_reference') or source_reference(row))}\par",
                rf"{rtf_escape(row.get('answer_text') or row.get('snippet') or 'Fragments nav pieejams.')}\par\par",
                rf"Pārbaudāmais avota fragments: {rtf_escape(row.get('source_excerpt') or source_excerpt(row))}\par\par",
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


def render_legal_norms(row: dict) -> None:
    norms = legal_norms(row)
    if norms:
        st.write(", ".join(norms))
    else:
        st.caption("Piemērotās tiesību normas nav norādītas.")


def render_saved_search_actions() -> None:
    cols = st.columns(2)
    cols[0].button("Saglabāt meklējumu", disabled=True, width="stretch")
    cols[1].button("Paziņot par jauniem nolēmumiem", disabled=True, width="stretch")
    st.caption("Saglabātie meklējumi būs pieejami pēc lietotāja profila funkcionalitātes ieviešanas.")


def clear_search_filters() -> None:
    defaults = {
        "search_query": "",
        "court_filter": "Visas",
        "topic_filter": "Visas",
        "instance_filter": "Visas",
        "document_type_filter": "Visi",
        "case_number_filter": "",
        "identifier_filter": "",
        "date_from_filter": "",
        "date_to_filter": "",
        "legal_norm_filter": "",
        "publication_status_filter": "Visi",
        "anonymization_filter": "Visi",
        "language_filter": "Visas",
        "sort_filter": "Atbilstība",
    }
    for key, value in defaults.items():
        st.session_state[key] = value


def active_filter_labels(filters: dict[str, str | None]) -> list[str]:
    labels = []
    for label, value in filters.items():
        if value:
            labels.append(f"{label}: {value}")
    return labels


def render_future_ai_panel(row: dict) -> None:
    with st.expander("AI palīgs", expanded=False):
        st.caption(
            "Nākotnē šeit būs iespējams uzdot jautājumus par nolēmumu, atrast līdzīgus nolēmumus un sagatavot īsu skaidrojumu."
        )
        action_cols = st.columns(2)
        action_cols[0].button("Izskaidro nolēmumu vienkāršā valodā", disabled=True, key=f"ai-explain-{row['materialfileid']}")
        action_cols[1].button("Atrodi līdzīgus nolēmumus", disabled=True, key=f"ai-similar-{row['materialfileid']}")
        action_cols[0].button("Parādi piemērotās tiesību normas", disabled=True, key=f"ai-norms-{row['materialfileid']}")
        action_cols[1].button("Salīdzini ar citu nolēmumu", disabled=True, key=f"ai-compare-{row['materialfileid']}")


def render_judgment_detail(row: dict, query: str) -> None:
    text = full_text(str(DB_PATH), row["materialfileid"])
    citation = citation_text(row)
    st.markdown(f"### {html.escape(result_title(row))}")
    header_cols = st.columns(4)
    header_cols[0].metric("Tiesa", clean_text(row.get("court")) or "-")
    header_cols[1].metric("Datums", clean_text(row.get("registrationdate")) or "-")
    header_cols[2].metric("Lieta", result_title(row))
    header_cols[3].metric("Identifikators", judgment_identifier(row))

    meta_cols = st.columns(4)
    meta_cols[0].caption(f"Nolēmuma veids: {clean_text(row.get('materialtype')) or '-'}")
    meta_cols[1].caption(f"Instance: {clean_text(row.get('courtinstance')) or '-'}")
    meta_cols[2].caption(f"Publicēšana: {publication_status(row)}")
    meta_cols[3].caption(f"Dokumenta statuss: {clean_text(row.get('status')) or publication_status(row)}")

    action_cols = st.columns([1, 1, 1, 1, 1])
    if action_cols[0].button("Kopēt citāciju", key=f"copy-cite-{row['materialfileid']}", width="stretch"):
        st.success("Citācija nokopēta.")
    if row.get("downloadurl"):
        action_cols[1].link_button("Lejupielādēt PDF", row["downloadurl"], width="stretch")
    else:
        action_cols[1].button("Lejupielādēt PDF", disabled=True, key=f"download-pdf-{row['materialfileid']}", width="stretch")
    action_cols[2].button("Drukāt", disabled=True, key=f"print-{row['materialfileid']}", width="stretch")
    action_cols[3].button("Kopīgot saiti", disabled=True, key=f"share-{row['materialfileid']}", width="stretch")
    action_cols[4].button("Ziņot par kļūdu", disabled=True, key=f"report-error-{row['materialfileid']}", width="stretch")
    st.code(citation, language=None)

    content_col, meta_col = st.columns([2, 1])
    with content_col:
        st.markdown("#### Nolēmuma teksts")
        inside_query = st.text_input(
            "Meklēt nolēmuma tekstā",
            value=query,
            key=f"inside-search-{row['materialfileid']}",
            placeholder="Ievadiet vārdu vai frāzi šajā nolēmumā",
        )
        if text:
            if inside_query and clean_query(inside_query):
                term = clean_query(inside_query).split()[0].lower()
                lower_text = text.lower()
                pos = lower_text.find(term)
                if pos >= 0:
                    start = max(pos - 500, 0)
                    end = min(pos + 1200, len(text))
                    st.markdown(clean_snippet(text[start:end].replace(text[pos : pos + len(term)], f"[{text[pos : pos + len(term)]}]")), unsafe_allow_html=True)
                else:
                    st.info("Šajā nolēmumā ievadītā frāze netika atrasta.")
            st.text_area("Pilns nolēmuma teksts", text[:100000], height=520, key=f"full-text-{row['materialfileid']}")
        else:
            st.info("Pilns teksts nav pieejams.")

    with meta_col:
        st.markdown("#### Metadati")
        st.write(f"**Tiesa:** {clean_text(row.get('court')) or '-'}")
        st.write("**Tiesneši:** nav norādīti")
        st.write(f"**Lietas kategorija:** {case_category(row)}")
        st.write(f"**Tiesību joma:** {clean_text(row.get('processtype')) or '-'}")
        st.write(f"**Anonimizācija:** {anonymization_status(row)}")
        st.write("**Piemērotās tiesību normas:**")
        render_legal_norms(row)
        st.write("**Saistītie nolēmumi:**")
        st.caption("Saistītie nolēmumi pašlaik nav pieejami.")
        st.write("**Citētie nolēmumi:**")
        st.caption("Citētie nolēmumi pašlaik nav pieejami.")
        st.write("**Citējošie nolēmumi:**")
        st.caption("Citējošie nolēmumi pašlaik nav pieejami.")

    st.markdown("#### Saistītie materiāli")
    st.info("Saistītie nolēmumi pašlaik nav pieejami.")
    st.caption("Personas dati nolēmumos var būt aizklāti, lai aizsargātu privātumu un ievērotu normatīvo aktu prasības.")
    render_future_ai_panel(row)


def render_result(row: dict, index: int, query: str) -> bool:
    title = html.escape(result_title(row))
    meta = " · ".join(
        clean_text(value) or "-"
        for value in [row.get("court"), row.get("registrationdate"), row.get("casenumber"), judgment_identifier(row)]
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
    detail_rows += f"<div><strong>Nolēmuma veids:</strong> {html.escape(clean_text(row.get('materialtype')) or '-')}</div>"
    detail_rows += f"<div><strong>Lietas kategorija:</strong> {html.escape(case_category(row))}</div>"
    detail_rows += f"<div><strong>Anonimizācija:</strong> {html.escape(anonymization_status(row))}</div>"
    norms = legal_norms(row)
    norms_label = ", ".join(norms) if norms else "Piemērotās tiesību normas nav norādītas."
    detail_rows += f"<div><strong>Tiesību normas:</strong> {html.escape(norms_label)}</div>"
    reference = source_reference(row)
    with st.container():
        st.markdown(
            f"""
            <article class="case-card">
              <div class="case-topline">
                <div>
                  <div class="case-index">Rezultāts {index}</div>
                  <h3>Lieta {title}</h3>
                </div>
                <div class="badges">{badge}<span class="topic">{html.escape(topic)}</span><span class="plain-badge">{html.escape(anonymization_status(row))}</span></div>
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
        actions = st.columns([1, 1, 1, 2])
        with actions[0]:
            selected = st.checkbox("Pārskatam", key=f"select-report-{row['materialfileid']}")
        with actions[1]:
            if row.get("downloadurl"):
                st.link_button("Atvērt PDF", row["downloadurl"], width="stretch")
        with actions[2]:
            with st.expander("Atsauce"):
                st.code(reference, language=None)
        with actions[3]:
            with st.expander("Atvērt nolēmumu"):
                render_judgment_detail(row, query)
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
    .link-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.55rem;margin:.85rem 0}
    .link-tile{border:1px solid var(--line);border-radius:8px;padding:.72rem;background:#fff;color:var(--ink);font-weight:650}
    .link-tile span{display:block;color:var(--muted);font-size:.78rem;font-weight:500;margin-top:.15rem}
    .source-note{border-left:4px solid var(--green);background:#f7fefb;border-radius:6px;padding:.72rem;margin:.8rem 0;color:#244033}
    .filter-chips{display:flex;gap:.35rem;flex-wrap:wrap;margin:.35rem 0 1rem}
    .filter-chip{border:1px solid #cbd5e1;background:#f8fafc;border-radius:999px;padding:.15rem .55rem;font-size:.8rem;color:#334155}
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
    div[data-testid="stButton"] button:focus, div[data-testid="stDownloadButton"] button:focus,
    input:focus, textarea:focus, select:focus{outline:3px solid #bfdbfe!important;outline-offset:2px}
    @media (max-width: 760px){
      .block-container{padding-left:.85rem;padding-right:.85rem;padding-top:.9rem}
      .status-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
      .link-grid{grid-template-columns:1fr}
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
      <h1>Tiesu nolēmumu meklēšana</h1>
      <p>Meklējiet tiesu nolēmumus pēc atslēgvārda, lietas numura, tiesas, datuma, tiesību normas vai ECLI identifikatora.</p>
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
st.markdown("<div class='section-label'>Meklēt nolēmumus</div>", unsafe_allow_html=True)
st.markdown(
    """
    <div class="source-note">
      Oficiālais avots ir pilns nolēmuma teksts. Kopsavilkumi, fragmenti un pārskati ir darba palīglīdzekļi, kas jāpārbauda pret avotu.
    </div>
    <div class="link-grid">
      <div class="link-tile">Jaunākie nolēmumi<span>Kārtojiet rezultātus pēc datuma</span></div>
      <div class="link-tile">Meklēt pēc tiesas<span>Izvēlieties konkrētu tiesu filtros</span></div>
      <div class="link-tile">Meklēt pēc tiesību normas<span>Ievadiet normu paplašinātajā meklēšanā</span></div>
      <div class="link-tile">Par anonimizāciju<span>Skatiet statusu pie katra nolēmuma</span></div>
    </div>
    """,
    unsafe_allow_html=True,
)
render_quick_queries()
search_col, button_col, limit_col = st.columns([4, 1, 1])
with search_col:
    query = st.text_input(
        "Meklējamā frāze",
        placeholder="Meklēt pēc atslēgvārda, lietas numura, ECLI, tiesas vai tiesību normas",
        key="search_query",
    )
with button_col:
    st.write("")
    st.button("Meklēt", type="primary", width="stretch")
with limit_col:
    limit = st.slider("Rezultāti", 5, 50, 10, 5)
sort_choice = st.selectbox("Kārtot pēc", DOCUMENT_SORTS, key="sort_filter")
with st.expander("Paplašinātā meklēšana", expanded=False):
    st.markdown("**A. Pamatinformācija**")
    basic_col1, basic_col2, basic_col3 = st.columns(3)
    with basic_col1:
        case_number_filter = st.text_input("Lietas numurs", key="case_number_filter")
    with basic_col2:
        identifier_filter = st.text_input("ECLI / unikālais identifikators", key="identifier_filter")
    with basic_col3:
        document_type_choice = st.selectbox("Nolēmuma veids", ["Visi"] + distinct_values(str(DB_PATH), "materialtype"), key="document_type_filter")

    st.markdown("**B. Tiesa un instance**")
    court_col, instance_col, category_col = st.columns(3)
    with court_col:
        court_choice = st.selectbox("Tiesa", ["Visas"] + courts(str(DB_PATH)), key="court_filter")
    with instance_col:
        instance_choice = st.selectbox("Instance", ["Visas"] + distinct_values(str(DB_PATH), "courtinstance"), key="instance_filter")
    with category_col:
        topic_choice = st.selectbox("Lietas kategorija", ["Visas"] + topics(str(DB_PATH)), key="topic_filter")

    st.markdown("**C. Datums un periods**")
    date_col1, date_col2 = st.columns(2)
    with date_col1:
        date_from_filter = st.text_input("Datums no", placeholder="YYYY-MM-DD", key="date_from_filter")
    with date_col2:
        date_to_filter = st.text_input("Datums līdz", placeholder="YYYY-MM-DD", key="date_to_filter")

    st.markdown("**D. Tiesību normas**")
    legal_norm_filter = st.text_input("Tiesību norma", placeholder="piemēram: Civillikuma 1765. pants", key="legal_norm_filter")
    st.caption("TODO: sasaistīt nolēmumos minētās tiesību normas ar normatīvo aktu datubāzi.")

    st.markdown("**E. Dokumenta statuss**")
    status_col1, status_col2, status_col3 = st.columns(3)
    with status_col1:
        publication_status_choice = st.selectbox("Publicēšanas statuss", PUBLICATION_STATUSES, key="publication_status_filter")
    with status_col2:
        anonymization_choice = st.selectbox("Anonimizācijas statuss", ANONYMIZATION_STATUSES, key="anonymization_filter")
    with status_col3:
        language_choice = st.selectbox("Valoda", LANGUAGES, key="language_filter")
    st.caption("Publicēšanas, anonimizācijas un valodas filtri pagaidām ir UI vietturi, līdz dati būs normalizēti datu modelī.")
    st.button("Notīrīt filtrus", on_click=clear_search_filters)
render_saved_search_actions()
st.markdown("</section>", unsafe_allow_html=True)

selected_court = None if court_choice == "Visas" else court_choice
selected_topic = None if topic_choice == "Visas" else topic_choice
selected_instance = None if instance_choice == "Visas" else instance_choice
selected_document_type = None if document_type_choice == "Visi" else document_type_choice
selected_date_from = clean_text(date_from_filter) or None
selected_date_to = clean_text(date_to_filter) or None
selected_case_number = clean_text(case_number_filter) or None
selected_identifier = clean_text(identifier_filter) or None
selected_legal_norm = clean_text(legal_norm_filter) or None
filter_labels = active_filter_labels(
    {
        "Tiesa": selected_court,
        "Instance": selected_instance,
        "Kategorija": selected_topic,
        "Veids": selected_document_type,
        "Datums no": selected_date_from,
        "Datums līdz": selected_date_to,
        "Lietas numurs": selected_case_number,
        "Identifikators": selected_identifier,
        "Tiesību norma": selected_legal_norm,
        "Publicēšana": None if publication_status_choice == "Visi" else publication_status_choice,
        "Anonimizācija": None if anonymization_choice == "Visi" else anonymization_choice,
        "Valoda": None if language_choice == "Visas" else language_choice,
    }
)

if not query and not any([selected_court, selected_topic, selected_instance, selected_document_type, selected_date_from, selected_date_to, selected_case_number, selected_identifier]):
    st.markdown(
        "<div class='empty-state'>Ievadiet meklējamo frāzi vai izmantojiet paplašinātos filtrus. Rezultāti parādīsies uzreiz zem meklēšanas formas.</div>",
        unsafe_allow_html=True,
    )
    overview = topic_overview(str(DB_PATH))
    if not overview.empty:
        st.subheader("Tēmu pārskats")
        st.bar_chart(overview.set_index("Tēma"))
    st.stop()

rows = search(
    str(DB_PATH),
    query,
    selected_court,
    selected_topic,
    limit,
    selected_instance,
    selected_document_type,
    selected_date_from,
    selected_date_to,
    selected_case_number,
    selected_identifier,
    sort_choice,
)
st.subheader(f"Atrasti rezultāti: {len(rows)}")
if filter_labels:
    chips = "".join(f"<span class='filter-chip'>{html.escape(label)}</span>" for label in filter_labels)
    st.markdown(f"<div class='filter-chips'>{chips}</div>", unsafe_allow_html=True)
if not rows:
    st.markdown(
        """
        <div class="empty-state">
          <strong>Nolēmumi netika atrasti</strong><br>
          Mēģiniet mainīt meklēšanas vārdus, noņemt daļu filtru vai pārbaudīt lietas numura formātu.
        </div>
        """,
        unsafe_allow_html=True,
    )
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
