#!/usr/bin/env bash
# Shared driver for run_batch_{claude,gpt,gemini}.sh. Source it after setting:
#   LABEL PROVIDER KEY_VAR DEEP_MODEL QUICK_MODEL EFFORT_FLAG EFFORT
# A wrapper may also set INDEX_DEEP_MODEL: INDEX_TICKERS from default_tickers.sh
# then go in their own batch run with that deep model; all else is shared.
#
# Usage (through a wrapper):
#   run_batch_claude.sh [submit] [TICKER ...]   tickers default to the missing ones
#   run_batch_claude.sh status|collect|retry|wait [RUN_ID]   RUN_ID defaults to the runs of the last submit
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

# Sets RUN_IDS to the given id, else to every run of the last submit.
resolve_run_ids() {
  local line
  RUN_IDS=()
  if [ -n "${1:-}" ]; then
    RUN_IDS=("$1")
  elif [ -f "$LATEST_FILE" ]; then
    while IFS= read -r line; do
      [ -n "$line" ] && RUN_IDS+=("$line")
    done < "$LATEST_FILE"
  fi
  if [ "${#RUN_IDS[@]}" -eq 0 ]; then
    echo "No RUN_ID given and no previous ${LABEL} submit recorded." >&2
    exit 1
  fi
}

is_index_ticker() {
  case " ${INDEX_TICKERS[*]:-} " in
    *" $1 "*) return 0 ;;
  esac
  return 1
}

# submit_group DEEP_MODEL TICKER...: submit the tickers that have no report for
# DEEP_MODEL yet (report identity follows the deep model) and record the run id.
submit_group() {
  local model="$1" missing csv out run_id
  shift
  missing="$(python3 "$ROOT/scripts/report_guard.py" missing --reports-dir "$ROOT/docs" \
    --date "$DATE" --model "$model" -- "$@")" || exit 1
  if [ -z "$missing" ]; then
    echo "Nothing to run — every ticker already has a ${DATE} ${model} report."
    return 0
  fi
  csv="$(printf '%s\n' "$missing" | paste -sd, -)"
  echo "Submitting $(printf '%s\n' "$missing" | wc -l | tr -d ' ') ticker(s) to ${PROVIDER} batch with ${model}: ${csv}"
  out="$(batch_cli submit --tickers "$csv" --date "$DATE" --analysts "$ANALYSTS" \
    --depth "$DEPTH" --language English --provider "$PROVIDER" \
    --deep-model "$model" --quick-model "$QUICK_MODEL" \
    "$EFFORT_FLAG" "$EFFORT")" || { printf '%s\n' "$out"; exit 1; }
  printf '%s\n' "$out"
  run_id="$(printf '%s\n' "$out" | sed -n 's/.*Batch run submitted: *\([0-9A-Za-z_-]*\).*/\1/p' | head -1)"
  if [ -z "$run_id" ]; then
    echo "Could not read the run id from the submit output." >&2
    exit 1
  fi
  # The first run of a submit replaces the previous record; later runs join it.
  if [ "$SUBMITTED" -eq 0 ]; then
    printf '%s\n' "$run_id" > "$LATEST_FILE"
  else
    printf '%s\n' "$run_id" >> "$LATEST_FILE"
  fi
  SUBMITTED=$((SUBMITTED + 1))
  echo "Run id ${run_id} saved; next: bash scripts/run_batch_${LABEL}.sh collect"
}

do_submit() {
  local tickers=() index=() other=() t
  source "$ROOT/scripts/default_tickers.sh"
  if [ "$#" -gt 0 ]; then
    tickers=("$@")
  else
    tickers=("${DEFAULT_TICKERS[@]}")
  fi
  # Uppercase and dedupe, then split off the index tickers when they have a model.
  while IFS= read -r t; do
    if [ -n "${INDEX_DEEP_MODEL:-}" ] && is_index_ticker "$t"; then
      index+=("$t")
    else
      other+=("$t")
    fi
  done < <(printf '%s\n' "${tickers[@]}" | tr '[:lower:]' '[:upper:]' | awk 'NF && !seen[$0]++')
  SUBMITTED=0
  if [ "${#index[@]}" -gt 0 ]; then
    submit_group "$INDEX_DEEP_MODEL" "${index[@]}"
  fi
  if [ "${#other[@]}" -gt 0 ]; then
    submit_group "$DEEP_MODEL" "${other[@]}"
  fi
}

do_wait() {
  resolve_run_ids "${1:-}"
  local pending=("${RUN_IDS[@]}") next failed=0 id out
  while :; do
    next=()
    for id in "${pending[@]}"; do
      out="$(batch_cli collect "$id")" || { printf '%s\n' "$out"; exit 1; }
      printf '%s\n' "$out"
      case "$out" in
        *'"status": "completed"'*) echo "Batch ${id} completed." ;;
        *'"status": "failed"'*) echo "Batch ${id} has failed runs; try: retry ${id}" >&2; failed=1 ;;
        *) next+=("$id") ;;
      esac
    done
    if [ "${#next[@]}" -eq 0 ]; then
      return "$failed"
    fi
    pending=("${next[@]}")
    echo "[$(date +%T)] not finished: ${pending[*]}; sleeping ${POLL_SECONDS}s"
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
    status|collect|retry)
      shift
      resolve_run_ids "${1:-}"
      local id rc=0
      for id in "${RUN_IDS[@]}"; do
        batch_cli "$cmd" "$id" || rc=1
      done
      return "$rc" ;;
    wait) shift; do_wait "${1:-}" ;;
    *) do_submit "$@" ;;  # bare tickers
  esac
}
