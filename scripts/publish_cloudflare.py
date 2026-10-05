#!/usr/bin/env python3
"""Mirror a published gh-pages commit to a Cloudflare Pages project.

GitHub Pages keeps serving gh-pages at the origin URL. This uploads the same
commit to Cloudflare Pages under a path prefix (default ``TradingAgents/``), so
another site such as altairworld can serve the reports as one of its paths.
Nothing is rebuilt: Cloudflare always serves exactly a published gh-pages commit.

Settings come from the environment, then the project .env:
  CLOUDFLARE_PAGES_PROJECT    Pages project to deploy to (unset: mirror disabled)
  CLOUDFLARE_PAGES_BRANCH     production branch of that project (default: main)
  CLOUDFLARE_PAGES_PATH       path prefix for the site (default: TradingAgents)
  CLOUDFLARE_PAGES_MAX_FILES  per-deployment file limit (default: 20000, Free plan)
  CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID  Wrangler credentials (or `wrangler login`)
TRADINGAGENTS_WRANGLER overrides the Wrangler command (default: npx --yes wrangler@4).
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import (  # noqa: E402
    build_publish_site as publisher,
    report_workflow as workflow,
)

DEFAULT_BRANCH = "main"
DEFAULT_PATH = "TradingAgents"
DEFAULT_WRANGLER = "npx --yes wrangler@4"
DEFAULT_MAX_FILES = 20_000
MAX_FILE_BYTES = 25 * 1024 * 1024  # Cloudflare Pages per-file limit
PROJECT_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,56}[a-z0-9])?")
BRANCH_NAME = re.compile(r"[A-Za-z0-9._/-]+")
PATH_SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Mirror:
    project: str
    branch: str
    path: str
    max_files: int
    wrangler: tuple[str, ...]
    env: dict[str, str]


def load_mirror(environ: Mapping[str, str] | None = None) -> Mirror | None:
    """Return the configured mirror, or None when CLOUDFLARE_PAGES_PROJECT is unset."""
    environ = os.environ if environ is None else environ
    # Only Cloudflare settings are taken from .env; the environment wins, so an
    # empty CLOUDFLARE_PAGES_PROJECT= disables a mirror configured in .env.
    settings = {
        key: value or ""
        for key, value in dotenv_values(publisher.ROOT / ".env").items()
        if key.startswith("CLOUDFLARE_")
    }
    settings.update({key: value for key, value in environ.items() if key.startswith("CLOUDFLARE_")})

    project = settings.get("CLOUDFLARE_PAGES_PROJECT", "").strip()
    if not project:
        return None
    if not PROJECT_NAME.fullmatch(project):
        raise workflow.WorkflowError(
            f"CLOUDFLARE_PAGES_PROJECT={project!r} is not a Cloudflare Pages project name "
            "(lowercase letters, digits and dashes)."
        )
    branch = settings.get("CLOUDFLARE_PAGES_BRANCH", "").strip() or DEFAULT_BRANCH
    if not BRANCH_NAME.fullmatch(branch):
        raise workflow.WorkflowError(f"CLOUDFLARE_PAGES_BRANCH={branch!r} is not a branch name.")
    path = settings.get("CLOUDFLARE_PAGES_PATH", "").strip().strip("/") or DEFAULT_PATH
    if not all(PATH_SEGMENT.fullmatch(part) for part in path.split("/")):
        raise workflow.WorkflowError(
            f"CLOUDFLARE_PAGES_PATH={path!r} must be URL path segments such as TradingAgents."
        )
    limit = settings.get("CLOUDFLARE_PAGES_MAX_FILES", "").strip() or str(DEFAULT_MAX_FILES)
    if not limit.isdigit() or int(limit) < 1:
        raise workflow.WorkflowError(
            f"CLOUDFLARE_PAGES_MAX_FILES={limit!r} must be a positive integer."
        )
    wrangler = tuple(shlex.split(environ.get("TRADINGAGENTS_WRANGLER") or DEFAULT_WRANGLER))
    if not wrangler:
        raise workflow.WorkflowError("TRADINGAGENTS_WRANGLER must name a command.")
    env = {**environ, **settings}
    env.setdefault("WRANGLER_SEND_METRICS", "false")
    return Mirror(project, branch, path, int(limit), wrangler, env)


def check(mirror: Mirror) -> None:
    """Fail before anything is published if Wrangler cannot be started."""
    if shutil.which(mirror.wrangler[0], path=mirror.env.get("PATH")) is None:
        raise workflow.WorkflowError(
            f"Cannot run {mirror.wrangler[0]!r} for the Cloudflare Pages mirror. "
            "Install Node.js or set TRADINGAGENTS_WRANGLER."
        )


def stage(commit: str, upload: Path, mirror: Mirror) -> str:
    """Extract a gh-pages commit under the mirror path; return the full commit SHA."""
    site = upload.joinpath(*mirror.path.split("/"))
    site.parent.mkdir(parents=True, exist_ok=True)
    sha = publisher.snapshot_published_site(site, commit)
    # The bare project URL lands on the reports instead of a 404.
    (upload / "_redirects").write_text(f"/ /{mirror.path}/ 302\n", encoding="utf-8")
    files = [path for path in upload.rglob("*") if path.is_file()]
    if len(files) > mirror.max_files:
        raise workflow.WorkflowError(
            f"The site has {len(files):,} files, above the Cloudflare Pages limit of "
            f"{mirror.max_files:,}. Raise CLOUDFLARE_PAGES_MAX_FILES on a paid plan."
        )
    oversized = sorted(
        path.relative_to(upload).as_posix()
        for path in files
        if path.stat().st_size > MAX_FILE_BYTES
    )
    if oversized:
        raise workflow.WorkflowError(
            "Cloudflare Pages rejects files over 25 MiB: " + ", ".join(oversized)
        )
    return sha


def deploy(mirror: Mirror, commit: str) -> str:
    """Upload one gh-pages commit as the project's new production deployment."""
    root = publisher.ROOT
    scratch = root / ".tradingagents"
    scratch.mkdir(parents=True, exist_ok=True)
    with (
        tempfile.TemporaryDirectory(prefix="cloudflare-pages-", dir=scratch) as tmp,
        # Wrangler reads functions/ and wrangler config from its working
        # directory, so run it in an empty directory outside the repository.
        tempfile.TemporaryDirectory(prefix="tradingagents-wrangler-") as cwd,
    ):
        upload = Path(tmp) / "upload"
        upload.mkdir()
        sha = stage(commit, upload, mirror)
        message = subprocess.run(
            ["git", "log", "-1", "--format=%s", sha],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        command = [
            *mirror.wrangler,
            "pages",
            "deploy",
            str(upload),
            "--project-name",
            mirror.project,
            "--branch",
            mirror.branch,
            "--commit-hash",
            sha,
            "--commit-message",
            message or "Publish trading reports",
            # The upload is exactly this commit, never a dirty work tree.
            "--commit-dirty=false",
        ]
        if subprocess.run(command, cwd=cwd, env=mirror.env).returncode:
            raise workflow.WorkflowError(
                f"Wrangler could not deploy to Cloudflare Pages project {mirror.project!r}."
            )
    return sha


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--commit",
        default="refs/remotes/origin/gh-pages",
        help="Published gh-pages commit to deploy (default: the fetched origin/gh-pages).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only validate the mirror settings; deploy nothing.",
    )
    parser.add_argument(
        "--require",
        action="store_true",
        help="Fail instead of skipping when the mirror is not configured.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        mirror = load_mirror()
        if mirror is None:
            if args.require:
                raise workflow.WorkflowError(
                    "Set CLOUDFLARE_PAGES_PROJECT in the environment or .env "
                    "to enable the Cloudflare Pages mirror."
                )
            print(
                "Cloudflare Pages mirror is not configured; "
                + ("publishing gh-pages only." if args.check else "skipped.")
            )
            return 0
        check(mirror)
        if args.check:
            print(f"Cloudflare Pages mirror: project {mirror.project}, path /{mirror.path}/")
            return 0
        sha = deploy(mirror, args.commit)
    except (workflow.WorkflowError, OSError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Deployed gh-pages {sha[:12]} to Cloudflare Pages project "
        f"{mirror.project} at /{mirror.path}/."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
