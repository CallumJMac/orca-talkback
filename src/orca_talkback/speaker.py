"""Plays spoken sentences through Kokoro with barge-in, one reply at a time."""

import queue
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import numpy as np

from orca_talkback.audio import CHUNK_SAMPLES, SAMPLE_RATE

WRITE_BLOCK_SAMPLES = 2048  # about 85 ms at 24 kHz between barge-in checks
FADE_SECONDS = 0.03
GAP_FILL_SECONDS = 0.02  # silence written while the next sentence is synthesised

Synthesize = Callable[[str], tuple[np.ndarray, int]]


class PlaybackStats:
    """Per-reply timing of the write loop, logged after each reply to diagnose choppy audio."""

    def __init__(self) -> None:
        self.blocks = 0
        self.underflows = 0
        self.slowest_check = 0.0
        self.slowest_write = 0.0

    def record(self, check_seconds: float, write_seconds: float, underflowed: bool) -> None:
        self.blocks += 1
        self.underflows += int(underflowed)
        self.slowest_check = max(self.slowest_check, check_seconds)
        self.slowest_write = max(self.slowest_write, write_seconds)

    def summary(self) -> str:
        return (f"played {self.blocks} blocks, {self.underflows} underruns, slowest barge-in check "
                f"{self.slowest_check * 1000:.1f} ms, slowest write {self.slowest_write * 1000:.1f} ms")


