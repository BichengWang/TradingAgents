"""Regression coverage for publishing after the original report sources are lost."""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scripts import (
    build_publish_site as publisher,
    published_history as history,
    report_workflow as workflow,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
OLD_RUN = "20260909_test-model_20260909_120000"
NEW_RUN = "20260923_test-model_20260923_120000"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def make_run(docs: Path, ticker: str, folder: str, action: str = "Hold") -> Path:
    run = docs / ticker / folder
    for relative, text in {
        "1_analysts/market.md": "# Synthetic test fixture\n\n**Current Price**: $100\n",
        "3_trading/trader.md": f"**Action**: {action}\n",
        "5_portfolio/decision.md": (
            "**Rating**: Neutral\n\n**Price Target**: $120\n\n"
            "**Time Horizon**: 12 months\n\n**Confidence**: High\n"
        ),
    }.items():
        path = run / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return run


def hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): publisher.digest(path)
        for path in directory.rglob("*")
        if path.is_file()
    }


def commit_site(repo: Path, directory: Path, ref: str = "refs/remotes/origin/gh-pages") -> str:
    index = repo / ".git" / "test-publish-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    command = ["git", "-C", str(repo), f"--work-tree={directory}"]
    subprocess.run([*command, "read-tree", "--empty"], env=env, check=True)
    subprocess.run([*command, "add", "--all", "--force"], env=env, check=True)
    tree = subprocess.check_output([*command, "write-tree"], env=env, text=True).strip()
    commit = git(repo, "commit-tree", tree, "-m", "Test publication")
    git(repo, "update-ref", ref, commit)
    index.unlink()
    return commit


@pytest.fixture
def publication(tmp_path, monkeypatch):
    pytest.importorskip("mkdocs")
    pytest.importorskip("material")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Publish test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    shutil.copy2(REPO_ROOT / "mkdocs.yml", repo / "mkdocs.yml")
    docs = repo / "docs"
    shutil.copytree(REPO_ROOT / "docs" / "stylesheets", docs / "stylesheets")
    make_run(docs, "AAPL", OLD_RUN)
    make_run(docs, "MSFT", OLD_RUN)
    monkeypatch.setattr(workflow, "DOCS", docs)
    base = repo / "baseline"
    workflow.run_workflow("20260909", allow_na=True, work_root=repo, site_dir=base)
    # A legacy asset must survive a newer theme build as well as the report HTML.
    (base / "assets" / "legacy.js").write_text("legacy asset", encoding="utf-8")
    commit_site(repo, base)
    # Simulate the user's lost source data, keeping only the published HTML.
    shutil.rmtree(docs / "AAPL")
    shutil.rmtree(docs / "MSFT")
    (docs / "index.md").unlink()
    monkeypatch.setattr(publisher, "ROOT", repo)
    monkeypatch.setattr(publisher, "DOCS", docs)
    return repo, docs, base


def build(repo: Path, **kwargs):
    return publisher.build_site(
        analysis_date=kwargs.pop("analysis_date", None),
        retain_dates=kwargs.pop("retain_dates", None),
        site_dir=repo / "_site",
        **kwargs,
    )


