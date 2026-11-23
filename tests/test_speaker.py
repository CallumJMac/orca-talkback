import threading
import time

import numpy as np
import pytest

from orca_talkback.speaker import (
    FADE_SECONDS,
    Speaker,
    first_real_input,
    looks_virtual,
    resolve_input_device,
)

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

    def sound_samples(self) -> int:
        return sum(int(np.count_nonzero(block)) for block in self.blocks)


class FakeDetector:
    def __init__(self, speaking_after_checks: int | None = None):
        self.speaking_after_checks = speaking_after_checks
        self.checks = 0
        self.sentences = 0
        self.reference_samples = 0

    def push_mic(self, chunk):
        pass

    def drain_mic(self):
        pass

    def begin_sentence(self, samples, sample_rate):
        self.sentences += 1
        self.reference_samples += len(samples)

    def add_reference(self, samples, sample_rate):
        self.reference_samples += len(samples)

    def user_is_speaking(self):
        self.checks += 1
        return self.speaking_after_checks is not None and self.checks > self.speaking_after_checks


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
    assert outputs[0].sound_samples() == 2 * int(SAMPLE_RATE * 0.1)  # silence may fill the wait between items
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


FAKE_DEVICES = [
    {"name": "BlackHole 16ch", "max_input_channels": 16},
    {"name": "MacBook Pro Speakers", "max_input_channels": 0},
    {"name": "MacBook Pro Microphone", "max_input_channels": 1},
    {"name": "ZoomAudioDevice", "max_input_channels": 2},
]


def test_resolve_input_device_matches_name_case_insensitively():
    assert resolve_input_device("macbook pro mic", lambda: FAKE_DEVICES) == (2, "MacBook Pro Microphone")


def test_resolve_input_device_ignores_output_only_devices():
    with pytest.raises(ValueError, match="MacBook Pro Microphone"):
        resolve_input_device("Speakers", lambda: FAKE_DEVICES)


def test_virtual_input_names_are_flagged():
    assert looks_virtual("ZoomAudioDevice")
    assert looks_virtual("Blackhole Input New")
    assert not looks_virtual("MacBook Pro Microphone")


def test_echo_reference_matches_everything_written_even_while_waiting_for_synthesis():
    """The echo canceller can only remove what it was told was played, sample for sample."""
    detector = FakeDetector()
    outputs: list[FakeOutput] = []
    calls = []

    def slow_second_synthesize(text):
        calls.append(text)
        if len(calls) == 2:
            time.sleep(0.3)  # the speaker has to fill this wait with real silence
        return np.ones(int(SAMPLE_RATE * 0.1), dtype=np.float32), SAMPLE_RATE

    def open_output(sample_rate):
        outputs.append(FakeOutput())
        return outputs[-1]

    speaker = Speaker(slow_second_synthesize, open_output, open_input=FakeInput,
                      make_detector=lambda: detector, log=lambda line: None)
    speaker.speak(["The first sentence is here.", "The second sentence is slow."])
    speaker.wait(5)
    written = outputs[0].total_samples()
    assert written > 2 * int(SAMPLE_RATE * 0.1)  # silence was written during the wait
    assert detector.reference_samples == written


def test_first_real_microphone_is_chosen_over_virtual_devices():
    assert first_real_input(lambda: FAKE_DEVICES) == (2, "MacBook Pro Microphone")


def test_no_real_microphone_returns_none():
    virtual_only = [{"name": "BlackHole 16ch", "max_input_channels": 16}, {"name": "ZoomAudioDevice", "max_input_channels": 2}]
    assert first_real_input(lambda: virtual_only) is None


def test_audio_devices_are_refreshed_before_each_reply_so_reconnected_headsets_work():
    events: list[str] = []

    def open_output(sample_rate):
        events.append("open output")
        return FakeOutput()

    def synthesize(text):
        return np.ones(SAMPLE_RATE // 20, dtype=np.float32), SAMPLE_RATE

    speaker = Speaker(synthesize, open_output, refresh_devices=lambda: events.append("refresh"), log=lambda line: None)
    speaker.speak(["One."])
    speaker.wait(2)
    speaker.speak(["Two."])
    speaker.wait(2)
    assert events == ["refresh", "open output", "refresh", "open output"]


def test_a_failed_device_refresh_is_logged_and_the_reply_still_plays():
    outputs: list[FakeOutput] = []
    logs: list[str] = []

    def open_output(sample_rate):
        outputs.append(FakeOutput())
        return outputs[-1]

    def broken_refresh():
        raise RuntimeError("PortAudio busy")

    speaker = Speaker(lambda text: (np.ones(100, dtype=np.float32), SAMPLE_RATE), open_output,
                      refresh_devices=broken_refresh, log=logs.append)
    speaker.speak(["Hello."])
    speaker.wait(2)
    assert outputs and outputs[0].total_samples() > 0
    assert any("device refresh failed" in line for line in logs)


def test_devices_are_not_refreshed_while_the_previous_reply_is_still_playing():
    refreshes: list[int] = []
    release = threading.Event()

    class StuckOutput(FakeOutput):
        def write(self, block):
            release.wait(5)

    speaker = Speaker(lambda text: (np.ones(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE),
                      lambda sample_rate: StuckOutput(), refresh_devices=lambda: refreshes.append(1), log=lambda line: None)
    speaker.speak(["Stuck."])
    time.sleep(0.1)
    speaker.speak(["Next."])  # the stuck reply ignores cancel for more than the 2 s join
    release.set()
    speaker.wait(5)
    assert refreshes == [1]


def test_a_microphone_chosen_by_name_is_looked_up_again_on_each_open():
    from orca_talkback.speaker import open_named_input

    device_lists = [
        [{"name": "MacBook Pro Microphone", "max_input_channels": 1}, {"name": "External Microphone", "max_input_channels": 1}],
        [{"name": "External Microphone", "max_input_channels": 1}],
    ]
    opened: list[int] = []

    def open_input(on_chunk, device=None):
        opened.append(device)
        return FakeInput(on_chunk)

    for devices in device_lists:
        open_named_input(lambda chunk: None, name="External Microphone",
                         query_devices=lambda devices=devices: devices, open_input=open_input)
    assert opened == [1, 0]
