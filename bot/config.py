"""Settings, read once from environment variables.

Secrets are *removed* from ``os.environ`` after loading so the Claude Code
subprocess (which inherits the environment) only ever sees the one credential
we hand it explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

SECRET_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)

AUTH_MODES = ("api_key", "subscription")
TRANSPORTS = ("polling", "webhook")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    family_chat_id: int
    owner_user_id: int
    auth_mode: str
    anthropic_api_key: str | None
    claude_oauth_token: str | None
    model: str = "sonnet"
    daily_limit: int = 5
    timezone: str = "America/Chicago"
    max_turns: int = 12
    max_cost_per_call_usd: float | None = 0.50
    call_timeout_seconds: int = 300
    max_concurrent_calls: int = 3
    max_images: int = 4
    forward_buffer_seconds: int = 120
    forward_buffer_max_messages: int = 20
    forward_grace_seconds: float = 2.0
    transport: str = "polling"
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080
    webhook_secret: str | None = None
    data_dir: str = "/data"
    work_dir: str = "/tmp/bot-work"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def usage_db_path(self) -> str:
        return os.path.join(self.data_dir, "usage.db")

    def agent_env(self) -> dict[str, str]:
        """The only credential the Claude Code subprocess receives."""
        if self.auth_mode == "api_key":
            return {"ANTHROPIC_API_KEY": self.anthropic_api_key or ""}
        return {"CLAUDE_CODE_OAUTH_TOKEN": self.claude_oauth_token or ""}


def _int(env: dict[str, str], name: str, default: int | None = None) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        if default is None:
            raise ConfigError(f"{name} is required")
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


def _float(env: dict[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as e:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from e


def load_config(env: dict[str, str] | None = None, *, scrub: bool = True) -> Config:
    """Build a Config from ``env`` (defaults to ``os.environ``).

    With ``scrub=True`` and the real environment, secret variables are deleted
    from ``os.environ`` once read.
    """
    real_env = env is None
    env = dict(os.environ) if env is None else env

    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("TELEGRAM_BOT_TOKEN is required")

    auth_mode = env.get("AUTH_MODE", "api_key").strip().lower()
    if auth_mode not in AUTH_MODES:
        raise ConfigError(f"AUTH_MODE must be one of {AUTH_MODES}, got {auth_mode!r}")
    api_key = env.get("ANTHROPIC_API_KEY", "").strip() or None
    oauth = env.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip() or None
    if auth_mode == "api_key" and not api_key:
        raise ConfigError("AUTH_MODE=api_key needs ANTHROPIC_API_KEY")
    if auth_mode == "subscription" and not oauth:
        raise ConfigError(
            "AUTH_MODE=subscription needs CLAUDE_CODE_OAUTH_TOKEN (run `claude setup-token`)"
        )

    transport = env.get("TRANSPORT", "polling").strip().lower()
    if transport not in TRANSPORTS:
        raise ConfigError(f"TRANSPORT must be one of {TRANSPORTS}, got {transport!r}")
    webhook_secret = env.get("TELEGRAM_WEBHOOK_SECRET", "").strip() or None
    if transport == "webhook" and not webhook_secret:
        raise ConfigError("TRANSPORT=webhook needs TELEGRAM_WEBHOOK_SECRET")

    timezone = env.get("TIMEZONE", "America/Chicago").strip()
    try:
        ZoneInfo(timezone)
    except Exception as e:
        raise ConfigError(f"Unknown TIMEZONE {timezone!r}") from e

    # 0 (or empty) disables the per-call cost cap, e.g. for subscription mode.
    max_cost = _float(env, "MAX_COST_PER_CALL_USD", 0.50)

    cfg = Config(
        telegram_bot_token=token,
        family_chat_id=_int(env, "FAMILY_CHAT_ID"),
        owner_user_id=_int(env, "OWNER_USER_ID"),
        auth_mode=auth_mode,
        anthropic_api_key=api_key,
        claude_oauth_token=oauth,
        model=env.get("MODEL", "sonnet").strip() or "sonnet",
        daily_limit=_int(env, "DAILY_LIMIT", 5),
        timezone=timezone,
        max_turns=_int(env, "MAX_TURNS", 12),
        max_cost_per_call_usd=max_cost if max_cost > 0 else None,
        call_timeout_seconds=_int(env, "CALL_TIMEOUT_SECONDS", 300),
        max_concurrent_calls=_int(env, "MAX_CONCURRENT_CALLS", 3),
        max_images=_int(env, "MAX_IMAGES", 4),
        forward_buffer_seconds=_int(env, "FORWARD_BUFFER_SECONDS", 120),
        forward_buffer_max_messages=_int(env, "FORWARD_BUFFER_MAX_MESSAGES", 20),
        forward_grace_seconds=_float(env, "FORWARD_GRACE_SECONDS", 2.0),
        transport=transport,
        webhook_host=env.get("WEBHOOK_HOST", "0.0.0.0").strip() or "0.0.0.0",
        webhook_port=_int(env, "WEBHOOK_PORT", 8080),
        webhook_secret=webhook_secret,
        data_dir=env.get("DATA_DIR", "/data").strip() or "/data",
        work_dir=env.get("WORK_DIR", "/tmp/bot-work").strip() or "/tmp/bot-work",
    )

    if real_env and scrub:
        for name in SECRET_VARS:
            os.environ.pop(name, None)
    return cfg
