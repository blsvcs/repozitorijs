from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

DB_PATH = Path("pilot/pilot.sqlite")

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
                   d.registrationdate, d.downloadurl, coalesce(t.topic, '') as topic
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
    sql = f"""
    select d.materialfileid, d.court, d.casenumber, d.processtype, d.materialtype,
           d.registrationdate, d.downloadurl,
           {"t.topic as topic," if topic else "coalesce(t2.topic, '') as topic,"}
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


def build_smart_report(question: str, answers: list[dict]) -> str:
    lines = [
        "# Gudrais ziņojums par tiesu praksi",
        "",
        f"**Jautājums:** {question}",
        "",
        "## Īss secinājums",
        "Zemāk apkopoti atlasītie nolēmumi, kas datubāzē atrasti kā atbilstošākie pēc jautājuma teksta. Šis ir sākotnējs automātisks pārskats; pirms izmantošanas juridiskā darbā jāpārbauda oriģinālie nolēmumi.",
        "",
        "## Galvenie novērojumi",
    ]
    if not answers:
        lines.append("Nav atrasti pietiekami atbilstoši nolēmumi.")
    else:
        topic_counts: dict[str, int] = {}
        for item in answers:
            topic = item.get("topic") or "Bez tēmas"
            topic_counts[topic] = topic_counts.get(topic, 0) + 1
        for topic, count in sorted(topic_counts.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {topic}: {count} atlasīti nolēmumi.")

    lines += ["", "## Atlasīto nolēmumu kopsavilkumi"]
    for i, item in enumerate(answers, 1):
        title = item.get("casenumber") or item.get("materialfileid")
        lines += [
            "",
            f"### {i}. Lieta {title}",
            f"- Tiesa: {item.get('court') or '-'}",
            f"- Datums: {item.get('registrationdate') or '-'}",
            f"- Tēma: {item.get('topic') or 'Bez tēmas'}",
            f"- Nolēmuma veids: {item.get('materialtype') or '-'}",
            "",
            item.get("answer_text") or "Kopsavilkums vēl nav pieejams; skatīt oriģinālo fragmentu vai PDF.",
        ]
    lines += ["", "## Avoti"]
    for item in answers:
        title = item.get("casenumber") or item.get("materialfileid")
        lines.append(f"- {title} — {item.get('court') or '-'} — {item.get('registrationdate') or '-'}")
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
<html lang="lv">
<head>
<meta charset="utf-8">
<title>Gudrais ziņojums</title>
<style>
body {{ font-family: Arial, sans-serif; color: #1f2937; max-width: 900px; margin: 40px auto; line-height: 1.55; }}
h1 {{ color: #111827; border-bottom: 3px solid #2563eb; padding-bottom: 12px; }}
h2 {{ color: #1d4ed8; margin-top: 28px; }}
h3 {{ color: #374151; margin-top: 22px; }}
p {{ margin: 0 0 12px 0; }}
li {{ margin: 6px 0; }}
.footer {{ margin-top: 40px; color: #6b7280; font-size: 12px; border-top: 1px solid #e5e7eb; padding-top: 12px; }}
@media print {{ body {{ margin: 20mm; }} }}
</style>
</head>
<body>
<p>{escaped}</p>
<div class="footer">Automātiski ģenerēts sākotnējs pārskats. Pirms juridiskas izmantošanas pārbaudīt oriģinālos nolēmumus.</div>
</body>
</html>"""


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


def render_similar_list(source_id: str, sims: list[dict]) -> None:
    if not sims:
        st.info("Šai lietai vēl nav semantiskā indeksa vai līdzīgās lietas nav atrastas.")
        return
    for sim in sims:
        pct = round(sim["similarity"] * 100, 1)
        title = sim.get("casenumber") or sim.get("materialfileid")
        topic_text = f" · {sim.get('topic')}" if sim.get("topic") else ""
        st.markdown(f"**{title}** — līdzība **{pct}%**  \n{sim.get('court') or '-'} · {sim.get('registrationdate') or '-'} · {sim.get('processtype') or '-'} · {sim.get('materialtype') or '-'}{topic_text}")
        if sim.get("downloadurl"):
            st.link_button("📄 Atvērt līdzīgās lietas PDF", sim["downloadurl"], key=f"pdf-{source_id}-{sim['materialfileid']}")
        st.divider()


