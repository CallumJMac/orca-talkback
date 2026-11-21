"""Turn a written Claude Code reply into short spoken sentences."""

import re
from collections.abc import Callable, Iterable, Iterator

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MIN_SENTENCE_CHARS = 20
MAX_FALLBACK_CHARS = 600
# Replies at or under this length, with no markdown, code, paths or links, are spoken as written.
MAX_PLAIN_REPLY_CHARS = 300
# The line an agent in voice mode adds to the end of its reply; hidden when the reply is rendered.
SPOKEN_LINE = re.compile(r"<!--\s*spoken:\s*(.*?)\s*-->", re.DOTALL | re.IGNORECASE)
NEEDS_REWRITE = re.compile(r"```|`|^\s*\||^\s*([-*+]|\d+\.)\s|^\s*#|https?://|\w/\w", re.MULTILINE)

# A rewriter takes the full prompt and streams back text fragments (see providers.py).
Rewriter = Callable[[str], Iterable[str]]

SPOKEN_PROMPT = """You turn a coding assistant's written reply into what it would say out loud \
to the developer, like a colleague talking across the desk.
Rules: one to three short sentences, plain conversational English, no markdown, no code, \
no file paths, no URLs, no lists. Say what was done or found and any question being asked. \
Never use dashes. Output only the spoken text.

Written reply:
{reply}"""


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


def needs_rewrite(reply: str) -> bool:
    """True when a reply would sound wrong read aloud: long, or containing markdown, code, paths or links."""
    return len(reply.strip()) > MAX_PLAIN_REPLY_CHARS or bool(NEEDS_REWRITE.search(reply))


def strip_for_speech(text: str) -> str:
    """Fallback cleanup when the rewrite is unavailable."""
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


def spoken_sentences(
    reply: str,
    rewrite: Rewriter | None,
    log: Callable[[str], None] = print,
) -> Iterator[str]:
    """Yield spoken sentences, cheapest source first.

    1. The agent's own hidden spoken line, when it wrote one (voice mode).
    2. The reply itself, when it is short and plain enough to read aloud.
    3. The model rewrite, when one is configured; otherwise the cleaned-up reply.
    """
    spoken_line = extract_spoken_line(reply)
    if spoken_line is not None:
        yield from split_sentences(spoken_line)
        return
    if not needs_rewrite(reply):
        yield from split_sentences(strip_for_speech(reply))
        return
    if rewrite is not None:
        chunker = SentenceChunker()
        yielded_any = False
        try:
            for fragment in rewrite(SPOKEN_PROMPT.format(reply=reply[:12000])):
                for sentence in chunker.feed(fragment):
                    yielded_any = True
                    yield sentence
            for sentence in chunker.flush():
                yielded_any = True
                yield sentence
            if yielded_any:
                return
        except Exception as error:  # the rewrite is best effort
            log(f"[spoken] rewrite failed: {error!r}")
            if yielded_any:
                yield from chunker.flush()
                return
    yield from split_sentences(strip_for_speech(reply))
