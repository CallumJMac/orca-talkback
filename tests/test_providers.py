import boto3

from orca_talkback.providers import (
    ANTHROPIC_DEFAULT_MODEL,
    BEDROCK_DEFAULT_MODEL,
    anthropic_rewriter,
    bedrock_rewriter,
    make_anthropic_client,
    make_bedrock_client,
)


class FakeAnthropicStream:
    def __init__(self, fragments: list[str]):
        self.text_stream = iter(fragments)

    def __enter__(self):
        return self

    def __exit__(self, *exception_info):
        return False


class FakeAnthropicClient:
    def __init__(self, fragments: list[str]):
        self.fragments = fragments
        self.requests: list[dict] = []
        self.messages = self

    def stream(self, **request):
        self.requests.append(request)
        return FakeAnthropicStream(self.fragments)


class FakeBedrockClient:
    def __init__(self, fragments: list[str]):
        self.fragments = fragments
        self.requests: list[dict] = []

    def converse_stream(self, **request):
        self.requests.append(request)
        events = [{"contentBlockDelta": {"delta": {"text": fragment}}} for fragment in self.fragments]
        return {"stream": events + [{"messageStop": {"stopReason": "end_turn"}}]}


def test_anthropic_rewriter_streams_text_from_haiku_by_default():
    client = FakeAnthropicClient(["Fixed the form. ", "Want a PR?"])
    rewrite = anthropic_rewriter(client)
    assert list(rewrite("the prompt")) == ["Fixed the form. ", "Want a PR?"]
    request = client.requests[0]
    assert request["model"] == ANTHROPIC_DEFAULT_MODEL == "claude-haiku-4-5"
    assert request["messages"] == [{"role": "user", "content": "the prompt"}]
    assert request["max_tokens"] == 200


def test_anthropic_rewriter_uses_the_requested_model():
    client = FakeAnthropicClient([])
    list(anthropic_rewriter(client, "claude-sonnet-5-5")("prompt"))
    assert client.requests[0]["model"] == "claude-sonnet-5-5"


def test_anthropic_client_fails_fast(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = make_anthropic_client()
    assert client.max_retries == 0
    assert client.timeout == 3.0


def test_bedrock_rewriter_yields_only_text_deltas():
    client = FakeBedrockClient(["Fixed the form. ", "", "Want a PR?"])
    rewrite = bedrock_rewriter(client)
    assert list(rewrite("the prompt")) == ["Fixed the form. ", "Want a PR?"]
    request = client.requests[0]
    assert request["modelId"] == BEDROCK_DEFAULT_MODEL
    assert request["messages"][0]["content"][0]["text"] == "the prompt"


def test_bedrock_client_fails_fast_after_three_seconds(monkeypatch):
    monkeypatch.setattr(
        boto3, "Session",
        lambda profile_name, region_name: boto3.session.Session(
            region_name=region_name, aws_access_key_id="test", aws_secret_access_key="test",
        ),
    )
    client = make_bedrock_client(None, "us-east-1")
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client.meta.config.connect_timeout == 3
    assert client.meta.config.read_timeout == 3
