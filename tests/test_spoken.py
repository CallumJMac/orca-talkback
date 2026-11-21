from orca_talkback.spoken import (
    SentenceChunker,
    extract_spoken_line,
    split_sentences,
    spoken_sentences,
    strip_for_speech,
)


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


def test_last_spoken_line_wins():
    reply = "<!-- spoken: Old line here, ignore it. -->\nBody.\n<!-- spoken: The newest spoken line wins. -->"
    assert extract_spoken_line(reply) == "The newest spoken line wins."


def test_no_spoken_line_returns_none():
    assert extract_spoken_line("Just a normal reply.") is None


def test_cleanup_removes_html_comments():
    assert strip_for_speech("Visible text here. <!-- hidden note -->") == "Visible text here."


def test_hidden_spoken_line_is_what_gets_spoken():
    reply = "## Done\n| a | b |\n\nPushed `abc123`.\n\n<!-- spoken: I pushed the fix. Want me to tag a release? -->"
    assert list(spoken_sentences(reply)) == ["I pushed the fix. Want me to tag a release?"]


def test_reply_without_a_spoken_line_is_cleaned_up_and_spoken():
    assert list(spoken_sentences("I changed `app.ts` for you. Tests pass.")) == ["I changed app.ts for you.", "Tests pass."]


def test_technical_reply_without_a_spoken_line_drops_code_and_tables():
    reply = "Pushed it for you just now.\n\n| Repo | Commit |\n|---|---|\n| orca-talkback | `486d9cb` |\n\n```sh\ngit push\n```"
    assert list(spoken_sentences(reply)) == ["Pushed it for you just now."]


def test_markdown_comment_spoken_line_is_found():
    reply = "Results now go to the sheet.\n\n[//]: # (spoken: The study now saves results to your sheet. Can you do one test run?)"
    assert extract_spoken_line(reply) == "The study now saves results to your sheet. Can you do one test run?"


def test_latest_spoken_line_wins_across_both_forms():
    reply = "<!-- spoken: Old form line here. -->\nBody.\n\n[//]: # (spoken: New form line wins here.)"
    assert extract_spoken_line(reply) == "New form line wins here."


def test_cleanup_drops_markdown_comments():
    assert strip_for_speech("Visible text here.\n\n[//]: # (spoken: hidden line)") == "Visible text here."
