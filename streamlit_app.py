from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

DB_PATH = Path("pilot/pilot.sqlite")
EXAMPLE_QUERIES = [
    "kredīta parāds",
    "darba samaksa",
    "būvatļauja",
    "nodokļu parāds",
    "iepirkums",
]
DEMO_SCENARIOS = [
    {
        "Vaicājums": "kredīta parāds",
        "Fokuss": "civillietas par aizdevumiem, procentiem un piedziņu",
        "Ko skatīt": "tēmu pārliecību, prasības iznākumu un atlasīto lietu pārskatu",
    },
    {
        "Vaicājums": "darba samaksa",
        "Fokuss": "darba tiesību strīdi un atlīdzības jautājumi",
        "Ko skatīt": "darba tiesību tēmu, tiesas un fragmentus par samaksu",
    },
    {
        "Vaicājums": "būvatļauja",
        "Fokuss": "administratīvās lietas par būvniecību un plānošanu",
        "Ko skatīt": "būvniecības tēmu, administratīvo procesu un avotu PDF",
    },
    {
        "Vaicājums": "nodokļu parāds",
        "Fokuss": "nodokļu administrēšana un parāda jautājumi",
        "Ko skatīt": "tēmu sadalījumu, zemas pārliecības brīdinājumus un ierobežojumus",
    },
    {
        "Vaicājums": "iepirkums",
        "Fokuss": "publisko iepirkumu strīdi",
        "Ko skatīt": "mazāku, fokusētu rezultātu kopu un pārskata eksportu",
    },
]

st.set_page_config(page_title="Latvijas tiesu nolēmumu pilots", page_icon="⚖️", layout="wide")


def clean_query(q: str) -> str:
    return " ".join(re.findall(r"[\wāčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ]+", q, flags=re.UNICODE))


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\n", " ")).strip()


def clean_snippet(value: str | None) -> str:
    text = html.escape(clean_text(value))
    return text.replace("[", "<mark>").replace("]", "</mark>")


def pct(part: int, total: int) -> float:
    return round((part / total) * 100, 1) if total else 0.0


def rule_summary(row: dict, snippet: str, full_text: str) -> str:
    text = clean_text((full_text or snippet)[:3000]).lower()
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
    else:
        topic = f"Lieta saistīta ar procesu: {process}." if process else "Lietas būtība jāvērtē pēc fragmenta un pilnā teksta."

    if "prasība apmierināta" in text or "prasību apmierināt" in text:
        result = "Rezultāts: prasība apmierināta."
    elif "prasība noraidīta" in text or "prasību noraidīt" in text:
        result = "Rezultāts: prasība noraidīta."
    elif "daļēji apmierin" in text:
        result = "Rezultāts: prasība apmierināta daļēji."
    else:
        result = f"Nolēmuma veids: {material_type}." if material_type else "Rezultāts automātiski nav droši nosakāms."
    return topic + " " + result


@st.cache_data(show_spinner=False)
def stats(db: str) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        out = {
            "metadata": conn.execute("select count(*) from decisions").fetchone()[0],
            "documents": conn.execute("select count(*) from documents").fetchone()[0],
            "downloaded": conn.execute("select count(*) from documents where download_status='downloaded'").fetchone()[0],
            "with_text": conn.execute("select count(*) from documents where extracted_text is not null and extracted_text<>''").fetchone()[0],
            "ai": 0,
            "semantic": 0,
            "topics": 0,
        }
        for key, sql in {
            "ai": "select count(*) from ai_summaries where error_message is null",
            "semantic": "select count(*) from semantic_embeddings",
            "topics": "select count(*) from case_topics",
        }.items():
            try:
                out[key] = conn.execute(sql).fetchone()[0]
            except sqlite3.OperationalError:
                pass
        return out


