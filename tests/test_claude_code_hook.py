import json
import subprocess
import threading
from pathlib import Path

import pytest

from orca_talkback.rules import FocusRules
from orca_talkback.server import VoiceDaemon, make_server

HOOK = Path(__file__).resolve().parent.parent / "src" / "orca_talkback" / "hooks" / "talkback-voice-mode.sh"


class SilentSpeaker:
    is_playing = False

    def speak(self, sentences):
        list(sentences)

    def stop(self, reason):
        pass


@pytest.fixture
def daemon_url():
    daemon = VoiceDaemon(FocusRules(), SilentSpeaker(), lambda pane: "", lambda reply: [], log=lambda line: None)
    server = make_server(daemon, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield daemon, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def run_hook(url: str, session_id: str = "claude-session-1", orca_chat: bool = True,
             orca_terminal: bool = False, prompt: str = "hi") -> str:
    environment = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "TALKBACK_URL": url}
    if orca_chat:
        environment["ORCA_STRUCTURED_SESSION"] = "1"  # set by Orca for chat sessions, absent in terminals
    if orca_terminal:
        environment["ORCA_PANE_KEY"] = "pane-1"  # set by Orca for its terminal panes
    completed = subprocess.run(
        ["sh", str(HOOK)], input=json.dumps({"session_id": session_id, "prompt": prompt}),
        capture_output=True, text=True, timeout=10, env=environment, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_voice_mode_on_adds_the_spoken_line_instruction(daemon_url):
    _, url = daemon_url
    output = json.loads(run_hook(url))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "[//]: # (spoken:" in context


def test_muted_session_gets_no_instruction(daemon_url):
    daemon, url = daemon_url
    daemon.handle_mutes({"muted": [], "off": True})
    assert run_hook(url) == ""


def test_daemon_down_means_reading_mode():
    assert run_hook("http://127.0.0.1:9") == ""


def test_terminal_sessions_get_no_instruction_because_terminals_show_the_line(daemon_url):
    _, url = daemon_url
    assert run_hook(url, orca_chat=False) == ""


def test_a_chat_prompt_is_reported_as_yours(daemon_url):
    daemon, url = daemon_url
    run_hook(url, session_id="chat-session", prompt="Please fix the build")
    assert daemon._prompts["chat-session"][1] is True


def test_a_worker_message_is_reported_as_another_agent(daemon_url):
    daemon, url = daemon_url
    run_hook(url, session_id="chat-session", prompt="──── From: STRUCTWORKER_1 ────\nPR opened")
    assert daemon._prompts["chat-session"][1] is False


def test_terminal_prompts_are_reported_without_adding_the_instruction(daemon_url):
    daemon, url = daemon_url
    assert run_hook(url, session_id="terminal-session", orca_chat=False, orca_terminal=True) == ""
    assert daemon._prompts["terminal-session"][1] is True


def test_prompts_outside_orca_are_not_reported(daemon_url):
    daemon, url = daemon_url
    run_hook(url, session_id="plain-session", orca_chat=False)
    assert "plain-session" not in daemon._prompts
