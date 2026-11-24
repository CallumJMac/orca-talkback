import plistlib
import subprocess

import pytest

from orca_talkback import service
from orca_talkback.service import (
    ServiceError,
    build_plist,
    install,
    restart,
    status,
    uninstall,
)

COMMAND = ["/Users/me/.local/bin/orca-talkback"]


class FakeLaunchctl:
    """Records launchctl calls. `loaded` is what `print` reports; bootstrap can be made to fail."""

    def __init__(self, loaded: bool = False, bootstrap_fails_times: int = 0):
        self.loaded = loaded
        self.bootstrap_fails_times = bootstrap_fails_times
        self.calls: list[list[str]] = []

    def __call__(self, arguments):
        arguments = list(arguments)
        self.calls.append(arguments)
        code, err = 0, ""
        if arguments[0] == "print":
            code = 0 if self.loaded else 113
        elif arguments[0] == "bootout":
            self.loaded = False
        elif arguments[0] == "bootstrap":
            if self.bootstrap_fails_times > 0:
                self.bootstrap_fails_times -= 1
                code, err = 5, "Bootstrap failed: 5: Input/output error"
            else:
                self.loaded = True
        return subprocess.CompletedProcess(arguments, code, "", err)

    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture(autouse=True)
def darwin_and_no_sleep(monkeypatch):
    monkeypatch.setattr(service.sys, "platform", "darwin")
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)


def do_install(tmp_path, launchctl, args=(), responding=False):
    return install(
        list(args), plist_path=tmp_path / "agents" / "agent.plist", log_path=tmp_path / "logs" / "talkback.log",
        launchctl=launchctl, responding=lambda port: responding, command=COMMAND,
    )


def read_plist(tmp_path):
    return plistlib.loads((tmp_path / "agents" / "agent.plist").read_bytes())


def test_plist_runs_the_daemon_at_login_and_keeps_it_alive(tmp_path):
    plist = build_plist(COMMAND, ["--voice", "af_heart"], tmp_path / "talkback.log")
    assert plist["Label"] == "com.orca-talkback"
    assert plist["ProgramArguments"] == [*COMMAND, "--voice", "af_heart"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == plist["StandardErrorPath"] == str(tmp_path / "talkback.log")


def test_plist_path_includes_where_the_daemon_lives_and_the_system_directories():
    path = build_plist(COMMAND, [])["EnvironmentVariables"]["PATH"].split(":")
    assert path[0] == "/Users/me/.local/bin"
    assert "/usr/bin" in path and "/opt/homebrew/bin" in path
    assert len(path) == len(set(path))


def test_install_writes_the_plist_and_loads_it(tmp_path):
    launchctl = FakeLaunchctl()
    message = do_install(tmp_path, launchctl, ["--no-barge-in"])
    assert read_plist(tmp_path)["ProgramArguments"] == [*COMMAND, "--no-barge-in"]
    assert launchctl.verbs() == ["print", "bootstrap"]
    assert launchctl.calls[1][2].endswith("agent.plist")
    assert "starts at login" in message


def test_installing_again_replaces_the_agent_instead_of_adding_a_second_one(tmp_path):
    launchctl = FakeLaunchctl()
    do_install(tmp_path, launchctl, ["--voice", "bf_emma"])
    do_install(tmp_path, launchctl, ["--voice", "af_heart"])
    assert read_plist(tmp_path)["ProgramArguments"][1:] == ["--voice", "af_heart"]
    assert launchctl.verbs() == ["print", "bootstrap", "print", "bootout", "bootstrap"]


def test_install_refuses_when_a_daemon_is_already_running_outside_launchd(tmp_path):
    launchctl = FakeLaunchctl(loaded=False)
    with pytest.raises(ServiceError, match="already running"):
        do_install(tmp_path, launchctl, responding=True)
    assert not (tmp_path / "agents" / "agent.plist").exists()
    assert "bootstrap" not in launchctl.verbs()


def test_install_replaces_its_own_running_daemon_without_complaint(tmp_path):
    launchctl = FakeLaunchctl(loaded=True)
    do_install(tmp_path, launchctl, responding=True)
    assert launchctl.verbs() == ["print", "bootout", "bootstrap"]


def test_failed_first_install_leaves_no_plist_behind(tmp_path):
    launchctl = FakeLaunchctl(bootstrap_fails_times=1)
    with pytest.raises(ServiceError, match="could not load"):
        do_install(tmp_path, launchctl)
    assert not (tmp_path / "agents" / "agent.plist").exists()


def test_failed_reinstall_restores_the_previous_working_settings(tmp_path):
    launchctl = FakeLaunchctl()
    do_install(tmp_path, launchctl, ["--voice", "bf_emma"])
    launchctl.bootstrap_fails_times = 1
    with pytest.raises(ServiceError):
        do_install(tmp_path, launchctl, ["--voice", "af_heart"])
    assert read_plist(tmp_path)["ProgramArguments"][1:] == ["--voice", "bf_emma"]
    assert launchctl.loaded


def test_install_is_refused_off_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(service.sys, "platform", "linux")
    with pytest.raises(ServiceError, match="macOS"):
        do_install(tmp_path, FakeLaunchctl())


def test_uninstall_stops_the_agent_and_removes_the_plist(tmp_path):
    launchctl = FakeLaunchctl()
    do_install(tmp_path, launchctl)
    message = uninstall(plist_path=tmp_path / "agents" / "agent.plist", launchctl=launchctl)
    assert not (tmp_path / "agents" / "agent.plist").exists()
    assert launchctl.verbs()[-1] == "bootout"
    assert message.startswith("Removed")


def test_uninstall_when_nothing_is_installed_says_so(tmp_path):
    message = uninstall(plist_path=tmp_path / "agent.plist", launchctl=FakeLaunchctl())
    assert "not installed" in message


def test_restart_needs_an_installed_agent(tmp_path):
    with pytest.raises(ServiceError, match="not installed"):
        restart(plist_path=tmp_path / "agent.plist", launchctl=FakeLaunchctl())


def test_restart_reloads_the_agent(tmp_path):
    launchctl = FakeLaunchctl()
    do_install(tmp_path, launchctl)
    restart(plist_path=tmp_path / "agents" / "agent.plist", launchctl=launchctl)
    assert launchctl.verbs()[-3:] == ["print", "bootout", "bootstrap"]


def test_status_reports_flags_and_whether_the_daemon_answers(tmp_path):
    launchctl = FakeLaunchctl()
    do_install(tmp_path, launchctl, ["--no-barge-in"])
    report = status(plist_path=tmp_path / "agents" / "agent.plist", launchctl=launchctl, responding=lambda port: True)
    assert "loaded" in report and "answering" in report and "--no-barge-in" in report


def test_status_when_not_installed(tmp_path):
    assert "not installed" in status(plist_path=tmp_path / "agent.plist", launchctl=FakeLaunchctl())


def test_mistyped_daemon_flags_are_rejected_before_anything_is_installed(capsys):
    with pytest.raises(SystemExit):
        service.run_service(["install", "--no-such-flag"])


def test_unknown_service_action_prints_usage_and_fails(capsys):
    assert service.run_service(["frobnicate"]) == 2
    assert "service install" in capsys.readouterr().err
