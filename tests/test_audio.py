import numpy as np

from orca_talkback.audio import (
    CHUNK_SAMPLES,
    BargeInDetector,
    reference_segment,
    resample_to_16k,
)

SPEECH = 1.0
QUIET = 0.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_detector(required_chunks: int = 3):
    clock = FakeClock()
    references: list[np.ndarray] = []

    def aec_process(mic, reference):
        references.append(reference.copy())
        return mic

    def speech_probability(chunk):
        return float(chunk[0])

    detector = BargeInDetector(
        aec_process, speech_probability, reset_vad=lambda: None,
        required_chunks=required_chunks, threshold=0.8, clock=clock,
    )
    return detector, clock, references


def mic_chunk(level: float) -> np.ndarray:
    return np.full(CHUNK_SAMPLES, level, dtype=np.float32)


def test_reference_segment_pads_past_the_end():
    reference = np.arange(10, dtype=np.float32)
    assert reference_segment(reference, 8, 4).tolist() == [8, 9, 0, 0]
    assert reference_segment(reference, 20, 2).tolist() == [0, 0]


def test_resample_to_16k_halves_32k_audio():
    assert len(resample_to_16k(np.ones(3200, dtype=np.float32), 32000)) == 1600


def test_speech_during_inhibit_window_is_ignored():
    detector, clock, _ = make_detector()
    detector.begin_sentence(np.zeros(16000, dtype=np.float32), 16000)
    for _ in range(5):
        detector.push_mic(mic_chunk(SPEECH))
    clock.now += 0.2
    assert detector.user_is_speaking() is False


def test_consecutive_speech_after_inhibit_interrupts():
    detector, clock, _ = make_detector(required_chunks=3)
    detector.begin_sentence(np.zeros(16000, dtype=np.float32), 16000)
    clock.now += 0.6
    for _ in range(3):
        detector.push_mic(mic_chunk(SPEECH))
    assert detector.user_is_speaking() is True


def test_interrupted_speech_resets_the_count():
    detector, clock, _ = make_detector(required_chunks=3)
    detector.begin_sentence(np.zeros(16000, dtype=np.float32), 16000)
    clock.now += 0.6
    for level in (SPEECH, SPEECH, QUIET, SPEECH, SPEECH):
        detector.push_mic(mic_chunk(level))
    assert detector.user_is_speaking() is False


def test_reference_stays_aligned_with_mic_position():
    detector, clock, references = make_detector()
    sentence = np.arange(CHUNK_SAMPLES * 3, dtype=np.float32)
    detector.begin_sentence(sentence, 16000)
    clock.now += 0.6
    detector.push_mic(mic_chunk(QUIET))
    detector.push_mic(mic_chunk(QUIET))
    detector.user_is_speaking()
    assert references[0][0] == 0
    assert references[1][0] == CHUNK_SAMPLES


def test_short_mic_chunks_are_skipped():
    detector, clock, references = make_detector()
    detector.begin_sentence(np.zeros(16000, dtype=np.float32), 16000)
    clock.now += 0.6
    detector.push_mic(np.zeros(100, dtype=np.float32))
    detector.user_is_speaking()
    assert references == []


def test_add_reference_extends_alignment_without_resetting_inhibit():
    detector, clock, references = make_detector()
    detector.begin_sentence(np.arange(CHUNK_SAMPLES, dtype=np.float32), 16000)
    clock.now += 0.6
    detector.add_reference(np.full(CHUNK_SAMPLES, 7.0, dtype=np.float32), 16000)
    detector.push_mic(mic_chunk(QUIET))
    detector.push_mic(mic_chunk(QUIET))
    detector.user_is_speaking()
    assert len(references) == 2  # still past the inhibit window: add_reference did not restart it
    assert references[1][0] == 7.0  # the second mic chunk lines up with the added audio


def test_drain_mic_discards_queued_chunks():
    detector, clock, references = make_detector()
    detector.push_mic(mic_chunk(SPEECH))
    detector.drain_mic()
    detector.begin_sentence(np.zeros(16000, dtype=np.float32), 16000)
    clock.now += 0.6
    assert detector.user_is_speaking() is False
    assert references == []
