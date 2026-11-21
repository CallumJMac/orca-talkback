"""Run the orca-talkback daemon: uv run python -m orca_talkback"""

import argparse
import math
import os
import signal
import subprocess
from functools import partial
import sys
import urllib.request
from pathlib import Path

from orca_talkback.audio import CHUNK_SAMPLES, SAMPLE_RATE, BargeInDetector, make_aec_processor, make_speech_probability
from orca_talkback.replies import last_assistant_message
from orca_talkback.rules import FocusRules
from orca_talkback.server import VoiceDaemon, make_server
from orca_talkback.speaker import (
    Speaker,
    default_input_name,
    looks_virtual,
    open_sounddevice_input,
    open_sounddevice_output,
    resolve_input_device,
)
from orca_talkback.providers import (
    ANTHROPIC_DEFAULT_MODEL,
    BEDROCK_DEFAULT_MODEL,
    anthropic_rewriter,
    bedrock_rewriter,
    make_anthropic_client,
    make_bedrock_client,
)
from orca_talkback.spoken import Rewriter, spoken_sentences

KOKORO_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


def log(line: str) -> None:
    print(line, flush=True)


def raise_keyboard_interrupt(signal_number: int, frame: object) -> None:
    raise KeyboardInterrupt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="orca-talkback", description="Hear your Orca coding agents talk back.")
    parser.add_argument("--port", type=int, default=47823)
    parser.add_argument("--voice", default="bf_emma", help="Kokoro voice")
    parser.add_argument("--speed", type=float, default=1.1)
    parser.add_argument("--barge-in", action=argparse.BooleanOptionalAction, default=True,
                        help="stop speaking when you talk over it")
    parser.add_argument("--barge-in-ms", type=int, default=160,
                        help="continuous speech needed to interrupt")
    parser.add_argument("--vad-threshold", type=float, default=0.8)
    parser.add_argument("--input-device", default=None,
                        help="microphone for barge-in, matched by name (default: the system input)")
    parser.add_argument("--provider", choices=["anthropic", "bedrock", "none"], default="anthropic",
                        help="who rewrites replies into speech: the Anthropic API, Amazon Bedrock, or none (plain text)")
    parser.add_argument("--model", default=None,
                        help=f"rewrite model (default {ANTHROPIC_DEFAULT_MODEL}; on Bedrock {BEDROCK_DEFAULT_MODEL})")
    parser.add_argument("--aws-profile", default=os.environ.get("AWS_PROFILE"),
                        help="AWS profile for --provider bedrock (default: the standard AWS credential chain)")
    parser.add_argument("--aws-region", default=os.environ.get("AWS_REGION", "us-east-1"))
    return parser.parse_args(argv)


def kokoro_language(voice: str) -> str:
    return "en-gb" if voice.startswith("b") else "en-us"


def model_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "orca-talkback"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "orca-talkback"


def make_rewriter(args: argparse.Namespace) -> Rewriter | None:
    if args.provider == "none":
        log("[spoken] rewrite off; speaking cleaned-up reply text")
        return None
    try:
        if args.provider == "bedrock":
            model = args.model or BEDROCK_DEFAULT_MODEL
            profile = args.aws_profile or "default credential chain"
            log(f"[spoken] rewriting with {model} on Bedrock ({profile}, {args.aws_region})")
            return bedrock_rewriter(make_bedrock_client(args.aws_profile, args.aws_region), model)
        model = args.model or ANTHROPIC_DEFAULT_MODEL
        log(f"[spoken] rewriting with {model} on the Anthropic API")
        return anthropic_rewriter(make_anthropic_client(), model)
    except Exception as error:
        log(f"[spoken] {args.provider} unavailable ({error}); using plain text")
        return None


def choose_microphone(requested_name: str | None):
    """Pick the barge-in mic and say which one, since a virtual default input silently breaks barge-in."""
    if requested_name:
        index, device_name = resolve_input_device(requested_name)
        log(f"[audio] barge-in microphone: {device_name}")
        return partial(open_sounddevice_input, device=index)
    device_name = default_input_name()
    log(f"[audio] barge-in microphone: {device_name} (system default)")
    if looks_virtual(device_name):
        log(f"[audio] warning: {device_name!r} looks like a virtual device, so barge-in may never hear you; "
            "pass --input-device \"MacBook Pro Microphone\" (or your mic's name)")
    return open_sounddevice_input


def load_kokoro():
    try:
        prefix = subprocess.check_output(["brew", "--prefix", "espeak-ng"], text=True).strip()
        os.environ.setdefault("PHONEMIZER_ESPEAK_LIBRARY", f"{prefix}/lib/libespeak-ng.dylib")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    from kokoro_onnx import Kokoro

    cache_dir = model_cache_dir()
    model_file = cache_dir / "kokoro-v1.0.onnx"
    voices_file = cache_dir / "voices-v1.0.bin"
    if not model_file.exists() or not voices_file.exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        print(f"  Downloading Kokoro model (~340MB) to {cache_dir}...", flush=True)
        for target in (model_file, voices_file):
            partial = target.with_suffix(target.suffix + ".part")
            urllib.request.urlretrieve(f"{KOKORO_BASE_URL}/{target.name}", partial)
            partial.replace(target)  # only complete files ever get the real name
    return Kokoro(str(model_file), str(voices_file))


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    print("Loading Kokoro TTS...", flush=True)
    kokoro = load_kokoro()

    def synthesize(text: str):
        return kokoro.create(text, voice=args.voice, speed=args.speed, lang=kokoro_language(args.voice))

    detector_factory = None
    if args.barge_in:
        print("Loading Silero VAD and WebRTC AEC3...", flush=True)
        speech_probability, reset_vad = make_speech_probability()
        required_chunks = max(1, math.ceil(args.barge_in_ms / (CHUNK_SAMPLES / SAMPLE_RATE * 1000)))

        def new_detector() -> BargeInDetector:
            return BargeInDetector(
                make_aec_processor(), speech_probability, reset_vad,
                required_chunks=required_chunks, threshold=args.vad_threshold,
            )

        detector_factory = new_detector

    speaker = Speaker(
        synthesize, open_sounddevice_output,
        open_input=choose_microphone(args.input_device) if args.barge_in else None,
        make_detector=detector_factory, log=log,
    )
    rewrite = make_rewriter(args)
    daemon = VoiceDaemon(
        FocusRules(), speaker, last_assistant_message,
        lambda reply: spoken_sentences(reply, rewrite, log=log),
        log=log,
    )
    server = make_server(daemon, port=args.port, log=log)
    print(f"orca-talkback listening on http://127.0.0.1:{args.port} (Ctrl+C to quit)", flush=True)
    signal.signal(signal.SIGTERM, raise_keyboard_interrupt)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        speaker.stop("shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
