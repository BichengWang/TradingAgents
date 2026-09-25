"""Offline coverage for the native Gemini proxy launcher."""
import io
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import claude_proxy

ROOT = Path(__file__).resolve().parents[1]


def test_gemini_catalog(monkeypatch):
    monkeypatch.setenv("CLIPROXY_API_KEY", "fixture-key")
    with patch.object(claude_proxy.urllib.request, "urlopen", return_value=io.BytesIO(
        b'{"data":[{"id":"gemini-test"}]}'
    )):
        claude_proxy.check_models("http://localhost:8317", ["gemini-test"], "gemini")
    with patch.object(claude_proxy.urllib.request, "urlopen", return_value=io.BytesIO(
        b'{"data":[]}'
    )), pytest.raises(ValueError, match="Configure Gemini credentials"):
        claude_proxy.check_models("http://localhost:8317", ["gemini-test"], "gemini")


@pytest.mark.parametrize("mode,brew_status,check_only", [
    ("proxy", "0", True), ("proxy", "1", True),
    ("direct", "0", True), ("proxy", "0", False), ("direct", "0", False),
])
def test_launcher(tmp_path, mode, brew_status, check_only):
    root = tmp_path / "repo with spaces"
    (root / "scripts").mkdir(parents=True)
    (root / "bin").mkdir()
    script = root / "scripts/run_missing_today_gemini.sh"
    shutil.copy2(ROOT / "scripts/run_missing_today_gemini.sh", script)
    programs = {
        "brew": 'echo "brew $*" >> "$CAPTURE"\nexit "$BREW_STATUS"',
        "python": 'echo python >> "$CAPTURE"\nif [ "$2" = --key ]; then echo fixture-key; else [ "$3" = gemini ]; fi',
        "uv": '''
[ "$TRADINGAGENTS_REPORTS_DIR" = "$PWD/docs" ] || exit 2
[ "$TRADINGAGENTS_LLM_PROVIDER" = google ] || exit 3
if [ "$TRADINGAGENTS_GEMINI_MODE" = proxy ]; then
  [ "$GOOGLE_API_KEY" = fixture-key ] || exit 4
  [ "$GOOGLE_GENAI_USE_VERTEXAI" = false ] || exit 5
  [ "$TRADINGAGENTS_LLM_BACKEND_URL" = http://127.0.0.1:8317 ] || exit 6
else
  [ "$GOOGLE_API_KEY" = direct-key ] || exit 7
  [ "$TRADINGAGENTS_LLM_BACKEND_URL" = https://generativelanguage.googleapis.com ] || exit 8
fi
mkdir -p docs/NVDA/20000101_gemini-test_fixture
''',
    }
    for name, body in programs.items():
        p = root / "bin" / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)
    capture = root / "calls"
    env = {"PATH": str(root / "bin") + os.pathsep + os.defpath,
           "TRADINGAGENTS_PYTHON": str(root / "bin/python"),
           "TRADINGAGENTS_GEMINI_MODE": mode, "BREW_STATUS": brew_status,
           "CAPTURE": str(capture), "TRADINGAGENTS_DATE": "2000-01-01",
           "TRADINGAGENTS_DEEP_MODEL": "gemini-test", "GOOGLE_API_KEY": "direct-key",
           "TA_LOGDIR": str(root / "logs"), "TRADINGAGENTS_REPORTS_DIR": "/.../docs"}
    result = subprocess.run(["bash", str(script), "--check-only" if check_only else "NVDA"],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == (1 if brew_status == "1" else 0), result.stdout + result.stderr
    calls = capture.read_text().splitlines() if capture.exists() else []
    assert calls == ([] if mode == "direct" else ["brew services start cliproxyapi"] +
                     ([] if brew_status == "1" else ["python", "python"]))
    assert (root / "docs").exists() is (not check_only)
