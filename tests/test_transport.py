from aiohttp.test_utils import TestClient, TestServer

from bot.transport import SECRET_HEADER, make_webhook_app

from conftest import message
from test_core import make_bot


async def client_for(bot):
    client = TestClient(TestServer(make_webhook_app(bot, "s3cret")))
    await client.start_server()
    return client


async def test_webhook_rejects_bad_secret():
    bot, tg, _ = await make_bot()
    client = await client_for(bot)
    try:
        resp = await client.post("/telegram", json=message("/claude hi"))
        assert resp.status == 401
        resp = await client.post("/telegram", json=message("/claude hi"), headers={SECRET_HEADER: "nope"})
        assert resp.status == 401
        assert tg.calls[1:] == []  # only set_my_commands from start()
    finally:
        await client.close()


async def test_webhook_handles_update_like_polling():
    bot, tg, _ = await make_bot()
    client = await client_for(bot)
    try:
        resp = await client.post("/telegram", json=message("/claude hi"), headers={SECRET_HEADER: "s3cret"})
        assert resp.status == 200
        await bot.wait_idle()
        assert tg.sent("edit_message")[0]["text"].startswith("Here's the answer.")
        assert (await client.get("/healthz")).status == 200
    finally:
        await client.close()
