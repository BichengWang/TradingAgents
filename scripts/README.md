# scripts/

Run TradingAgents analyses and build the report site.

| Script | What it does | Key args |
|--------|--------------|----------|
| `report_workflow.py` | **Main entry.** Reassemble reports, validate `docs/`, and compile `_site`. | `--analysis-date YYYYMMDD`, `--dry-run` |
| `build_publish_site.py` | Merge unpublished local reports with the compiled `gh-pages` history. | `--analysis-date`, `--base-ref`, `--dry-run` |
| `publish_site.sh` | Incrementally publish new reports, preserving prior dates and Git history. | `--analysis-date`, `--build-only`, `--dry-run`, `--cloudflare-only` |
| `publish_cloudflare.py` | Deploy a published `gh-pages` commit to the Cloudflare Pages mirror. | `--commit`, `--check` |
| `run_one.py` | One-ticker headless run (max depth). | `--ticker` (req), `--date` |
| `run_top_tickers.sh` | Parallel run, one Docker container per ticker. | env: `CONCURRENCY`, `TRADINGAGENTS_DATE` |
| `build_reports_site.py` | Lower-level generated Markdown renderer used by `report_workflow.py`. | `--summary-analysis-date`, `--summary-only` |
| `reassemble_complete_reports.py` | Rebuild missing `complete_report.md`. | — |
| `prune_report_headings.py` | Normalize headings across reports. | — |
| `smoke_structured_output.py` | Smoke-test structured-output agents vs. a real LLM. | `provider`, `--deep-model`, `--quick-model` |

Run every ticker missing today's report via the `/run-missing` skill.

## Missing-report launcher mode

`run_missing_today_claude.sh`, `run_missing_today_gemini.sh`, and
`run_missing_today_gpt.sh` use CLIProxyAPI by default. Set one shared variable
to use each provider's direct API instead:

```bash
export TRADINGAGENTS_MODE=direct
bash scripts/run_missing_today_gemini.sh --check-only
bash scripts/run_missing_today_claude.sh --check-only
bash scripts/run_missing_today_gpt.sh --check-only
```

`TRADINGAGENTS_CLAUDE_MODE`, `TRADINGAGENTS_GEMINI_MODE`, and
`TRADINGAGENTS_GPT_MODE` override the shared setting for their own launcher.
Each mode accepts `proxy` or `direct`. Direct mode uses the corresponding
provider API key from the environment or project `.env`.
The older `run_all_today.sh` has no proxy/direct selector.

## Incremental releases

Install the documentation dependencies with `python3 -m pip install '.[docs]'`.
Place completed runs in `docs/<TICKER>/<YYYYMMDD>_<MODEL>_<RUN_TIMESTAMP>/`.
Use a new run folder when correcting a published report: existing published
folders are immutable and are skipped, even if their local Markdown changes.

```bash
# Offline preview against the locally cached origin/gh-pages commit:
bash scripts/publish_site.sh --analysis-date 20260923 --dry-run
bash scripts/publish_site.sh --analysis-date 20260923 --build-only

# Fetch the latest deployment, add this date's unpublished runs, and publish:
bash scripts/publish_site.sh --analysis-date 20260923
```

Omit `--analysis-date` to add all unpublished local runs, including backfills.
Each release starts from `gh-pages`, compiles only unpublished runs using the
report workflow, merges daily summaries, ticker indexes and sitemaps, then
checks that all historical report pages are unchanged and reachable. Old assets
remain available for old pages. Historical report pages keep their original
navigation; the homepage and updated ticker hubs link to the merged history.

The normal push extends the prior deployment commit. If another release lands
while building, the push fails safely; rerun to rebuild against that deployment.
Running again after a successful release creates no additional release commit.
`PUBLISH_RETAIN_DATES` only limits which **local** dates to consider; it never
deletes published dates. The build refuses to exceed a 1,000,000,000-byte site
budget instead of pruning historical reports.

