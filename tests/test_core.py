import asyncio

import pytest

from bot.core import FamilyBot
from bot.usage import SqliteUsageStore, day_key

from conftest import BOT, DAD, FAMILY, MOM, OWNER, OWNER_USER, make_config, mention_entities, message
from fakes import FakeAgent, FakeTelegram


async def make_bot(cfg=None, agent=None, **cfg_overrides):
    cfg = cfg or make_config(**cfg_overrides)
    tg = FakeTelegram()
    store = SqliteUsageStore(":memory:")
    bot = FamilyBot(cfg, tg, store, agent_runner=agent or FakeAgent())
    await bot.start()
    return bot, tg, store


async def ask(bot, update):
    await bot.handle_update(update)
    await bot.wait_idle()


async def test_answer_replaces_placeholder_and_shows_remaining():
    bot, tg, _ = await make_bot(agent=FakeAgent("**Paris** is the capital."))
    await ask(bot, message("/claude capital of France?"))
    first = tg.sent("send_message")[0]
    assert first["text"] == "🔎 Thinking…"
    edit = tg.sent("edit_message")[0]
    assert edit["text"].startswith("<b>Paris</b> is the capital.")
    assert "4 of 5 questions left today" in edit["text"]


async def test_sixth_question_is_refused_and_agent_not_called():
    agent = FakeAgent()
    bot, tg, _ = await make_bot(agent=agent)
    for _ in range(6):
        await ask(bot, message("/claude hi"))
    assert len(agent.calls) == 5
    assert "used all 5" in tg.texts()[-1]


async def test_owner_has_no_limit_and_no_counter():
    agent = FakeAgent("ok")
    bot, tg, store = await make_bot(agent=agent)
    for _ in range(8):
        await ask(bot, message("/claude hi", user=OWNER_USER))
    assert len(agent.calls) == 8
    assert all("left today" not in t for t in tg.texts())
    assert await store.used(OWNER, day_key(bot.cfg.tz)) == 0


async def test_failure_refunds_and_reports():
    bot, tg, store = await make_bot(agent=FakeAgent(error=RuntimeError("boom")))
    await ask(bot, message("/claude hi"))
    assert "didn't count" in tg.texts()[-1]
    assert await store.used(MOM["id"], day_key(bot.cfg.tz)) == 0


async def test_timeout_refunds():
    bot, tg, store = await make_bot(agent=FakeAgent(delay=5), CALL_TIMEOUT_SECONDS=0)
    await ask(bot, message("/claude hi"))
    assert "too long" in tg.texts()[-1]
    assert await store.used(MOM["id"], day_key(bot.cfg.tz)) == 0


async def test_empty_question_refunds_and_prompts():
    agent = FakeAgent()
    bot, tg, store = await make_bot(agent=agent)
    await ask(bot, message("/claude"))
    assert agent.calls == []
    assert "What would you like to know" in tg.texts()[-1]
    assert await store.used(MOM["id"], day_key(bot.cfg.tz)) == 0


async def test_forwards_before_and_after_command_are_included():
    agent = FakeAgent()
    bot, tg, _ = await make_bot(agent=agent, FORWARD_GRACE_SECONDS="0.05")
    origin = {"type": "user", "sender_user": DAD, "date": 1_700_000_000}
    await bot.handle_update(message("before", forward_origin=origin))
    await bot.handle_update(message("/claude summarize these"))
    await bot.handle_update(message("after", forward_origin=origin))  # forward-with-comment order
    await bot.handle_update(message("dad's own", user=DAD, forward_origin=origin))
    await bot.wait_idle()
    prompt = agent.calls[0]["prompt"]
    assert "before" in prompt and "after" in prompt
    assert "dad's own" not in prompt  # someone else's forwards stay separate
    assert "Forwarded from Dad" in prompt


async def test_only_forwards_with_empty_command_is_a_valid_question():
    agent = FakeAgent()
    bot, tg, _ = await make_bot(agent=agent)
    await bot.handle_update(message("article text", forward_origin={"type": "hidden_user", "sender_user_name": "X", "date": 1}))
    await ask(bot, message("/claude"))
    assert len(agent.calls) == 1
    assert "article text" in agent.calls[0]["prompt"]


async def test_photos_are_downloaded_and_sent_as_images():
    agent = FakeAgent()
    bot, tg, _ = await make_bot(agent=agent)
    caption = f"@{BOT.username} what is this"
    upd = message(
        None, caption=caption, caption_entities=mention_entities(caption),
        photo=[{"file_id": "p1", "width": 10, "height": 10, "file_size": 100}],
    )
    await ask(bot, upd)
    images = agent.calls[0]["images"]
    assert [i.data for i in images] == [b"img-p1"]
    assert images[0].media_type == "image/jpeg"


async def test_images_from_answer_are_sent_as_album():
    bot, tg, _ = await make_bot(agent=FakeAgent("look", images=["https://a/1.jpg", "https://a/2.jpg"]))
    await ask(bot, message("/claude show me otters"))
    assert tg.sent("send_media_group")[0]["urls"] == ["https://a/1.jpg", "https://a/2.jpg"]


async def test_album_failure_falls_back_to_single_photos():
    bot, tg, _ = await make_bot(agent=FakeAgent("look", images=["https://a/1.jpg", "https://a/2.jpg"]))
    tg.fail_album = True
    await ask(bot, message("/claude show me otters"))
    assert [p["url"] for p in tg.sent("send_photo")] == ["https://a/1.jpg", "https://a/2.jpg"]


async def test_html_rejection_falls_back_to_plain_text():
    bot, tg, _ = await make_bot(agent=FakeAgent("**hi**"))
    tg.fail_html = True
    await ask(bot, message("/claude hi"))
    edit = tg.sent("edit_message")[-1]
    assert edit["html"] is False and edit["text"].startswith("hi")


async def test_long_answer_is_split_into_messages():
    long = "\n\n".join("paragraph %d " % i + "x" * 500 for i in range(20))
    bot, tg, _ = await make_bot(agent=FakeAgent(long))
    await ask(bot, message("/claude essay please"))
    assert len(tg.sent("edit_message")) == 1
    answers = [m for m in tg.sent("send_message") if m["text"] != "🔎 Thinking…"]
    assert len(answers) >= 2
    assert all(len(m["text"]) <= 4096 for m in answers)


async def test_one_question_at_a_time_per_user():
    agent = FakeAgent(delay=0.1)
    bot, tg, store = await make_bot(agent=agent)
    await bot.handle_update(message("/claude one"))
    await bot.handle_update(message("/claude two"))
    await bot.wait_idle()
    assert len(agent.calls) == 1
    assert any("still working" in t for t in tg.texts())
    assert await store.used(MOM["id"], day_key(bot.cfg.tz)) == 1


async def test_usage_command():
    bot, tg, _ = await make_bot()
    await ask(bot, message("/claude hi"))
    await ask(bot, message("/usage"))
    assert tg.texts()[-1] == "You have 4 of 5 questions left today."
    await ask(bot, message("/usage", user=OWNER_USER))
    assert "no daily limit" in tg.texts()[-1]


async def test_other_group_is_left_and_strangers_refused():
    agent = FakeAgent()
    bot, tg, _ = await make_bot(agent=agent)
    await ask(bot, message("/claude hi", chat_id=-777))
    assert tg.sent("leave_chat") == [{"chat_id": -777}]
    await ask(bot, message("/claude hi", user=DAD, chat_id=DAD["id"], chat_type="private"))
    assert "only answer in the family group" in tg.texts()[-1]
    assert agent.calls == []
