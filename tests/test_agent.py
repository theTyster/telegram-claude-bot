import base64
import os

import pytest

from claude_agent_sdk import AssistantMessage, CLIConnectionError, ResultMessage, TextBlock
from claude_agent_sdk._errors import ResultError

from bot.agent import AgentError, ImageInput, build_options, build_user_message, extract_images, run_agent

from conftest import make_config


def result(text="answer", subtype="success", is_error=False, cost=0.01):
    return ResultMessage(
        subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error,
        num_turns=2, session_id="s", total_cost_usd=cost, result=text,
    )


def test_api_key_mode_passes_only_api_key(tmp_path):
    cfg = make_config(ANTHROPIC_API_KEY="sk-1", CLAUDE_CODE_OAUTH_TOKEN="oauth-1")
    opts = build_options(cfg, str(tmp_path))
    assert opts.env == {"ANTHROPIC_API_KEY": "sk-1"}
    assert opts.max_budget_usd == 0.5


def test_subscription_mode_passes_only_oauth_token(tmp_path):
    cfg = make_config(AUTH_MODE="subscription", ANTHROPIC_API_KEY="sk-1", CLAUDE_CODE_OAUTH_TOKEN="oauth-1")
    opts = build_options(cfg, str(tmp_path))
    assert opts.env == {"CLAUDE_CODE_OAUTH_TOKEN": "oauth-1"}
    assert opts.max_budget_usd is None  # cost cap only makes sense when paying per call


def test_options_lock_down_tools(tmp_path):
    opts = build_options(make_config(), str(tmp_path))
    assert opts.tools == ["WebSearch", "WebFetch"]
    assert opts.allowed_tools == ["WebSearch", "WebFetch"]
    assert {"Bash", "Read", "Write", "Edit"} <= set(opts.disallowed_tools)
    assert opts.permission_mode == "dontAsk"
    assert opts.setting_sources == []
    assert opts.model == "sonnet"
    assert opts.extra_args == {"no-session-persistence": None}


def test_user_message_has_images_then_text():
    msg = build_user_message("hi", [ImageInput("image/png", b"\x89PNG")])
    content = msg["message"]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(content[0]["source"]["data"]) == b"\x89PNG"
    assert content[1] == {"type": "text", "text": "hi"}


def test_extract_images():
    text = "Here you go:\n[[image: https://a.com/x.jpg]]\n[[image: javascript:alert(1)]]\n[[ image: http://b.org/y.png ]]\nEnjoy"
    cleaned, urls = extract_images(text)
    assert urls == ["https://a.com/x.jpg", "http://b.org/y.png"]
    assert "[[" not in cleaned.replace("[[image: javascript:alert(1)]]", "")
    assert cleaned.startswith("Here you go:") and cleaned.endswith("Enjoy")


def test_extract_images_caps_and_dedupes():
    text = "\n".join(f"[[image: https://a.com/{i % 5}.jpg]]" for i in range(10))
    _, urls = extract_images(text, max_images=4)
    assert urls == [f"https://a.com/{i}.jpg" for i in range(4)]


async def test_run_agent_returns_text_and_images_and_cleans_up(cfg):
    seen = {}

    async def fake_query(*, prompt, options):
        seen["messages"] = [m async for m in prompt]
        seen["cwd"] = options.cwd
        assert os.path.isdir(options.cwd)
        yield AssistantMessage(content=[TextBlock("thinking")], model="m")
        yield result("Pangolins!\n[[image: https://x.org/p.jpg]]")

    out = await run_agent(cfg, "what is a pangolin", [], query_fn=fake_query)
    assert out.text == "Pangolins!"
    assert out.image_urls == ["https://x.org/p.jpg"]
    assert out.cost_usd == 0.01
    assert seen["messages"][0]["message"]["content"][-1]["text"] == "what is a pangolin"
    assert not os.path.exists(seen["cwd"])


async def test_run_agent_max_turns_falls_back_to_last_text(cfg):
    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock("partial findings")], model="m")
        yield result(None, subtype="error_max_turns", is_error=True)

    out = await run_agent(cfg, "q", [], query_fn=fake_query)
    assert out.text.startswith("partial findings")
    assert "ran out of research steps" in out.text


async def test_run_agent_error_without_text_raises(cfg):
    async def fake_query(*, prompt, options):
        yield result(None, subtype="error_during_execution", is_error=True)

    with pytest.raises(AgentError):
        await run_agent(cfg, "q", [], query_fn=fake_query)


async def test_api_error_text_is_never_posted_as_an_answer(cfg):
    # What the CLI really does with a bad key: an is_error "success" result
    # whose text is the error, then a non-zero exit (raised by the SDK).
    async def fake_query(*, prompt, options):
        yield result("Invalid API key · Fix external API key", is_error=True)
        raise ResultError("Claude Code returned an error result")

    with pytest.raises(AgentError, match="Invalid API key"):
        await run_agent(cfg, "q", [], query_fn=fake_query)


async def test_partial_answer_survives_sdk_exception_after_max_turns(cfg):
    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock("found most of it")], model="m")
        yield result(None, subtype="error_max_turns", is_error=True)
        raise ResultError("Claude Code returned an error result")

    out = await run_agent(cfg, "q", [], query_fn=fake_query)
    assert out.text.startswith("found most of it")


async def test_sdk_failure_before_any_result_propagates(cfg):
    async def fake_query(*, prompt, options):
        raise CLIConnectionError("no CLI")
        yield  # pragma: no cover

    with pytest.raises(CLIConnectionError):
        await run_agent(cfg, "q", [], query_fn=fake_query)
