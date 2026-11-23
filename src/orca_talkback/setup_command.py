"""`orca-talkback setup`: install the Claude Code voice-mode hook, fetch the voice, check the mic and plugin."""

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from importlib import resources
from pathlib import Path

HOOK_SCRIPT_NAME = "talkback-voice-mode.sh"
HOOK_TIMEOUT_SECONDS = 2
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
INSTALLED_HOOK = Path.home() / ".config" / "orca-talkback" / HOOK_SCRIPT_NAME
PLUGIN_SOURCE = "CallumJMac/orca-talkback-plugin"


def install_claude_hook(settings_path: Path, hook_path: Path) -> bool:
    """Copy the hook script to `hook_path` and register it in Claude Code's settings.

    Other settings and hooks are left alone, an older copy of this hook at another path is
    replaced, and the original settings file is backed up. Returns whether settings changed.
    """
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    hook_path.write_bytes(resources.files("orca_talkback.hooks").joinpath(HOOK_SCRIPT_NAME).read_bytes())
    hook_path.chmod(0o755)

    original_text = settings_path.read_text() if settings_path.exists() else ""
    try:
        settings = json.loads(original_text) if original_text.strip() else {}
    except json.JSONDecodeError as error:
        raise ValueError(f"{settings_path} is not valid JSON ({error}); fix it and run setup again") from error
    if not isinstance(settings, dict):
        raise ValueError(f"{settings_path} does not contain a JSON object")

    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault("UserPromptSubmit", [])
    wanted = {"type": "command", "command": str(hook_path), "timeout": HOOK_TIMEOUT_SECONDS}
    kept = []
    for entry in entries:
        others = [hook for hook in entry.get("hooks", []) if not str(hook.get("command", "")).endswith(HOOK_SCRIPT_NAME)]
        if others:
            kept.append({**entry, "hooks": others})
    kept.append({"hooks": [wanted]})
    if kept == entries:
        return False
    hooks["UserPromptSubmit"] = kept

    if original_text:
        settings_path.with_name(settings_path.name + ".bak-orca-talkback").write_text(original_text)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return True


def check_microphone() -> None:
    from orca_talkback.speaker import (
        default_input_name,
        first_real_input,
        looks_virtual,
    )

    name = default_input_name()
    if not looks_virtual(name):
        print(f"  Microphone: {name}")
        return
    real_input = first_real_input()
    if real_input is None:
        print(f"  Microphone: only virtual inputs found ({name!r}), so talking over replies will not stop them.")
    else:
        print(f"  Microphone: {real_input[1]} (your system default, {name!r}, is virtual, so it will be skipped)")


def check_plugin() -> None:
    from orca_talkback.replies import orca_cli_path

    orca = orca_cli_path()
    try:
        listed = subprocess.run([orca, "plugin", "list"], capture_output=True, text=True, timeout=10, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        print(f"  Orca plugin: could not run the Orca CLI; install the plugin with: orca plugin install {PLUGIN_SOURCE}")
        return
    if "talkback" in listed:
        print("  Orca plugin: Talkback is installed")
    else:
        command = "orca" if shutil.which("orca") else orca
        print(f"  Orca plugin: not installed yet. Run: {command} plugin install {PLUGIN_SOURCE}")
        print("  then re-run it with the --approve fingerprint Orca prints.")


def parse_setup_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="orca-talkback setup", description="One-time setup for orca-talkback.")
    parser.add_argument(
        "--launch-at-login", action=argparse.BooleanOptionalAction, default=None,
        help="run the daemon in the background and start it at login (macOS launchd agent); "
             "without this flag, setup asks when run in a terminal",
    )
    return parser.parse_args(argv)


def offer_launch_at_login(
    choice: bool | None,
    *,
    interactive: bool,
    ask: Callable[[str], str] = input,
    plist_path: Path | None = None,
) -> bool:
    """Install the launch agent if wanted. `choice` is the flag: True, False, or None to ask.

    Returns whether the agent is now set up. A failure here is reported but never fails setup.
    """
    from orca_talkback import service

    plist_path = plist_path or service.PLIST_PATH
    if sys.platform != "darwin" or choice is False:
        return False
    if plist_path.exists():
        print("  Launch at login: already set up (see: orca-talkback service status)")
        return True
    if choice is None:
        if not interactive:
            print("  Launch at login: not set up. To run it in the background: orca-talkback service install")
            return False
        answer = ask("  Start orca-talkback automatically at login? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("  Launch at login: skipped. You can add it later with: orca-talkback service install")
            return False
    try:
        print("  Launch at login: " + service.install([]).replace("\n", "\n  "))
    except service.ServiceError as error:
        print(f"  Launch at login: could not set up ({error}). Try later with: orca-talkback service install")
        return False
    return True


def run_setup(launch_at_login: bool | None = None) -> None:
    from orca_talkback.__main__ import load_kokoro

    print("Setting up orca-talkback")
    changed = install_claude_hook(CLAUDE_SETTINGS, INSTALLED_HOOK)
    state = "installed" if changed else "already installed"
    print(f"  Claude Code voice-mode hook: {state} ({INSTALLED_HOOK})")
    print("  Voice: downloading Kokoro if needed...")
    load_kokoro()
    print("  Voice: ready")
    check_microphone()
    check_plugin()
    background = offer_launch_at_login(launch_at_login, interactive=sys.stdin.isatty())
    if background:
        print("\nDone. orca-talkback runs in the background and starts at every login.")
    else:
        print("\nDone. Start it with: orca-talkback")
