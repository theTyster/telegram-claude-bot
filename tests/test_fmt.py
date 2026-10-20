from bot.telegram_fmt import md_to_html, split_markdown, strip_markdown


def test_escapes_html():
    assert md_to_html("1 < 2 & 3 > 2") == "1 &lt; 2 &amp; 3 &gt; 2"


def test_basic_markdown():
    out = md_to_html("**bold** and _it_ and *it2* and ~~gone~~")
    assert out == "<b>bold</b> and <i>it</i> and <i>it2</i> and <s>gone</s>"


def test_links_and_code():
    out = md_to_html("see [the docs](https://x.com/a_b?c=1&d=2) and `a<b`")
    assert '<a href="https://x.com/a_b?c=1&amp;d=2">the docs</a>' in out
    assert "<code>a&lt;b</code>" in out


def test_code_block_contents_untouched():
    out = md_to_html("```python\nx = a**2 * b_c_d\n```")
    assert out == "<pre>x = a**2 * b_c_d</pre>"


def test_headings_and_bullets():
    out = md_to_html("# Title\n- one\n* two")
    assert out == "<b>Title</b>\n• one\n• two"


def test_snake_case_not_italicized():
    assert md_to_html("use my_var_name here") == "use my_var_name here"


def test_split_short_text_single_chunk():
    assert split_markdown("hello") == ["hello"]


def test_split_on_paragraphs_under_limit():
    paras = ["para %d " % i + "x" * 80 for i in range(10)]
    chunks = split_markdown("\n\n".join(paras), limit=300)
    assert all(len(c) <= 300 for c in chunks)
    assert "".join(chunks).replace("\n", "") == "".join(paras)


def test_split_hard_splits_huge_paragraph():
    chunks = split_markdown("word " * 1000, limit=500)
    assert all(len(c) <= 500 for c in chunks)
    assert len(chunks) >= 10


def test_split_does_not_break_inside_code_fence():
    text = "intro\n\n```\nline1\n\nline2\n```\n\noutro"
    chunks = split_markdown(text, limit=30)
    fence = next(c for c in chunks if "```" in c)
    assert fence.count("```") == 2


def test_strip_markdown():
    assert strip_markdown("**hi** [x](https://y.z)") == "hi x (https://y.z)"
