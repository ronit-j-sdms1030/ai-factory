"""Choosing a provider from the model id.

Every provider worth using speaks the OpenAI chat-completions format, so
supporting one is a base URL and a key. Which provider a stage runs on is a
question answered by cost and rate limits on the day — this session moved
stages three times — so it belongs in the environment, not in code.
"""

from __future__ import annotations

import pytest

from app import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in list(config.os.environ):
        if key.startswith("PROVIDER_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


class TestDefaultRouting:
    def test_an_unprefixed_model_goes_to_openrouter(self):
        base, key, model = config.provider_for("deepseek/deepseek-v3.2")
        assert base == config.OPENROUTER_BASE_URL
        assert model == "deepseek/deepseek-v3.2"

    def test_a_prefix_without_a_key_is_not_a_provider(self):
        """`qwen/qwen3-coder` is a model name, not a routing instruction."""
        base, _, model = config.provider_for("qwen/qwen3-coder")
        assert base == config.OPENROUTER_BASE_URL
        assert model == "qwen/qwen3-coder"


class TestPrefixRouting:
    def test_a_known_provider_uses_its_builtin_url(self, monkeypatch):
        monkeypatch.setenv("PROVIDER_GROQ_API_KEY", "gsk-1")
        base, key, model = config.provider_for("groq/openai/gpt-oss-120b")
        assert base == "https://api.groq.com/openai/v1"
        assert key == "gsk-1"
        assert model == "openai/gpt-oss-120b"     # the prefix is routing, not part of the name

    def test_an_unknown_provider_needs_a_base_url(self, monkeypatch):
        monkeypatch.setenv("PROVIDER_ACME_API_KEY", "k")
        with pytest.raises(RuntimeError, match="PROVIDER_ACME_BASE_URL"):
            config.provider_for("acme/some-model")

    def test_an_explicit_base_url_wins(self, monkeypatch):
        monkeypatch.setenv("PROVIDER_GROQ_API_KEY", "k")
        monkeypatch.setenv("PROVIDER_GROQ_BASE_URL", "https://proxy.internal/v1")
        base, _, _ = config.provider_for("groq/openai/gpt-oss-120b")
        assert base == "https://proxy.internal/v1"


class TestLegacyKey:
    def test_groq_api_key_still_works(self, monkeypatch):
        """It was the first provider key added; existing .env files keep working."""
        monkeypatch.delenv("PROVIDER_GROQ_API_KEY", raising=False)
        monkeypatch.setenv("GROQ_API_KEY", "gsk-legacy")
        config._alias_legacy_keys()
        _, key, _ = config.provider_for("groq/openai/gpt-oss-120b")
        assert key == "gsk-legacy"
