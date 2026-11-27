import http.client
import json
import threading
import urllib.error
import urllib.request

import pytest

from orca_talkback.rules import FocusRules
from orca_talkback.server import VoiceDaemon, make_server

FOCUSED = "workspace-focused"


class FakeSpeaker:
    def __init__(self):
        self.spoken: list[list[str]] = []
        self.stops: list[str] = []
        self.is_playing = False

    def speak(self, sentences):
        self.spoken.append(list(sentences))

    def stop(self, reason):
        self.stops.append(reason)


def make_daemon(messages: dict[str, str] | None = None, lookup_message=None, verbose: bool = False,
                orca_visible_check=None):
    speaker = FakeSpeaker()
    log_lines: list[str] = []
    daemon = VoiceDaemon(
        FocusRules(), speaker,
        lookup_message=lookup_message or (lambda pane_key: (messages or {}).get(pane_key, "")),
        to_sentences=lambda reply: [f"spoken: {reply}"],
        log=log_lines.append,
        verbose=verbose,
        orca_visible_check=orca_visible_check,
    )
    daemon.log_lines = log_lines
    return daemon, speaker


def focus_payload(workspace_id: str) -> dict:
    return {"context": {"workspaceId": workspace_id, "branch": "", "displayName": "Demo", "terminals": []}}


def status_payload(workspace_id: str, pane_key: str, state: str) -> dict:
    return {"workspaceId": workspace_id, "paneKey": pane_key, "state": state, "receivedAt": 1}


def test_finished_reply_in_focused_workspace_is_spoken():
    daemon, speaker = make_daemon({"pane-a": "All tests pass."})
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    assert speaker.spoken == [["spoken: All tests pass."]]


def test_reply_in_other_workspace_is_silent():
    daemon, speaker = make_daemon({"pane-b": "Hello."})
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload("workspace-other", "pane-b", "working"))
    daemon.handle_status(status_payload("workspace-other", "pane-b", "done"))
    assert speaker.spoken == []


def test_empty_reply_is_not_spoken():
    daemon, speaker = make_daemon({"pane-a": "   "})
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    assert speaker.spoken == []


def test_focus_change_and_new_work_stop_playback():
    daemon, speaker = make_daemon()
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    assert speaker.stops == ["focus changed", "agent started working"]


def test_status_without_pane_key_is_rejected():
    daemon, _ = make_daemon()
    with pytest.raises(ValueError):
        daemon.handle_status({"workspaceId": FOCUSED, "state": "done"})


