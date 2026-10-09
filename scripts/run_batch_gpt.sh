#!/usr/bin/env bash
# Run missing reports through the OpenAI Batch API (~50% cheaper, asynchronous).
# Needs a real OPENAI_API_KEY.
# Usage:
#   bash scripts/run_batch_gpt.sh NVDA AMD        # submit (no args: all missing defaults)
#   bash scripts/run_batch_gpt.sh wait            # poll + collect until done
#   bash scripts/run_batch_gpt.sh status|collect|retry [RUN_ID]
# See scripts/batch_common.sh for details. Models: TRADINGAGENTS_DEEP_MODEL /
# TRADINGAGENTS_QUICK_MODEL; effort: TRADINGAGENTS_OPENAI_REASONING_EFFORT (max).
LABEL=gpt
PROVIDER=openai
KEY_VAR=OPENAI_API_KEY
DEEP_MODEL="${TRADINGAGENTS_DEEP_MODEL:-gpt-6-astra}"
QUICK_MODEL="${TRADINGAGENTS_QUICK_MODEL:-gpt-6.1-sol}"
EFFORT_FLAG=--openai-reasoning-effort
EFFORT="${TRADINGAGENTS_OPENAI_REASONING_EFFORT:-max}"
source "$(dirname "$0")/batch_common.sh"
batch_main "$@"
