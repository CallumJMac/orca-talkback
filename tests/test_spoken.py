from orca_talkback.spoken import (
    SentenceChunker,
    extract_spoken_line,
    needs_rewrite,
    split_sentences,
    spoken_sentences,
    strip_for_speech,
)


class FakeRewriter:
    def __init__(self, fragments: list[str], fail: bool = False):
        self.fragments = fragments
        self.fail = fail
        self.prompts: list[str] = []

    def __call__(self, prompt: str):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("provider unavailable")
        yield from self.fragments


def test_chunker_releases_complete_sentences():
    chunker = SentenceChunker()
    assert chunker.feed("I fixed the login form for you") == []
    assert chunker.feed(". All the tests pass now. Want a") == [
        "I fixed the login form for you.",
        "All the tests pass now.",
    ]
    assert chunker.flush() == ["Want a"]


def test_chunker_merges_short_fragments():
    assert split_sentences("Done. I updated the README for you. Ok?") == [
        "Done. I updated the README for you.",
        "Ok?",
    ]


def test_strip_removes_code_links_and_markdown():
    text = "## Result\nI changed `app.ts` here, see [the docs](https://example.com).\n\n```ts\nconst value = 1\n```\n- **All** tests pass."
    assert strip_for_speech(text) == "Result I changed app.ts here, see the docs. All tests pass."


def test_strip_truncates_at_sentence_boundary():
    text = "This sentence is short enough. " * 40
    stripped = strip_for_speech(text)
    assert len(stripped) <= 600
    assert stripped.endswith(".")


def test_spoken_sentences_streams_the_rewrite():
    rewrite = FakeRewriter(["I fixed the login form", " for you. Should I open", " a pull request?"])
    assert list(spoken_sentences("Updated `src/app.ts` for you.", rewrite)) == [
        "I fixed the login form for you.",
        "Should I open a pull request?",
    ]
    assert "Updated `src/app.ts` for you." in rewrite.prompts[0]


def test_spoken_sentences_falls_back_when_rewrite_fails():
    logs: list[str] = []
    rewrite = FakeRewriter([], fail=True)
    sentences = list(spoken_sentences("I changed `app.ts` for you. Tests pass.", rewrite, log=logs.append))
    assert sentences == ["I changed app.ts for you.", "Tests pass."]
    assert "rewrite failed" in logs[0]


def test_spoken_sentences_without_rewriter_uses_fallback():
    assert list(spoken_sentences("All forty two tests pass now.", None)) == ["All forty two tests pass now."]


def test_partial_stream_failure_still_speaks_the_buffered_text():
    def broken_rewrite(prompt: str):
        yield "I fixed the login form. And then I"
        raise RuntimeError("connection reset")

    logs: list[str] = []
    sentences = list(spoken_sentences("Updated `src/app.ts` for you.", broken_rewrite, log=logs.append))
    assert sentences == ["I fixed the login form.", "And then I"]
    assert "rewrite failed" in logs[0]


def test_hidden_spoken_line_is_spoken_without_a_rewrite():
    rewrite = FakeRewriter(["should not be used. "])
    reply = "## Done\n| a | b |\n\nPushed `abc123`.\n\n<!-- spoken: I pushed the fix. Want me to tag a release? -->"
    assert list(spoken_sentences(reply, rewrite)) == ["I pushed the fix. Want me to tag a release?"]
    assert rewrite.prompts == []


def test_last_spoken_line_wins():
    reply = "<!-- spoken: Old line here, ignore it. -->\nBody.\n<!-- spoken: The newest spoken line wins. -->"
    assert extract_spoken_line(reply) == "The newest spoken line wins."


def test_no_spoken_line_returns_none():
    assert extract_spoken_line("Just a normal reply.") is None


def test_short_plain_reply_is_spoken_directly_without_a_rewrite():
    rewrite = FakeRewriter(["should not be used. "])
    assert list(spoken_sentences("Here's a quick one for you. It should play right away.", rewrite)) == [
        "Here's a quick one for you.", "It should play right away.",
    ]
    assert rewrite.prompts == []


def test_long_or_technical_reply_still_uses_the_rewrite():
    rewrite = FakeRewriter(["Summarised for speech. "])
    technical = "Pushed it.\n\n| Repo | Commit |\n|---|---|\n| orca-talkback | `486d9cb` |"
    assert list(spoken_sentences(technical, rewrite)) == ["Summarised for speech."]
    assert len(rewrite.prompts) == 1


def test_needs_rewrite_rules():
    assert not needs_rewrite("A short plain sentence. And another one.")
    assert needs_rewrite("See `src/app.ts` for details.")
    assert needs_rewrite("- a bullet\n- another")
    assert needs_rewrite("Open https://example.com to check.")
    assert needs_rewrite("word " * 80)


def test_cleanup_removes_html_comments():
    assert strip_for_speech("Visible text here. <!-- hidden note -->") == "Visible text here."
