"""Whether any Orca window is visible on any screen, from macOS's window list (no permissions needed).

macOS reports every on-screen window front to back, with its owning app and bounds. An Orca
window counts as visible when enough of it is not covered by windows in front of it, so Orca
on a second screen counts while you work in another app, and Orca buried under a browser does not.
"""

import ctypes
import ctypes.util
import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass

ORCA_APP_NAME = "Orca"
NORMAL_WINDOW_LAYER = 0
# Windows smaller than this (tooltips, drag images) are ignored on both sides of the check.
MINIMUM_WINDOW_SIDE = 50
SAMPLE_GRID = 16
MINIMUM_VISIBLE_FRACTION = 0.1


@dataclass(frozen=True)
class Window:
    owner: str
    layer: int
    left: float
    top: float
    width: float
    height: float
    alpha: float = 1.0

    def counts(self) -> bool:
        return (self.layer == NORMAL_WINDOW_LAYER and self.alpha > 0
                and self.width >= MINIMUM_WINDOW_SIDE and self.height >= MINIMUM_WINDOW_SIDE)

    def contains(self, horizontal: float, vertical: float) -> bool:
        return self.left <= horizontal < self.left + self.width and self.top <= vertical < self.top + self.height


def visible_fraction(window: Window, windows_in_front: Sequence[Window]) -> float:
    """Share of sample points across `window` that no window in front of it covers."""
    uncovered = 0
    for column in range(SAMPLE_GRID):
        for row in range(SAMPLE_GRID):
            horizontal = window.left + (column + 0.5) * window.width / SAMPLE_GRID
            vertical = window.top + (row + 0.5) * window.height / SAMPLE_GRID
            if not any(front.contains(horizontal, vertical) for front in windows_in_front):
                uncovered += 1
    return uncovered / (SAMPLE_GRID * SAMPLE_GRID)


def orca_window_visible(windows: Sequence[Window]) -> bool:
    """True when some Orca window, given windows ordered front to back, is at least partly uncovered."""
    normal = [window for window in windows if window.counts()]
    for index, window in enumerate(normal):
        if window.owner == ORCA_APP_NAME and visible_fraction(window, normal[:index]) >= MINIMUM_VISIBLE_FRACTION:
            return True
    return False


# CoreGraphics / CoreFoundation constants.
ON_SCREEN_ONLY = 1 << 0
EXCLUDE_DESKTOP_ELEMENTS = 1 << 4
NULL_WINDOW_ID = 0
UTF8_ENCODING = 0x08000100
DOUBLE_NUMBER_TYPE = 13


@functools.cache
def _load_frameworks() -> tuple[ctypes.CDLL, ctypes.CDLL] | None:
    graphics_path = ctypes.util.find_library("CoreGraphics")
    foundation_path = ctypes.util.find_library("CoreFoundation")
    if not graphics_path or not foundation_path:
        return None
    graphics = ctypes.CDLL(graphics_path)
    foundation = ctypes.CDLL(foundation_path)
    graphics.CGWindowListCopyWindowInfo.restype = ctypes.c_void_p
    graphics.CGWindowListCopyWindowInfo.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    foundation.CFArrayGetCount.restype = ctypes.c_long
    foundation.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    foundation.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    foundation.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    foundation.CFDictionaryGetValue.restype = ctypes.c_void_p
    foundation.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    foundation.CFStringGetCString.restype = ctypes.c_bool
    foundation.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    foundation.CFNumberGetValue.restype = ctypes.c_bool
    foundation.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    return graphics, foundation


def on_screen_windows() -> list[Window] | None:
    """Every on-screen window, front to back, or None when the window list cannot be read."""
    frameworks = _load_frameworks()
    if frameworks is None:
        return None
    graphics, foundation = frameworks
    keys: dict[str, int] = {}

    def key(name: str) -> int:
        if name not in keys:
            keys[name] = foundation.CFStringCreateWithCString(None, name.encode(), UTF8_ENCODING)
        return keys[name]

    def text(dictionary: int, name: str) -> str:
        value = foundation.CFDictionaryGetValue(dictionary, key(name))
        buffer = ctypes.create_string_buffer(512)
        if value and foundation.CFStringGetCString(value, buffer, len(buffer), UTF8_ENCODING):
            return buffer.value.decode(errors="replace")
        return ""

    def number(dictionary: int, name: str, default: float = 0.0) -> float:
        value = foundation.CFDictionaryGetValue(dictionary, key(name))
        result = ctypes.c_double(default)
        if value:
            foundation.CFNumberGetValue(value, DOUBLE_NUMBER_TYPE, ctypes.byref(result))
        return result.value

    window_list = graphics.CGWindowListCopyWindowInfo(ON_SCREEN_ONLY | EXCLUDE_DESKTOP_ELEMENTS, NULL_WINDOW_ID)
    if not window_list:
        return None
    try:
        windows = []
        for index in range(foundation.CFArrayGetCount(window_list)):
            entry = foundation.CFArrayGetValueAtIndex(window_list, index)
            bounds = foundation.CFDictionaryGetValue(entry, key("kCGWindowBounds"))
            if not bounds:
                continue
            windows.append(Window(
                owner=text(entry, "kCGWindowOwnerName"),
                layer=int(number(entry, "kCGWindowLayer")),
                left=number(bounds, "X"),
                top=number(bounds, "Y"),
                width=number(bounds, "Width"),
                height=number(bounds, "Height"),
                alpha=number(entry, "kCGWindowAlpha", 1.0),
            ))
        return windows
    finally:
        foundation.CFRelease(window_list)
        for created in keys.values():
            foundation.CFRelease(created)


def orca_is_visible(read_windows: Callable[[], list[Window] | None] = on_screen_windows) -> bool:
    """True when an Orca window is visible. Unknown counts as visible, so a failed check never silences replies."""
    try:
        windows = read_windows()
    except (OSError, AttributeError, ValueError):
        return True
    return windows is None or orca_window_visible(windows)
