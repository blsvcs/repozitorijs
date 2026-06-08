from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

import requests


DEFAULT_REPO = "blsvcs/repozitorijs"
DEFAULT_WORKFLOW = "build_pilot_dataset.yml"
DEFAULT_ARTIFACT = "pilot-dataset"
DEFAULT_OUTPUT = Path("pilot/pilot.sqlite")


def github_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "repozitorijs-pilot-downloader",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def api_get(url: str, token: str | None) -> dict:
    response = requests.get(url, headers=github_headers(token), timeout=30)
    response.raise_for_status()
    return response.json()


def latest_successful_run(repo: str, workflow: str, branch: str, token: str | None) -> dict:
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/runs"
        f"?branch={branch}&status=success&per_page=20"
    )
    runs = api_get(url, token).get("workflow_runs", [])
    for run in runs:
        if run.get("conclusion") == "success":
            return run
    raise RuntimeError(f"No successful {workflow} run found on branch {branch}.")


def find_artifact(repo: str, run_id: int, artifact_name: str, token: str | None) -> dict:
    url = f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?name={artifact_name}"
    artifacts = api_get(url, token).get("artifacts", [])
    for artifact in artifacts:
        if artifact.get("name") == artifact_name and not artifact.get("expired"):
            return artifact
    raise RuntimeError(f"No active artifact named {artifact_name!r} found for run {run_id}.")


def download_zip(url: str, destination: Path, token: str | None) -> None:
    with requests.get(url, headers=github_headers(token), stream=True, timeout=120) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def extract_sqlite(zip_path: Path, output: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if not name.endswith("/") and Path(name).name == "pilot.sqlite"
        ]
        if not candidates:
            raise RuntimeError("The artifact ZIP does not contain pilot.sqlite.")

        output.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(candidates[0]) as source:
            with tempfile.NamedTemporaryFile(delete=False, dir=output.parent, suffix=".tmp") as tmp:
                tmp.write(source.read())
                tmp_path = Path(tmp.name)

    tmp_path.replace(output)


def validate_database(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as conn:
        return {
            "decisions": conn.execute("select count(*) from decisions").fetchone()[0],
            "documents": conn.execute("select count(*) from documents").fetchone()[0],
            "with_text": conn.execute(
                "select count(*) from documents where extracted_text is not null and extracted_text<>''"
            ).fetchone()[0],
        }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download the latest pilot.sqlite GitHub Actions artifact.")
    parser.add_argument("--repo", default=os.getenv("PILOT_REPO", DEFAULT_REPO))
    parser.add_argument("--workflow", default=os.getenv("PILOT_WORKFLOW", DEFAULT_WORKFLOW))
    parser.add_argument("--artifact", default=os.getenv("PILOT_ARTIFACT", DEFAULT_ARTIFACT))
    parser.add_argument("--branch", default=os.getenv("PILOT_BRANCH", "main"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-id", type=int, default=None, help="Use a specific workflow run instead of the latest success.")
    parser.add_argument("--force", action="store_true", help="Replace the local database if it already exists.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")

    if args.output.exists() and not args.force:
        print(f"Local database already exists: {args.output}")
        print("Use --force to replace it with the latest artifact.")
        return 0

    try:
        if args.run_id:
            run_id = args.run_id
            run_url = f"https://github.com/{args.repo}/actions/runs/{run_id}"
        else:
            run = latest_successful_run(args.repo, args.workflow, args.branch, token)
            run_id = int(run["id"])
            run_url = run.get("html_url", f"https://github.com/{args.repo}/actions/runs/{run_id}")

        artifact = find_artifact(args.repo, run_id, args.artifact, token)

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "pilot-dataset.zip"
            download_zip(artifact["archive_download_url"], zip_path, token)
            extract_sqlite(zip_path, args.output)

        stats = validate_database(args.output)
        print(f"Downloaded {args.artifact} from {run_url}")
        print(f"Saved database: {args.output}")
        print(
            "Stats: "
            f"decisions={stats['decisions']}, "
            f"documents={stats['documents']}, "
            f"with_text={stats['with_text']}"
        )
        return 0
    except requests.RequestException as exc:
        print(f"GitHub artifact download failed: {exc}", file=sys.stderr)
        print("Check network access. If GitHub asks for authentication, set GITHUB_TOKEN or GH_TOKEN.", file=sys.stderr)
        return 1
    except (OSError, RuntimeError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"Pilot database update failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
