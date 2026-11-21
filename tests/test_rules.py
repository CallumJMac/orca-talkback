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


def test_worktree_focus_matches_the_status_list_id():
    """Orca sends a worktree's focus as its identity key, which status events carry as workspaceListId."""
    rules = FocusRules()
    rules.on_focus("identity:wt2:local:abc")
    rules.on_status("repo-id::/path/to/worktree", "pane-a", "working", workspace_list_id="identity:wt2:local:abc")
    assert rules.on_status("repo-id::/path/to/worktree", "pane-a", "done", workspace_list_id="identity:wt2:local:abc") == Speak("pane-a")


def test_chat_you_just_prompted_speaks_even_though_orca_reports_another_workspace():
    """Orca's focus event does not follow chats, so prompting one is the signal that you are viewing it."""
    rules = focused_rules()
    rules.on_status(OTHER, "chat-pane", "working")
    rules.on_status(OTHER, "chat-pane", "done")
    assert rules.on_status(OTHER, "chat-pane", "working") == Stop("you sent a new prompt")
    assert rules.on_status(OTHER, "chat-pane", "done") == Speak("chat-pane")


def test_picking_another_workspace_means_you_moved_on_from_the_chat():
    rules = focused_rules()
    rules.on_status(OTHER, "chat-pane", "done")
    rules.on_status(OTHER, "chat-pane", "working")
    rules.on_focus("workspace-elsewhere")
    assert rules.on_status(OTHER, "chat-pane", "done") is None


def test_repeated_focus_for_the_same_workspace_keeps_the_chat():
    rules = focused_rules()
    rules.on_status(OTHER, "chat-pane", "done")
    rules.on_status(OTHER, "chat-pane", "working")
    rules.on_focus(FOCUSED)  # the plugin re-sends focus before every status
    assert rules.on_status(OTHER, "chat-pane", "done") == Speak("chat-pane")


def test_background_agent_first_seen_working_is_not_treated_as_prompted():
    rules = focused_rules()
    rules.on_status(OTHER, "background-pane", "working")
    assert rules.on_status(OTHER, "background-pane", "done") is None


def test_only_the_most_recently_prompted_chat_speaks():
    rules = focused_rules()
    for pane in ("chat-a", "chat-b"):
        rules.on_status(OTHER, pane, "done")
    rules.on_status(OTHER, "chat-a", "working")
    rules.on_status(OTHER, "chat-b", "working")
    assert rules.on_status(OTHER, "chat-a", "done") is None
    assert rules.on_status(OTHER, "chat-b", "done") == Speak("chat-b")


def test_answering_a_question_in_the_focused_workspace_stops_then_speaks():
    rules = focused_rules()
    rules.on_status(FOCUSED, "chat-pane", "waiting")
    assert rules.on_status(FOCUSED, "chat-pane", "working") == Stop("you sent a new prompt")
    assert rules.on_status(FOCUSED, "chat-pane", "done") == Speak("chat-pane")