@st.cache_data(show_spinner=False)
def courts(db: str) -> list[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("select distinct court from decisions where court is not null and court<>'' order by court").fetchall()
    return [r[0] for r in rows]


@st.cache_data(show_spinner=False)
def topics(db: str) -> list[str]:
    try:
        with sqlite3.connect(db) as conn:
            rows = conn.execute("select topic, count(*) as n from case_topics group by topic order by n desc, topic").fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


@st.cache_data(show_spinner=False)
def topic_overview(db: str) -> pd.DataFrame:
    try:
        with sqlite3.connect(db) as conn:
            return pd.read_sql_query(
                """
                select
                    topic as Tēma,
                    count(*) as Skaits,
                    round(avg(score), 1) as "Vidējais punktu skaits",
                    sum(case when score <= 1 then 1 else 0 end) as "Zema pārliecība",
                    sum(case when score between 2 and 3 then 1 else 0 end) as "Vidēja pārliecība",
                    sum(case when score >= 4 then 1 else 0 end) as "Augsta pārliecība"
                from case_topics
                group by topic
                order by Skaits desc, Tēma
                """,
                conn,
            )
    except Exception:
        return pd.DataFrame(columns=["Tēma", "Skaits", "Vidējais punktu skaits", "Zema pārliecība", "Vidēja pārliecība", "Augsta pārliecība"])


@st.cache_data(show_spinner=False)
def trend_data(db: str, topic: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    try:
        with sqlite3.connect(db) as conn:
            topic_where = "where t.topic = ?" if topic else ""
            params = (topic,) if topic else ()
            by_topic = pd.read_sql_query("select topic as Tēma, count(*) as Skaits from case_topics group by topic order by Skaits desc limit 15", conn)
            by_year = pd.read_sql_query(
                f"""
                select substr(d.registrationdate,1,4) as Gads, count(*) as Skaits
                from decisions d join case_topics t on t.materialfileid=d.materialfileid
                {topic_where}
                group by Gads having Gads is not null and Gads<>'' order by Gads
                """,
                conn,
                params=params,
            )
            by_court = pd.read_sql_query(
                f"""
                select d.court as Tiesa, count(*) as Skaits
                from decisions d join case_topics t on t.materialfileid=d.materialfileid
                {topic_where}
                group by d.court having Tiesa is not null and Tiesa<>'' order by Skaits desc limit 15
                """,
                conn,
                params=params,
            )
        return by_topic, by_year, by_court
    except Exception:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()


@st.cache_data(show_spinner=False)
def find_case(db: str, case_or_id: str) -> dict | None:
    value = case_or_id.strip()
    if not value:
        return None
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            select d.materialfileid, d.court, d.casenumber, d.processtype, d.materialtype,
                   d.registrationdate, d.downloadurl, coalesce(t.topic, '') as topic,
                   coalesce(t.score, 0) as topic_score,
                   coalesce(t.matched_keywords, '') as matched_keywords
            from decisions d
            left join case_topics t on t.materialfileid=d.materialfileid
            where d.materialfileid = ? or d.casenumber = ?
            limit 1
            """,
            (value, value),
        ).fetchone()
    return dict(row) if row else None


@st.cache_data(show_spinner=False)
def search(db: str, query: str, court: str | None, topic: str | None, limit: int) -> list[dict]:
    q = clean_query(query)
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
    params.append(limit)
    topic_select = (
        "t.topic as topic, t.score as topic_score, coalesce(t.matched_keywords, '') as matched_keywords,"
        if topic
        else "coalesce(t2.topic, '') as topic, coalesce(t2.score, 0) as topic_score, coalesce(t2.matched_keywords, '') as matched_keywords,"
    )
    sql = f"""
    select d.materialfileid, d.court, d.casenumber, d.processtype, d.materialtype,
           d.registrationdate, d.downloadurl,
           {topic_select}
           snippet(documents_fts, 3, '[', ']', ' ... ', 42) as snippet,
           bm25(documents_fts) as rank
    from documents_fts
    {' '.join(joins)}
    {"left join case_topics t2 on t2.materialfileid = d.materialfileid" if not topic else ""}
    where {' and '.join(where)}
    order by rank
    limit ?
    """
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


@st.cache_data(show_spinner=False)
def answer_question(db: str, question: str, topic: str | None, limit: int = 5) -> list[dict]:
    rows = search(db, question, None, topic, limit)
    for row in rows:
        ai = ai_summary(db, row["materialfileid"])
        row["answer_text"] = ai["summary"] if ai and ai.get("summary") else clean_text(row.get("snippet"))
    return rows


def count_by(items: list[dict], key: str, fallback: str = "Nav norādīts") -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = clean_text(str(item.get(key) or "")) or fallback
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: (-x[1], x[0])))


def top_counts_text(counts: dict[str, int], limit: int = 5) -> str:
    if not counts:
        return "-"
    return "; ".join(f"{name} ({count})" for name, count in list(counts.items())[:limit])


def result_profile(rows: list[dict]) -> dict[str, str]:
    return {
        "Tēmas": top_counts_text(count_by(rows, "topic", "Bez tēmas"), 4),
        "Tiesas": top_counts_text(count_by(rows, "court", "Tiesa nav norādīta"), 4),
        "Procesi": top_counts_text(count_by(rows, "processtype", "Process nav norādīts"), 4),
        "Datumi": date_range(rows),
    }


def date_range(items: list[dict]) -> str:
    dates = sorted(str(item.get("registrationdate")) for item in items if item.get("registrationdate"))
    if not dates:
        return "nav droši nosakāms"
    if dates[0] == dates[-1]:
        return dates[0]
    return f"{dates[0]} līdz {dates[-1]}"


def infer_outcome(item: dict) -> str:
    text = clean_text(f"{item.get('materialtype') or ''} {item.get('answer_text') or ''} {item.get('snippet') or ''}").lower()
    if any(value in text for value in ["apmierināta daļēji", "daļēji apmierin"]):
        return "prasība apmierināta daļēji"
    if any(value in text for value in ["prasības apmierināšanu", "prasība apmierināta", "prasību apmierināt", "prasība apmierināta"]):
        return "prasība apmierināta"
    if any(value in text for value in ["prasības noraidīšanu", "prasība noraidīta", "prasību noraidīt", "pieteikuma noraidīšanu"]):
        return "prasība vai pieteikums noraidīts"
    if any(value in text for value in ["lietas izbeigšanu", "lieta izbeigta"]):
        return "lieta izbeigta"
    if "maksātnespējas procesa pasludināšanu" in text:
        return "pasludināts maksātnespējas process"
    return "iznākums automātiski nav droši nosakāms"


def source_label(item: dict) -> str:
    title = item.get("casenumber") or item.get("materialfileid") or "-"
    return f"{title} ({item.get('court') or '-'}, {item.get('registrationdate') or '-'})"


def build_smart_report(question: str, answers: list[dict]) -> str:
    total = len(answers)
    topic_counts = count_by(answers, "topic", "Bez tēmas")
    court_counts = count_by(answers, "court", "Tiesa nav norādīta")
    process_counts = count_by(answers, "processtype", "Process nav norādīts")
    material_counts = count_by(answers, "materialtype", "Nolēmuma veids nav norādīts")
    outcome_counts: dict[str, int] = {}
    for item in answers:
        outcome = infer_outcome(item)
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
    outcome_counts = dict(sorted(outcome_counts.items(), key=lambda x: (-x[1], x[0])))

    dominant_topic = next(iter(topic_counts), "nav nosakāma")
    dominant_outcome = next(iter(outcome_counts), "nav droši nosakāms")

    lines = [
        "# Juridiskās prakses pārskata melnraksts",
        "",
        f"**Jautājums:** {question}",
        f"**Atlasītie nolēmumi:** {total}",
        f"**Datumu intervāls:** {date_range(answers)}",
        "",
        "## Īss secinājums",
    ]
    if not answers:
        lines.append("Nav atrasti pietiekami atbilstoši nolēmumi.")
    elif total == 1:
        lines.append(
            f"Atlasē ir viens nolēmums: {source_label(answers[0])}. Tas ir izmantojams kā sākotnējs piemērs, "
            "bet no viena avota nevar droši secināt stabilu tiesu praksi."
        )
    else:
        lines.append(
            f"Atlasē dominē tēma **{dominant_topic}**, un biežākais automātiski nolasītais iznākums ir "
            f"**{dominant_outcome}**. Secinājumi jāuztver kā melnraksts, jo tie balstās uz atlasīto rezultātu kopu, "
            "nevis pilnu tiesu prakses revīziju."
        )

    lines += [
        "",
        "## Atlases profils",
        f"- Tēmas: {top_counts_text(topic_counts)}",
        f"- Tiesas: {top_counts_text(court_counts)}",
        f"- Procesi: {top_counts_text(process_counts)}",
        f"- Nolēmumu veidi: {top_counts_text(material_counts)}",
        f"- Automātiski nolasītie iznākumi: {top_counts_text(outcome_counts)}",
        "",
        "## Galvenie novērojumi",
    ]
    if not answers:
        lines.append("- Nav avotu, no kuriem veidot novērojumus.")
    else:
        lines.append(f"- Atlasē ir {total} nolēmumi, kas saistīti ar vaicājumu vai izvēlēto rezultātu kopu.")
        if dominant_topic != "nav nosakāma":
            lines.append(f"- Dominējošā tēma ir **{dominant_topic}**; tā jāizmanto kā galvenais pārskata rāmis.")
        if court_counts:
            lines.append(f"- Biežāk pārstāvētā tiesa atlasē: **{next(iter(court_counts))}**.")
        if outcome_counts:
            lines.append(f"- Biežākais automātiski noteiktais iznākums: **{dominant_outcome}**.")
        lines.append("- Pirms juridiskas izmantošanas jāpārbauda pilnie nolēmumu teksti un procesuālais konteksts.")

    lines += ["", "## Atlasīto nolēmumu kopsavilkumi"]
    for i, item in enumerate(answers, 1):
        title = item.get("casenumber") or item.get("materialfileid")
        outcome = infer_outcome(item)
        confidence = topic_confidence(item.get("topic_score")) if item.get("topic_score") is not None else "nav novērtēta"
        lines += [
            "",
            f"### {i}. Lieta {title}",
            f"- Tiesa: {item.get('court') or '-'}",
            f"- Datums: {item.get('registrationdate') or '-'}",
            f"- Tēma: {item.get('topic') or 'Bez tēmas'}",
            f"- Tēmas pārliecība: {confidence}",
            f"- Nolēmuma veids: {item.get('materialtype') or '-'}",
            f"- Automātiski nolasīts iznākums: {outcome}",
            "",
            item.get("answer_text") or "Kopsavilkums vēl nav pieejams; skatīt oriģinālo fragmentu vai PDF.",
        ]
    lines += [
        "",
        "## Ierobežojumi",
        "- Šis ir automātiski sagatavots melnraksts, nevis juridisks atzinums.",
        "- Tēmas un iznākumi ir nolasīti automātiski, tāpēc tie var būt neprecīzi.",
        "- Jāpārbauda pilnie nolēmumi, īpaši rezolutīvā daļa, pārsūdzības statuss un faktiskie apstākļi.",
        "",
        "## Avoti",
    ]
    for item in answers:
        lines.append(f"- {source_label(item)} — MaterialFileId: {item.get('materialfileid') or '-'}")
    return "\n".join(lines)


def markdown_to_html(report: str) -> str:
    escaped = html.escape(report)
    escaped = re.sub(r"^# (.*)$", r"<h1>\1</h1>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^## (.*)$", r"<h2>\1</h2>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^### (.*)$", r"<h3>\1</h3>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"^- (.*)$", r"<li>\1</li>", escaped, flags=re.MULTILINE)
    escaped = escaped.replace("\n\n", "</p><p>").replace("\n", "<br>")
    return f"""<!doctype html>
<html lang="lv"><head><meta charset="utf-8"><title>Gudrais ziņojums</title>
<style>body{{font-family:Arial,sans-serif;color:#1f2937;max-width:900px;margin:40px auto;line-height:1.55}}h1{{border-bottom:3px solid #2563eb;padding-bottom:12px}}h2{{color:#1d4ed8;margin-top:28px}}h3{{color:#374151;margin-top:22px}}li{{margin:6px 0}}.footer{{margin-top:40px;color:#6b7280;font-size:12px;border-top:1px solid #e5e7eb;padding-top:12px}}@media print{{body{{margin:20mm}}}}</style>
</head><body><p>{escaped}</p><div class="footer">Automātiski ģenerēts sākotnējs pārskats. Pirms juridiskas izmantošanas pārbaudīt oriģinālos nolēmumus.</div></body></html>"""


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
            row = conn.execute("select summary, legal_issue, court_reasoning, outcome from ai_summaries where materialfileid=? and error_message is null", (materialfileid,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


@st.cache_data(show_spinner=False)
def similar_cases(db: str, materialfileid: str, limit: int = 5) -> list[dict]:
    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            src = conn.execute("select embedding, model_name from semantic_embeddings where materialfileid=?", (materialfileid,)).fetchone()
            if not src:
                return []
            source = np.frombuffer(src["embedding"], dtype=np.float32)
            rows = conn.execute(
                """
                select e.materialfileid, e.embedding, d.court, d.casenumber, d.processtype,
                       d.materialtype, d.registrationdate, d.downloadurl, coalesce(t.topic, '') as topic
                from semantic_embeddings e
                join decisions d on d.materialfileid=e.materialfileid
                left join case_topics t on t.materialfileid=d.materialfileid
                where e.model_name=? and e.materialfileid<>?
                """,
                (src["model_name"], materialfileid),
            ).fetchall()
    except sqlite3.OperationalError:
        return []
    scored = []
    for r in rows:
        vec = np.frombuffer(r["embedding"], dtype=np.float32)
        if vec.shape != source.shape:
            continue
        item = dict(r)
        item.pop("embedding", None)
        item["similarity"] = float(np.dot(source, vec))
        scored.append(item)
    return sorted(scored, key=lambda x: x["similarity"], reverse=True)[:limit]


def summary_html(ai: dict | None, fallback: str) -> tuple[str, bool]:
    if not ai:
        return f"<strong>Būtība:</strong><br>{html.escape(fallback)}", False
    parts = []
    labels = [("summary", "AI kopsavilkums"), ("legal_issue", "Juridiskais jautājums"), ("court_reasoning", "Tiesas secinājums"), ("outcome", "Rezultāts")]
    for key, label in labels:
        if ai.get(key):
            parts.append(f"<strong>{label}:</strong><br>{html.escape(ai[key])}")
    return ("<br><br>".join(parts) if parts else f"<strong>Būtība:</strong><br>{html.escape(fallback)}"), bool(parts)


def topic_confidence(score: int | None) -> str:
    value = int(score or 0)
    if value >= 4:
        return "augsta"
    if value >= 2:
        return "vidēja"
    if value == 1:
        return "zema"
    return "nav novērtēta"


def topic_confidence_class(score: int | None) -> str:
    value = int(score or 0)
    if value >= 4:
        return "confidence-high"
    if value >= 2:
        return "confidence-medium"
    return "confidence-low"


def topic_confidence_note(score: int | None) -> str:
    value = int(score or 0)
    if value >= 4:
        return "Tēma balstās uz vairākām sakritībām."
    if value >= 2:
        return "Tēma ir ticama, bet vēl pārbaudāma pēc pilnā teksta."
    if value == 1:
        return "Tēma noteikta ar zemu pārliecību; pārbaudi pilno tekstu."
    return "Tēmas kvalitāte nav novērtēta."


def result_title(row: dict) -> str:
    return row.get("casenumber") or row.get("materialfileid") or "Bez lietas numura"


def metadata_lines(row: dict) -> list[str]:
    return [
        f"Tiesa: {row.get('court') or '-'}",
        f"Datums: {row.get('registrationdate') or '-'}",
        f"Process: {row.get('processtype') or '-'}",
        f"Nolēmuma veids: {row.get('materialtype') or '-'}",
        f"Tēma: {row.get('topic') or 'Bez tēmas'}",
    ]


def report_item(row: dict, summary: str) -> dict:
    item = dict(row)
    item["answer_text"] = summary
    return item


def render_start_screen(db: str) -> None:
    overview = topic_overview(db)
    st.subheader("Demo scenāriji")
    st.dataframe(pd.DataFrame(DEMO_SCENARIOS), width="stretch", hide_index=True)

    left, right = st.columns([2, 1])
    with left:
        st.subheader("Datu profils")
        if overview.empty:
            st.info("Tēmu pārskats vēl nav pieejams.")
        else:
            st.bar_chart(overview.set_index("Tēma")["Skaits"])
    with right:
        st.subheader("Biežākās tēmas")
        if overview.empty:
            st.write("Nav tēmu datu.")
        else:
            for _, row in overview.head(6).iterrows():
                st.metric(str(row["Tēma"]), int(row["Skaits"]))


def render_similar_list(source_id: str, sims: list[dict]) -> None:
    if not sims:
        st.info("Šai lietai vēl nav semantiskā indeksa vai līdzīgās lietas nav atrastas.")
        return
    for sim in sims:
        pct_value = round(sim["similarity"] * 100, 1)
        title = sim.get("casenumber") or sim.get("materialfileid")
        topic_text = f" · {sim.get('topic')}" if sim.get("topic") else ""
        st.markdown(f"**{title}** — līdzība **{pct_value}%**  \n{sim.get('court') or '-'} · {sim.get('registrationdate') or '-'} · {sim.get('processtype') or '-'} · {sim.get('materialtype') or '-'}{topic_text}")
        if sim.get("downloadurl"):
            st.link_button("📄 Atvērt līdzīgās lietas PDF", sim["downloadurl"], key=f"pdf-{source_id}-{sim['materialfileid']}")
        st.divider()


st.markdown("""
<style>
mark{background:#fff3a3;padding:.05rem .18rem;border-radius:.2rem}.card{border:1px solid #e6e8ef;border-radius:.5rem;padding:1rem;margin-bottom:.85rem;background:#fff}.meta{color:#5f6673;font-size:.92rem;margin:.25rem 0 .65rem}.box{background:#f6f8fb;border-left:4px solid #4f7cff;border-radius:.35rem;padding:.75rem;margin-bottom:.75rem;line-height:1.45}.ai{background:#edf7ed;color:#1f7a1f;border:1px solid #b7e0b7;border-radius:999px;padding:.1rem .5rem;font-size:.82rem}.rules{background:#f6f6f6;color:#666;border:1px solid #ddd;border-radius:999px;padding:.1rem .5rem;font-size:.82rem}.topic{background:#eef4ff;color:#2457a6;border:1px solid #c9d8ff;border-radius:999px;padding:.1rem .5rem;font-size:.82rem;margin-left:.35rem}.confidence-high{background:#ecfdf5;color:#047857;border:1px solid #a7f3d0;border-radius:999px;padding:.1rem .5rem;font-size:.82rem;margin-left:.35rem}.confidence-medium{background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;border-radius:999px;padding:.1rem .5rem;font-size:.82rem;margin-left:.35rem}.confidence-low{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;border-radius:999px;padding:.1rem .5rem;font-size:.82rem;margin-left:.35rem}.smallnote{color:#6b7280;font-size:.88rem}.caution{color:#991b1b;font-size:.9rem;margin:.35rem 0}
</style>
""", unsafe_allow_html=True)

st.title("⚖️ Latvijas tiesu nolēmumu pilots")
st.caption("SQLite, pilnteksta meklēšana, AI kopsavilkumi, PDF, tēmas, tendences, jautājumi, ziņojumi un līdzīgo lietu meklēšana.")

if not DB_PATH.exists():
    st.error(f"Datubāze nav atrasta: `{DB_PATH}`")
    st.stop()

s = stats(str(DB_PATH))
with st.expander("📌 Vadības panelis", expanded=True):
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Datu kopas apjoms", s["metadata"])
    k2.metric("Teksta pārklājums", f"{pct(s['with_text'], s['documents'])}%")
    k3.metric("AI pārklājums", f"{pct(s['ai'], s['with_text'])}%")
    k4.metric("Semantikas pārklājums", f"{pct(s['semantic'], s['with_text'])}%")
    p1, p2, p3 = st.columns(3)
    p1.progress(pct(s["with_text"], s["documents"]) / 100, text=f"Teksts: {s['with_text']} no {s['documents']}")
    p2.progress(pct(s["ai"], s["with_text"]) / 100, text=f"AI: {s['ai']} no {s['with_text']}")
    p3.progress(pct(s["semantic"], s["with_text"]) / 100, text=f"Semantika: {s['semantic']} no {s['with_text']}")
    if s["with_text"] > 0 and s["semantic"] >= min(1000, s["with_text"]):
        st.success("Pilots ir demonstrējams: ir pilnteksta meklēšana, semantika, tēmas, līdzīgās lietas un ziņojumi.")
    else:
        st.info("Pilots darbojas, bet datu pārklājumu vēl var palielināt ar papildu teksta ekstrakciju, AI kopsavilkumiem un semantisko indeksu.")

c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Metadati", s["metadata"]); c2.metric("Dokumenti", s["documents"]); c3.metric("Lejupielādēti", s["downloaded"])
c4.metric("Ar tekstu", s["with_text"]); c5.metric("AI", s["ai"]); c6.metric("Semantika", s["semantic"]); c7.metric("Tēmas", s["topics"])

with st.expander("💬 Jautājums nolēmumu datubāzei", expanded=True):
    qa_col1, qa_col2 = st.columns([3, 1])
    with qa_col1:
        question = st.text_input("Jautājums", placeholder="piemēram: Kāda ir tiesu prakse par kredīta procentu piedziņu?")
    with qa_col2:
        qa_topic_choice = st.selectbox("Jautājuma tēma", ["Visas"] + topics(str(DB_PATH)), key="qa_topic")
    if question:
        qa_topic = None if qa_topic_choice == "Visas" else qa_topic_choice
        answers = answer_question(str(DB_PATH), question, qa_topic, 8)
        if not answers:
            st.warning("Nav atrasti pietiekami atbilstoši nolēmumi. Pamēģini īsāku jautājumu vai noņem tēmas filtru.")
        else:
            st.markdown("**Īsa sintēze no atrastajiem avotiem:**")
            for n, ans in enumerate(answers[:5], 1):
                st.markdown(f"{n}. {ans.get('answer_text') or 'Avotā pieejams fragments, bet kopsavilkums vēl nav sagatavots.'}")
            report = build_smart_report(question, answers)
            report_html = markdown_to_html(report)
            with st.expander("🧾 Gudrais ziņojums"):
                col_md, col_html = st.columns(2)
                with col_md:
                    st.download_button("⬇️ Lejupielādēt Markdown", report, file_name="gudrais_zinojums.md", mime="text/markdown")
                with col_html:
                    st.download_button("⬇️ Lejupielādēt HTML/PDF", report_html, file_name="gudrais_zinojums.html", mime="text/html")
                st.caption("HTML failu atver pārlūkā un izvēlies Print / Save as PDF, lai iegūtu noformētu PDF.")
                st.text_area("Ziņojuma teksts", report, height=520)
            st.markdown("**Avoti:**")
            for ans in answers:
                title = ans.get("casenumber") or ans.get("materialfileid")
                st.markdown(f"**{title}** · {ans.get('court') or '-'} · {ans.get('registrationdate') or '-'} · {ans.get('topic') or 'Bez tēmas'}")
                if ans.get("downloadurl"):
                    st.link_button("📄 Atvērt avota PDF", ans["downloadurl"], key=f"qa-pdf-{ans['materialfileid']}")

with st.expander("📊 Tiesu prakses tendences", expanded=False):
    trend_topic = st.selectbox("Tendences tēma", ["Visas"] + topics(str(DB_PATH)), key="trend_topic")
    selected_trend_topic = None if trend_topic == "Visas" else trend_topic
    by_topic, by_year, by_court = trend_data(str(DB_PATH), selected_trend_topic)
    t1, t2, t3, t4 = st.tabs(["Tēmas", "Gadi", "Tiesas", "Kvalitāte"])
    with t1:
        if by_topic.empty:
            st.info("Tēmu statistika vēl nav pieejama. Palaid `python scripts/classify_topics.py --limit 1500`.")
        else:
            st.bar_chart(by_topic.set_index("Tēma"))
            st.dataframe(by_topic, width="stretch")
    with t2:
        if by_year.empty:
            st.info("Gadu statistika vēl nav pieejama.")
        else:
            st.line_chart(by_year.set_index("Gads"))
            st.dataframe(by_year, width="stretch")
    with t3:
        if by_court.empty:
            st.info("Tiesu statistika vēl nav pieejama.")
        else:
            st.bar_chart(by_court.set_index("Tiesa"))
            st.dataframe(by_court, width="stretch")
    with t4:
        overview = topic_overview(str(DB_PATH))
        if overview.empty:
            st.info("Tēmu kvalitātes pārskats vēl nav pieejams.")
        else:
            st.dataframe(overview, width="stretch", hide_index=True)
            st.caption("Punktu skaits rāda, cik daudz klasifikatora atslēgvārdu sakrita ar nolēmuma tekstu. Tas nav juridisks vērtējums, bet palīdz pamanīt vājāk klasificētas lietas.")

st.subheader("🔎 Līdzīgas lietas pēc būtības")
sim_col1, sim_col2 = st.columns([3, 1])
with sim_col1:
    similar_lookup = st.text_input("Ievadi lietas numuru vai MaterialFileId", placeholder="piemēram: C32339412")
with sim_col2:
    similar_limit = st.slider("Līdzīgo skaits", 3, 15, 5, 1)

if similar_lookup:
    source_case = find_case(str(DB_PATH), similar_lookup)
    if not source_case:
        st.warning("Lieta nav atrasta pēc ievadītā lietas numura vai MaterialFileId.")
    else:
        st.markdown(f"**Avota lieta:** {source_case.get('casenumber') or source_case['materialfileid']}  \n{source_case.get('court') or '-'} · {source_case.get('registrationdate') or '-'} · {source_case.get('processtype') or '-'} · {source_case.get('materialtype') or '-'} · {source_case.get('topic') or 'Bez tēmas'}")
        if source_case.get("downloadurl"):
            st.link_button("📄 Atvērt avota PDF", source_case["downloadurl"])
        render_similar_list(source_case["materialfileid"], similar_cases(str(DB_PATH), source_case["materialfileid"], similar_limit))

st.divider()

with st.sidebar:
    st.header("Meklēšana")
    st.caption("Ātrie vaicājumi")
    for example in EXAMPLE_QUERIES:
        if st.button(example, key=f"example-{example}", width="stretch"):
            st.session_state["search_query"] = example
    query = st.text_input("Meklējamā frāze", placeholder="piemēram: kredīta parāds", key="search_query")
    limit = st.slider("Rezultātu skaits", 5, 50, 10, 5)
    court = st.selectbox("Tiesa", ["Visas"] + courts(str(DB_PATH)))
    topic_choice = st.selectbox("Tēma", ["Visas"] + topics(str(DB_PATH)))
    selected_court = None if court == "Visas" else court
    selected_topic = None if topic_choice == "Visas" else topic_choice
    st.divider()
    st.caption("Datu pārklājums")
    st.progress(pct(s["with_text"], s["documents"]) / 100, text=f"Teksti: {s['with_text']} no {s['documents']}")
    st.progress(pct(s["topics"], s["with_text"]) / 100, text=f"Tēmas: {s['topics']} no {s['with_text']}")

if not query:
    render_start_screen(str(DB_PATH))
    st.stop()

rows = search(str(DB_PATH), query, selected_court, selected_topic, limit)
st.subheader(f"Atrasti rezultāti: {len(rows)}")
active_filters = []
if selected_court:
    active_filters.append(f"tiesa: {selected_court}")
if selected_topic:
    active_filters.append(f"tēma: {selected_topic}")
if active_filters:
    st.caption("Aktīvie filtri: " + " · ".join(active_filters))
if not rows:
    st.warning("Nav rezultātu ar pašreizējiem filtriem.")
    fallback_cols = st.columns(min(3, len(EXAMPLE_QUERIES)))
    for idx, example in enumerate(EXAMPLE_QUERIES[:3]):
        with fallback_cols[idx]:
            if st.button(example, key=f"empty-example-{example}", width="stretch"):
                st.session_state["search_query"] = example
                st.rerun()
    st.stop()

profile = result_profile(rows)
profile_cols = st.columns(4)
for col, (label, value) in zip(profile_cols, profile.items()):
    col.metric(label, value if len(value) <= 40 else value[:37] + "...")
with st.expander("Rezultātu kopaina", expanded=False):
    st.write(f"**Tēmas:** {profile['Tēmas']}")
    st.write(f"**Tiesas:** {profile['Tiesas']}")
    st.write(f"**Procesi:** {profile['Procesi']}")
    st.write(f"**Datumi:** {profile['Datumi']}")

selected_for_report = []

for i, row in enumerate(rows, 1):
    ft = full_text(str(DB_PATH), row["materialfileid"])
    fallback = rule_summary(row, row.get("snippet") or "", ft)
    block, has_ai = summary_html(ai_summary(str(DB_PATH), row["materialfileid"]), fallback)
    badge = "<span class='ai'>AI</span>" if has_ai else "<span class='rules'>noteikumi</span>"
    topic_badge = f"<span class='topic'>{html.escape(row.get('topic') or 'Bez tēmas')}</span>" if row.get("topic") else ""
    confidence_badge = f"<span class='confidence'>tēmas pārliecība: {topic_confidence(row.get('topic_score'))}</span>" if row.get("topic") else ""
    if row.get("topic"):
        confidence_badge = f"<span class='{topic_confidence_class(row.get('topic_score'))}'>tēmas pārliecība: {topic_confidence(row.get('topic_score'))}</span>"
    snippet = clean_snippet(row.get("snippet")) or "Fragments nav pieejams."
    keywords = clean_text(row.get("matched_keywords"))
    keyword_line = f"<div class='smallnote'>Atslēgvārdi: {html.escape(keywords)}</div>" if keywords else ""
    caution_line = f"<div class='caution'>{html.escape(topic_confidence_note(row.get('topic_score')))}</div>" if int(row.get("topic_score") or 0) <= 1 else ""
    why_line = f"<div class='smallnote'>Kāpēc atrasts: pilnteksta sakritība ar vaicājumu “{html.escape(query)}”; tēma noteikta pēc procesa un atslēgvārdiem.</div>"

    st.markdown(f"""
    <div class='card'>
      <strong>#{i} · Lieta {html.escape(result_title(row))}</strong> {badge} {topic_badge} {confidence_badge}
      <div class='meta'>{html.escape(row.get('court') or '-')} · {html.escape(str(row.get('registrationdate') or '-'))} · {html.escape(row.get('processtype') or '-')} · {html.escape(row.get('materialtype') or '-')}</div>
      <div class='box'>{block}</div>
      {why_line}
      {keyword_line}
      {caution_line}
      <strong>Fragments:</strong><br>“{snippet}”
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1, 3])
    with col1:
        if row.get("downloadurl"):
            st.link_button("📄 Atvērt PDF", row["downloadurl"])
        selected = st.checkbox("Atlasīt pārskatam", key=f"select-report-{row['materialfileid']}")
        if selected:
            selected_for_report.append(report_item(row, fallback))
    with col2:
        with st.expander("Lietas detaļas un pilns teksts"):
            st.write(f"**MaterialFileId:** `{row['materialfileid']}`")
            st.table(pd.DataFrame({"Lauks": ["Tiesa", "Datums", "Process", "Nolēmuma veids", "Tēma", "Tēmas pārliecība"], "Vērtība": [
                row.get("court") or "-",
                row.get("registrationdate") or "-",
                row.get("processtype") or "-",
                row.get("materialtype") or "-",
                row.get("topic") or "Bez tēmas",
                topic_confidence(row.get("topic_score")),
            ]}))
            if keywords:
                st.caption(f"Tēmas atslēgvārdi: {keywords}")
            if int(row.get("topic_score") or 0) <= 1:
                st.warning(topic_confidence_note(row.get("topic_score")))
            st.text_area("Pilns nolēmuma teksts", ft[:100000], height=500) if ft else st.warning("Pilns teksts nav pieejams.")

    with st.expander("🔎 Līdzīgas lietas"):
        render_similar_list(row["materialfileid"], similar_cases(str(DB_PATH), row["materialfileid"], 5))

if selected_for_report:
    st.divider()
    st.subheader(f"Pārskats no atlasītajiem nolēmumiem: {len(selected_for_report)}")
    selected_report = build_smart_report(query, selected_for_report)
    selected_report_html = markdown_to_html(selected_report)
    dl1, dl2 = st.columns(2)
    with dl1:
        st.download_button("⬇️ Lejupielādēt Markdown", selected_report, file_name="atlasitie_nolemumi.md", mime="text/markdown")
    with dl2:
        st.download_button("⬇️ Lejupielādēt HTML/PDF", selected_report_html, file_name="atlasitie_nolemumi.html", mime="text/html")
    st.text_area("Pārskata teksts", selected_report, height=420)
