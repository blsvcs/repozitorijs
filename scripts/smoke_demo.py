from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


DB_PATH = Path("pilot/pilot.sqlite")
DEMO_QUERIES = [
    "\u006b\u0072\u0065\u0064\u012b\u0074\u0061 \u0070\u0061\u0072\u0101\u0064\u0073",
    "\u0064\u0061\u0072\u0062\u0061 \u0073\u0061\u006d\u0061\u006b\u0073\u0061",
    "\u0062\u016b\u0076\u0061\u0074\u013c\u0061\u0075\u006a\u0061",
]


def clean_query(query: str) -> str:
    return " ".join(re.findall(r"[\w\u0101\u010d\u0113\u0123\u012b\u0137\u013c\u0146\u0161\u016b\u017e]+", query, flags=re.UNICODE))


def db_stats() -> dict[str, int]:
    with sqlite3.connect(DB_PATH) as conn:
        return {
            "decisions": conn.execute("select count(*) from decisions").fetchone()[0],
            "documents": conn.execute("select count(*) from documents").fetchone()[0],
            "with_text": conn.execute(
                "select count(*) from documents where extracted_text is not null and extracted_text<>''"
            ).fetchone()[0],
            "topics": conn.execute("select count(*) from case_topics").fetchone()[0],
            "fts": conn.execute("select count(*) from documents_fts").fetchone()[0],
        }


def query_result_count(query: str) -> tuple[int, int]:
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            select d.downloadurl
            from documents_fts
            join decisions d on d.materialfileid = documents_fts.materialfileid
            where documents_fts match ?
            order by bm25(documents_fts)
            limit 10
            """,
            (clean_query(query),),
        ).fetchall()
    pdf_links = sum(1 for row in rows if row[0])
    return len(rows), pdf_links


def check_database() -> None:
    if not DB_PATH.exists():
        raise AssertionError(f"Missing database: {DB_PATH}")
    stats = db_stats()
    minimums = {
        "decisions": 10000,
        "documents": 2000,
        "with_text": 2000,
        "topics": 1500,
        "fts": 2000,
    }
    for key, minimum in minimums.items():
        if stats[key] < minimum:
            raise AssertionError(f"{key}={stats[key]} is below expected minimum {minimum}")
    print(f"DB OK: {stats}")


def check_queries() -> None:
    for query in DEMO_QUERIES:
        results, pdf_links = query_result_count(query)
        if results < 5:
            raise AssertionError(f"Query {query!r} returned only {results} results")
        if pdf_links < 5:
            raise AssertionError(f"Query {query!r} returned only {pdf_links} PDF links")
        print(f"QUERY OK: {query!r} results={results} pdf_links={pdf_links}")


def check_streamlit() -> None:
    for query in DEMO_QUERIES:
        app = AppTest.from_file("streamlit_app.py")
        app.run(timeout=30)
        app.text_input(key="search_query").set_value(query)
        app.run(timeout=30)
        if app.exception:
            raise AssertionError(f"Streamlit raised exceptions for {query!r}: {app.exception}")
        subheaders = [subheader.value for subheader in app.subheader]
        if "Atrasti rezult\u0101ti: 10" not in subheaders:
            raise AssertionError(f"Streamlit did not show 10 results for {query!r}: {subheaders}")
        print(f"STREAMLIT OK: {query!r}")

    app = AppTest.from_file("streamlit_app.py")
    app.run(timeout=30)
    app.text_input(key="search_query").set_value(DEMO_QUERIES[0])
    app.run(timeout=30)
    if len(app.checkbox) < 1:
        raise AssertionError("No report selection checkbox found")
    app.checkbox[0].set_value(True)
    app.run(timeout=30)
    subheaders = [subheader.value for subheader in app.subheader]
    if "P\u0101rskats no atlas\u012btajiem nol\u0113mumiem: 1" not in subheaders:
        raise AssertionError(f"Selected report section was not rendered: {subheaders}")
    print("REPORT OK: selected-result report renders")


def main() -> int:
    check_database()
    check_queries()
    check_streamlit()
    print("SMOKE DEMO OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"SMOKE DEMO FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
