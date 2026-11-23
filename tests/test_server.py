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
        "[event] status pane=pane-b state=working workspace=workspace-other focused=workspace-focused",
        "[event] status pane=pane-b state=done workspace=workspace-other focused=workspace-focused",
    ]


def test_quiet_by_default():
    daemon, _ = make_daemon()
    daemon.handle_status(status_payload("workspace-other", "pane-b", "working"))
    assert not [line for line in daemon.log_lines if line.startswith("[event]")]


def test_reply_is_not_spoken_while_no_orca_window_is_visible():
    daemon, speaker = make_daemon({"pane-a": "All tests pass."}, orca_visible_check=lambda: False)
    daemon.handle_focus(focus_payload(FOCUSED))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "working"))
    daemon.handle_status(status_payload(FOCUSED, "pane-a", "done"))
    assert speaker.spoken == []
    assert "[speak] skipped: no Orca window is visible" in daemon.log_lines


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
