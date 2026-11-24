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


class TestLaunchAtLoginOffer:
    @pytest.fixture(autouse=True)
    def darwin_and_recorded_install(self, monkeypatch):
        from orca_talkback import service

        monkeypatch.setattr("orca_talkback.setup_command.sys.platform", "darwin")
        self.installed = []
        monkeypatch.setattr(service, "install", lambda args: self.installed.append(args) or "Installed it")

    def offer(self, tmp_path, choice, interactive, answer=""):
        from orca_talkback.setup_command import offer_launch_at_login

        return offer_launch_at_login(choice, interactive=interactive, ask=lambda prompt: answer,
                                     plist_path=tmp_path / "agent.plist")

    def test_flag_installs_without_asking(self, tmp_path):
        assert self.offer(tmp_path, True, interactive=False) is True
        assert self.installed == [[]]

    def test_no_flag_asks_and_a_blank_answer_means_yes(self, tmp_path):
        assert self.offer(tmp_path, None, interactive=True, answer="") is True
        assert self.installed == [[]]

    def test_answering_no_installs_nothing(self, tmp_path, capsys):
        assert self.offer(tmp_path, None, interactive=True, answer="n") is False
        assert self.installed == []
        assert "service install" in capsys.readouterr().out

    def test_without_a_terminal_it_does_not_ask_and_prints_the_command(self, tmp_path, capsys):
        assert self.offer(tmp_path, None, interactive=False) is False
        assert self.installed == []
        assert "service install" in capsys.readouterr().out

    def test_opting_out_with_the_flag_is_silent(self, tmp_path, capsys):
        assert self.offer(tmp_path, False, interactive=True) is False
        assert self.installed == [] and capsys.readouterr().out == ""

    def test_an_existing_agent_is_left_alone(self, tmp_path):
        (tmp_path / "agent.plist").write_text("x")
        assert self.offer(tmp_path, None, interactive=True) is True
        assert self.installed == []

    def test_a_failed_install_is_reported_but_does_not_stop_setup(self, tmp_path, monkeypatch, capsys):
        from orca_talkback import service

        def fail(args):
            raise service.ServiceError("launchctl said no")

        monkeypatch.setattr(service, "install", fail)
        assert self.offer(tmp_path, True, interactive=False) is False
        assert "launchctl said no" in capsys.readouterr().out
