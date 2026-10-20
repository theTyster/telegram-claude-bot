"""Turn Claude's Markdown into Telegram-safe HTML and split long answers."""

from __future__ import annotations

import html
import re

# Telegram's limit is 4096 characters after entity parsing; leave headroom
# for the tags we add.
CHUNK_CHARS = 3500

_FENCE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?![\w*])|(?<![\w_])_(?!\s)([^_\n]+?)_(?![\w_])")
_STRIKE = re.compile(r"~~(.+?)~~")
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)
_BULLET = re.compile(r"^(\s*)[-*+]\s+", re.MULTILINE)


def md_to_html(text: str) -> str:
    """Convert the small Markdown subset Claude is asked to use."""
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    # Pull out code first so nothing inside it is touched.
    text = _FENCE.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = _INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = _LINK.sub(
        lambda m: keep(
            f'<a href="{html.escape(m.group(2), quote=True)}">{html.escape(m.group(1))}</a>'
        ),
        text,
    )

    text = html.escape(text, quote=False)
    text = _HEADING.sub(r"<b>\1</b>", text)
    text = _BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = _STRIKE.sub(r"<s>\1</s>", text)
    text = _BULLET.sub(r"\1• ", text)

    return re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)


def split_markdown(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraph breaks (outside code fences) into chunks under ``limit``."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        current.append(line)
        if not in_fence and line.strip() == "":
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))

    chunks: list[str] = []
    buf = ""
    for block in blocks:
        while len(block) > limit:  # a single huge paragraph: hard split
            if buf:
                chunks.append(buf)
                buf = ""
            cut = block.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append(block[:cut])
            block = block[cut:].lstrip()
        if len(buf) + len(block) + 1 > limit and buf:
            chunks.append(buf)
            buf = block
        else:
            buf = f"{buf}\n{block}" if buf else block
    if buf.strip():
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()] or [""]


def strip_markdown(text: str) -> str:
    """Plain-text fallback if Telegram rejects our HTML."""
    text = _FENCE.sub(lambda m: m.group(1), text)
    text = _LINK.sub(r"\1 (\2)", text)
    for pattern in (_BOLD, _STRIKE, _INLINE_CODE):
        text = pattern.sub(lambda m: next(g for g in m.groups() if g is not None), text)
    return text
