from orca_talkback.rules import FocusRules, Speak, Stop

FOCUSED = "workspace-focused"
OTHER = "workspace-other"


def focused_rules() -> FocusRules:
    rules = FocusRules()
    rules.on_focus(FOCUSED)
    return rules


def test_focus_change_stops_playback():
    rules = FocusRules()
    assert rules.on_focus(FOCUSED) == Stop("focus changed")
    assert rules.focused_workspace_id == FOCUSED


def test_same_focus_does_nothing():
    rules = focused_rules()
    assert rules.on_focus(FOCUSED) is None


def test_done_after_working_in_focused_workspace_speaks():
    rules = focused_rules()
    rules.on_status(FOCUSED, "pane-a", "working")
    assert rules.on_status(FOCUSED, "pane-a", "done") == Speak("pane-a")


def test_repeated_working_events_stop_only_once():
    rules = focused_rules()
    assert rules.on_status(FOCUSED, "pane-a", "working") == Stop("agent started working")
    assert rules.on_status(FOCUSED, "pane-a", "working") is None


def test_repeated_done_events_speak_only_once():
    rules = focused_rules()
    rules.on_status(FOCUSED, "pane-a", "working")
    assert rules.on_status(FOCUSED, "pane-a", "done") == Speak("pane-a")
    assert rules.on_status(FOCUSED, "pane-a", "done") is None


def test_done_in_other_workspace_is_silent():
    rules = focused_rules()
    rules.on_status(OTHER, "pane-b", "working")
    assert rules.on_status(OTHER, "pane-b", "done") is None


def test_working_in_other_workspace_does_not_stop():
    rules = focused_rules()
    assert rules.on_status(OTHER, "pane-b", "working") is None


def test_first_seen_done_is_not_replayed():
    rules = focused_rules()
    assert rules.on_status(FOCUSED, "pane-a", "done") is None


def test_unknown_workspace_is_ignored():
    rules = focused_rules()
    rules.on_status(None, "pane-c", "working")
    assert rules.on_status(None, "pane-c", "done") is None


def test_blocked_and_waiting_do_nothing():
    rules = focused_rules()
    rules.on_status(FOCUSED, "pane-a", "working")
    assert rules.on_status(FOCUSED, "pane-a", "blocked") is None
    assert rules.on_status(FOCUSED, "pane-a", "waiting") is None


def test_done_after_blocked_speaks():
    rules = focused_rules()
    rules.on_status(FOCUSED, "pane-a", "working")
    rules.on_status(FOCUSED, "pane-a", "blocked")
    assert rules.on_status(FOCUSED, "pane-a", "done") == Speak("pane-a")


def test_pane_state_is_tracked_while_unfocused():
    rules = focused_rules()
    rules.on_status(OTHER, "pane-b", "working")
    rules.on_focus(OTHER)
    assert rules.on_status(OTHER, "pane-b", "done") == Speak("pane-b")
