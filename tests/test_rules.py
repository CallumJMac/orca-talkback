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


def test_an_agent_waking_itself_does_not_count_as_your_prompt():
    rules = FocusRules()
    rules.on_focus("viewed")
    rules.on_status("elsewhere", "busy-pane", "working")
    rules.on_status("elsewhere", "busy-pane", "done")
    assert rules.on_status("elsewhere", "busy-pane", "working", user_prompted=False) is None
    assert rules.on_status("elsewhere", "busy-pane", "done") is None


def test_a_confirmed_prompt_marks_the_chat_you_are_viewing():
    rules = FocusRules()
    rules.on_focus("sidebar")
    rules.on_status("chat", "chat-pane", "done")
    assert rules.on_status("chat", "chat-pane", "working", user_prompted=True) == Stop("you sent a new prompt")
    assert rules.on_status("chat", "chat-pane", "done") == Speak("chat-pane")


def test_an_agent_you_are_viewing_that_wakes_itself_is_still_spoken():
    rules = FocusRules()
    rules.on_focus("viewed")
    rules.on_status("viewed", "pane", "done")
    assert rules.on_status("viewed", "pane", "working", user_prompted=False) == Stop("agent started working")
    assert rules.on_status("viewed", "pane", "done") == Speak("pane")


def test_unknown_prompt_source_keeps_the_old_behaviour():
    rules = FocusRules()
    rules.on_focus("sidebar")
    rules.on_status("chat", "chat-pane", "done")
    assert rules.on_status("chat", "chat-pane", "working") == Stop("you sent a new prompt")


def test_orca_and_claude_agent_messages_are_not_your_prompts():
    from orca_talkback.rules import is_agent_message

    assert is_agent_message("──── From: STRUCTWORKER_2351251B (structworker_2351251b) ────\nDone with PR 12")
    assert is_agent_message("  <task-notification>\n<task-id>abc</task-id>")
    assert is_agent_message("You are working inside Orca, a multi-agent IDE. You are a dispatched worker.")
    assert not is_agent_message("Do all the strava ones we can and review them")
    assert not is_agent_message("")


def test_a_late_confirmed_prompt_marks_a_working_chat():
    rules = FocusRules()
    rules.on_focus("sidebar")
    rules.on_status("chat", "chat-pane", "done")
    assert rules.on_status("chat", "chat-pane", "working", user_prompted=False) is None
    assert rules.mark_prompted("chat-pane") == Stop("you sent a new prompt")
    assert rules.on_status("chat", "chat-pane", "done") == Speak("chat-pane")


def test_a_late_prompt_for_a_pane_that_is_not_working_is_ignored():
    rules = FocusRules()
    rules.on_status("chat", "chat-pane", "done")
    assert rules.mark_prompted("chat-pane") is None
    assert rules.mark_prompted("unknown-pane") is None


def test_a_reply_that_finished_while_you_were_away_plays_when_you_arrive():
    rules = FocusRules()
    rules.on_focus("evals", now=0)
    rules.on_status("rfc", "rfc-pane", "working", now=0)
    assert rules.on_status("rfc", "rfc-pane", "done", now=10) is None
    assert rules.on_focus("rfc", now=16) == Speak("rfc-pane")


def test_an_unheard_reply_plays_only_once():
    rules = FocusRules()
    rules.on_focus("evals", now=0)
    rules.on_status("rfc", "rfc-pane", "working", now=0)
    rules.on_status("rfc", "rfc-pane", "done", now=10)
    assert rules.on_focus("rfc", now=16) == Speak("rfc-pane")
    rules.on_focus("evals", now=20)
    assert rules.on_focus("rfc", now=30) == Stop("focus changed")


def test_an_unheard_reply_is_dropped_once_the_agent_works_again():
    rules = FocusRules()
    rules.on_focus("evals", now=0)
    rules.on_status("rfc", "rfc-pane", "working", now=0)
    rules.on_status("rfc", "rfc-pane", "done", now=10)
    rules.on_status("rfc", "rfc-pane", "working", now=12)
    assert rules.on_focus("rfc", now=16) == Stop("focus changed")


def test_an_unheard_reply_expires_after_ten_minutes():
    rules = FocusRules()
    rules.on_focus("evals", now=0)
    rules.on_status("rfc", "rfc-pane", "working", now=0)
    rules.on_status("rfc", "rfc-pane", "done", now=10)
    assert rules.on_focus("rfc", now=611) == Stop("focus changed")


def test_arriving_matches_a_worktree_by_its_list_id():
    rules = FocusRules()
    rules.on_focus("identity:other", now=0)
    rules.on_status("rfc", "rfc-pane", "working", workspace_list_id="identity:rfc", now=0)
    rules.on_status("rfc", "rfc-pane", "done", workspace_list_id="identity:rfc", now=10)
    assert rules.on_focus("identity:rfc", now=15) == Speak("rfc-pane")


def test_a_reply_you_already_heard_is_not_replayed_on_arrival():
    rules = FocusRules()
    rules.on_focus("rfc", now=0)
    rules.on_status("rfc", "rfc-pane", "working", now=0)
    assert rules.on_status("rfc", "rfc-pane", "done", now=10) == Speak("rfc-pane")
    rules.on_focus("evals", now=12)
    assert rules.on_focus("rfc", now=14) == Stop("focus changed")


def test_the_latest_unheard_reply_in_the_workspace_plays():
    rules = FocusRules()
    rules.on_focus("evals", now=0)
    for pane in ("first-pane", "second-pane"):
        rules.on_status("rfc", pane, "working", now=0)
    rules.on_status("rfc", "first-pane", "done", now=5)
    rules.on_status("rfc", "second-pane", "done", now=8)
    assert rules.on_focus("rfc", now=10) == Speak("second-pane")
