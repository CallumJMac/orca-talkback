import json
import subprocess
import threading
from pathlib import Path

import pytest

from orca_talkback.rules import FocusRules
from orca_talkback.server import VoiceDaemon, make_server

HOOK = Path(__file__).resolve().parent.parent / "integrations" / "claude-code" / "talkback-voice-mode.sh"


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


def run_hook(url: str, session_id: str = "claude-session-1") -> str:
    completed = subprocess.run(
        ["sh", str(HOOK)], input=json.dumps({"session_id": session_id, "prompt": "hi"}),
        capture_output=True, text=True, timeout=10, env={"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "TALKBACK_URL": url},
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def test_voice_mode_on_adds_the_spoken_line_instruction(daemon_url):
    _, url = daemon_url
    output = json.loads(run_hook(url))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "<!-- spoken:" in context


def test_muted_session_gets_no_instruction(daemon_url):
    daemon, url = daemon_url
    daemon.handle_mutes({"muted": [], "off": True})
    assert run_hook(url) == ""


def test_daemon_down_means_reading_mode():
    assert run_hook("http://127.0.0.1:9") == ""
