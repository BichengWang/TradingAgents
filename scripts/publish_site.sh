#!/usr/bin/env bash
# Incrementally add local report runs to the published gh-pages site.
#
# Report Markdown under docs/ is gitignored and never reaches the remote, so CI
# cannot build the site. Instead we build from the local working tree and push
# the compiled HTML to gh-pages, which GitHub Pages serves (Settings -> Pages ->
# Source = "Deploy from a branch", branch = gh-pages / root).
#
# This script is intentionally model-free: it only reassembles existing report
# stage files, validates generated links, builds MkDocs HTML, and optionally
# publishes the compiled site. Published report HTML is retained even when its
# source Markdown is missing locally. Releases extend the existing Git history.
#
# With CLOUDFLARE_PAGES_PROJECT set (environment or .env), each new gh-pages
# commit is also deployed to that Cloudflare Pages project under /TradingAgents/
# (see scripts/publish_cloudflare.py), so another site can serve it as a path.

# Usage:
#  bash scripts/publish_site.sh --analysis-date 20261003
# 
# Parse the complete body before running; edits during a build must not shift
# the file positions Bash reads afterward. Exit inside this block.
# 

{
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: bash scripts/publish_site.sh [options]

Options:
  --analysis-date YYYYMMDD|YYYY-MM-DD
      Add only unpublished run folders for this date (default: all local dates).
  --build-only
      Build and validate _site using cached origin/gh-pages; do not fetch or push.
  --dry-run
      Validate the merged site in temporary directories; do not write _site,
      fetch, or push gh-pages.
  --cloudflare-only
      Deploy the current gh-pages commit to the Cloudflare Pages mirror
      without building or pushing; use it for the first sync or a retry.
  -h, --help
      Show this help.

Existing published runs are skipped. Old dates are never removed.
Set CLOUDFLARE_PAGES_PROJECT to also deploy each release to Cloudflare Pages.
No LLM/model calls are made by this script.
EOF
}

analysis_date=""
build_only=0
dry_run=0
cloudflare_only=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --analysis-date)
      if [ "$#" -lt 2 ]; then
        echo "error: --analysis-date requires a value" >&2
        exit 2
      fi
      analysis_date="$2"
      shift 2
      ;;
    --build-only)
      build_only=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --cloudflare-only)
      cloudflare_only=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$cloudflare_only" -eq 1 ] && {
  [ -n "$analysis_date" ] || [ "$build_only" -eq 1 ] || [ "$dry_run" -eq 1 ]
}; then
  echo "error: --cloudflare-only cannot be combined with other options" >&2
  exit 2
fi

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python3
fi

if [ "$cloudflare_only" -eq 1 ]; then
  git fetch origin +refs/heads/gh-pages:refs/remotes/origin/gh-pages
  echo "==> Deploying the published gh-pages site to Cloudflare Pages"
  "$PY" scripts/publish_cloudflare.py --require --commit refs/remotes/origin/gh-pages
  exit 0
fi

# Pin the baseline before building. A normal push below rejects a concurrent
# release rather than overwriting it. Offline preview modes use the cached ref.
if [ "$build_only" -eq 0 ] && [ "$dry_run" -eq 0 ]; then
  # Reject a broken Cloudflare mirror setup before anything is published.
  "$PY" scripts/publish_cloudflare.py --check
  git fetch origin +refs/heads/gh-pages:refs/remotes/origin/gh-pages
fi
base_commit="$(git rev-parse --verify refs/remotes/origin/gh-pages)"
workflow_args=(--base-ref "$base_commit" --retain-dates "${PUBLISH_RETAIN_DATES:-all}")
if [ -n "$analysis_date" ]; then
  workflow_args+=(--analysis-date "$analysis_date")
fi
if [ "$dry_run" -eq 1 ]; then
  workflow_args+=(--dry-run)
fi

echo "==> Adding unpublished reports to gh-pages history"
"$PY" scripts/build_publish_site.py "${workflow_args[@]}"

if [ "$dry_run" -eq 1 ]; then
  echo "==> Dry run complete; _site was not rebuilt and gh-pages was not pushed."
  exit 0
fi

if [ "$build_only" -eq 1 ]; then
  echo "==> Build complete; gh-pages was not pushed."
  exit 0
fi

echo "==> Publishing an incremental gh-pages commit"
site_dir="$ROOT/_site"

# Use an isolated index: the developer's branch, worktree, and staged files
# remain untouched. The published commit has the fetched deployment as parent.
index_dir="$(mktemp -d)"
trap 'rm -f "$index_dir/index" "$index_dir/index.lock"; rmdir "$index_dir"' EXIT
export GIT_INDEX_FILE="$index_dir/index"
git read-tree "$base_commit"
git --work-tree="$site_dir" add --all --force -- .
site_tree="$(git write-tree)"
if [ "$site_tree" = "$(git rev-parse "$base_commit^{tree}")" ]; then
  echo "==> No unpublished reports; gh-pages is unchanged."
  exit 0
fi
if [ -n "$(git diff --name-only --diff-filter=D "$base_commit" "$site_tree")" ]; then
  echo "error: refusing to publish a release that removes existing files" >&2
  exit 1
fi
release_commit="$(git -c user.useConfigOnly=true commit-tree "$site_tree" \
  -p "$base_commit" -m "Add unpublished trading reports")"
git push origin "$release_commit:refs/heads/gh-pages"

# gh-pages stays the record; the mirror only ever receives a pushed commit.
if ! "$PY" scripts/publish_cloudflare.py --commit "$release_commit"; then
  echo "error: gh-pages was published, but the Cloudflare Pages deploy failed." >&2
  echo "Retry the mirror with: bash scripts/publish_site.sh --cloudflare-only" >&2
  exit 1
fi

echo "==> Done. GitHub Pages will serve the updated gh-pages branch shortly."
exit 0
}
