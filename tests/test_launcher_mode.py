"""Shared proxy/direct mode selection for the three dual-route launchers."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from scripts import anthropic_model_probe

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = {
    "claude": "TRADINGAGENTS_CLAUDE_MODE",
    "gemini": "TRADINGAGENTS_GEMINI_MODE",
    "gpt": "TRADINGAGENTS_GPT_MODE",
}


@pytest.mark.parametrize("provider", LAUNCHERS)
@pytest.mark.parametrize("shared,specific,expected", [
    (None, None, "proxy"),
    ("direct", None, "direct"),
    ("direct", "proxy", "proxy"),
    ("proxy", "direct", "direct"),
])
def test_shared_mode_and_provider_override(tmp_path, provider, shared, specific, expected):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "bin").mkdir()
    script = root / f"scripts/run_missing_today_{provider}.sh"
    shutil.copy2(ROOT / script.relative_to(root), script)
    shutil.copy2(ROOT / "scripts/default_tickers.sh", root / "scripts/default_tickers.sh")

    brew = root / "bin/brew"
    brew.write_text('#!/bin/bash\necho brew >> "$CAPTURE"\n')
    brew.chmod(0o755)
    python = root / "bin/python"
    python.write_text(
        '#!/bin/bash\necho python >> "$CAPTURE"\n'
        'if [ "$2" = --key ]; then echo fixture-key; fi\n'
    )
    python.chmod(0o755)

    capture = root / "calls"
    env = {
        "PATH": str(root / "bin") + os.pathsep + os.defpath,
        "TRADINGAGENTS_PYTHON": str(python),
        "CAPTURE": str(capture),
    }
    if shared is not None:
        env["TRADINGAGENTS_MODE"] = shared
    if specific is not None:
        env[LAUNCHERS[provider]] = specific
    result = subprocess.run(
        ["bash", str(script), "--check-only"], cwd=tmp_path,
        env=env, capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = capture.read_text().splitlines() if capture.exists() else []
    assert ("brew" in calls) is (expected == "proxy")
    assert ("Direct" in result.stdout) is (expected == "direct")


@pytest.mark.parametrize("provider", LAUNCHERS)
def test_invalid_shared_mode_fails_before_preflight(tmp_path, provider):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    script = root / f"scripts/run_missing_today_{provider}.sh"
    shutil.copy2(ROOT / script.relative_to(root), script)
    result = subprocess.run(
        ["bash", str(script), "--check-only"], cwd=tmp_path,
        env={"PATH": os.defpath, "TRADINGAGENTS_MODE": "invalid"},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert "TRADINGAGENTS_MODE must be proxy or direct" in result.stderr


@pytest.mark.parametrize("provider", LAUNCHERS)
def test_no_arguments_uses_all_shared_tickers(tmp_path, provider):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    script = root / f"scripts/run_missing_today_{provider}.sh"
    shutil.copy2(ROOT / script.relative_to(root), script)
    shutil.copy2(ROOT / "scripts/report_guard.py", root / "scripts/report_guard.py")
    (root / "cli").mkdir(exist_ok=True)
    for helper in ("__init__.py", "report_fields.py"):
        shutil.copy2(ROOT / "cli" / helper, root / "cli" / helper)
    # A changed shared list must apply to every launcher without local copies.
    tickers = ["SPY", "YINN", "CUSTOM"]
    (root / "scripts/default_tickers.sh").write_text(
        "DEFAULT_TICKERS=(" + " ".join(tickers) + ")\n"
    )
    for ticker in tickers:
        report = root / "docs" / ticker / "20000101_fixture-model_20000102_030405"
        for stage in ("complete_report.md", "1_analysts/market.md", "3_trading/trader.md", "5_portfolio/decision.md"):
            path = report / stage
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Price Target: 120")
    result = subprocess.run(
        ["bash", str(script)], cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "TRADINGAGENTS_MODE": "direct",
            "TRADINGAGENTS_PYTHON": "/usr/bin/true",
            "TRADINGAGENTS_DATE": "2000-01-01",
            "TRADINGAGENTS_DEEP_MODEL": "fixture-model",
            "TA_LOGDIR": str(root / "logs"),
        },
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Nothing to run — all 3 tickers already have" in result.stdout
    assert not result.stderr


@pytest.mark.parametrize("token_budget", [None, "65536"])
@pytest.mark.parametrize("effort", [None, "high", "max"])
# Unset, the budget is exported empty so a project .env cap (python-dotenv never
# overrides set variables) cannot truncate xhigh thinking; the SDK then applies
# each model's own maximum.
def test_claude_workers_receive_output_budget(tmp_path, token_budget, effort):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "bin").mkdir()
    for name in ("run_missing_today_claude.sh", "report_guard.py", "default_tickers.sh"):
        shutil.copy2(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "cli").mkdir(exist_ok=True)
    for helper in ("__init__.py", "report_fields.py"):
        shutil.copy2(ROOT / "cli" / helper, root / "cli" / helper)
    worker = root / "bin/uv"
    worker.write_text('''#!/bin/bash
printf '%s' "${TRADINGAGENTS_MAX_TOKENS-unset}" > "$CAPTURE"
printf '%s\\n' "$@" > "$CAPTURE.args"
report="docs/NVDA/20000101_${TRADINGAGENTS_DEEP_THINK_LLM}_20000102_030405"
mkdir -p "$report/1_analysts" "$report/3_trading" "$report/5_portfolio"
for stage in complete_report.md 1_analysts/market.md 3_trading/trader.md 5_portfolio/decision.md; do
  echo "Price Target: 120" > "$report/$stage"
done
''')
    worker.chmod(0o755)
    capture = root / "budget"
    env = {
        "PATH": str(root / "bin") + os.pathsep + os.defpath,
        "TRADINGAGENTS_MODE": "direct",
        "TRADINGAGENTS_PYTHON": "/usr/bin/true",
        "TRADINGAGENTS_DATE": "2000-01-01",
        "TA_LOGDIR": str(root / "logs"),
        "CAPTURE": str(capture),
    }
    if token_budget is not None:
        env["TRADINGAGENTS_MAX_TOKENS"] = token_budget
    if effort is not None:
        env["TRADINGAGENTS_OPENAI_REASONING_EFFORT"] = effort
    result = subprocess.run(
        ["bash", str(root / "scripts/run_missing_today_claude.sh"), "NVDA"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert capture.read_text() == (token_budget or "")
    args = capture.with_suffix(".args").read_text().splitlines()
    assert args[args.index("--anthropic-effort") + 1] == (effort or "xhigh")


def test_direct_anthropic_probe_surfaces_usage_limit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fixture-key")
    message = ("You have reached your specified API usage limits. "
               "You will regain access on 2030-04-01 at 00:00 UTC.")
    response = Mock(status_code=400, headers={})
    error = anthropic_model_probe.anthropic.BadRequestError(
        "Bad request", response=response, body={"error": {"message": message}},
    )
    with patch.object(anthropic_model_probe.anthropic, "Anthropic") as sdk:
        create = sdk.return_value.__enter__.return_value.messages.create
        create.side_effect = error
        with pytest.raises(ValueError, match="HTTP 400") as failure:
            anthropic_model_probe.probe_models("https://example.invalid", ["quick", "deep"])
        assert message in str(failure.value)
        assert "https://platform.claude.com/settings/spend-limits" in str(failure.value)
        assert sdk.call_args.kwargs["max_retries"] == 0
        create.assert_called_once_with(model="quick", max_tokens=1, messages=[{"role": "user", "content": "OK"}])


@pytest.mark.parametrize("quick_provider,quick_url,expected", [
    ("anthropic", None, [("https://deep.invalid", ["model"])]),
    ("anthropic", "https://quick.invalid", [("https://quick.invalid", ["model"]), ("https://deep.invalid", ["model"])]),
    ("openai", "https://quick.invalid", [("https://deep.invalid", ["model"])]),
])
def test_direct_probe_follows_tier_routes(monkeypatch, quick_provider, quick_url, expected):
    monkeypatch.setitem(anthropic_model_probe.DEFAULT_CONFIG, "quick_think_provider", quick_provider)
    monkeypatch.setitem(anthropic_model_probe.DEFAULT_CONFIG, "quick_think_backend_url", quick_url)
    monkeypatch.setattr(sys, "argv", ["probe", "--base-url", "https://deep.invalid", "model", "model"])
    with patch.object(anthropic_model_probe, "probe_models") as probe:
        assert anthropic_model_probe.main() == 0
        assert [call.args for call in probe.call_args_list] == expected
