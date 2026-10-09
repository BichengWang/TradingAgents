#!/usr/bin/env bash
# Run missing reports through the Anthropic Message Batches API (~50% cheaper,
# no per-request timeout, but asynchronous). Needs a real ANTHROPIC_API_KEY.
# Usage:
#   bash scripts/run_batch_claude.sh NVDA AMD     # submit (no args: all missing defaults)
#   bash scripts/run_batch_claude.sh wait         # poll + collect until done
#   bash scripts/run_batch_claude.sh status|collect|retry [RUN_ID]
# See scripts/batch_common.sh for details. Models: TRADINGAGENTS_DEEP_MODEL /
# TRADINGAGENTS_QUICK_MODEL; effort: TRADINGAGENTS_OPENAI_REASONING_EFFORT (max).
LABEL=claude
PROVIDER=anthropic
KEY_VAR=ANTHROPIC_API_KEY
DEEP_MODEL="${TRADINGAGENTS_DEEP_MODEL:-claude-opus-5-5}"
QUICK_MODEL="${TRADINGAGENTS_QUICK_MODEL:-claude-opus-5-5}"
EFFORT_FLAG=--anthropic-effort
EFFORT="${TRADINGAGENTS_OPENAI_REASONING_EFFORT:-max}"
source "$(dirname "$0")/batch_common.sh"
batch_main "$@"