st.markdown("""
<style>
mark{background:#fff3a3;padding:.05rem .18rem;border-radius:.2rem}.card{border:1px solid #e6e8ef;border-radius:.75rem;padding:1rem;margin-bottom:.85rem;background:#fff}.meta{color:#5f6673;font-size:.92rem;margin:.25rem 0 .65rem}.box{background:#f6f8fb;border-left:4px solid #7c9cff;border-radius:.45rem;padding:.75rem;margin-bottom:.75rem;line-height:1.45}.ai{background:#edf7ed;color:#1f7a1f;border:1px solid #b7e0b7;border-radius:999px;padding:.1rem .5rem;font-size:.82rem}.rules{background:#f6f6f6;color:#666;border:1px solid #ddd;border-radius:999px;padding:.1rem .5rem;font-size:.82rem}.topic{background:#eef4ff;color:#2457a6;border:1px solid #c9d8ff;border-radius:999px;padding:.1rem .5rem;font-size:.82rem;margin-left:.35rem}
</style>
""", unsafe_allow_html=True)

st.title("⚖️ Latvijas tiesu nolēmumu pilots")
st.caption("SQLite, pilnteksta meklēšana, AI kopsavilkumi, PDF, tēmas, tendences, jautājumi, ziņojumi un līdzīgo lietu meklēšana.")

if not DB_PATH.exists():
    st.error(f"Datubāze nav atrasta: `{DB_PATH}`")
    st.stop()

s = stats(str(DB_PATH))
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
    t1, t2, t3 = st.tabs(["Tēmas", "Gadi", "Tiesas"])
    with t1:
        if by_topic.empty:
            st.info("Tēmu statistika vēl nav pieejama. Palaid `python scripts/classify_topics.py --limit 1500`.")
        else:
            st.bar_chart(by_topic.set_index("Tēma"))
            st.dataframe(by_topic, use_container_width=True)
    with t2:
        if by_year.empty:
            st.info("Gadu statistika vēl nav pieejama.")
        else:
            st.line_chart(by_year.set_index("Gads"))
            st.dataframe(by_year, use_container_width=True)
    with t3:
        if by_court.empty:
            st.info("Tiesu statistika vēl nav pieejama.")
        else:
            st.bar_chart(by_court.set_index("Tiesa"))
            st.dataframe(by_court, use_container_width=True)

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
    query = st.text_input("Meklējamā frāze", placeholder="piemēram: kredīta parāds")
    limit = st.slider("Rezultātu skaits", 5, 50, 10, 5)
    court = st.selectbox("Tiesa", ["Visas"] + courts(str(DB_PATH)))
    topic_choice = st.selectbox("Tēma", ["Visas"] + topics(str(DB_PATH)))
    selected_court = None if court == "Visas" else court
    selected_topic = None if topic_choice == "Visas" else topic_choice

if not query:
    st.info("Ieraksti meklējamo frāzi kreisajā pusē, lai sāktu pilnteksta meklēšanu.")
    st.stop()

rows = search(str(DB_PATH), query, selected_court, selected_topic, limit)
st.subheader(f"Atrasti rezultāti: {len(rows)}")
if not rows:
    st.warning("Nav rezultātu. Pamēģini īsāku frāzi vai noņem tēmas/tiesas filtru.")
    st.stop()

for i, row in enumerate(rows, 1):
    ft = full_text(str(DB_PATH), row["materialfileid"])
    fallback = rule_summary(row, row.get("snippet") or "", ft)
    block, has_ai = summary_html(ai_summary(str(DB_PATH), row["materialfileid"]), fallback)
    badge = "<span class='ai'>AI</span>" if has_ai else "<span class='rules'>noteikumi</span>"
    topic_badge = f"<span class='topic'>{html.escape(row.get('topic') or 'Bez tēmas')}</span>" if row.get("topic") else ""
    snippet = clean_snippet(row.get("snippet")) or "Fragments nav pieejams."

    st.markdown(f"""
    <div class='card'>
      <strong>#{i} · Lieta {html.escape(row.get('casenumber') or 'Bez lietas numura')}</strong> {badge} {topic_badge}
      <div class='meta'>{html.escape(row.get('court') or '-')} · {html.escape(str(row.get('registrationdate') or '-'))} · {html.escape(row.get('processtype') or '-')} · {html.escape(row.get('materialtype') or '-')}</div>
      <div class='box'>{block}</div>
      <strong>Fragments:</strong><br>“{snippet}”
    </div>
    """, unsafe_allow_html=True)

    col1, col2 = st.columns([1, 3])
    with col1:
        if row.get("downloadurl"):
            st.link_button("📄 Atvērt PDF", row["downloadurl"])
    with col2:
        with st.expander("Atvērt pilnu nolēmuma tekstu"):
            st.write(f"**MaterialFileId:** `{row['materialfileid']}`")
            st.text_area("Pilns nolēmuma teksts", ft[:100000], height=500) if ft else st.warning("Pilns teksts nav pieejams.")

    with st.expander("🔎 Līdzīgas lietas"):
        render_similar_list(row["materialfileid"], similar_cases(str(DB_PATH), row["materialfileid"], 5))
