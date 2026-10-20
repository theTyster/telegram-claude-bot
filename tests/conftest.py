import itertools

import pytest

from bot.config import load_config
from bot.gate import BotIdentity

BOT = BotIdentity(id=999, username="FamilyClaudeBot")
FAMILY = -100123
OWNER = 1
MOM = {"id": 2, "first_name": "Mom", "is_bot": False}
DAD = {"id": 3, "first_name": "Dad", "is_bot": False}
OWNER_USER = {"id": OWNER, "first_name": "Owner", "is_bot": False}
BOT_USER = {"id": BOT.id, "first_name": "Claude", "is_bot": True, "username": BOT.username}

_ids = itertools.count(1)


def fmt_date(unix):
    return f"d{unix}"


def message(text=None, *, user=MOM, chat_id=FAMILY, chat_type="supergroup", **extra):
    msg = {
        "message_id": next(_ids),
        "from": user,
        "chat": {"id": chat_id, "type": chat_type},
        "date": 1_700_000_000,
        **extra,
    }
    if text is not None:
        msg["text"] = text
        entities = list(extra.get("entities", []))
        if text.startswith("/"):
            cmd = text.split()[0]
            entities.insert(0, {"type": "bot_command", "offset": 0, "length": len(cmd)})
        msg["entities"] = entities
    return {"update_id": next(_ids), "message": msg}


def mention_entities(text, username=BOT.username):
    start = text.index("@" + username)
    return [{"type": "mention", "offset": start, "length": len(username) + 1}]


def make_config(**overrides):
    env = {
        "TELEGRAM_BOT_TOKEN": "123:abc",
        "FAMILY_CHAT_ID": str(FAMILY),
        "OWNER_USER_ID": str(OWNER),
        "AUTH_MODE": "api_key",
        "ANTHROPIC_API_KEY": "sk-test",
        "TIMEZONE": "America/Chicago",
        "FORWARD_GRACE_SECONDS": "0",
        "DATA_DIR": "/tmp/test-data",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return load_config(env)


@pytest.fixture
def cfg(tmp_path):
    return make_config(DATA_DIR=str(tmp_path), WORK_DIR=str(tmp_path / "work"))
