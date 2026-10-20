import itertools

from bot.agent import AgentResult
from bot.telegram_client import TelegramError

from conftest import BOT


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self._ids = itertools.count(10_000)
        self.fail_html = False
        self.fail_album = False
        self.files = {}

    def _record(self, method, **kw):
        self.calls.append((method, kw))

    def sent(self, method=None):
        return [kw for m, kw in self.calls if method is None or m == method]

    def texts(self):
        return [kw["text"] for m, kw in self.calls if m in ("send_message", "edit_message")]

    async def get_me(self):
        return {"id": BOT.id, "username": BOT.username}

    async def set_my_commands(self, commands):
        self._record("set_my_commands", commands=commands)

    async def send_message(self, chat_id, text, *, reply_to=None, html=True):
        if html and self.fail_html:
            raise TelegramError("sendMessage", 400, "Bad Request: can't parse entities")
        self._record("send_message", chat_id=chat_id, text=text, reply_to=reply_to, html=html)
        return {"message_id": next(self._ids)}

    async def edit_message(self, chat_id, message_id, text, *, html=True):
        if html and self.fail_html:
            raise TelegramError("editMessageText", 400, "Bad Request: can't parse entities")
        self._record("edit_message", chat_id=chat_id, message_id=message_id, text=text, html=html)

    async def send_chat_action(self, chat_id, action="typing"):
        self._record("send_chat_action", chat_id=chat_id)

    async def send_photo(self, chat_id, url, *, reply_to=None):
        self._record("send_photo", chat_id=chat_id, url=url)

    async def send_media_group(self, chat_id, urls, *, reply_to=None):
        if self.fail_album:
            raise TelegramError("sendMediaGroup", 400, "Bad Request: failed to get HTTP URL content")
        self._record("send_media_group", chat_id=chat_id, urls=urls)

    async def leave_chat(self, chat_id):
        self._record("leave_chat", chat_id=chat_id)

    async def download_file(self, file_id, *, max_bytes):
        self._record("download_file", file_id=file_id)
        return self.files.get(file_id, b"img-" + file_id.encode())


class FakeAgent:
    def __init__(self, text="Here's the answer.", images=(), error=None, delay=0):
        self.text = text
        self.images = list(images)
        self.error = error
        self.delay = delay
        self.calls = []

    async def __call__(self, cfg, prompt, images):
        import asyncio

        self.calls.append({"prompt": prompt, "images": images})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return AgentResult(self.text, self.images, 0.02, 3)
