"""Check direct Anthropic Messages access before starting report workers."""

import argparse
import os
import sys

import anthropic

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.llm_clients.api_key_env import is_anthropic_setup_token
from tradingagents.llm_clients.factory import tier_provider


def probe_models(base_url: str, models: list[str]) -> None:
    # Importing tradingagents loads the same project .env as the report workers.
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("Set ANTHROPIC_API_KEY in the environment or project .env.")
    auth = {"api_key": "", "auth_token": key} if is_anthropic_setup_token(key) else {"api_key": key}
    with anthropic.Anthropic(base_url=base_url, timeout=30, max_retries=0, **auth) as client:
        for model in models:
            try:
                client.messages.create(
                    model=model, max_tokens=1, messages=[{"role": "user", "content": "OK"}],
                )
            except anthropic.APIStatusError as exc:
                error = exc.body.get("error") if isinstance(exc.body, dict) else None
                message = error.get("message") if isinstance(error, dict) else None
                reason = message if isinstance(message, str) else exc.response.reason_phrase
                reason = reason.replace(key, "[redacted]")
                if exc.status_code == 400 and reason.startswith((
                    "You have reached your specified API usage limits",
                    "You have reached your specified workspace API usage limits",
                )):
                    reason += " Review the monthly spend limit at https://platform.claude.com/settings/spend-limits."
                raise ValueError(f"{model} returned HTTP {exc.status_code}. {reason}") from None
            except anthropic.APIConnectionError as exc:
                raise ValueError(f"{model} could not reach the Anthropic API. {exc.message}") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("quick_model")
    parser.add_argument("deep_models", nargs="+")
    args = parser.parse_args()
    config = {**DEFAULT_CONFIG, "llm_provider": "anthropic"}
    routes = {}
    for tier, models in (("quick", [args.quick_model]), ("deep", args.deep_models)):
        if tier_provider(config, tier) == "anthropic":
            url = config.get(f"{tier}_think_backend_url") or args.base_url
            routes.setdefault(url, []).extend(models)
    try:
        for url, models in routes.items():
            models = list(dict.fromkeys(models))
            probe_models(url, models)
            print("Direct Anthropic preflight passed for " + ", ".join(models))
    except ValueError as exc:
        print(f"Direct Anthropic preflight failed. {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