Builds use temporary directories before installing `_site/`. Previous local
previews are kept in `.tradingagents/site-backups/`; these backups accumulate
and can be removed manually once no longer needed. Dry runs leave `_site/` and
`docs/` unchanged. Missing or malformed published history stops the release.

`report_workflow.py` and the pre-commit hook still build from local Markdown
only. Use `publish_site.sh` for releases: it restores historical content from
`gh-pages` even if a local-only build has replaced `_site/`.

All report Markdown under `docs/` is currently gitignored. Incremental releases
preserve published HTML, **not** original Markdown, stage outputs, or raw market
data. Back up those source files separately if you need to rerun analyses.

## Cloudflare Pages mirror

`publish_site.sh` can publish every release to two places: GitHub Pages
(`https://bichengwang.github.io/TradingAgents/`, from `gh-pages` as before) and
a Cloudflare Pages project that serves the same site under `/TradingAgents/`.
A site such as altairworld can then show the reports as one of its own paths by
proxying `/TradingAgents/*` to that project.

One-time setup:

```bash
npx wrangler login   # or set CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID
npx wrangler pages project create tradingagents-reports --production-branch main
```

Then set `CLOUDFLARE_PAGES_PROJECT=tradingagents-reports` in the environment or
the project `.env`, and copy the current site once:

```bash
bash scripts/publish_site.sh --cloudflare-only
```

From then on, each release pushes `gh-pages` first and then deploys that same
commit; Wrangler uploads only files Cloudflare does not have yet. A broken
mirror setting stops the release before `gh-pages` moves. If the upload itself
fails after the push, the script exits non-zero and `--cloudflare-only` retries
it. Unchanged releases create no deployment. Wrangler runs through
`npx --yes wrangler@4` (Node.js required); set `TRADINGAGENTS_WRANGLER` to use
another command.

| Setting | Default | Meaning |
|---------|---------|---------|
| `CLOUDFLARE_PAGES_PROJECT` | unset (mirror off) | Pages project to deploy to |
| `CLOUDFLARE_PAGES_BRANCH` | `main` | The project's production branch |
| `CLOUDFLARE_PAGES_PATH` | `TradingAgents` | Path prefix; keep it equal to the `site_url` path |
| `CLOUDFLARE_PAGES_MAX_FILES` | `20000` | Files per deployment (Free plan; raise on paid plans) |

Only `CLOUDFLARE_*` keys are read from `.env`; the environment overrides them,
so `CLOUDFLARE_PAGES_PROJECT=` turns the mirror off for one run. The release is
refused before upload if it exceeds the file limit or has a file over 25 MiB.
GitHub Pages still caps the published site at 1 GB, so the existing
1,000,000,000-byte budget applies to both copies.

## Duplicate report protection

All four generation launchers (`run_missing_today_claude.sh`,
`run_missing_today_gemini.sh`, `run_missing_today_gpt.sh`, and `run_all_today.sh`)
skip existing `docs/<TICKER>/<YYYYMMDD>_<DEEP_MODEL_SLUG>_<RUN_DATE>_<RUN_TIME>/`
report folders, using the writer's `YYYYMMDD_HHMMSS` timestamp suffix.
The deep model uses the report writer's normalization (`/`, `:`, and `.` become
`-`). Ticker arguments are uppercased and deduplicated. Different dates, models,
or tickers remain eligible; quick model and proxy/direct mode do not change
this identity.

A shared Python 3 helper holds an OS lock for each report identity while its
worker runs, then waiting launchers recheck `docs/` before generating. A failed
worker that saved no report can be retried. Duplicate skips do not truncate the
original worker log. Lock files live in `.tradingagents/report-locks/`; they
remain on disk but the OS releases their locks when processes exit. Do not
remove lock files while workers are running. These locks coordinate the shell
launchers on one host; standalone CLI invocations do not participate.

`run_all_today.sh` now honors `TRADINGAGENTS_DEEP_MODEL` and
`TRADINGAGENTS_QUICK_MODEL` so its completion check matches the models it runs.
