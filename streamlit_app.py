from __future__ import annotations

import html
import re
import sqlite3
from pathlib import Path

import numpy as np
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
        try:
            out["ai"] = conn.execute("select count(*) from ai_summaries where error_message is null").fetchone()[0]
        except sqlite3.OperationalError:
            pass
        try:
            out["semantic"] = conn.execute("select count(*) from semantic_embeddings").fetchone()[0]
        except sqlite3.OperationalError:
            pass
        try:
            out["topics"] = conn.execute("select count(*) from case_topics").fetchone()[0]
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
                "select summary, legal_issue, court_reasoning, outcome from ai_summaries where materialfileid=? and error_message is null",
                (materialfileid,),
            ).fetchone()
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
st.caption("SQLite, pilnteksta meklēšana, AI kopsavilkumi, PDF, tēmas un līdzīgo lietu meklēšana.")

if not DB_PATH.exists():
    st.error(f"Datubāze nav atrasta: `{DB_PATH}`")
    st.stop()

s = stats(str(DB_PATH))
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Metadati", s["metadata"]); c2.metric("Dokumenti", s["documents"]); c3.metric("Lejupielādēti", s["downloaded"])
c4.metric("Ar tekstu", s["with_text"]); c5.metric("AI", s["ai"]); c6.metric("Semantika", s["semantic"]); c7.metric("Tēmas", s["topics"])

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
        st.markdown(
            f"**Avota lieta:** {source_case.get('casenumber') or source_case['materialfileid']}  \n"
            f"{source_case.get('court') or '-'} · {source_case.get('registrationdate') or '-'} · "
            f"{source_case.get('processtype') or '-'} · {source_case.get('materialtype') or '-'} · {source_case.get('topic') or 'Bez tēmas'}"
        )
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
