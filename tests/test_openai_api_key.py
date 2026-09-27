"""The unified ``api_key`` kwarg must work for the OpenAI-compatible family.

``api_key`` is forwarded to the chat model like the native Anthropic, Google
and Azure clients do, but the key check ran before that and only read the
provider env var, so an explicit key raised whenever the env var was unset.
"""

import pytest

from tradingagents.llm_clients.factory import create_llm_client


def _key(llm) -> str:
    key = llm.openai_api_key
    return key.get_secret_value() if hasattr(key, "get_secret_value") else key


@pytest.mark.unit
@pytest.mark.parametrize("provider,env_var,model", [
    ("openai", "OPENAI_API_KEY", "gpt-5.5"),
    ("xai", "XAI_API_KEY", "grok-4.3"),
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-flash"),
    ("openrouter", "OPENROUTER_API_KEY", "openai/gpt-5.5"),
])
def test_explicit_api_key_used_without_env_var(monkeypatch, provider, env_var, model):
    monkeypatch.delenv(env_var, raising=False)
    llm = create_llm_client(provider, model, api_key="sk-explicit").get_llm()
    assert _key(llm) == "sk-explicit"


@pytest.mark.unit
def test_explicit_api_key_wins_over_env_var(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    llm = create_llm_client("openai", "gpt-5.5", api_key="sk-explicit").get_llm()
    assert _key(llm) == "sk-explicit"


@pytest.mark.unit
def test_env_var_used_without_explicit_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    llm = create_llm_client("openai", "gpt-5.5").get_llm()
    assert _key(llm) == "sk-from-env"


@pytest.mark.unit
def test_missing_key_still_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_llm_client("openai", "gpt-5.5").get_llm()
