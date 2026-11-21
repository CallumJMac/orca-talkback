"""Echo cancellation and barge-in detection, ported from Trelis Research voice-loop (voice_loop_mac.py, Apache-2.0).

Timing invariants:
- one AEC processor per reply, so the learned room response carries across sentences
- one continuous reference buffer indexed by mic position, so mic and reference stay aligned
- the reference is exactly the audio written to the speaker, sentences and the silence
  between them alike; it is never padded from mic timing, because audio still in the
  output buffer at a sentence boundary would then be mistaken for silence and every
  later sentence would play ahead of its reference
- a 0.5 s inhibit window at the start of every sentence
"""

import queue
import time
from collections.abc import Callable, Iterator

import numpy as np

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 512  # 32 ms at 16 kHz, required by Silero VAD
AEC_FRAME_SAMPLES = 160  # 10 ms at 16 kHz
SENTENCE_INHIBIT_SECONDS = 0.5

AecProcess = Callable[[np.ndarray, np.ndarray], np.ndarray]
SpeechProbability = Callable[[np.ndarray], float]


def make_aec_processor() -> AecProcess:
    from livekit.rtc import AudioFrame
    from livekit.rtc.apm import AudioProcessingModule

    processing_module = AudioProcessingModule(echo_cancellation=True, noise_suppression=True)

    def to_int16(samples: np.ndarray) -> np.ndarray:
        scaled = (samples * 32767).clip(-32768, 32767).astype(np.int16)
        if len(scaled) < AEC_FRAME_SAMPLES:
            return np.pad(scaled, (0, AEC_FRAME_SAMPLES - len(scaled)))
        return scaled

    def to_frame(samples_int16: np.ndarray) -> AudioFrame:
        return AudioFrame(
            samples_int16.tobytes(), sample_rate=SAMPLE_RATE,
            num_channels=1, samples_per_channel=AEC_FRAME_SAMPLES,
        )

    def process(mic: np.ndarray, reference: np.ndarray) -> np.ndarray:
        cleaned = np.zeros_like(mic)
        for start in range(0, len(mic), AEC_FRAME_SAMPLES):
            end = start + AEC_FRAME_SAMPLES
            mic_frame = to_frame(to_int16(mic[start:end]))
            processing_module.process_reverse_stream(to_frame(to_int16(reference[start:end])))
            processing_module.process_stream(mic_frame)
            cleaned_frame = np.frombuffer(bytes(mic_frame.data), dtype=np.int16).astype(np.float32) / 32767
            cleaned[start:end] = cleaned_frame[: len(mic[start:end])]
        return cleaned

    return process


def make_speech_probability() -> tuple[SpeechProbability, Callable[[], None]]:
    import torch
    from silero_vad import load_silero_vad

    vad_model = load_silero_vad(onnx=True)

    def speech_probability(chunk: np.ndarray) -> float:
        result = vad_model(torch.from_numpy(chunk.astype(np.float32)), SAMPLE_RATE)
        return float(result.item() if hasattr(result, "item") else result)

    return speech_probability, vad_model.reset_states


def resample_to_16k(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == SAMPLE_RATE:
        return samples.astype(np.float32)
    positions = np.arange(0, len(samples), sample_rate / SAMPLE_RATE)
    return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def reference_segment(reference: np.ndarray, position: int, length: int) -> np.ndarray:
    if position >= len(reference):
        return np.zeros(length, dtype=np.float32)
    segment = reference[position:position + length]
    if len(segment) < length:
        return np.concatenate([segment, np.zeros(length - len(segment), dtype=np.float32)])
    return segment


class BargeInDetector:
    """Decides, from echo-cancelled mic audio, whether the user is talking over playback.

    The input stream callback feeds chunks with push_mic() from any thread. The
    playback thread calls begin_sentence() as each sentence starts playing,
    add_reference() for any other audio it writes (such as silence while the next
    sentence is synthesised), and user_is_speaking() between writes.
    """

    def __init__(
        self,
        aec_process: AecProcess,
        speech_probability: SpeechProbability,
        reset_vad: Callable[[], None],
        required_chunks: int,
        threshold: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._aec_process = aec_process
        self._speech_probability = speech_probability
        self._reset_vad = reset_vad
        self._required_chunks = required_chunks
        self._threshold = threshold
        self._clock = clock
        self._mic_chunks: queue.Queue[np.ndarray] = queue.Queue()
        self._reference_parts: list[np.ndarray] = []
        self._reference = np.zeros(0, dtype=np.float32)
        self._reference_part_count = 0
        self._mic_position = 0
        self._consecutive_speech = 0
        self._sentence_started_at: float | None = None

    def push_mic(self, chunk: np.ndarray) -> None:
        self._mic_chunks.put(chunk)

    def drain_mic(self) -> None:
        for _ in self._pending_mic_chunks(include_short=True):
            pass

    def begin_sentence(self, samples: np.ndarray, sample_rate: int) -> None:
        self.add_reference(samples, sample_rate)
        self._reset_vad()
        self._sentence_started_at = self._clock()
        self._consecutive_speech = 0

    def user_is_speaking(self) -> bool:
        if self._sentence_started_at is None:
            return False
        if self._clock() - self._sentence_started_at < SENTENCE_INHIBIT_SECONDS:
            return False
        reference = self._reference_buffer()
        for mic_chunk in self._pending_mic_chunks():
            segment = reference_segment(reference, self._mic_position, len(mic_chunk))
            self._mic_position += len(mic_chunk)
            if self._is_speech(self._aec_process(mic_chunk, segment)):
                return True
        return False

    def add_reference(self, samples: np.ndarray, sample_rate: int) -> None:
        """Record audio just written to the speaker, so the echo canceller knows to expect it."""
        self._reference_parts.append(resample_to_16k(samples, sample_rate))

    def _pending_mic_chunks(self, include_short: bool = False) -> Iterator[np.ndarray]:
        while True:
            try:
                mic_chunk = self._mic_chunks.get_nowait()
            except queue.Empty:
                return
            if include_short or len(mic_chunk) >= CHUNK_SAMPLES:
                yield mic_chunk

    def _reference_buffer(self) -> np.ndarray:
        if len(self._reference_parts) != self._reference_part_count:
            self._reference = np.concatenate(self._reference_parts)
            self._reference_part_count = len(self._reference_parts)
        return self._reference

    def _is_speech(self, cleaned: np.ndarray) -> bool:
        if self._speech_probability(cleaned.astype(np.float32)) > self._threshold:
            self._consecutive_speech += 1
            return self._consecutive_speech >= self._required_chunks
        self._consecutive_speech = 0
        return False
