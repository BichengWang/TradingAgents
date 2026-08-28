#!/usr/bin/env python3
"""Build a GitHub Pages-sized reports site from completed analysis dates.

The full local ``docs/`` history is intentionally retained for research, but
rendering every report stage makes the static site exceed GitHub Pages' 1 GB
published-site limit. This command creates an isolated docs snapshot, keeps
every completed report by default, regenerates derived pages, and builds it
into the requested site directory.
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

import build_reports_site as site
import report_workflow as workflow


ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def available_dates() -> list[str]:
    runs = workflow.discover_runs(DOCS)
    return sorted({run.analysis_date for ticker_runs in runs.values() for run in ticker_runs})


def copy_publication_docs(destination: Path, dates: set[str]) -> None:
    """Copy only static assets and report folders matching ``dates``."""
    destination.mkdir()
    for entry in sorted(DOCS.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name in site.NON_TICKER_DIRS:
            shutil.copytree(entry, destination / entry.name)
            continue
        if not site.TICKER_DIR_RE.match(entry.name):
            continue

        target_ticker = destination / entry.name
        for run_dir in sorted(entry.iterdir()):
            run = site.parse_run_folder(entry, run_dir)
            if run is None or run.analysis_date not in dates:
                continue
            target_ticker.mkdir(exist_ok=True)
            shutil.copytree(run_dir, target_ticker / run_dir.name)


def build_site(
    *,
    analysis_date: str | None,
    retain_dates: int | None,
    site_dir: Path,
) -> list[str]:
    dates = available_dates()
    if not dates:
        raise workflow.WorkflowError("No run folders found under docs/.")
    if retain_dates is not None and retain_dates < 1:
        raise workflow.WorkflowError("--retain-dates must be at least 1")

    selected_dates = dates if retain_dates is None else dates[-retain_dates:]
    if analysis_date:
        try:
            normalized = site.normalize_analysis_date(analysis_date)
        except ValueError as exc:
            raise workflow.WorkflowError(str(exc)) from exc
        if normalized not in selected_dates:
            selected_dates = sorted((set(selected_dates) | {normalized}))
    selected = set(selected_dates)

    with tempfile.TemporaryDirectory(prefix="tradingagents-publish-") as tmp:
        tmp_root = Path(tmp)
        tmp_docs = tmp_root / "docs"
        copy_publication_docs(tmp_docs, selected)
        shutil.copy2(ROOT / "mkdocs.yml", tmp_root / "mkdocs.yml")

        old_docs, old_site_docs = workflow.DOCS, site.DOCS_DIR
        workflow.DOCS = tmp_docs
        site.DOCS_DIR = tmp_docs
        try:
            workflow.run_workflow(
                analysis_date or selected_dates[-1],
                require_coverage=False,
                allow_na=True,
                work_root=tmp_root,
                site_dir=site_dir,
            )
        finally:
            workflow.DOCS = old_docs
            site.DOCS_DIR = old_site_docs

    return selected_dates


def parse_args() -> argparse.Namespace:
    def retention_count(value: str) -> int | None:
        if value.lower() == "all":
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a positive integer or 'all'") from exc

    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-date", help="Optional YYYYMMDD or YYYY-MM-DD focus date.")
    parser.add_argument(
        "--retain-dates",
        type=retention_count,
        default=None,
        help="Number of newest analysis dates to publish, or 'all' (default: all).",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=ROOT / "_site",
        help="Destination directory for the compiled site.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build in a temporary site directory without writing the repository.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.dry_run:
            with tempfile.TemporaryDirectory(prefix="tradingagents-publish-output-") as tmp:
                dates = build_site(
                    analysis_date=args.analysis_date,
                    retain_dates=args.retain_dates,
                    site_dir=Path(tmp) / "_site",
                )
        else:
            dates = build_site(
                analysis_date=args.analysis_date,
                retain_dates=args.retain_dates,
                site_dir=args.site_dir.resolve(),
            )
    except workflow.WorkflowError as exc:
        print(str(exc))
        return 1
    print(("Validated" if args.dry_run else "Published") + " analysis dates: " + ", ".join(dates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
