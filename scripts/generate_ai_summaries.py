#!/usr/bin/env python3
"""Generate cached AI summaries for the pilot SQLite database.

The script is intentionally batch-oriented and safe for free/limited quotas.
It only processes decisions that do not already have an AI summary.

Usage:
  python scripts/generate_ai_summaries.py --limit 10

Environment:
  OPENAI_API_KEY is used first when available.
  OPENAI_BASE_URL defaults to https://api.openai.com/v1
  OPENAI_MODEL defaults to gpt-4o-mini
  GITHUB_TOKEN is supported as a fallback for GitHub Models.
  GITHUB_MODELS_ENDPOINT defaults to https://models.github.ai/inference
  GITHUB_MODELS_MODEL defaults to deepseek/DeepSeek-V3-0324
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path("pilot/pilot.sqlite")
DEFAULT_OPENAI_ENDPOINT = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_ENDPOINT = "https://models.github.ai/inference"
DEFAULT_MODEL = "deepseek/DeepSeek-V3-0324"


def configure_output() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

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


def generate_summary(client: Any, model: str, row: sqlite3.Row) -> dict[str, str]:
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
    configure_output()

    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL") or os.environ.get("GITHUB_MODELS_MODEL") or DEFAULT_OPENAI_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="Show pending rows and a prompt preview without calling an AI model.")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"Database not found: {args.db}")

    with sqlite3.connect(args.db) as conn:
        conn.row_factory = sqlite3.Row
        init_db(conn)
        rows = get_pending(conn, args.limit)
        print(f"Pending summaries: {len(rows)}")
        if args.dry_run:
            if rows:
                print("\nPrompt preview:")
                print(build_prompt(rows[0])[:3000])
            return 0

        try:
            from openai import OpenAI
        except ModuleNotFoundError as exc:
            raise SystemExit("openai package is not installed. Run: pip install -r requirements.txt") from exc

        openai_token = os.environ.get("OPENAI_API_KEY")
        github_token = os.environ.get("GITHUB_TOKEN")
        if openai_token:
            endpoint = os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_ENDPOINT)
            token = openai_token
        elif github_token:
            endpoint = os.environ.get("GITHUB_MODELS_ENDPOINT", DEFAULT_ENDPOINT)
            token = github_token
            args.model = os.environ.get("GITHUB_MODELS_MODEL", DEFAULT_MODEL)
        else:
            raise SystemExit("OPENAI_API_KEY or GITHUB_TOKEN is not available.")

        print(f"AI endpoint: {endpoint}")
        print(f"AI model: {args.model}")
        client = OpenAI(base_url=endpoint, api_key=token)

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
