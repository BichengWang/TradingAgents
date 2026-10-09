#!/usr/bin/env bash
# Run missing reports through the Gemini Batch API (~50% cheaper, asynchronous).
# Needs a real GOOGLE_API_KEY (env or .env); the CLIProxyAPI login is not used.
# Usage:
#   bash scripts/run_batch_gemini.sh NVDA AMD     # submit (no args: all missing defaults)
#   bash scripts/run_batch_gemini.sh wait         # poll + collect until done
#   bash scripts/run_batch_gemini.sh status|collect|retry [RUN_ID]
# See scripts/batch_common.sh for details. Models: TRADINGAGENTS_DEEP_MODEL /
# TRADINGAGENTS_QUICK_MODEL; thinking: TRADINGAGENTS_GOOGLE_THINKING_LEVEL (high).
LABEL=gemini
PROVIDER=google
KEY_VAR=GOOGLE_API_KEY
DEEP_MODEL="${TRADINGAGENTS_DEEP_MODEL:-gemini-3.8-flash}"
QUICK_MODEL="${TRADINGAGENTS_QUICK_MODEL:-gemini-3.8-flash}"
EFFORT_FLAG=--google-thinking-level
EFFORT="${TRADINGAGENTS_GOOGLE_THINKING_LEVEL:-high}"
source "$(dirname "$0")/batch_common.sh"
batch_main "$@"