class Speaker:
    """Speaks one reply at a time. A new reply or stop() cuts the current one with a short fade.

    open_output(sample_rate) and open_input(on_chunk) return stream objects with
    start(), stop() and close(); output streams also take write(frames).
    make_detector() returns a BargeInDetector for each reply.
    refresh_devices() rereads the audio device list. It is only called when a stream fails to
    open (a headset reconnected since startup), because refreshing after every reply leaves
    macOS audio resources behind and playback gradually starts to underrun.
    """

    def __init__(
        self,
        synthesize: Synthesize,
        open_output: Callable[[int], Any],
        open_input: Callable[[Callable[[np.ndarray], None]], Any] | None = None,
        make_detector: Callable[[], Any] | None = None,
        refresh_devices: Callable[[], None] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self._synthesize = synthesize
        self._open_output = open_output
        self._open_input = open_input
        self._make_detector = make_detector
        self._refresh_devices = refresh_devices
        self._log = log
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._playback_stats = PlaybackStats()

    @property
    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def speak(self, sentences: Iterable[str]) -> None:
        with self._lock:
            self._stop_locked("replaced by a newer reply")
            self._cancel = threading.Event()
            self._thread = threading.Thread(
                target=self._play, args=(iter(sentences), self._cancel), daemon=True,
            )
            self._thread.start()

    def stop(self, reason: str) -> None:
        with self._lock:
            self._stop_locked(reason)

    def wait(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _refresh_after(self, what: str, error: Exception) -> bool:
        """Reread the audio devices after `what` failed to open; returns whether a retry makes sense."""
        if self._refresh_devices is None:
            return False
        self._log(f"[speaker] could not open the {what} ({error!r}); rereading audio devices and retrying")
        try:
            self._refresh_devices()
        except Exception as refresh_error:
            self._log(f"[speaker] device refresh failed: {refresh_error!r}")
            return False
        return True

    def _stop_locked(self, reason: str) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._log(f"[speaker] stop: {reason}")
            self._cancel.set()
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                self._log("[speaker] previous playback did not stop within 2 s")

    def _play(self, sentences: Iterator[str], cancel: threading.Event) -> None:
        synthesized: queue.Queue = queue.Queue(maxsize=1)
        threading.Thread(
            target=self._synthesize_ahead, args=(sentences, synthesized, cancel), daemon=True,
        ).start()
        detector, input_stream = self._start_barge_in(allow_refresh=True)
        self._playback_stats = PlaybackStats()
        output_stream = None
        output_rate = 0
        try:
            while not cancel.is_set():
                item = self._next_item(synthesized, cancel, output_stream, output_rate, detector)
                if item is None:
                    break
                samples, sample_rate = item
                if output_stream is None:
                    try:
                        output_stream = self._start_output(sample_rate)
                    except Exception as error:
                        if not self._refresh_after("speaker", error):
                            raise
                        # The refresh closed the barge-in stream too, so reopen it after the speaker.
                        self._close_stream(input_stream)
                        input_stream = None
                        output_stream = self._start_output(sample_rate)
                        detector, input_stream = self._start_barge_in(allow_refresh=False)
                    output_rate = sample_rate
                    if detector is not None:
                        detector.drain_mic()
                if detector is not None:
                    detector.begin_sentence(samples, sample_rate)
                if not self._write(output_stream, samples, sample_rate, detector, cancel):
                    break
        except Exception as error:
            self._log(f"[speaker] playback failed: {error!r}")
        finally:
            if self._playback_stats.blocks:
                self._log(f"[speaker] {self._playback_stats.summary()}")
            cancel.set()  # also stops the synthesis thread
            for stream in (input_stream, output_stream):
                self._close_stream(stream)

    def _close_stream(self, stream: Any) -> None:
        if stream is None:
            return
        try:
            try:
                stream.stop()
            finally:
                stream.close()
        except Exception as error:
            self._log(f"[speaker] stream cleanup failed: {error!r}")

    def _start_output(self, sample_rate: int) -> Any:
        output_stream = self._open_output(sample_rate)
        try:
            output_stream.start()
        except Exception:
            self._close_stream(output_stream)
            raise
        return output_stream

    def _start_barge_in(self, allow_refresh: bool) -> tuple[Any, Any]:
        if self._make_detector is None or self._open_input is None:
            return None, None
        try:
            detector = self._make_detector()
            input_stream = self._open_input(detector.push_mic)
            try:
                input_stream.start()
            except Exception:
                try:
                    input_stream.close()
                except Exception as close_error:
                    self._log(f"[speaker] stream cleanup failed: {close_error!r}")
                raise
            return detector, input_stream
        except Exception as error:
            if allow_refresh and self._refresh_after("microphone", error):
                return self._start_barge_in(allow_refresh=False)
            self._log(f"[speaker] barge-in unavailable: {error!r}")
            return None, None

    def _synthesize_ahead(self, sentences: Iterator[str], synthesized: queue.Queue, cancel: threading.Event) -> None:
        """Synthesise the first sentence alone for a fast start, then pairs for natural prosody."""
        def put(item: Any) -> None:
            while not cancel.is_set():
                try:
                    synthesized.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue

        try:
            group: list[str] = []
            group_size = 1
            for sentence in sentences:
                if cancel.is_set():
                    return
                group.append(sentence)
                if len(group) == group_size:
                    put(self._synthesize(" ".join(group)))
                    group = []
                    group_size = 2
            if group and not cancel.is_set():
                put(self._synthesize(" ".join(group)))
        except Exception as error:
            self._log(f"[speaker] synthesis failed: {error!r}")
        finally:
            put(None)

    def _next_item(self, synthesized: queue.Queue, cancel: threading.Event, output_stream: Any,
                   output_rate: int, detector: Any) -> Any:
        """Wait for the next synthesised item.

        Once audio has started, the wait is filled with real silence written to the
        speaker and to the echo reference alike, so the reference never drifts from
        what was actually played, and barge-in keeps listening between sentences.
        """
        while not cancel.is_set():
            if output_stream is None:
                try:
                    return synthesized.get(timeout=0.1)
                except queue.Empty:
                    continue
            try:
                return synthesized.get_nowait()
            except queue.Empty:
                silence = np.zeros(int(output_rate * GAP_FILL_SECONDS), dtype=np.float32)
                if detector is not None:
                    detector.add_reference(silence, output_rate)
                if not self._write(output_stream, silence, output_rate, detector, cancel):
                    return None
        return None

    def _write(self, output_stream: Any, samples: np.ndarray, sample_rate: int, detector: Any, cancel: threading.Event) -> bool:
        """Write one synthesised item; returns False if playback was cut short."""
        frames = samples.reshape(-1, 1).astype(np.float32)
        for start in range(0, len(frames), WRITE_BLOCK_SAMPLES):
            checked_at = time.perf_counter()
            if detector is not None and detector.user_is_speaking():
                self._barge_in(cancel)
            if cancel.is_set():
                self._fade_out(output_stream, frames[start:], sample_rate)
                return False
            written_at = time.perf_counter()
            underflowed = output_stream.write(frames[start:start + WRITE_BLOCK_SAMPLES])
            self._playback_stats.record(written_at - checked_at, time.perf_counter() - written_at, underflowed is True)
        return True

    @staticmethod
    def _fade_out(output_stream: Any, remaining: np.ndarray, sample_rate: int) -> None:
        fade = remaining[: int(FADE_SECONDS * sample_rate)]
        if len(fade):
            ramp = np.linspace(1.0, 0.0, len(fade), dtype=np.float32).reshape(-1, 1)
            output_stream.write(fade * ramp)

    def _barge_in(self, cancel: threading.Event) -> None:
        self._log("[speaker] stop: you started talking")
        cancel.set()


def refresh_sounddevice_devices() -> None:
    """Reread the device list. PortAudio reads it once at startup, so a reconnected headset otherwise fails to open."""
    import sounddevice

    sounddevice._terminate()
    sounddevice._initialize()


def open_sounddevice_output(sample_rate: int) -> Any:
    import sounddevice

    return sounddevice.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")


VIRTUAL_INPUT_MARKERS = ("blackhole", "zoom", "teams", "loopback", "soundflower", "aggregate", "virtual")


def looks_virtual(device_name: str) -> bool:
    """True for loopback and meeting-app devices, which never carry your voice."""
    lowered = device_name.lower()
    return any(marker in lowered for marker in VIRTUAL_INPUT_MARKERS)


def resolve_input_device(name: str, query_devices: Callable[[], Any] | None = None) -> tuple[int, str]:
    """Find the first input device whose name contains `name` (case-insensitive)."""
    if query_devices is None:
        import sounddevice

        query_devices = sounddevice.query_devices
    inputs = [(index, device["name"]) for index, device in enumerate(query_devices()) if device["max_input_channels"] > 0]
    for index, device_name in inputs:
        if name.lower() in device_name.lower():
            return index, device_name
    available = ", ".join(device_name for _, device_name in inputs)
    raise ValueError(f"no input device matching {name!r}; available inputs: {available}")


def first_real_input(query_devices: Callable[[], Any] | None = None) -> tuple[int, str] | None:
    """The first input device that is not a loopback or meeting-app device, if any."""
    if query_devices is None:
        import sounddevice

        query_devices = sounddevice.query_devices
    for index, device in enumerate(query_devices()):
        if device["max_input_channels"] > 0 and not looks_virtual(device["name"]):
            return index, device["name"]
    return None


def default_input_name() -> str:
    import sounddevice

    return sounddevice.query_devices(kind="input")["name"]


def open_sounddevice_input(on_chunk: Callable[[np.ndarray], None], device: int | None = None) -> Any:
    import sounddevice

    def callback(indata, frames, time_info, status):
        on_chunk(indata[:, 0].copy())

    return sounddevice.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        blocksize=CHUNK_SAMPLES, callback=callback, device=device,
    )


def open_named_input(
    on_chunk: Callable[[np.ndarray], None],
    name: str,
    query_devices: Callable[[], Any] | None = None,
    open_input: Callable[..., Any] = open_sounddevice_input,
) -> Any:
    """Open the input matching `name`, looked up again each time because device numbers change after a refresh."""
    index, _ = resolve_input_device(name, query_devices)
    return open_input(on_chunk, device=index)