def test_new_date_retains_html_only_history_and_rerun_is_noop(publication, monkeypatch):
    repo, docs, base = publication
    make_run(docs, "AAPL", NEW_RUN, "Buy")
    make_run(docs, "NVDA", NEW_RUN)
    # A local edit to an already-published folder must not change its report.
    make_run(docs, "AAPL", OLD_RUN, "Sell")
    source_before = hashes(docs)
    assert build(repo, analysis_date="20260923", retain_dates=1) == ["2026-09-23"]
    output = repo / "_site"
    assert len(history.report_paths(output)) == 4
    for relative in history.report_paths(base):
        assert (output / relative).read_bytes() == (base / relative).read_bytes()
    assert (output / "assets/legacy.js").read_bytes() == b"legacy asset"
    assert hashes(docs) == source_before
    page = history.read_page(output / "index.html")
    assert list(history.daily_sections(page)) == ["2026-09-23", "2026-09-09"]
    assert "4 runs across 3 tickers" in page.get_text()
    assert {a.get_text(strip=True) for a in page.select(".md-tabs__list a")} == {
        "Home",
        "AAPL",
        "MSFT",
        "NVDA",
    }
    history.validate_indexes(output, history.report_paths(output))
    assert (
        gzip.decompress((output / "sitemap.xml.gz").read_bytes())
        == (output / "sitemap.xml").read_bytes()
    )
    assert "20260909" in (output / "sitemap.xml").read_text()
    assert "20260923" in (output / "sitemap.xml").read_text()

    commit_site(repo, output)
    before = hashes(output)
    monkeypatch.setattr(
        workflow, "run_workflow", lambda *a, **kw: pytest.fail("Nothing new to compile")
    )
    assert build(repo) == []
    assert hashes(output) == before


def test_same_date_reruns_models_and_backfills_keep_latest_summary(publication):
    repo, docs, base = publication
    newer = "20260909_test-model_20260909_130000"
    earlier = "20260909_test-model_20260909_110000"
    alternate = "20260909_other-model_20260909_140000"
    for folder, action in [(newer, "Buy"), (earlier, "Sell"), (alternate, "Hold")]:
        make_run(docs, "AAPL", folder, action)
    build(repo)
    output = repo / "_site"
    assert len(history.report_paths(output)) == 5
    rows = history.read_page(output / "index.html").select(".daily-summary-tables tbody tr")
    assert len(rows) == 3  # AAPL's two models, plus the original MSFT.
    aapl = next(row for row in rows if history.summary_key(row) == ("AAPL", "test-model"))
    assert "Buy" in aapl.get_text()
    assert newer in aapl.select_one("a")["href"]
    history.validate_indexes(output, history.report_paths(output))


def test_date_filter_leaves_other_unpublished_dates_for_next_release(publication):
    repo, docs, base = publication
    make_run(docs, "AAPL", NEW_RUN)
    backfill = "20260908_test-model_20260923_150000"
    make_run(docs, "MSFT", backfill)
    build(repo, analysis_date="20260923")
    output = repo / "_site"
    assert not (output / "MSFT" / backfill).exists()
    commit_site(repo, output)
    assert build(repo) == ["2026-09-08"]
    assert len(history.report_paths(output)) == 4


@pytest.mark.parametrize("failure", ["compile", "size", "index"])
def test_failed_release_preserves_local_site_and_sources(publication, monkeypatch, failure):
    repo, docs, base = publication
    shutil.copytree(base, repo / "_site")
    make_run(docs, "AAPL", NEW_RUN)
    before_site, before_docs = hashes(repo / "_site"), hashes(docs)
    if failure == "compile":

        def fail(*args, **kwargs):
            raise workflow.WorkflowError("Build failed")

        monkeypatch.setattr(workflow, "build_static_site", fail)
    elif failure == "size":
        monkeypatch.setattr(publisher, "MAX_SITE_BYTES", 1)
    else:
        (base / "index.html").write_text("<html>unexpected page</html>", encoding="utf-8")
        commit_site(repo, base)
    with pytest.raises(workflow.WorkflowError):
        build(repo)
    assert hashes(repo / "_site") == before_site
    assert hashes(docs) == before_docs


def test_missing_baseline_cannot_replace_existing_preview(publication):
    repo, docs, base = publication
    shutil.copytree(base, repo / "_site")
    make_run(docs, "AAPL", NEW_RUN)
    before = hashes(repo / "_site")
    with pytest.raises(workflow.WorkflowError, match="baseline"):
        build(repo, base_ref="missing-branch")
    assert hashes(repo / "_site") == before


