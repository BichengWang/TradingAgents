"""Cloudflare Pages mirror of the published gh-pages site."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import (
    build_publish_site as publisher,
    publish_cloudflare as cloudflare,
    report_workflow as workflow,
)

FAKE_WRANGLER = """\
import json, os, sys
from pathlib import Path
upload = Path(sys.argv[3])
log = Path(os.environ["FAKE_WRANGLER_LOG"])
calls = json.loads(log.read_text()) if log.exists() else []
calls.append({
    "argv": sys.argv[1:],
    "cwd": os.getcwd(),
    "files": sorted(p.relative_to(upload).as_posix() for p in upload.rglob("*") if p.is_file()),
    "redirects": (upload / "_redirects").read_text(),
    "token": os.environ.get("CLOUDFLARE_API_TOKEN"),
})
log.write_text(json.dumps(calls))
sys.exit(int(os.environ.get("FAKE_WRANGLER_EXIT", "0")))
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def fake_wrangler(directory: Path) -> tuple[str, Path]:
    """Return a TRADINGAGENTS_WRANGLER value and the log its calls are written to."""
    script = directory / "fake_wrangler.py"
    script.write_text(FAKE_WRANGLER, encoding="utf-8")
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}", directory / "calls.json"


def calls(log: Path) -> list[dict]:
    return json.loads(log.read_text()) if log.exists() else []


@pytest.fixture
def published(tmp_path, monkeypatch):
    """A repository whose gh-pages ref holds a tiny compiled site."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "--quiet")
    git(repo, "config", "user.name", "Publish test")
    git(repo, "config", "user.email", "test@example.invalid")
    site = tmp_path / "site"
    (site / "AAPL").mkdir(parents=True)
    for relative in ("index.html", "404.html", "AAPL/index.html"):
        (site / relative).write_text(f"<h1>{relative}</h1>", encoding="utf-8")
    env = {**os.environ, "GIT_INDEX_FILE": str(tmp_path / "index")}
    command = ["git", "-C", str(repo), f"--work-tree={site}"]
    subprocess.run([*command, "add", "--all", "--force"], env=env, check=True)
    tree = subprocess.check_output([*command, "write-tree"], env=env, text=True).strip()
    commit = git(repo, "commit-tree", tree, "-m", "Add unpublished trading reports")
    git(repo, "update-ref", "refs/remotes/origin/gh-pages", commit)
    monkeypatch.setattr(publisher, "ROOT", repo)
    for key in list(os.environ):
        if key.startswith("CLOUDFLARE_") or key == "TRADINGAGENTS_WRANGLER":
            monkeypatch.delenv(key)
    return repo, commit


def mirror(tmp_path: Path, **settings: str) -> tuple[cloudflare.Mirror, Path]:
    wrangler, log = fake_wrangler(tmp_path)
    environ = {
        **os.environ,
        "CLOUDFLARE_PAGES_PROJECT": "reports",
        "TRADINGAGENTS_WRANGLER": wrangler,
        "FAKE_WRANGLER_LOG": str(log),
        **settings,
    }
    loaded = cloudflare.load_mirror(environ)
    assert loaded is not None
    return loaded, log


def test_mirror_is_off_unless_a_project_is_set(published):
    assert cloudflare.load_mirror({}) is None
    assert cloudflare.main(["--check"]) == 0
    assert cloudflare.main([]) == 0
    assert cloudflare.main(["--require"]) == 1


def test_environment_overrides_dotenv_and_only_cloudflare_keys_are_read(published):
    repo, _ = published
    (repo / ".env").write_text(
        "CLOUDFLARE_PAGES_PROJECT=from-dotenv\n"
        "CLOUDFLARE_API_TOKEN=dotenv-token\n"
        "OPENAI_API_KEY=not-for-wrangler\n",
        encoding="utf-8",
    )
    loaded = cloudflare.load_mirror({"CLOUDFLARE_PAGES_BRANCH": "production"})
    assert loaded is not None
    assert (loaded.project, loaded.branch, loaded.path) == ("from-dotenv", "production", "TradingAgents")
    assert loaded.max_files == cloudflare.DEFAULT_MAX_FILES
    assert loaded.env["CLOUDFLARE_API_TOKEN"] == "dotenv-token"
    assert "OPENAI_API_KEY" not in loaded.env
    # An empty project in the environment switches a .env mirror off for one run.
    assert cloudflare.load_mirror({"CLOUDFLARE_PAGES_PROJECT": ""}) is None


@pytest.mark.parametrize(
    "setting",
    [
        {"CLOUDFLARE_PAGES_PROJECT": "Bad_Name"},
        {"CLOUDFLARE_PAGES_BRANCH": "has space"},
        {"CLOUDFLARE_PAGES_PATH": "../escape"},
        {"CLOUDFLARE_PAGES_PATH": "reports/./x"},
        {"CLOUDFLARE_PAGES_MAX_FILES": "0"},
    ],
)
def test_invalid_settings_are_rejected(published, setting):
    with pytest.raises(workflow.WorkflowError):
        cloudflare.load_mirror({"CLOUDFLARE_PAGES_PROJECT": "reports", **setting})


def test_deploy_uploads_the_commit_under_the_mirror_path(published, tmp_path):
    repo, commit = published
    loaded, log = mirror(tmp_path, CLOUDFLARE_API_TOKEN="token", CLOUDFLARE_PAGES_PATH="/research/")
    assert cloudflare.deploy(loaded, "refs/remotes/origin/gh-pages") == commit
    [call] = calls(log)
    assert call["argv"][:2] == ["pages", "deploy"]
    assert call["argv"][3:] == [
        "--project-name", "reports",
        "--branch", "main",
        "--commit-hash", commit,
        "--commit-message", "Add unpublished trading reports",
        "--commit-dirty=false",
    ]
    assert call["files"] == [
        "_redirects", "research/404.html", "research/AAPL/index.html", "research/index.html",
    ]
    assert call["redirects"] == "/ /research/ 302\n"
    assert call["token"] == "token"
    # Wrangler must not pick up repository config, and the staging copy is removed.
    assert not Path(call["cwd"]).resolve().is_relative_to(repo.resolve())
    assert not list((repo / ".tradingagents").iterdir())


def test_limits_stop_the_upload_before_wrangler_runs(published, tmp_path, monkeypatch):
    loaded, log = mirror(tmp_path, CLOUDFLARE_PAGES_MAX_FILES="3")
    with pytest.raises(workflow.WorkflowError, match="limit of 3"):
        cloudflare.deploy(loaded, "refs/remotes/origin/gh-pages")
    monkeypatch.setattr(cloudflare, "MAX_FILE_BYTES", 20)
    with pytest.raises(workflow.WorkflowError, match="25 MiB: TradingAgents/AAPL/index.html,"):
        cloudflare.deploy(mirror(tmp_path)[0], "refs/remotes/origin/gh-pages")
    assert calls(log) == []


def test_wrangler_failures_are_reported(published, tmp_path, monkeypatch):
    loaded, log = mirror(tmp_path, FAKE_WRANGLER_EXIT="1")
    with pytest.raises(workflow.WorkflowError, match="could not deploy"):
        cloudflare.deploy(loaded, "refs/remotes/origin/gh-pages")
    assert len(calls(log)) == 1
    monkeypatch.setenv("CLOUDFLARE_PAGES_PROJECT", "reports")
    monkeypatch.setenv("TRADINGAGENTS_WRANGLER", "tradingagents-missing-wrangler")
    assert cloudflare.main(["--check"]) == 1
