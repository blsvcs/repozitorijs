#!/usr/bin/env python3
"""Generate cached AI summaries for the pilot SQLite database using GitHub Models.

The script is intentionally batch-oriented and safe for free/limited quotas.
It only processes decisions that do not already have an AI summary.

Usage:
  python scripts/generate_ai_summaries.py --limit 10

Environment:
  GITHUB_TOKEN is used by default.
  GITHUB_MODELS_ENDPOINT defaults to https://models.github.ai/inference
  GITHUB_MODELS_MODEL defaults to deepseek/DeepSeek-V3-0324
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

DB_PATH = Path("pilot/pilot.sqlite")
DEFAULT_ENDPOINT = "https://models.github.ai/inference"
DEFAULT_MODEL = "deepseek/DeepSeek-V3-0324"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_summaries (
    materialfileid TEXT PRIMARY KEY,
    summary TEXT,
    legal_issue TEXT,
    court_reasoning TEXT,
    outcome TEXT,
    model_name TEXT,
    created_at TEXT,
    error_message TEXT,
    FOREIGN KEY(materialfileid) REFERENCES decisions(materialfileid)
);
"""

SYSTEM_PROMPT = """Tu esi juridisko nolēmumu analītiķis Latvijā.
Izveido īsu, praktisku un neitrālu kopsavilkumu latviešu valodā.
Neizdomā faktus. Ja informācija tekstā nav skaidra, norādi, ka to nevar droši noteikt.
Atbildi tikai JSON formātā ar laukiem:
summary, legal_issue, court_reasoning, outcome.
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def get_pending(conn: sqlite3.Connection, limit: int):
    return conn.execute(
        """
        SELECT
            d.materialfileid,
            d.court,
            d.casenumber,
            d.processtype,
            d.materialtype,
            d.registrationdate,
            doc.extracted_text
        FROM documents doc
        JOIN decisions d ON d.materialfileid = doc.materialfileid
        LEFT JOIN ai_summaries s ON s.materialfileid = doc.materialfileid
        WHERE doc.download_status = 'downloaded'
          AND doc.extracted_text IS NOT NULL
          AND doc.extracted_text <> ''
          AND s.materialfileid IS NULL
        ORDER BY d.registrationdate, d.materialfileid
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def build_prompt(row: sqlite3.Row) -> str:
    text = (row["extracted_text"] or "")[:12000]
    return f"""
Metadati:
- Lieta: {row['casenumber'] or '-'}
- Tiesa: {row['court'] or '-'}
- Datums: {row['registrationdate'] or '-'}
- Process: {row['processtype'] or '-'}
- Nolēmuma veids: {row['materialtype'] or '-'}

Nolēmuma teksts:
{text}
""".strip()


def parse_json_response(content: str) -> dict[str, str]:
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content.replace("json\n", "", 1).replace("JSON\n", "", 1).strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {"summary": content, "legal_issue": "", "court_reasoning": "", "outcome": ""}
    return {
        "summary": str(data.get("summary", "")).strip(),
        "legal_issue": str(data.get("legal_issue", "")).strip(),
        "court_reasoning": str(data.get("court_reasoning", "")).strip(),
        "outcome": str(data.get("outcome", "")).strip(),
    }


def generate_summary(client: OpenAI, model: str, row: sqlite3.Row) -> dict[str, str]:
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        max_tokens=650,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_prompt(row)},
        ],
    )
    content = response.choices[0].message.content or ""
    return parse_json_response(content)


def save_summary(conn: sqlite3.Connection, materialfileid: str, data: dict[str, str], model: str, error: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO ai_summaries(
            materialfileid, summary, legal_issue, court_reasoning, outcome,
            model_name, created_at, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(materialfileid) DO UPDATE SET
            summary = excluded.summary,
            legal_issue = excluded.legal_issue,
            court_reasoning = excluded.court_reasoning,
            outcome = excluded.outcome,
            model_name = excluded.model_name,
            created_at = excluded.created_at,
            error_message = excluded.error_message
        """,
        (
            materialfileid,
            data.get("summary", ""),
            data.get("legal_issue", ""),
            data.get("court_reasoning", ""),
            data.get("outcome", ""),
            model,
            now_iso(),
            error,
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--model", default=os.environ.get("GITHUB_MODELS_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not available. Run this in GitHub Actions or Codespaces.")

    endpoint = os.environ.get("GITHUB_MODELS_ENDPOINT", DEFAULT_ENDPOINT)
    client = OpenAI(base_url=endpoint, api_key=token)

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        init_db(conn)
        rows = get_pending(conn, args.limit)
        print(f"Pending summaries: {len(rows)}")

        for idx, row in enumerate(rows, start=1):
            materialfileid = row["materialfileid"]
            print(f"[{idx}/{len(rows)}] {materialfileid}")
            try:
                data = generate_summary(client, args.model, row)
                save_summary(conn, materialfileid, data, args.model)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                print(f"FAILED {materialfileid}: {message}")
                save_summary(
                    conn,
                    materialfileid,
                    {"summary": "", "legal_issue": "", "court_reasoning": "", "outcome": ""},
                    args.model,
                    error=message[:1000],
                )
            time.sleep(args.sleep)

        total = conn.execute("SELECT count(*) FROM ai_summaries WHERE error_message IS NULL").fetchone()[0]
        failed = conn.execute("SELECT count(*) FROM ai_summaries WHERE error_message IS NOT NULL").fetchone()[0]
        print(f"AI summaries ready: {total}; failed: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
