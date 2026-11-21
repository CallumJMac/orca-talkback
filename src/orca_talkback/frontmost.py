"""Which macOS app is in front, via the built-in `lsappinfo` (no extra permissions needed)."""

import re
import subprocess
from collections.abc import Callable

ORCA_APP_NAME = "Orca"
DISPLAY_NAME = re.compile(r'"LSDisplayName"="([^"]*)"')


def frontmost_app_name(run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> str | None:
    """Name of the frontmost app, or None when it cannot be determined (for example off macOS)."""
    try:
        front = run(["lsappinfo", "front"], capture_output=True, text=True, timeout=1, check=False).stdout.strip()
        info = run(["lsappinfo", "info", "-only", "name", front], capture_output=True, text=True, timeout=1, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = DISPLAY_NAME.search(info)
    return match.group(1) if match else None


def orca_is_frontmost(run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> bool:
    """True when Orca is in front. Unknown counts as in front, so a failed check never silences replies."""
    name = frontmost_app_name(run)
    return name is None or name == ORCA_APP_NAME
