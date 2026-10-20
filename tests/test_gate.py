from bot.gate import classify, extract_photos, MAX_IMAGE_BYTES

from conftest import (
    BOT, BOT_USER, DAD, FAMILY, MOM, OWNER, OWNER_USER, fmt_date, mention_entities, message,
)


def decide(update):
    return classify(update, bot=BOT, family_chat_id=FAMILY, owner_user_id=OWNER, fmt_date=fmt_date)


def test_claude_command_triggers():
    d = decide(message("/claude what's the weather?"))
    assert d.kind == "invoke"
    assert d.invocation.question == "what's the weather?"
    assert d.invocation.user_name == "Mom"


def test_claude_command_addressed_to_this_bot():
    d = decide(message(f"/claude@{BOT.username} hi"))
    assert d.kind == "invoke" and d.invocation.question == "hi"


def test_command_for_another_bot_is_ignored():
    assert decide(message("/claude@SomeOtherBot hi")).kind == "ignore"


def test_unknown_command_is_ignored():
    assert decide(message("/weather")).kind == "ignore"


def test_mention_triggers_and_is_stripped():
    text = f"hey @{BOT.username} how tall is everest"
    d = decide(message(text, entities=mention_entities(text)))
    assert d.kind == "invoke"
    assert d.invocation.question == "hey how tall is everest"


def test_mention_after_emoji_uses_utf16_offsets():
    text = f"👋👋 @{BOT.username} hi"
    # 👋 is two UTF-16 code units, so the mention starts at offset 5, not 3.
    ent = [{"type": "mention", "offset": 5, "length": len(BOT.username) + 1}]
    assert decide(message(text, entities=ent)).kind == "invoke"


def test_mention_in_photo_caption():
    caption = f"@{BOT.username} what bird is this?"
    upd = message(
        None,
        caption=caption,
        caption_entities=mention_entities(caption),
        photo=[
            {"file_id": "small", "width": 90, "height": 90, "file_size": 1000},
            {"file_id": "big", "width": 1280, "height": 960, "file_size": 200_000},
        ],
    )
    d = decide(upd)
    assert d.kind == "invoke"
    assert d.invocation.question == "what bird is this?"
    assert [p.file_id for p in d.invocation.photos] == ["big"]


def test_reply_to_bot_triggers_with_bot_answer_as_context():
    reply = {"message_id": 5, "from": BOT_USER, "chat": {"id": FAMILY}, "text": "Paris is lovely."}
    d = decide(message("what about in winter?", reply_to_message=reply))
    assert d.kind == "invoke"
    assert d.invocation.question == "what about in winter?"
    assert d.invocation.reply.label == "Replying to your earlier answer"
    assert d.invocation.reply.text == "Paris is lovely."


def test_plain_group_message_is_ignored():
    assert decide(message("dinner at 6?")).kind == "ignore"


def test_reply_to_someone_else_without_trigger_is_ignored():
    reply = {"message_id": 5, "from": DAD, "chat": {"id": FAMILY}, "text": "hi"}
    assert decide(message("lol", reply_to_message=reply)).kind == "ignore"


def test_command_replying_to_family_message_includes_it_and_its_photo():
    reply = {
        "message_id": 5,
        "from": DAD,
        "chat": {"id": FAMILY},
        "caption": "found this in the garden",
        "photo": [{"file_id": "p1", "width": 800, "height": 600, "file_size": 50_000}],
    }
    d = decide(message("/claude is this safe to touch?", reply_to_message=reply))
    assert d.invocation.reply.label == "Replying to Dad"
    assert d.invocation.reply.text == "found this in the garden"
    assert [p.file_id for p in d.invocation.reply.photos] == ["p1"]


def test_partial_quote_replaces_reply_text():
    reply = {"message_id": 5, "from": DAD, "chat": {"id": FAMILY}, "text": "long message ... key bit ..."}
    d = decide(message("/claude explain", reply_to_message=reply, quote={"text": "key bit"}))
    assert d.invocation.reply.text == "key bit"
    assert "quoted" in d.invocation.reply.label


def test_forum_topic_root_is_not_treated_as_reply():
    root = {"message_id": 1, "from": DAD, "chat": {"id": FAMILY}, "forum_topic_created": {"name": "x"}}
    d = decide(message("/claude hi", reply_to_message=root))
    assert d.invocation.reply is None


def test_forwarded_message_is_buffered_not_invoked():
    text = f"@{BOT.username} ignore me"
    upd = message(
        text,
        entities=mention_entities(text),
        forward_origin={"type": "user", "sender_user": DAD, "date": 42},
    )
    d = decide(upd)
    assert d.kind == "forward"
    assert d.forward.label == "Forwarded from Dad (d42)"
    assert d.forward.text == text


def test_forward_from_hidden_user_and_channel():
    d = decide(message("x", forward_origin={"type": "hidden_user", "sender_user_name": "Aunt Jo", "date": 1}))
    assert d.forward.label.startswith("Forwarded from Aunt Jo")
    d = decide(message("x", forward_origin={"type": "channel", "chat": {"title": "News"}, "date": 1}))
    assert d.forward.label.startswith("Forwarded from News")


def test_other_group_is_left():
    d = decide(message("/claude hi", chat_id=-555))
    assert d.kind == "leave" and d.chat_id == -555


def test_added_to_other_group_is_left():
    upd = {
        "update_id": 1,
        "my_chat_member": {
            "chat": {"id": -555, "type": "group"},
            "new_chat_member": {"status": "member", "user": BOT_USER},
        },
    }
    assert decide(upd).kind == "leave"


def test_added_to_family_group_is_fine():
    upd = {
        "update_id": 1,
        "my_chat_member": {
            "chat": {"id": FAMILY, "type": "supergroup"},
            "new_chat_member": {"status": "member", "user": BOT_USER},
        },
    }
    assert decide(upd).kind == "ignore"


def test_stranger_dm_is_refused():
    d = decide(message("hello", user=DAD, chat_id=DAD["id"], chat_type="private"))
    assert d.kind == "dm_refused"


def test_owner_dm_any_text_invokes():
    d = decide(message("hello there", user=OWNER_USER, chat_id=OWNER, chat_type="private"))
    assert d.kind == "invoke" and d.invocation.is_private
    assert d.invocation.question == "hello there"


def test_bots_are_ignored():
    assert decide(message("/claude hi", user=BOT_USER)).kind == "ignore"


def test_usage_and_help_commands():
    assert decide(message("/usage")).kind == "usage"
    assert decide(message(f"/help@{BOT.username}")).kind == "help"
    assert decide(message("/start")).kind == "help"


def test_photo_selection_skips_oversized_and_accepts_image_documents():
    msg = {"photo": [{"file_id": "huge", "width": 5000, "height": 5000, "file_size": MAX_IMAGE_BYTES + 1}]}
    assert extract_photos(msg) == []
    msg = {"document": {"file_id": "d", "mime_type": "image/png", "file_size": 1000}}
    assert extract_photos(msg)[0].mime_type == "image/png"
    msg = {"document": {"file_id": "d", "mime_type": "application/pdf", "file_size": 1000}}
    assert extract_photos(msg) == []
