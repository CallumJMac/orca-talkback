import json
import subprocess

from orca_talkback.replies import last_assistant_message


EMPTY_READ = json.dumps({"ok": True, "result": {"entries": []}})


def fake_run(stdout: str, calls: list | None = None, read_stdout: str = EMPTY_READ):
    def run(command, **options):
        if calls is not None:
            calls.append(command)
        reply = read_stdout if command[1:3] == ["agent", "read"] else stdout
        return subprocess.CompletedProcess(command, 0, stdout=reply, stderr="")
    return run


def agents_json(*agents: dict) -> str:
    return json.dumps({"ok": True, "result": {"agents": list(agents)}})


def test_returns_message_for_matching_pane():
    stdout = agents_json(
        {"paneKey": "pane-a", "lastAssistantMessage": "First"},
        {"paneKey": "pane-b", "lastAssistantMessage": "Second"},
    )
    assert last_assistant_message("pane-b", run=fake_run(stdout)) == "Second"


def test_calls_agent_list_json():
    calls: list = []
    last_assistant_message("pane-a", run=fake_run(agents_json(), calls))
    assert calls[0][1:] == ["agent", "list", "--json"]


def test_missing_pane_returns_empty():
    stdout = agents_json({"paneKey": "pane-a", "lastAssistantMessage": "First"})
    assert last_assistant_message("pane-z", run=fake_run(stdout)) == ""


def test_null_message_returns_empty():
    stdout = agents_json({"paneKey": "pane-a", "lastAssistantMessage": None})
    assert last_assistant_message("pane-a", run=fake_run(stdout)) == ""


def test_non_string_message_returns_empty():
    stdout = agents_json({"paneKey": "pane-a", "lastAssistantMessage": 42})
    assert last_assistant_message("pane-a", run=fake_run(stdout)) == ""


def test_bad_output_returns_empty():
    assert last_assistant_message("pane-a", run=fake_run("not json")) == ""
    assert last_assistant_message("pane-a", run=fake_run('{"ok": false}')) == ""


def raising_run(error: BaseException):
    def run(command, **options):
        raise error
    return run


def test_missing_cli_returns_empty():
    assert last_assistant_message("pane-a", run=raising_run(FileNotFoundError())) == ""


def test_timeout_returns_empty():
    error = subprocess.TimeoutExpired(cmd=["orca"], timeout=5)
    assert last_assistant_message("pane-a", run=raising_run(error)) == ""


def test_non_dict_agent_entries_are_skipped():
    stdout = agents_json(None, {"paneKey": "pane-a", "lastAssistantMessage": "Hi"})
    assert last_assistant_message("pane-a", run=fake_run(stdout)) == "Hi"


def test_agents_not_a_list_returns_empty():
    stdout = json.dumps({"ok": True, "result": {"agents": "oops"}})
    assert last_assistant_message("pane-a", run=fake_run(stdout)) == ""


def read_json(*entries: dict) -> str:
    return json.dumps({"ok": True, "result": {"entries": list(entries)}})


def assistant_entry(text) -> dict:
    return {"kind": "message", "role": "assistant", "text": text}


SESSION_AGENT = {
    "paneKey": "pane-a",
    "orcaSessionId": "session-1",
    "lastAssistantMessage": "truncated",
}


def test_full_text_from_read_preferred_over_truncated_list_field():
    full_text = "x" * 2342
    run = fake_run(agents_json(SESSION_AGENT), read_stdout=read_json(assistant_entry(full_text)))
    assert last_assistant_message("pane-a", run=run) == full_text


def test_read_command_arguments_are_exact():
    calls: list = []
    options_seen: list = []

    def run(command, **options):
        calls.append(command)
        options_seen.append(options)
        reply = EMPTY_READ if command[1:3] == ["agent", "read"] else agents_json(SESSION_AGENT)
        return subprocess.CompletedProcess(command, 0, stdout=reply, stderr="")

    last_assistant_message("pane-a", run=run)
    assert calls[1][1:] == ["agent", "read", "session-1", "--json", "--limit", "20"]
    assert options_seen[1] == {
        "capture_output": True,
        "text": True,
        "timeout": 5,
        "check": False,
    }


def test_last_assistant_message_entry_wins_and_other_entries_are_skipped():
    entries = read_json(
        assistant_entry("older"),
        {"kind": "message", "role": "user", "text": "question"},
        assistant_entry("newest"),
        {"kind": "tool", "name": "Bash", "state": "completed"},
        {"kind": "message", "role": "user", "text": "another question"},
        assistant_entry(""),
    )
    run = fake_run(agents_json(SESSION_AGENT), read_stdout=entries)
    assert last_assistant_message("pane-a", run=run) == "newest"


def test_read_without_assistant_entries_falls_back_to_list_field():
    entries = read_json(
        {"kind": "tool", "name": "Bash", "state": "completed"},
        {"kind": "message", "role": "user", "text": "question"},
    )
    run = fake_run(agents_json(SESSION_AGENT), read_stdout=entries)
    assert last_assistant_message("pane-a", run=run) == "truncated"


def test_read_bad_json_falls_back_to_list_field():
    run = fake_run(agents_json(SESSION_AGENT), read_stdout="not json")
    assert last_assistant_message("pane-a", run=run) == "truncated"


def test_read_raising_falls_back_to_list_field():
    for error in (FileNotFoundError(), subprocess.TimeoutExpired(cmd=["orca"], timeout=5)):

        def run(command, error=error, **options):
            if command[1:3] == ["agent", "read"]:
                raise error
            return subprocess.CompletedProcess(
                command, 0, stdout=agents_json(SESSION_AGENT), stderr=""
            )

        assert last_assistant_message("pane-a", run=run) == "truncated"


def test_missing_session_id_falls_back_without_calling_read():
    calls: list = []
    stdout = agents_json({"paneKey": "pane-a", "lastAssistantMessage": "Hi"})
    assert last_assistant_message("pane-a", run=fake_run(stdout, calls)) == "Hi"
    assert len(calls) == 1
