import json
from pathlib import Path

import pytest

from orca_talkback.setup_command import HOOK_SCRIPT_NAME, install_claude_hook


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def test_installs_hook_into_empty_settings(tmp_path):
    settings = tmp_path / "settings.json"
    hook = tmp_path / "config" / HOOK_SCRIPT_NAME
    changed = install_claude_hook(settings, hook)
    assert changed
    assert hook.exists() and hook.stat().st_mode & 0o111
    entries = read(settings)["hooks"]["UserPromptSubmit"]
    assert entries == [{"hooks": [{"type": "command", "command": str(hook), "timeout": 2}]}]


def test_keeps_other_settings_and_hooks_and_backs_up(tmp_path):
    settings = tmp_path / "settings.json"
    original = {"model": "opus", "hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-hook"}]}],
                                           "Stop": [{"hooks": [{"type": "command", "command": "stop-hook"}]}]}}
    settings.write_text(json.dumps(original))
    install_claude_hook(settings, tmp_path / HOOK_SCRIPT_NAME)
    updated = read(settings)
    assert updated["model"] == "opus"
    assert updated["hooks"]["Stop"] == original["hooks"]["Stop"]
    commands = [hook["command"] for entry in updated["hooks"]["UserPromptSubmit"] for hook in entry["hooks"]]
    assert commands == ["other-hook", str(tmp_path / HOOK_SCRIPT_NAME)]
    assert read(settings.with_name("settings.json.bak-orca-talkback")) == original


def test_running_twice_changes_nothing(tmp_path):
    settings = tmp_path / "settings.json"
    hook = tmp_path / HOOK_SCRIPT_NAME
    install_claude_hook(settings, hook)
    before = settings.read_text()
    assert not install_claude_hook(settings, hook)
    assert settings.read_text() == before


def test_replaces_an_older_copy_of_the_hook_at_another_path(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": "/old/checkout/integrations/claude-code/talkback-voice-mode.sh", "timeout": 2}]}]}}))
    hook = tmp_path / HOOK_SCRIPT_NAME
    install_claude_hook(settings, hook)
    commands = [hook_entry["command"] for entry in read(settings)["hooks"]["UserPromptSubmit"] for hook_entry in entry["hooks"]]
    assert commands == [str(hook)]


def test_refuses_to_touch_unreadable_settings(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text("{ not json")
    with pytest.raises(ValueError):
        install_claude_hook(settings, tmp_path / HOOK_SCRIPT_NAME)
    assert settings.read_text() == "{ not json"
