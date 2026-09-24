#!/usr/bin/env python3
"""Add unpublished local reports to a snapshot of the published gh-pages site."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import (  # noqa: E402
    build_reports_site as site,
    published_history as history,
    report_workflow as workflow,
)

DOCS = ROOT / "docs"
MAX_SITE_BYTES = 1_000_000_000


def available_dates() -> list[str]:
    runs = workflow.discover_runs(DOCS)
    return sorted({run.analysis_date for ticker_runs in runs.values() for run in ticker_runs})


def copy_publication_docs(destination: Path, runs: list[site.Run]) -> None:
    """Copy assets and unpublished report folders, never the historical sources."""
    destination.mkdir()
    for entry in sorted(DOCS.iterdir()):
        if entry.is_dir() and entry.name in {"assets", "stylesheets"}:
            shutil.copytree(entry, destination / entry.name)
    for run in runs:
        relative = Path(run.ticker) / run.folder_name
        shutil.copytree(DOCS / relative, destination / relative)


def snapshot_published_site(destination: Path, base_ref: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{base_ref}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise workflow.WorkflowError(
            f"Published baseline {base_ref!r} is unavailable. Fetch origin gh-pages first."
        )
    commit = result.stdout.strip()
    destination.mkdir()
    with subprocess.Popen(["git", "archive", commit], cwd=ROOT, stdout=subprocess.PIPE) as proc:
        assert proc.stdout is not None
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            for member in archive:
                target = destination / member.name
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise workflow.WorkflowError("Unsafe path in published site")
                if ".git" in Path(member.name).parts:
                    raise workflow.WorkflowError("Unexpected .git path in published site")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = archive.extractfile(member)
                    assert source is not None
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                else:
                    raise workflow.WorkflowError("Published site contains a link or special file")
        if proc.wait():
            raise workflow.WorkflowError("Could not read the published site archive")
    if not (destination / "index.html").is_file():
        raise workflow.WorkflowError("Published baseline has no index.html")
    return commit


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identical_sites(left: Path, right: Path) -> bool:
    if not right.is_dir():
        return False
    left_files = {p.relative_to(left) for p in left.rglob("*") if p.is_file()}
    right_files = {p.relative_to(right) for p in right.rglob("*") if p.is_file()}
    return left_files == right_files and all(
        digest(left / path) == digest(right / path) for path in left_files
    )


def install_preview(staged: Path, destination: Path) -> None:
    """Install a validated build while keeping the previous local preview recoverable."""
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise workflow.WorkflowError(f"Site destination must be a directory: {destination}")
    # Limit this build tool to output directories, never source/worktree roots.
    if (
        destination == ROOT
        or destination in ROOT.parents
        or destination.is_relative_to(DOCS)
        or destination.is_relative_to(ROOT / ".git")
    ):
        raise workflow.WorkflowError(f"Unsafe site destination: {destination}")
    if destination.exists() and not (destination / "index.html").is_file():
        raise workflow.WorkflowError(f"Existing destination is not a compiled site: {destination}")
    previous = None
    if destination.exists():
        backup_root = ROOT / ".tradingagents" / "site-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        previous = Path(tempfile.mkdtemp(prefix="previous-", dir=backup_root)) / "site"
        destination.rename(previous)
    try:
        staged.rename(destination)
    except OSError:
        if previous is not None:
            previous.rename(destination)
        raise
    if previous is not None:
        print(f"Previous local preview saved to {previous}")


def build_site(
    *,
    analysis_date: str | None,
    retain_dates: int | None,
    site_dir: Path,
    base_ref: str = "origin/gh-pages",
) -> list[str]:
    dates = available_dates()
    if retain_dates is not None and retain_dates < 1:
        raise workflow.WorkflowError("--retain-dates must be at least 1")

    selected_dates = dates if retain_dates is None else dates[-retain_dates:]
    if analysis_date:
        try:
            normalized = site.normalize_analysis_date(analysis_date)
        except ValueError as exc:
            raise workflow.WorkflowError(str(exc)) from exc
        if normalized not in dates:
            raise workflow.WorkflowError(f"No local run folders for analysis date {normalized}.")
        selected_dates = [normalized]
    selected = set(selected_dates)

    if site_dir.is_symlink():
        raise workflow.WorkflowError(f"Site destination must not be a symlink: {site_dir}")
    site_dir = site_dir.resolve()
    if (
        site_dir == ROOT
        or site_dir in ROOT.parents
        or site_dir.is_relative_to(DOCS)
        or site_dir.is_relative_to(ROOT / ".git")
    ):
        raise workflow.WorkflowError(f"Unsafe site destination: {site_dir}")
    site_dir.parent.mkdir(parents=True, exist_ok=True)
    # Stage on the destination filesystem so the final rename is atomic.
    with tempfile.TemporaryDirectory(prefix=".publish-", dir=site_dir.parent) as tmp:
        tmp_root = Path(tmp)
        merged = tmp_root / "merged"
        commit = snapshot_published_site(merged, base_ref)
        old_paths = history.report_paths(merged)
        runs = [
            run
            for ticker_runs in workflow.discover_runs(DOCS).values()
            for run in ticker_runs
            if run.analysis_date in selected
            and f"{run.ticker}/{run.folder_name}/complete_report/index.html" not in old_paths
        ]
        if not runs:
            print(f"No unpublished report folders; baseline {commit[:12]} is unchanged.")
            history.validate_indexes(merged, old_paths)
            if not identical_sites(merged, site_dir):
                install_preview(merged, site_dir)
            return []

        old_hashes = {path: digest(merged / path) for path in old_paths}
        tmp_docs = tmp_root / "docs"
        new_site = tmp_root / "new-site"
        copy_publication_docs(tmp_docs, runs)
        shutil.copy2(ROOT / "mkdocs.yml", tmp_root / "mkdocs.yml")

        old_docs, old_site_docs = workflow.DOCS, site.DOCS_DIR
        workflow.DOCS = tmp_docs
        site.DOCS_DIR = tmp_docs
        try:
            # Reassemble every new run, including backfills and multiple runs of
            # the same ticker/model; the homepage still selects its latest run.
            workflow.process_selected_runs({(r.ticker, r.folder_name): r for r in runs})
            workflow.run_workflow(
                analysis_date or max(r.analysis_date for r in runs),
                require_coverage=False,
                allow_na=True,
                work_root=tmp_root,
                site_dir=new_site,
            )
        finally:
            workflow.DOCS = old_docs
            site.DOCS_DIR = old_site_docs

        new_paths = history.report_paths(new_site)
        expected_new = {f"{r.ticker}/{r.folder_name}/complete_report/index.html" for r in runs}
        if new_paths != expected_new:
            raise workflow.WorkflowError("Compiled reports do not match unpublished run folders")
        all_paths = old_paths | new_paths
        focus = site.normalize_analysis_date(analysis_date) or max(r.analysis_date for r in runs)
        history.merge_home(merged / "index.html", new_site / "index.html", all_paths, focus)
        tickers = sorted({path.split("/", 1)[0] for path in all_paths})
        for ticker in tickers:
            old_hub, new_hub = merged / ticker / "index.html", new_site / ticker / "index.html"
            if old_hub.is_file() and new_hub.is_file():
                history.merge_ticker(old_hub, new_hub, tickers)
            elif new_hub.is_file():
                page = history.read_page(new_hub)
                history.navigation(page, tickers, ticker)
                new_hub.write_text(str(page), encoding="utf-8")
        history.merge_sitemap(merged / "sitemap.xml", new_site / "sitemap.xml")
        shutil.copytree(new_site, merged, dirs_exist_ok=True)
        (merged / ".nojekyll").touch()
        for path, checksum in old_hashes.items():
            if not (merged / path).is_file() or digest(merged / path) != checksum:
                raise workflow.WorkflowError(f"Published report changed during merge: {path}")
        history.validate_indexes(merged, all_paths)
        size = sum(p.stat().st_size for p in merged.rglob("*") if p.is_file())
        if size > MAX_SITE_BYTES:
            raise workflow.WorkflowError(
                f"Merged site is {size:,} bytes, above the {MAX_SITE_BYTES:,}-byte budget. "
                "No historical reports were removed; move to a larger host or archive explicitly."
            )
        install_preview(merged, site_dir)
        print(f"Preserved {len(old_paths)} reports; added {len(new_paths)}; total {size:,} bytes.")

    return sorted({run.analysis_date for run in runs})


def parse_args() -> argparse.Namespace:
    def retention_count(value: str) -> int | None:
        if value.lower() == "all":
            return None
        try:
            return int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a positive integer or 'all'") from exc

    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-date", help="Only add unpublished runs for this date.")
    parser.add_argument("--base-ref", default="origin/gh-pages", help="Published Git baseline.")
    parser.add_argument(
        "--retain-dates",
        type=retention_count,
        default=None,
        help="Consider this many newest local dates (default: all); never prune published dates.",
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
                    base_ref=args.base_ref,
                )
        else:
            dates = build_site(
                analysis_date=args.analysis_date,
                retain_dates=args.retain_dates,
                site_dir=args.site_dir,
                base_ref=args.base_ref,
            )
    except (workflow.WorkflowError, OSError, ValueError, tarfile.TarError) as exc:
        print(str(exc))
        return 1
    print(
        ("Validated" if args.dry_run else "Built")
        + " new analysis dates: "
        + (", ".join(dates) or "none")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
