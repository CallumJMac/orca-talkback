"""`orca-talkback service`: run the daemon at login as a launchd agent (macOS).

    orca-talkback service install [daemon flags]   install and start; flags are stored in the agent
    orca-talkback service uninstall                stop it and remove the agent
    orca-talkback service restart                  reload the agent (re-reads the stored flags)
    orca-talkback service status

launchd starts the agent at login and again if it exits, so the daemon is always there when
Orca sends an event.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

LABEL = "com.orca-talkback"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "orca-talkback.log"
DEFAULT_PORT = 47823
SYSTEM_PATH = ("/usr/local/bin", "/opt/homebrew/bin", "/usr/bin", "/bin")
RESTART_THROTTLE_SECONDS = 10  # launchd waits this long before restarting a daemon that keeps exiting

Launchctl = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class ServiceError(Exception):
    """A problem to show the user as one line, without a traceback."""


def run_launchctl(arguments: Sequence[str]) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(["launchctl", *arguments], capture_output=True, text=True, check=False)


def daemon_is_responding(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def daemon_command() -> list[str]:
    """The command that starts the daemon: the installed script, or this interpreter as a fallback."""
    installed = shutil.which("orca-talkback")
    return [installed] if installed else [sys.executable, "-m", "orca_talkback"]


def search_path(command: list[str]) -> str:
    """launchd agents start with a minimal PATH; add where the daemon and the `orca` CLI live."""
    from orca_talkback.replies import orca_cli_path

    candidates = [str(Path(command[0]).parent), str(Path.home() / ".local" / "bin")]
    orca_cli = orca_cli_path()
    if os.sep in orca_cli:
        candidates.append(str(Path(orca_cli).parent))
    entries: list[str] = []
    for entry in (*candidates, *SYSTEM_PATH):
        if entry not in entries:
            entries.append(entry)
    return ":".join(entries)


def build_plist(command: list[str], daemon_args: Sequence[str], log_path: Path = LOG_PATH) -> dict:
    environment = {"PATH": search_path(command)}
    if os.environ.get("ORCA_CLI_COMMAND"):
        environment["ORCA_CLI_COMMAND"] = os.environ["ORCA_CLI_COMMAND"]
    return {
        "Label": LABEL,
        "ProgramArguments": [*command, *daemon_args],
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": RESTART_THROTTLE_SECONDS,
        "ProcessType": "Interactive",  # audio playback should not be throttled as a background job
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
    }


def domain() -> str:
    return f"gui/{os.getuid()}"


def is_loaded(launchctl: Launchctl) -> bool:
    return launchctl(["print", f"{domain()}/{LABEL}"]).returncode == 0


def load(launchctl: Launchctl, plist_path: Path, loaded: bool | None = None) -> None:
    """Reload the agent. `bootout` then `bootstrap`: `kickstart` would not re-read the plist."""
    if is_loaded(launchctl) if loaded is None else loaded:
        launchctl(["bootout", f"{domain()}/{LABEL}"])
        time.sleep(1.5)  # let the old daemon release its port before the new one binds it
    result = launchctl(["bootstrap", domain(), str(plist_path)])
    if result.returncode != 0:
        raise ServiceError(f"launchctl could not load the agent: {result.stderr.strip() or result.stdout.strip()}")


def install(
    daemon_args: Sequence[str],
    *,
    plist_path: Path = PLIST_PATH,
    log_path: Path = LOG_PATH,
    launchctl: Launchctl = run_launchctl,
    port: int = DEFAULT_PORT,
    responding: Callable[[int], bool] = daemon_is_responding,
    command: list[str] | None = None,
) -> str:
    """Write the agent and start it. Safe to run again: it replaces the agent and its settings."""
    if sys.platform != "darwin":
        raise ServiceError("launch at login uses launchd, so it only works on macOS")
    loaded = is_loaded(launchctl)
    if not loaded and responding(port):
        raise ServiceError(
            f"an orca-talkback daemon is already running on port {port}. Stop it (Ctrl+C in its terminal) "
            "and run this again, so the agent does not fight it for the port."
        )
    plist = build_plist(command or daemon_command(), daemon_args, log_path)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    previous = plist_path.read_bytes() if plist_path.exists() else None
    plist_path.write_bytes(plistlib.dumps(plist))
    try:
        load(launchctl, plist_path, loaded)
    except ServiceError:
        if previous is not None:  # keep whatever was working before
            plist_path.write_bytes(previous)
            load(launchctl, plist_path)
        else:
            plist_path.unlink(missing_ok=True)
        raise
    return (
        f"Installed {plist_path}\n"
        f"  It starts at login and restarts if it stops. Loading the voice takes about 25 s.\n"
        f"  Log: {log_path}"
    )


def uninstall(*, plist_path: Path = PLIST_PATH, launchctl: Launchctl = run_launchctl) -> str:
    was_loaded = is_loaded(launchctl)
    if was_loaded:
        launchctl(["bootout", f"{domain()}/{LABEL}"])
    existed = plist_path.exists()
    plist_path.unlink(missing_ok=True)
    return "Removed the orca-talkback launch agent." if (was_loaded or existed) else "Nothing to remove: the launch agent is not installed."


def restart(*, plist_path: Path = PLIST_PATH, launchctl: Launchctl = run_launchctl) -> str:
    if not plist_path.exists():
        raise ServiceError("the launch agent is not installed; run: orca-talkback service install")
    load(launchctl, plist_path)
    return "Restarting. Loading the voice takes about 25 s."


def status(
    *,
    plist_path: Path = PLIST_PATH,
    launchctl: Launchctl = run_launchctl,
    port: int = DEFAULT_PORT,
    responding: Callable[[int], bool] = daemon_is_responding,
) -> str:
    if not plist_path.exists():
        return "Launch agent: not installed. Install it with: orca-talkback service install"
    arguments = plistlib.loads(plist_path.read_bytes()).get("ProgramArguments", [])
    flags = " ".join(arguments[1:]) or "(defaults)"
    loaded = "loaded" if is_loaded(launchctl) else "installed but not loaded (run: orca-talkback service restart)"
    daemon = "answering" if responding(port) else "not answering (it may still be starting)"
    return f"Launch agent: {loaded}\n  Daemon: {daemon} on port {port}\n  Flags: {flags}"


def run_service(arguments: list[str]) -> int:
    """Entry point for `orca-talkback service ...`. Returns the process exit code."""
    from orca_talkback.__main__ import parse_args

    action, rest = (arguments[0], arguments[1:]) if arguments else ("", [])
    try:
        if action == "install":
            parsed = parse_args(rest)  # reject mistyped daemon flags now, not as a crash loop at login
            print(install(rest, port=parsed.port))
        elif action == "uninstall" and not rest:
            print(uninstall())
        elif action == "restart" and not rest:
            print(restart())
        elif action == "status" and not rest:
            print(status())
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except ServiceError as error:
        print(f"orca-talkback service: {error}", file=sys.stderr)
        return 1
    return 0
