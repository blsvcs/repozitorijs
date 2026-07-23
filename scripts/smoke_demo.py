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
SEARCH_FORM_ID = "search_form"
SEARCH_BUTTON_LABEL = "\u004d\u0065\u006b\u006c\u0113\u0074"
EMPTY_SEARCH_HINT = "\u0052\u0065\u007a\u0075\u006c\u0074\u0101\u0074\u0069 \u0070\u0061\u0072\u0101\u0064\u012b\u0073\u0069\u0065\u0073"
RESULTS_HEADER = "\u0041\u0074\u0072\u0061\u0073\u0074\u0069 \u0072\u0065\u007a\u0075\u006c\u0074\u0101\u0074\u0069\u003a \u0031\u0030"
SELECTED_REPORT_HEADER = (
    "\u0050\u0101\u0072\u0073\u006b\u0061\u0074\u0073 \u006e\u006f "
    "\u0061\u0074\u006c\u0061\u0073\u012b\u0074\u0061\u006a\u0069\u0065\u006d "
    "\u006e\u006f\u006c\u0113\u006d\u0075\u006d\u0069\u0065\u006d\u003a \u0031"
)


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


def find_search_submit_button(app: AppTest):
    for button in app.button:
        if getattr(button, "label", "") == SEARCH_BUTTON_LABEL and getattr(button, "form_id", "") == SEARCH_FORM_ID:
            return button
    buttons = [(getattr(button, "label", None), getattr(button, "key", None), getattr(button, "form_id", None)) for button in app.button]
    raise AssertionError(f"Search submit button not found: {buttons}")


def submit_search(app: AppTest, query: str) -> AppTest:
    app.text_input(key="search_query").input(query)
    find_search_submit_button(app).click()
    return app.run(timeout=30)


def markdown_contains(app: AppTest, text: str) -> bool:
    return any(text in markdown.value for markdown in app.markdown)


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
        submit_search(app, query)
        if app.exception:
            raise AssertionError(f"Streamlit raised exceptions for {query!r}: {app.exception}")
        subheaders = [subheader.value for subheader in app.subheader]
        if RESULTS_HEADER not in subheaders:
            raise AssertionError(f"Streamlit did not show 10 results for {query!r}: {subheaders}")
        if markdown_contains(app, EMPTY_SEARCH_HINT):
            raise AssertionError(f"Streamlit still showed the empty-search hint after submit for {query!r}")
        print(f"STREAMLIT SUBMIT OK: {query!r}")

    app = AppTest.from_file("streamlit_app.py")
    app.run(timeout=30)
    app.button(key=f"example-{DEMO_QUERIES[0]}").click()
    app.run(timeout=30)
    subheaders = [subheader.value for subheader in app.subheader]
    if RESULTS_HEADER not in subheaders:
        raise AssertionError(f"Quick query did not show 10 results: {subheaders}")
    print("QUICK QUERY OK: search shortcut renders results")

    app = AppTest.from_file("streamlit_app.py")
    app.run(timeout=30)
    submit_search(app, DEMO_QUERIES[0])
    if len(app.checkbox) < 1:
        raise AssertionError("No report selection checkbox found")
    app.checkbox[0].set_value(True)
    app.run(timeout=30)
    subheaders = [subheader.value for subheader in app.subheader]
    if SELECTED_REPORT_HEADER not in subheaders:
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
