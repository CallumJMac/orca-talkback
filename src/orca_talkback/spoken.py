"""Turn a written agent reply into short spoken sentences."""

import re
from collections.abc import Iterator

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MIN_SENTENCE_CHARS = 20
MAX_FALLBACK_CHARS = 600
# The line an agent in voice mode adds to the end of its reply; hidden when the reply is rendered.
SPOKEN_LINE = re.compile(r"<!--\s*spoken:\s*(.*?)\s*-->", re.DOTALL | re.IGNORECASE)


class SentenceChunker:
    """Collects streamed text and releases complete sentences.

    Fragments shorter than MIN_SENTENCE_CHARS (like "Done.") are merged into
    the next sentence so Kokoro is never asked to say a lone word.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._carry = ""

    def feed(self, fragment: str) -> list[str]:
        self._buffer += fragment
        sentences: list[str] = []
        while match := SENTENCE_END.search(self._buffer):
            sentence = self._buffer[: match.start()].strip()
            self._buffer = self._buffer[match.end():]
            self._carry = f"{self._carry} {sentence}".strip()
            if len(self._carry) >= MIN_SENTENCE_CHARS:
                sentences.append(self._carry)
                self._carry = ""
        return sentences

    def flush(self) -> list[str]:
        remainder = f"{self._carry} {self._buffer.strip()}".strip()
        self._buffer = ""
        self._carry = ""
        return [remainder] if remainder else []


def split_sentences(text: str) -> list[str]:
    chunker = SentenceChunker()
    return chunker.feed(text) + chunker.flush()


def extract_spoken_line(reply: str) -> str | None:
    """The agent's own spoken version of its reply, if it wrote one (the last one wins)."""
    matches = [match.strip() for match in SPOKEN_LINE.findall(reply) if match.strip()]
    return matches[-1] if matches else None


def strip_for_speech(text: str) -> str:
    """Make a reply speakable when the agent wrote no spoken line: drop code, tables, links and markdown."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"^\|.*\|\s*$", " ", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"(?:~|\.{0,2})?/[\w./@-]+", " ", text)
    text = re.sub(r"^\s*(#+|[-*]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_>#]", "", text)
    text = re.sub(r"\s+([.,!?])", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_FALLBACK_CHARS:
        cut = text[:MAX_FALLBACK_CHARS]
        boundary = cut.rfind(". ")
        text = cut[: boundary + 1] if boundary > 0 else cut
    return text


def spoken_sentences(reply: str) -> Iterator[str]:
    """Yield what to say: the agent's own hidden spoken line, or else the cleaned-up reply."""
    spoken_line = extract_spoken_line(reply)
    yield from split_sentences(spoken_line if spoken_line is not None else strip_for_speech(reply))
