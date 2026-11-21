"""Plays spoken sentences through Kokoro with barge-in, one reply at a time."""

import queue
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any

import numpy as np

from orca_talkback.audio import CHUNK_SAMPLES, SAMPLE_RATE

WRITE_BLOCK_SAMPLES = 2048  # about 85 ms at 24 kHz between barge-in checks
FADE_SECONDS = 0.03

Synthesize = Callable[[str], tuple[np.ndarray, int]]


class Speaker:
    """Speaks one reply at a time. A new reply or stop() cuts the current one with a short fade.

    open_output(sample_rate) and open_input(on_chunk) return stream objects with
    start(), stop() and close(); output streams also take write(frames).
    make_detector() returns a BargeInDetector for each reply.
    """

    def __init__(
        self,
        synthesize: Synthesize,
        open_output: Callable[[int], Any],
        open_input: Callable[[Callable[[np.ndarray], None]], Any] | None = None,
        make_detector: Callable[[], Any] | None = None,
        log: Callable[[str], None] = print,
    ) -> None:
        self._synthesize = synthesize
        self._open_output = open_output
        self._open_input = open_input
        self._make_detector = make_detector
        self._log = log
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

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
        detector, input_stream = self._start_barge_in()
        output_stream = None
        first_item = True
        try:
            while not cancel.is_set():
                item = self._next_item(synthesized, cancel)
                if item is None:
                    break
                samples, sample_rate = item
                if detector is not None and not first_item and detector.gap_has_speech():
                    self._barge_in(cancel)
                    break
                if output_stream is None:
                    output_stream = self._open_output(sample_rate)
                    output_stream.start()
                    if detector is not None:
                        detector.drain_mic()
                if detector is not None:
                    detector.begin_sentence(samples, sample_rate)
                first_item = False
                if not self._write(output_stream, samples, sample_rate, detector, cancel):
                    break
        except Exception as error:
            self._log(f"[speaker] playback failed: {error!r}")
        finally:
            cancel.set()  # also stops the synthesis thread
            for stream in (input_stream, output_stream):
                if stream is None:
                    continue
                try:
                    try:
                        stream.stop()
                    finally:
                        stream.close()
                except Exception as error:
                    self._log(f"[speaker] stream cleanup failed: {error!r}")

    def _start_barge_in(self) -> tuple[Any, Any]:
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

    @staticmethod
    def _next_item(synthesized: queue.Queue, cancel: threading.Event) -> Any:
        while not cancel.is_set():
            try:
                return synthesized.get(timeout=0.1)
            except queue.Empty:
                continue
        return None

    def _write(self, output_stream: Any, samples: np.ndarray, sample_rate: int, detector: Any, cancel: threading.Event) -> bool:
        """Write one synthesised item; returns False if playback was cut short."""
        frames = samples.reshape(-1, 1).astype(np.float32)
        for start in range(0, len(frames), WRITE_BLOCK_SAMPLES):
            if detector is not None and detector.user_is_speaking():
                self._barge_in(cancel)
            if cancel.is_set():
                self._fade_out(output_stream, frames[start:], sample_rate)
                return False
            output_stream.write(frames[start:start + WRITE_BLOCK_SAMPLES])
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


def open_sounddevice_output(sample_rate: int) -> Any:
    import sounddevice

    return sounddevice.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")


def open_sounddevice_input(on_chunk: Callable[[np.ndarray], None]) -> Any:
    import sounddevice

    def callback(indata, frames, time_info, status):
        on_chunk(indata[:, 0].copy())

    return sounddevice.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32",
        blocksize=CHUNK_SAMPLES, callback=callback,
    )
