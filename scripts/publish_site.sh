#!/usr/bin/env bash
# Build the MkDocs reports site locally and optionally publish it to gh-pages.
#
# Report Markdown under docs/ is gitignored and never reaches the remote, so CI
# cannot build the site. Instead we build from the local working tree and push
# the compiled HTML to gh-pages, which GitHub Pages serves (Settings -> Pages ->
# Source = "Deploy from a branch", branch = gh-pages / root).
#
# This script is intentionally model-free: it only reassembles existing report
# stage files, validates generated links, builds MkDocs HTML, and optionally
# publishes the compiled site. It includes all completed reports while omitting
# redundant report-stage subpages so it stays below GitHub Pages' 1 GB limit.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: bash scripts/publish_site.sh [options]

Options:
  --analysis-date YYYYMMDD|YYYY-MM-DD
      Focus the summary for a specific analysis date.
  --build-only
      Build and validate _site locally, but do not push gh-pages.
  --dry-run
      Run the report workflow against a temporary docs copy. Does not write
      _site or push gh-pages.
  -h, --help
      Show this help.

No LLM/model calls are made by this script.
EOF
}

analysis_date=""
build_only=0
dry_run=0

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

if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
else
  PY=python
fi

workflow_args=(--retain-dates "${PUBLISH_RETAIN_DATES:-all}")
if [ -n "$analysis_date" ]; then
  workflow_args+=(--analysis-date "$analysis_date")
fi
if [ "$dry_run" -eq 1 ]; then
  workflow_args+=(--dry-run)
fi

echo "==> Building a compact, validated reports site without model calls"
"$PY" scripts/build_publish_site.py "${workflow_args[@]}"

if [ "$dry_run" -eq 1 ]; then
  echo "==> Dry run complete; _site was not rebuilt and gh-pages was not pushed."
  exit 0
fi

if [ "$build_only" -eq 1 ]; then
  echo "==> Build complete; gh-pages was not pushed."
  exit 0
fi

echo "==> Publishing compiled site to gh-pages"
site_dir="$ROOT/_site"
remote_url="$(git -C "$ROOT" remote get-url origin)"

# ``mkdocs gh-deploy`` always performs a second build from the full local
# docs tree. That tree is intentionally much larger than the Pages limit, so
# publish the compact artifact assembled above instead.
git -C "$site_dir" init --quiet
git -C "$site_dir" checkout --orphan gh-pages --quiet 2>/dev/null || \
  git -C "$site_dir" checkout -B gh-pages --quiet
git -C "$site_dir" add --all
if ! git -C "$site_dir" diff --cached --quiet; then
  git -C "$site_dir" -c user.useConfigOnly=true commit --quiet \
    -m "Deploy compact reports site"
fi
git -C "$site_dir" remote remove origin 2>/dev/null || true
git -C "$site_dir" remote add origin "$remote_url"
git -C "$site_dir" push --force origin HEAD:gh-pages

echo "==> Done. GitHub Pages will serve the updated gh-pages branch shortly."
