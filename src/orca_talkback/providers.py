"""Model providers for the spoken rewrite. Each returns a Rewriter: prompt in, streamed text out."""

from collections.abc import Iterator
from typing import Any

from orca_talkback.spoken import Rewriter

ANTHROPIC_DEFAULT_MODEL = "claude-haiku-4-5"
BEDROCK_DEFAULT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
REWRITE_TIMEOUT_SECONDS = 3.0
REWRITE_MAX_TOKENS = 200
REWRITE_TEMPERATURE = 0.3


def make_anthropic_client() -> Any:
    """Anthropic API client that gives up quickly; credentials from ANTHROPIC_API_KEY or `ant auth login`."""
    import anthropic

    return anthropic.Anthropic(timeout=REWRITE_TIMEOUT_SECONDS, max_retries=0)


def anthropic_rewriter(client: Any, model: str = ANTHROPIC_DEFAULT_MODEL) -> Rewriter:
    """Stream the rewrite from the Anthropic API."""

    def rewrite(prompt: str) -> Iterator[str]:
        with client.messages.stream(
            model=model,
            max_tokens=REWRITE_MAX_TOKENS,
            temperature=REWRITE_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream

    return rewrite


def make_bedrock_client(profile: str | None, region: str) -> Any:
    import boto3
    from botocore.config import Config

    return boto3.Session(profile_name=profile, region_name=region).client(
        "bedrock-runtime",
        config=Config(
            connect_timeout=REWRITE_TIMEOUT_SECONDS,
            read_timeout=REWRITE_TIMEOUT_SECONDS,
            retries={"total_max_attempts": 1},
        ),
    )


def bedrock_rewriter(client: Any, model: str = BEDROCK_DEFAULT_MODEL) -> Rewriter:
    """Stream the rewrite from Amazon Bedrock's Converse API."""

    def rewrite(prompt: str) -> Iterator[str]:
        response = client.converse_stream(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": REWRITE_MAX_TOKENS, "temperature": REWRITE_TEMPERATURE},
        )
        for event in response["stream"]:
            fragment = event.get("contentBlockDelta", {}).get("delta", {}).get("text", "")
            if fragment:
                yield fragment

    return rewrite
