#!/usr/bin/env bash
# Shared driver for run_batch_{claude,gpt,gemini}.sh. Source it after setting:
#   LABEL PROVIDER KEY_VAR DEEP_MODEL QUICK_MODEL EFFORT_FLAG EFFORT
#
# Usage (through a wrapper):
#   run_batch_claude.sh [submit] [TICKER ...]   tickers default to the missing ones
#   run_batch_claude.sh status|collect|retry|wait [RUN_ID]   RUN_ID defaults to the last submit
#
# Batch mode talks to the provider's native batch API (never CLIProxyAPI), so it
# needs a real API key in the environment or the project's .env. A ticker needs
# many dependent rounds: run `collect` (or `wait`) repeatedly until it completes.
# Env: TRADINGAGENTS_DATE, TRADINGAGENTS_DEPTH (5), TRADINGAGENTS_ANALYSTS,
# POLL_SECONDS (300) for `wait`.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1
export TRADINGAGENTS_REPORTS_DIR="$ROOT/docs"

DATE="${TRADINGAGENTS_DATE:-$(date +%F)}"
ANALYSTS="${TRADINGAGENTS_ANALYSTS:-market,social,news,fundamentals}"
DEPTH="${TRADINGAGENTS_DEPTH:-5}"
POLL_SECONDS="${POLL_SECONDS:-300}"
PYTHON="${TRADINGAGENTS_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="python3"
STATE_DIR="${TA_LOGDIR:-/tmp/ta_runlogs}/batch"
LATEST_FILE="$STATE_DIR/${LABEL}_latest"
mkdir -p "$STATE_DIR" || exit 1

have_key() {
  [ -n "${!KEY_VAR:-}" ] || rg -q "^${KEY_VAR}=." "$ROOT/.env" 2>/dev/null
}

batch_cli() { uv run python -m cli.main batch "$@"; }

resolve_run_id() {
  RUN_ID="${1:-}"
  if [ -z "$RUN_ID" ] && [ -f "$LATEST_FILE" ]; then
    RUN_ID="$(cat "$LATEST_FILE")"
  fi
  if [ -z "$RUN_ID" ]; then
    echo "No RUN_ID given and no previous ${LABEL} submit recorded." >&2
    exit 1
  fi
}

do_submit() {
  local tickers=()
  if [ "$#" -gt 0 ]; then
    tickers=("$@")
  else
    source "$ROOT/scripts/default_tickers.sh"
    tickers=("${DEFAULT_TICKERS[@]}")
  fi
  # Uppercase, dedupe, then drop tickers that already have today's report.
  local missing
  missing="$(printf '%s\n' "${tickers[@]}" | tr '[:lower:]' '[:upper:]' | awk 'NF && !seen[$0]++' \
    | xargs python3 "$ROOT/scripts/report_guard.py" missing --reports-dir "$ROOT/docs" \
        --date "$DATE" --model "$DEEP_MODEL" --)" || exit 1
  if [ -z "$missing" ]; then
    echo "Nothing to run — every ticker already has a ${DATE} ${DEEP_MODEL} report."
    exit 0
  fi
  local csv
  csv="$(printf '%s\n' "$missing" | paste -sd, -)"
  echo "Submitting $(printf '%s\n' "$missing" | wc -l | tr -d ' ') ticker(s) to ${PROVIDER} batch: ${csv}"
  local out
  out="$(batch_cli submit --tickers "$csv" --date "$DATE" --analysts "$ANALYSTS" \
    --depth "$DEPTH" --language English --provider "$PROVIDER" \
    --deep-model "$DEEP_MODEL" --quick-model "$QUICK_MODEL" \
    "$EFFORT_FLAG" "$EFFORT")" || { printf '%s\n' "$out"; exit 1; }
  printf '%s\n' "$out"
  RUN_ID="$(printf '%s\n' "$out" | sed -n 's/.*Batch run submitted: *\([0-9A-Za-z_-]*\).*/\1/p' | head -1)"
  if [ -z "$RUN_ID" ]; then
    echo "Could not read the run id from the submit output." >&2
    exit 1
  fi
  printf '%s\n' "$RUN_ID" > "$LATEST_FILE"
  echo "Run id ${RUN_ID} saved; next: bash scripts/run_batch_${LABEL}.sh collect"
}

do_wait() {
  resolve_run_id "${1:-}"
  while :; do
    out="$(batch_cli collect "$RUN_ID")" || { printf '%s\n' "$out"; exit 1; }
    printf '%s\n' "$out"
    case "$out" in
      *'"status": "completed"'*) echo "Batch ${RUN_ID} completed."; return 0 ;;
      *'"status": "failed"'*) echo "Batch ${RUN_ID} has failed runs; try: retry ${RUN_ID}" >&2; return 1 ;;
    esac
    echo "[$(date +%T)] not finished; sleeping ${POLL_SECONDS}s"
    sleep "$POLL_SECONDS"
  done
}

batch_main() {
  if ! have_key; then
    echo "${KEY_VAR} is not set (env or .env). Batch mode needs a real ${PROVIDER} API key; the CLIProxyAPI OAuth login is not used." >&2
    exit 1
  fi
  local cmd="${1:-submit}"
  case "$cmd" in
    submit) shift; do_submit "$@" ;;
    status|collect|retry) shift; resolve_run_id "${1:-}"; batch_cli "$cmd" "$RUN_ID" ;;
    wait) shift; do_wait "${1:-}" ;;
    *) do_submit "$@" ;;  # bare tickers
  esac
}
