from orca_talkback.spoken import (
    SentenceChunker,
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
    assert list(spoken_sentences("written reply", rewrite)) == [
        "I fixed the login form for you.",
        "Should I open a pull request?",
    ]
    assert "written reply" in rewrite.prompts[0]


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
    sentences = list(spoken_sentences("written reply", broken_rewrite, log=logs.append))
    assert sentences == ["I fixed the login form.", "And then I"]
    assert "rewrite failed" in logs[0]
