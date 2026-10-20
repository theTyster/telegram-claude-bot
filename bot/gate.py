"""Decide what an incoming Telegram update means. Pure functions, no I/O.

Who may call the bot:
- anyone in the family group (``FAMILY_CHAT_ID``),
- the owner in a private chat (handy for testing).

How they call it (in the family group):
- ``/claude <question>`` (or ``/claude@ThisBot``),
- an @mention of the bot,
- a reply to one of the bot's messages.

Forwarded messages never trigger the bot; they are handed back as
``Decision(kind="forward")`` so the caller can buffer them for a moment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

# The Claude API rejects images over 5 MB.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

ASK_COMMAND = "claude"
USAGE_COMMAND = "usage"
HELP_COMMANDS = {"help", "start"}


@dataclass(frozen=True)
class BotIdentity:
    id: int
    username: str  # without the leading "@"


@dataclass(frozen=True)
class PhotoRef:
    file_id: str
    file_size: int | None = None
    mime_type: str = "image/jpeg"


@dataclass
class ContextItem:
    """A message that came with the call: replied to, quoted or forwarded."""

    label: str
    text: str
    photos: list[PhotoRef] = field(default_factory=list)


@dataclass
class Invocation:
    chat_id: int
    message_id: int
    user_id: int
    user_name: str
    question: str
    photos: list[PhotoRef] = field(default_factory=list)
    reply: ContextItem | None = None
    is_private: bool = False


DecisionKind = Literal["ignore", "invoke", "forward", "usage", "help", "leave", "dm_refused"]


@dataclass
class Decision:
    kind: DecisionKind
    chat_id: int | None = None
    message_id: int | None = None
    user_id: int | None = None
    invocation: Invocation | None = None
    forward: ContextItem | None = None


IGNORE = Decision("ignore")


def display_name(user: dict | None) -> str:
    if not user:
        return "Someone"
    name = " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p)
    return name or user.get("username") or "Someone"


def _entity_text(text: str, entity: dict) -> str:
    # Telegram entity offsets count UTF-16 code units, not Python characters.
    raw = text.encode("utf-16-le")
    start = entity["offset"] * 2
    end = start + entity["length"] * 2
    return raw[start:end].decode("utf-16-le", errors="ignore")


def _text_and_entities(msg: dict) -> tuple[str, list[dict]]:
    if "text" in msg:
        return msg["text"], msg.get("entities", [])
    return msg.get("caption", ""), msg.get("caption_entities", [])


def extract_photos(msg: dict) -> list[PhotoRef]:
    """The best usable image on a message (photo or image document)."""
    sizes = msg.get("photo") or []
    usable = [s for s in sizes if (s.get("file_size") or 0) <= MAX_IMAGE_BYTES]
    if usable:
        best = max(usable, key=lambda s: s.get("width", 0) * s.get("height", 0))
        return [PhotoRef(best["file_id"], best.get("file_size"))]
    doc = msg.get("document")
    if (
        doc
        and doc.get("mime_type") in IMAGE_MIME_TYPES
        and (doc.get("file_size") or 0) <= MAX_IMAGE_BYTES
    ):
        return [PhotoRef(doc["file_id"], doc.get("file_size"), doc["mime_type"])]
    return []


def parse_command(msg: dict, bot: BotIdentity) -> tuple[str, str] | None:
    """``("claude", "rest of text")`` if the message starts with a command for us."""
    text, entities = _text_and_entities(msg)
    for ent in entities:
        if ent.get("type") == "bot_command" and ent.get("offset") == 0:
            command = _entity_text(text, ent)[1:]
            name, _, target = command.partition("@")
            if target and target.lower() != bot.username.lower():
                return None  # addressed to another bot
            rest = text[len(command) + 1 :].strip()
            return name.lower(), rest
    return None


def mentions_bot(msg: dict, bot: BotIdentity) -> bool:
    text, entities = _text_and_entities(msg)
    for ent in entities:
        if ent.get("type") == "mention":
            if _entity_text(text, ent).lower() == f"@{bot.username.lower()}":
                return True
        elif ent.get("type") == "text_mention":
            if (ent.get("user") or {}).get("id") == bot.id:
                return True
    return False


def strip_mentions(text: str, bot: BotIdentity) -> str:
    cleaned = re.sub(rf"@{re.escape(bot.username)}\b", "", text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def forward_label(origin: dict, *, fmt_date) -> str:
    kind = origin.get("type")
    if kind == "user":
        who = display_name(origin.get("sender_user"))
    elif kind == "hidden_user":
        who = origin.get("sender_user_name") or "a hidden user"
    elif kind == "chat":
        who = (origin.get("sender_chat") or {}).get("title") or "a group"
    elif kind == "channel":
        who = (origin.get("chat") or {}).get("title") or "a channel"
        if origin.get("author_signature"):
            who = f"{origin['author_signature']} in {who}"
    else:
        who = "someone"
    date = origin.get("date")
    return f"Forwarded from {who}" + (f" ({fmt_date(date)})" if date else "")


def _reply_context(msg: dict, bot: BotIdentity, *, fmt_date) -> ContextItem | None:
    quote = (msg.get("quote") or {}).get("text")
    reply = msg.get("reply_to_message")
    # In forum groups every message "replies" to the topic's creation message.
    if reply and not reply.get("forum_topic_created"):
        sender = reply.get("from") or {}
        if sender.get("id") == bot.id:
            label = "Replying to your earlier answer"
        else:
            label = f"Replying to {display_name(sender)}"
        if reply.get("forward_origin"):
            label += f" ({forward_label(reply['forward_origin'], fmt_date=fmt_date).lower()})"
        text, _ = _text_and_entities(reply)
        if quote:
            label += " (quoted part)"
            text = quote
        return ContextItem(label, text, extract_photos(reply))
    external = msg.get("external_reply")
    if external:
        origin = external.get("origin") or {}
        label = "Replying to a message " + forward_label(origin, fmt_date=fmt_date)[
            len("Forwarded ") :
        ]
        return ContextItem(label, quote or "", extract_photos(external))
    return None


def classify(
    update: dict,
    *,
    bot: BotIdentity,
    family_chat_id: int,
    owner_user_id: int,
    fmt_date,
) -> Decision:
    member = update.get("my_chat_member")
    if member:
        chat = member.get("chat") or {}
        status = (member.get("new_chat_member") or {}).get("status")
        if (
            chat.get("type") in ("group", "supergroup", "channel")
            and chat.get("id") != family_chat_id
            and status in ("member", "administrator")
        ):
            return Decision("leave", chat_id=chat.get("id"))
        return IGNORE

    msg = update.get("message")
    if not msg:
        return IGNORE
    user = msg.get("from") or {}
    if not user or user.get("is_bot"):
        return IGNORE
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    is_private = chat.get("type") == "private"

    if is_private:
        if user.get("id") != owner_user_id:
            return Decision("dm_refused", chat_id=chat_id, message_id=msg.get("message_id"))
    elif chat_id != family_chat_id:
        return Decision("leave", chat_id=chat_id)

    base = dict(chat_id=chat_id, message_id=msg.get("message_id"), user_id=user.get("id"))
    text, _ = _text_and_entities(msg)

    if msg.get("forward_origin"):
        item = ContextItem(
            forward_label(msg["forward_origin"], fmt_date=fmt_date), text, extract_photos(msg)
        )
        return Decision("forward", forward=item, **base)

    question: str | None = None
    command = parse_command(msg, bot)
    if command:
        name, rest = command
        if name == USAGE_COMMAND:
            return Decision("usage", **base)
        if name in HELP_COMMANDS:
            return Decision("help", **base)
        if name != ASK_COMMAND:
            return IGNORE
        question = strip_mentions(rest, bot)
    elif mentions_bot(msg, bot):
        question = strip_mentions(text, bot)
    elif ((msg.get("reply_to_message") or {}).get("from") or {}).get("id") == bot.id:
        question = text.strip()
    elif is_private:
        question = text.strip()
    else:
        return IGNORE

    invocation = Invocation(
        chat_id=chat_id,
        message_id=msg.get("message_id"),
        user_id=user.get("id"),
        user_name=display_name(user),
        question=question,
        photos=extract_photos(msg),
        reply=_reply_context(msg, bot, fmt_date=fmt_date),
        is_private=is_private,
    )
    return Decision("invoke", invocation=invocation, **base)
