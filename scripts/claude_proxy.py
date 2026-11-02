#!/usr/bin/env python3
"""Resolve CLIProxyAPI's client key or check its advertised Claude/Codex/Gemini models.

--probe also sends each model one tiny completion, catching quota cooldowns.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def client_key():
    key = os.environ.get("CLIPROXY_API_KEY")
    if key:
        return key
    path = Path(os.environ.get("CLIPROXY_CONFIG", "/opt/homebrew/etc/cliproxyapi.conf"))
    try:
        import yaml

        config = yaml.safe_load(path.read_text()) or {}
        # v8 moved client keys to access.api-keys and wins over the legacy root list.
        access = config.get("access") or {}
        keys = access["api-keys"] if "api-keys" in access else config.get("api-keys", [])
        if isinstance(keys, list) and keys and isinstance(keys[0], str) and keys[0]:
            return keys[0]
    except (ImportError, OSError, ValueError):
        pass
    except Exception:
        # YAML parser errors can contain secret-bearing source lines.
        raise ValueError("Could not parse proxy config; set CLIPROXY_API_KEY.") from None
    raise ValueError("Set CLIPROXY_API_KEY or CLIPROXY_CONFIG (YAML with api-keys).")


def check_models(base_url, models, provider="claude"):
    # Anthropic's SDK accepts the server root; tolerate callers including /v1.
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    request = urllib.request.Request(
        url + "/models", headers={"Authorization": "Bearer " + client_key()}
    )
    # Homebrew may return before a newly started server begins listening.
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                available = {item["id"] for item in json.load(response)["data"]}
            break
        except urllib.error.HTTPError as exc:
            raise ValueError(f"Proxy model check returned HTTP {exc.code}; check the client key.") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 4:
                raise ValueError("Cannot reach CLIProxyAPI; check its server and base URL.") from None
            time.sleep(1)
        except (ValueError, KeyError, TypeError):
            raise ValueError("Proxy returned an invalid model list.") from None
    if not all(isinstance(model, str) for model in available):
        raise ValueError("Proxy returned an invalid model list.")
    label = {"claude": "Claude", "codex": "Codex", "gemini": "Gemini"}[provider]
    prefixes = {"claude": ("claude",), "codex": ("gpt-", "o1", "o3", "o4", "codex"),
                "gemini": ("gemini",)}[provider]
    matching = sorted(model for model in available if model.lower().startswith(prefixes))
    auth_hint = (
        "Configure Gemini credentials in CLIProxyAPI. " if provider == "gemini"
        else f"If {label} is not authenticated, run: cliproxyapi --{provider}-login. "
    )
    missing = [model for model in models if model not in available]
    if missing:
        raise ValueError(
            "Proxy does not advertise: " + ", ".join(missing)
            + f". Available {label} models: " + (", ".join(matching) or "none")
            + ". " + auth_hint
            + "Otherwise set TRADINGAGENTS_DEEP_MODEL / TRADINGAGENTS_QUICK_MODEL "
            "to advertised model IDs."
        )
    print("Proxy preflight OK: " + ", ".join(models))


def probe_models(base_url, models):
    """Send one tiny completion per model; an advertised model can still be in quota cooldown."""
    url = base_url.rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    for model in models:
        request = urllib.request.Request(
            url + "/chat/completions",
            data=json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply OK."}]}).encode(),
            headers={"Authorization": "Bearer " + client_key(), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120):
                pass
        except urllib.error.HTTPError as exc:
            try:
                error = json.load(exc).get("error", {})
            except (ValueError, AttributeError):
                error = {}
            if not isinstance(error, dict):
                error = {}
            reset = error.get("reset_time") or exc.headers.get("Retry-After")
            raise ValueError(
                f"{model} probe returned HTTP {exc.code} ({error.get('code') or 'error'})"
                + (f"; resets in {reset}" if reset else "")
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError(f"{model} probe could not reach CLIProxyAPI.") from None
    print("Proxy quota probe OK: " + ", ".join(models))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", action="store_true", help="Emit client key for shell capture only")
    parser.add_argument("--base-url", default="http://127.0.0.1:8317")
    parser.add_argument("--provider", choices=("claude", "codex", "gemini"), default="claude")
    parser.add_argument("--probe", action="store_true", help="Also send one tiny request per model to catch quota cooldowns")
    parser.add_argument("models", nargs="*")
    args = parser.parse_args()
    try:
        if args.key:
            print(client_key())
        else:
            check_models(args.base_url, args.models, args.provider)
            if args.probe:
                probe_models(args.base_url, args.models)
    except ValueError as exc:
        print(f"CLIProxyAPI: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
