import os

import pytest

from bot.config import ConfigError, load_config

from conftest import make_config


def test_defaults():
    cfg = make_config()
    assert cfg.model == "sonnet"
    assert cfg.daily_limit == 5
    assert cfg.transport == "polling"


def test_requires_credential_for_mode():
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
        make_config(ANTHROPIC_API_KEY="")
    with pytest.raises(ConfigError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        make_config(AUTH_MODE="subscription")


def test_rejects_bad_values():
    with pytest.raises(ConfigError):
        make_config(AUTH_MODE="free")
    with pytest.raises(ConfigError):
        make_config(TIMEZONE="Mars/Base")
    with pytest.raises(ConfigError):
        make_config(FAMILY_CHAT_ID="family")
    with pytest.raises(ConfigError, match="WEBHOOK_SECRET"):
        make_config(TRANSPORT="webhook")


def test_zero_cost_cap_disables_it():
    assert make_config(MAX_COST_PER_CALL_USD="0").max_cost_per_call_usd is None


def test_secrets_are_scrubbed_from_real_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("FAMILY_CHAT_ID", "-1")
    monkeypatch.setenv("OWNER_USER_ID", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-x")
    cfg = load_config()
    assert cfg.anthropic_api_key == "sk-x"
    for name in ("TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert name not in os.environ