def test_shell_release_extends_history_and_preserves_main_index(publication):
    repo, docs, base = publication
    remote = repo.parent / "remote.git"
    subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    before = git(repo, "rev-parse", "origin/gh-pages")
    git(repo, "push", "--quiet", "origin", f"{before}:refs/heads/gh-pages")
    for folder in ("scripts", "cli"):
        shutil.copytree(
            REPO_ROOT / folder, repo / folder, ignore=shutil.ignore_patterns("__pycache__")
        )
    # Verify release plumbing doesn't change the developer's staged source file.
    (repo / "work.txt").write_text("staged work", encoding="utf-8")
    git(repo, "add", "work.txt")
    index_before = (repo / ".git/index").read_bytes()
    make_run(docs, "AAPL", NEW_RUN)
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}"}
    command = ["bash", str(repo / "scripts/publish_site.sh"), "--analysis-date", "20260923"]
    shutil.copytree(base, repo / "_site")
    preview_before, docs_before = hashes(repo / "_site"), hashes(docs)
    subprocess.run(
        [*command, "--dry-run"], cwd=repo, env=env, check=True, capture_output=True, text=True
    )
    assert hashes(repo / "_site") == preview_before
    assert hashes(docs) == docs_before
    assert git(remote, "rev-parse", "gh-pages") == before
    subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True, text=True)
    after = git(remote, "rev-parse", "gh-pages")
    assert git(remote, "rev-parse", "gh-pages^") == before
    assert (repo / ".git/index").read_bytes() == index_before
    assert git(remote, "diff", "--name-only", "--diff-filter=D", before, after) == ""
    subprocess.run(command, cwd=repo, env=env, check=True, capture_output=True, text=True)
    assert git(remote, "rev-parse", "gh-pages") == after

    # A second publisher advances the remote just before our push. The script
    # must refuse the stale release instead of erasing that publisher's commit.
    shim_dir = repo.parent / "git-shim"
    shim_dir.mkdir()
    real_git = shutil.which("git")
    shim = shim_dir / "git"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys\n"
        f"real_git = {real_git!r}\nremote = {str(remote)!r}\n"
        "if sys.argv[1:3] == ['push', 'origin']:\n"
        "    def git(*args):\n"
        "        return subprocess.check_output([real_git, '-C', remote, *args], text=True).strip()\n"
        "    parent = git('rev-parse', 'gh-pages')\n"
        "    tree = git('rev-parse', 'gh-pages^{tree}')\n"
        "    commit = git('-c', 'user.name=Concurrent publisher', '-c', 'user.email=test@example.invalid',\n"
        "                 'commit-tree', tree, '-p', parent, '-m', 'Concurrent release')\n"
        "    git('update-ref', 'refs/heads/gh-pages', commit)\n"
        "sys.exit(subprocess.call([real_git, *sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    make_run(docs, "AAPL", "20260923_second-model_20260923_130000")
    race_env = {**env, "PATH": f"{shim_dir}:{env['PATH']}"}
    result = subprocess.run(command, cwd=repo, env=race_env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "rejected" in result.stderr
    assert git(remote, "log", "-1", "--format=%s", "gh-pages") == "Concurrent release"
    assert git(remote, "rev-parse", "gh-pages^") == after
    assert (repo / ".git/index").read_bytes() == index_before


def test_summary_backfill_with_relative_link_does_not_replace_newer():
    def table(folder: str, action: str, prefix: str):
        return BeautifulSoup(
            f'<table><tbody><tr><td><a href="{prefix}AAPL/{folder}/complete_report/">AAPL</a>'
            f"</td><td>model</td><td>{action}</td>"
            + "<td>value</td>" * 6
            + "</tr></tbody></table>",
            "html.parser",
        ).table

    old = table("20260909_model_20260909_120000", "Buy", "./")
    new = table("20260909_model_20260909_110000", "Sell", "")
    history.merge_summary(old, new)
    assert "Buy" in old.get_text()
    assert "Sell" not in old.get_text()
