import threading
import time

import numpy as np

from orca_talkback.speaker import FADE_SECONDS, Speaker

SAMPLE_RATE = 24000


class FakeOutput:
    def __init__(self, write_delay: float = 0.0):
        self.write_delay = write_delay
        self.blocks: list[np.ndarray] = []
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def write(self, block):
        time.sleep(self.write_delay)
        self.blocks.append(block.copy())

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def total_samples(self) -> int:
        return sum(len(block) for block in self.blocks)


class FakeDetector:
    def __init__(self, speaking_after_checks: int | None = None):
        self.speaking_after_checks = speaking_after_checks
        self.checks = 0
        self.sentences = 0

    def push_mic(self, chunk):
        pass

    def drain_mic(self):
        pass

    def begin_sentence(self, samples, sample_rate):
        self.sentences += 1

    def user_is_speaking(self):
        self.checks += 1
        return self.speaking_after_checks is not None and self.checks > self.speaking_after_checks

    def gap_has_speech(self):
        return False


class FakeInput:
    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        self.closed = False

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        self.closed = True


def make_speaker(seconds_per_item: float = 0.1, write_delay: float = 0.0, detector=None, output_factory=None):
    synthesized: list[str] = []
    outputs: list[FakeOutput] = []
    inputs: list[FakeInput] = []

    def open_input(on_chunk):
        stream = FakeInput(on_chunk)
        inputs.append(stream)
        return stream
    logs: list[str] = []

    def synthesize(text):
        synthesized.append(text)
        return np.ones(int(SAMPLE_RATE * seconds_per_item), dtype=np.float32), SAMPLE_RATE

    def open_output(sample_rate):
        output = (output_factory or FakeOutput)(write_delay)
        outputs.append(output)
        return output

    speaker = Speaker(
        synthesize, open_output,
        open_input=open_input if detector else None,
        make_detector=(lambda: detector) if detector else None,
        log=logs.append,
    )
    speaker.test_inputs = inputs
    return speaker, synthesized, outputs, logs


def test_first_sentence_alone_then_pairs():
    speaker, synthesized, outputs, _ = make_speaker()
    speaker.speak(["First sentence here.", "Second sentence here.", "Third sentence here."])
    speaker.wait(5)
    assert synthesized == ["First sentence here.", "Second sentence here. Third sentence here."]
    assert outputs[0].total_samples() == 2 * int(SAMPLE_RATE * 0.1)
    assert outputs[0].closed


def test_stop_cuts_playback_with_a_fade():
    speaker, _, outputs, logs = make_speaker(seconds_per_item=5, write_delay=0.01)
    speaker.speak(["A long sentence to play."])
    time.sleep(0.1)
    speaker.stop("focus changed")
    assert not speaker.is_playing
    assert outputs[0].total_samples() < SAMPLE_RATE * 5
    fade = outputs[0].blocks[-1]
    assert len(fade) == int(FADE_SECONDS * SAMPLE_RATE)
    assert fade[0, 0] > fade[-1, 0]
    assert "[speaker] stop: focus changed" in logs


def test_new_reply_replaces_current_one():
    speaker, synthesized, outputs, _ = make_speaker(seconds_per_item=5, write_delay=0.01)
    speaker.speak(["The first reply is long."])
    time.sleep(0.1)
    speaker.speak(["The second reply is long."])
    time.sleep(0.1)
    assert len(outputs) == 2
    assert outputs[0].closed
    assert synthesized[-1] == "The second reply is long."
    speaker.stop("test over")


def test_barge_in_stops_playback():
    detector = FakeDetector(speaking_after_checks=3)
    speaker, _, outputs, logs = make_speaker(seconds_per_item=5, detector=detector)
    speaker.speak(["A long sentence to interrupt."])
    speaker.wait(5)
    assert not speaker.is_playing
    assert outputs[0].total_samples() < SAMPLE_RATE * 5
    assert "[speaker] stop: you started talking" in logs


def test_synthesis_error_is_logged_and_playback_ends():
    logs: list[str] = []

    def broken_synthesize(text):
        raise RuntimeError("kokoro failed")

    speaker = Speaker(broken_synthesize, lambda sample_rate: FakeOutput(), log=logs.append)
    speaker.speak(["Anything at all here."])
    speaker.wait(5)
    assert not speaker.is_playing
    assert any("synthesis failed" in line for line in logs)


def test_stop_from_another_thread_while_idle_is_safe():
    speaker, _, _, _ = make_speaker()
    thread = threading.Thread(target=speaker.stop, args=("nothing playing",))
    thread.start()
    thread.join(1)
    assert not speaker.is_playing


def test_input_stream_is_closed_after_a_normal_reply():
    speaker, _, _, _ = make_speaker(detector=FakeDetector())
    speaker.speak(["A sentence to speak aloud."])
    speaker.wait(5)
    assert len(speaker.test_inputs) == 1
    assert speaker.test_inputs[0].closed


def test_input_stream_is_closed_when_output_cleanup_fails():
    class FailingStopOutput(FakeOutput):
        def stop(self):
            raise RuntimeError("device vanished")

    speaker, _, _, logs = make_speaker(detector=FakeDetector(), output_factory=FailingStopOutput)
    speaker.speak(["A sentence to speak aloud."])
    speaker.wait(5)
    assert speaker.test_inputs[0].closed
    assert any("stream cleanup failed" in line for line in logs)
