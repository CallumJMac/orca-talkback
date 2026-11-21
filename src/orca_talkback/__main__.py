"""Run the orca-talkback daemon: uv run python -m orca_talkback"""

import argparse
import math
import os
import signal
import sys
import threading
import time
import urllib.request
from functools import partial
from pathlib import Path

from orca_talkback.audio import (
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    BargeInDetector,
    make_aec_processor,
    make_speech_probability,
)
from orca_talkback.frontmost import orca_is_frontmost
from orca_talkback.replies import last_assistant_message
from orca_talkback.rules import FocusRules
from orca_talkback.server import VoiceDaemon, make_server
from orca_talkback.speaker import (
    Speaker,
    default_input_name,
    first_real_input,
    looks_virtual,
    open_sounddevice_input,
    open_sounddevice_output,
    resolve_input_device,
)
from orca_talkback.spoken import spoken_sentences

KOKORO_BASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


def log(line: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)


def raise_keyboard_interrupt(signal_number: int, frame: object) -> None:
    raise KeyboardInterrupt


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="orca-talkback", description="Hear your Orca coding agents talk back.",
        epilog="Run `orca-talkback setup` once first to install the Claude Code hook and the voice.",
    )
    parser.add_argument("--port", type=int, default=47823)
    parser.add_argument("--speak-when-hidden", action="store_true",
                        help="also speak when Orca is not the app in front (default: only while you are looking at Orca)")
    parser.add_argument("--verbose", action="store_true",
                        help="log every Orca event the plugin forwards, for troubleshooting")
    parser.add_argument("--voice", default="bf_emma", help="Kokoro voice")
    parser.add_argument("--speed", type=float, default=1.1)
    parser.add_argument("--barge-in", action=argparse.BooleanOptionalAction, default=True,
                        help="stop speaking when you talk over it")
    parser.add_argument("--barge-in-ms", type=int, default=160,
                        help="continuous speech needed to interrupt")
    parser.add_argument("--vad-threshold", type=float, default=0.8)
    parser.add_argument("--input-device", default=None,
                        help="microphone for barge-in, matched by name (default: the system input)")
    return parser.parse_args(argv)


def kokoro_language(voice: str) -> str:
    return "en-gb" if voice.startswith("b") else "en-us"


def model_cache_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "orca-talkback"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "orca-talkback"


FRONT_APP_CHECK_SECONDS = 0.5


def start_front_app_watch(daemon: VoiceDaemon) -> None:
    """Stop playback soon after you switch away from Orca."""
    def watch() -> None:
        while True:
            time.sleep(FRONT_APP_CHECK_SECONDS)
            try:
                daemon.enforce_front_app()
            except Exception as error:  # never let the watcher die
                log(f"[speak] front app check failed: {error!r}")

    threading.Thread(target=watch, name="front-app-watch", daemon=True).start()


def choose_microphone(requested_name: str | None):
    """Pick the barge-in mic and say which one, since a virtual default input silently breaks barge-in."""
    if requested_name:
        index, device_name = resolve_input_device(requested_name)
        log(f"[audio] barge-in microphone: {device_name}")
        return partial(open_sounddevice_input, device=index)
    device_name = default_input_name()
    if not looks_virtual(device_name):
        log(f"[audio] barge-in microphone: {device_name} (system default)")
        return open_sounddevice_input
    real_input = first_real_input()
    if real_input is None:
        log(f"[audio] warning: the only microphones are virtual ({device_name!r}), so barge-in may never hear you")
        return open_sounddevice_input
    index, real_name = real_input
    log(f"[audio] barge-in microphone: {real_name} (the system default, {device_name!r}, is a virtual device)")
    return partial(open_sounddevice_input, device=index)


def load_kokoro():
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
    arguments = sys.argv[1:] if argv is None else argv
    if arguments[:1] == ["setup"]:
        from orca_talkback.setup_command import run_setup

        run_setup()
        return
    args = parse_args(arguments)
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
    daemon = VoiceDaemon(
        FocusRules(), speaker, last_assistant_message,
        spoken_sentences,
        log=log, verbose=args.verbose,
        front_app_check=None if args.speak_when_hidden else orca_is_frontmost,
    )
    if not args.speak_when_hidden:
        log("[speak] only while Orca is the app in front (--speak-when-hidden to change)")
        start_front_app_watch(daemon)
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
