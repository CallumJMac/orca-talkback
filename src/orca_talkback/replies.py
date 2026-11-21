"""Read an Orca pane's last assistant message through the Orca CLI."""

import json
import os
import shutil
import subprocess
from collections.abc import Callable

ORCA_APP_CLI = "/Applications/Orca.app/Contents/Resources/bin/orca"


def orca_cli_path() -> str:
    return os.environ.get("ORCA_CLI_COMMAND") or shutil.which("orca") or ORCA_APP_CLI


def full_assistant_text(
    orca_session_id: str,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    """Return the newest assistant message text for a session, or "" if unavailable."""
    try:
        completed = run(
            [orca_cli_path(), "agent", "read", orca_session_id, "--json", "--limit", "20"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        entries = json.loads(completed.stdout)["result"]["entries"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""
    if not isinstance(entries, list):
        return ""
    for entry in reversed(entries):
        if (
            isinstance(entry, dict)
            and entry.get("kind") == "message"
            and entry.get("role") == "assistant"
            and isinstance(entry.get("text"), str)
            and entry["text"]
        ):
            return entry["text"]
    return ""


def last_assistant_message(
    pane_key: str,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Return the pane's last assistant message, or "" if Orca does not list it."""
    try:
        completed = run(
            [orca_cli_path(), "agent", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        agents = json.loads(completed.stdout)["result"]["agents"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""
    if not isinstance(agents, list):
        return ""
    for agent in agents:
        if isinstance(agent, dict) and agent.get("paneKey") == pane_key:
            orca_session_id = agent.get("orcaSessionId")
            if isinstance(orca_session_id, str):
                full_text = full_assistant_text(orca_session_id, run)
                if full_text:
                    return full_text
            message = agent.get("lastAssistantMessage")
            return message if isinstance(message, str) else ""
    return ""