@pytest.fixture
def server_url():
    daemon, speaker = make_daemon({"pane-a": "Done and dusted."})
    server = make_server(daemon, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", speaker
    server.shutdown()
    server.server_close()


def post(url: str, body: bytes) -> int:
    request = urllib.request.Request(url, data=body, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_http_flow_speaks_reply(server_url):
    base_url, speaker = server_url
    assert post(f"{base_url}/focus", json.dumps(focus_payload(FOCUSED)).encode()) == 200
    assert post(f"{base_url}/status", json.dumps(status_payload(FOCUSED, "pane-a", "working")).encode()) == 200
    assert post(f"{base_url}/status", json.dumps(status_payload(FOCUSED, "pane-a", "done")).encode()) == 200
    assert speaker.spoken == [["spoken: Done and dusted."]]


def test_http_rejects_bad_json(server_url):
    base_url, _ = server_url
    assert post(f"{base_url}/status", b"not json") == 400
    assert post(f"{base_url}/status", b"[1, 2]") == 400


def test_http_unknown_path_is_404(server_url):
    base_url, _ = server_url
    assert post(f"{base_url}/nope", b"{}") == 404


def test_http_health(server_url):
    base_url, _ = server_url
    with urllib.request.urlopen(f"{base_url}/health", timeout=5) as response:
        assert json.loads(response.read()) == {"ok": True, "playing": False}


def test_decisions_are_logged():
    daemon, _ = make_daemon({"pane-a": "Hi."})
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    logs = daemon.log_lines
    assert any("[focus]" in line and "stop (focus changed)" in line for line in logs)
    assert any("[status]" in line and "stop (agent started working)" in line for line in logs)
    assert any("[status]" in line and "-> speak" in line for line in logs)
    assert "[say] spoken: Hi." in logs
    assert not any("-> None" in line for line in logs)


def test_stop_is_logged():
    daemon, _ = make_daemon()
    daemon.handle_stop({})
    assert "[stop] requested" in daemon.log_lines


def test_lookup_runs_while_lock_is_held():
    held: list[bool] = []

    def lookup(pane_key: str) -> str:
        held.append(daemon._lock.locked())
        return "Hi."

    daemon, _ = make_daemon(lookup_message=lookup)
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    assert held == [True]


def test_http_route_exception_is_500():
    def lookup(pane_key: str) -> str:
        raise RuntimeError("boom")

    daemon, _ = make_daemon(lookup_message=lookup)
    server = make_server(daemon, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        post(f"{base_url}/focus", json.dumps(focus_payload(FOCUSED)).encode())
        post(f"{base_url}/status", json.dumps(status_payload(FOCUSED, "pane-a", "working")).encode())
        assert post(f"{base_url}/status", json.dumps(status_payload(FOCUSED, "pane-a", "done")).encode()) == 500
    finally:
        server.shutdown()
        server.server_close()


def test_http_negative_content_length_is_400(server_url):
    base_url, _ = server_url
    connection = http.client.HTTPConnection(base_url.removeprefix("http://"), timeout=5)
    connection.putrequest("POST", "/status")
    connection.putheader("content-type", "application/json")
    connection.putheader("content-length", "-5")
    connection.endheaders()
    assert connection.getresponse().status == 400
    connection.close()


def test_http_stop_calls_speaker(server_url):
    base_url, speaker = server_url
    assert post(f"{base_url}/stop", b"{}") == 200
    assert speaker.stops == ["requested"]


def test_http_non_json_content_type_is_415(server_url):
    base_url, speaker = server_url
    body = json.dumps(focus_payload(FOCUSED)).encode()
    request = urllib.request.Request(f"{base_url}/focus", data=body, headers={"content-type": "text/plain"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=5)
    assert raised.value.code == 415
    assert post(f"{base_url}/status", json.dumps(status_payload(FOCUSED, "pane-a", "done")).encode()) == 200
    assert speaker.spoken == []


def test_http_json_content_type_with_charset_is_accepted(server_url):
    base_url, _ = server_url
    body = json.dumps(focus_payload(FOCUSED)).encode()
    request = urllib.request.Request(
        f"{base_url}/focus", data=body, headers={"content-type": "application/json; charset=utf-8"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200


def test_verbose_logs_every_event_even_without_an_action():
    daemon, _ = make_daemon(verbose=True)
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload("workspace-other", "pane-b", "working"))
    daemon.handle_status(status_payload("workspace-other", "pane-b", "done"))
    events = [line for line in daemon.log_lines if line.startswith("[event]")]
    assert events == [
        "[event] focus workspace=workspace-focused name='Demo'",
        "[event] status pane=pane-b state=working session=None workspace=workspace-other focused=workspace-focused",
        "[event] status pane=pane-b state=done session=None workspace=workspace-other focused=workspace-focused",
    ]


def test_quiet_by_default():
    daemon, _ = make_daemon()
    daemon.handle_status(status_payload("workspace-other", "pane-b", "working"))
    assert not [line for line in daemon.log_lines if line.startswith("[event]")]


def hidden_daemon(messages):
    orca_visible = [False]
    now = [1000.0]
    daemon, speaker = make_daemon(messages, orca_visible_check=lambda: orca_visible[0])
    daemon._clock = lambda: now[0]
    daemon.handle_focus(focus_payload(FOCUSED))
    return daemon, speaker, orca_visible, now


def finish(daemon, pane_key):
    daemon.handle_status(status_payload(FOCUSED, pane_key, "working"))
    daemon.handle_status(status_payload(FOCUSED, pane_key, "done"))


def test_a_reply_that_finishes_while_orca_is_hidden_plays_when_you_return():
    daemon, speaker, orca_visible, _ = hidden_daemon({"pane-a": "All tests pass."})
    finish(daemon, "pane-a")
    assert speaker.spoken == []
    assert "[speak] holding the reply until Orca is visible" in daemon.log_lines
    daemon.enforce_orca_visible()
    assert speaker.spoken == []
    orca_visible[0] = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == [["spoken: All tests pass."]]
    daemon.enforce_orca_visible()
    assert len(speaker.spoken) == 1


def test_a_held_reply_is_dropped_when_you_move_to_another_workspace():
    daemon, speaker, orca_visible, _ = hidden_daemon({"pane-a": "All tests pass."})
    finish(daemon, "pane-a")
    daemon.handle_focus(focus_payload("workspace-other"))
    orca_visible[0] = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == []


def test_a_held_reply_is_dropped_when_the_agent_starts_working_again():
    daemon, speaker, orca_visible, _ = hidden_daemon({"pane-a": "All tests pass."})
    finish(daemon, "pane-a")
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    orca_visible[0] = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == []


def test_a_held_reply_expires_after_ten_minutes():
    daemon, speaker, orca_visible, now = hidden_daemon({"pane-a": "All tests pass."})
    finish(daemon, "pane-a")
    now[0] += 601
    orca_visible[0] = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == []


def test_only_the_latest_held_reply_plays():
    daemon, speaker, orca_visible, _ = hidden_daemon({"pane-a": "First.", "pane-b": "Second."})
    finish(daemon, "pane-a")
    finish(daemon, "pane-b")
    orca_visible[0] = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == [["spoken: Second."]]


def test_playback_stops_when_orca_is_no_longer_visible():
    orca_visible = [True]
    daemon, speaker = make_daemon(orca_visible_check=lambda: orca_visible[0])
    speaker.is_playing = True
    daemon.enforce_orca_visible()
    assert speaker.stops == []
    orca_visible[0] = False
    daemon.enforce_orca_visible()
    assert speaker.stops == ["no Orca window is visible"]


def test_orca_visible_check_is_off_when_not_configured():
    daemon, speaker = make_daemon({"pane-a": "All tests pass."})
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    speaker.is_playing = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == [["spoken: All tests pass."]]
    assert speaker.stops == ["focus changed", "agent started working"]


def test_voice_mode_is_on_by_default():
    daemon, _ = make_daemon()
    assert daemon.voice_mode("claude-session-1") is True


def test_voice_mode_is_off_for_a_muted_workspace():
    daemon, _ = make_daemon()
    daemon.handle_mutes({"muted": ["identity:chat"], "off": False})
    daemon.handle_status({**status_payload("workspace-chat", "pane-a", "working"),
                          "workspaceListId": "identity:chat", "sessionId": "claude-session-1"})
    assert daemon.voice_mode("claude-session-1") is False
    assert daemon.voice_mode("claude-session-2") is True


def test_voice_mode_is_off_everywhere_when_talkback_is_off():
    daemon, _ = make_daemon()
    daemon.handle_mutes({"muted": [], "off": True})
    assert daemon.voice_mode("claude-session-1") is False


def test_mutes_payload_is_validated():
    daemon, _ = make_daemon()
    with pytest.raises(ValueError):
        daemon.handle_mutes({"muted": "not-a-list", "off": False})


def test_http_voice_mode(server_url):
    base_url, _ = server_url
    assert post(f"{base_url}/mutes", json.dumps({"muted": [], "off": True}).encode()) == 200
    with urllib.request.urlopen(f"{base_url}/voice-mode?session=claude-session-1", timeout=5) as response:
        assert json.loads(response.read()) == {"voice": False}


def make_clocked_daemon(messages):
    now = [1000.0]
    daemon, speaker = make_daemon(messages)
    daemon._clock = lambda: now[0]
    return daemon, speaker, now


def status(pane_key, state, workspace, session):
    return {"paneKey": pane_key, "state": state, "workspaceId": workspace, "sessionId": session}


def test_an_agent_woken_by_a_worker_message_is_not_spoken_while_you_view_another_chat():
    daemon, speaker, _ = make_clocked_daemon({"busy": "Worker results are in.", "viewed": "Hello."})
    daemon.handle_focus({"context": {"workspaceId": "viewed-workspace", "displayName": "TTS Paper"}})
    daemon.handle_status(status("busy", "done", "busy-workspace", "busy-session"))
    daemon.handle_prompt({"session": "busy-session", "prompt": "──── From: STRUCTWORKER_1 ────\nPR opened"})
    daemon.handle_status(status("busy", "working", "busy-workspace", "busy-session"))
    daemon.handle_status(status("busy", "done", "busy-workspace", "busy-session"))
    assert speaker.spoken == []
    assert "you sent a new prompt" not in speaker.stops


def test_your_prompt_to_a_chat_still_marks_it_as_the_one_you_are_viewing():
    daemon, speaker, now = make_clocked_daemon({"chat": "All done."})
    daemon.handle_focus({"context": {"workspaceId": "sidebar-workspace", "displayName": "Sidebar"}})
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    daemon.handle_prompt({"session": "chat-session", "prompt": "Please fix the flaky test"})
    now[0] += 2
    daemon.handle_status(status("chat", "working", "chat-workspace", "chat-session"))
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    assert speaker.spoken == [["spoken: All done."]]


def test_an_old_prompt_does_not_confirm_a_later_wake_up():
    daemon, speaker, now = make_clocked_daemon({"chat": "Later reply."})
    daemon.handle_focus({"context": {"workspaceId": "sidebar-workspace", "displayName": "Sidebar"}})
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    daemon.handle_prompt({"session": "chat-session", "prompt": "Start the long job"})
    now[0] += 120
    daemon.handle_status(status("chat", "working", "chat-workspace", "chat-session"))
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    assert speaker.spoken == []


def test_sessions_without_hook_reports_keep_the_old_prompt_guess():
    daemon, speaker, _ = make_clocked_daemon({"chat": "Reply."})
    daemon.handle_focus({"context": {"workspaceId": "sidebar-workspace", "displayName": "Sidebar"}})
    daemon.handle_status(status("chat", "done", "chat-workspace", "no-hook-session"))
    daemon.handle_status(status("chat", "working", "chat-workspace", "no-hook-session"))
    daemon.handle_status(status("chat", "done", "chat-workspace", "no-hook-session"))
    assert speaker.spoken == [["spoken: Reply."]]


def test_prompt_payload_is_validated():
    daemon, _, _ = make_clocked_daemon({})
    with pytest.raises(ValueError):
        daemon.handle_prompt({"session": 5, "prompt": "hi"})
    with pytest.raises(ValueError):
        daemon.handle_prompt({"session": "s", "prompt": None})


def test_your_prompt_reported_after_the_agent_starts_working_still_counts():
    # Orca can mark the chat working a few seconds before Claude Code runs the prompt hook.
    daemon, speaker, now = make_clocked_daemon({"chat": "Done."})
    daemon.handle_focus({"context": {"workspaceId": "sidebar-workspace", "displayName": "Sidebar"}})
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    daemon.handle_prompt({"session": "chat-session", "prompt": "First prompt"})
    now[0] += 60
    daemon.handle_status(status("chat", "working", "chat-workspace", "chat-session"))
    now[0] += 4
    daemon.handle_prompt({"session": "chat-session", "prompt": "Second prompt"})
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    assert speaker.spoken == [["spoken: Done."]]
    assert "you sent a new prompt" in speaker.stops


def test_a_late_report_of_an_agent_message_does_not_mark_the_chat():
    daemon, speaker, now = make_clocked_daemon({"chat": "Done."})
    daemon.handle_focus({"context": {"workspaceId": "sidebar-workspace", "displayName": "Sidebar"}})
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    daemon.handle_prompt({"session": "chat-session", "prompt": "First prompt"})
    now[0] += 60
    daemon.handle_status(status("chat", "working", "chat-workspace", "chat-session"))
    now[0] += 4
    daemon.handle_prompt({"session": "chat-session", "prompt": "──── From: STRUCTWORKER_1 ────"})
    daemon.handle_status(status("chat", "done", "chat-workspace", "chat-session"))
    assert speaker.spoken == []


def test_a_held_reply_is_dropped_when_you_stop_or_mute_talkback():
    daemon, speaker, orca_visible, _ = hidden_daemon({"pane-a": "All tests pass."})
    finish(daemon, "pane-a")
    daemon.handle_stop({})
    orca_visible[0] = True
    daemon.enforce_orca_visible()
    assert speaker.spoken == []


def test_arriving_at_a_workspace_speaks_the_reply_you_missed():
    daemon, speaker = make_daemon({"pane-a": "The RFC review is done."})
    daemon.handle_focus(focus_payload("workspace-other"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    assert speaker.spoken == []
    daemon.handle_focus(focus_payload(FOCUSED))
    assert speaker.spoken == [["spoken: The RFC review is done."]]
