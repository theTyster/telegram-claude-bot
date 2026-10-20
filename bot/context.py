"""Context that comes with a call, and the short-lived forward buffer.

Nothing here touches disk. Forwarded messages wait in memory for a couple of
minutes so "forward a few messages, then ask" works, and are dropped once
used, on expiry, or when the process restarts.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from .gate import ContextItem, Invocation, PhotoRef


@dataclass
class _Buffered:
    at: float
    item: ContextItem


class ForwardBuffer:
    def __init__(self, ttl_seconds: float, max_messages: int, clock=time.monotonic):
        self._ttl = ttl_seconds
        self._max = max_messages
        self._clock = clock
        self._items: dict[tuple[int, int], deque[_Buffered]] = defaultdict(deque)

    def add(self, chat_id: int, user_id: int, item: ContextItem) -> None:
        self._expire()
        queue = self._items[(chat_id, user_id)]
        queue.append(_Buffered(self._clock(), item))
        while len(queue) > self._max:
            queue.popleft()

    def take(self, chat_id: int, user_id: int) -> list[ContextItem]:
        self._expire()
        queue = self._items.pop((chat_id, user_id), None)
        return [b.item for b in queue] if queue else []

    def __len__(self) -> int:
        self._expire()
        return sum(len(q) for q in self._items.values())

    def _expire(self) -> None:
        cutoff = self._clock() - self._ttl
        for key in list(self._items):
            queue = self._items[key]
            while queue and queue[0].at < cutoff:
                queue.popleft()
            if not queue:
                del self._items[key]


def select_photos(
    inv: Invocation, forwards: list[ContextItem], max_images: int
) -> list[PhotoRef]:
    """Images to send to Claude, most relevant first: the question's own,
    then the replied-to message's, then forwarded ones."""
    photos = list(inv.photos)
    if inv.reply:
        photos += inv.reply.photos
    for item in forwards:
        photos += item.photos
    return photos[:max_images]


def has_content(inv: Invocation, forwards: list[ContextItem]) -> bool:
    return bool(
        inv.question
        or inv.photos
        or (inv.reply and (inv.reply.text or inv.reply.photos))
        or forwards
    )


def _describe(item: ContextItem) -> str:
    body = item.text.strip() or "(no text)"
    if item.photos:
        body += f"\n[{len(item.photos)} photo attached]"
    return f"{item.label}:\n{body}"


def build_prompt(inv: Invocation, forwards: list[ContextItem], *, now_text: str) -> str:
    """The user turn sent to Claude. Chat content is fenced in <context> and
    the system prompt tells Claude to treat it as data, not instructions."""
    parts = [f"Current date and time: {now_text}", f"Asked by: {inv.user_name}"]
    items = ([inv.reply] if inv.reply else []) + forwards
    if items:
        parts.append(
            "<context>\n" + "\n\n".join(_describe(i) for i in items) + "\n</context>"
        )
    if inv.photos:
        parts.append(f"[{len(inv.photos)} photo attached to the question]")
    question = inv.question.strip()
    if not question:
        question = (
            "(No question was typed. Help with the context above: describe, "
            "summarize or answer what it most likely needs.)"
        )
    parts.append(f"<question>\n{question}\n</question>")
    return "\n\n".join(parts)
