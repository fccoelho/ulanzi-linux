"""Synthetic keyboard: a uinput virtual keyboard (works on Wayland and X11),
falling back to xdotool (X11 / XWayland windows only)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

try:
    from evdev import UInput
    from evdev import ecodes as e
except ImportError:  # pragma: no cover
    UInput = None
    e = None

ALIASES = {
    "ctrl": "LEFTCTRL", "control": "LEFTCTRL", "lctrl": "LEFTCTRL", "rctrl": "RIGHTCTRL",
    "shift": "LEFTSHIFT", "lshift": "LEFTSHIFT", "rshift": "RIGHTSHIFT",
    "alt": "LEFTALT", "lalt": "LEFTALT", "ralt": "RIGHTALT", "altgr": "RIGHTALT",
    "super": "LEFTMETA", "meta": "LEFTMETA", "win": "LEFTMETA", "cmd": "LEFTMETA",
    "enter": "ENTER", "return": "ENTER", "esc": "ESC", "escape": "ESC",
    "del": "DELETE", "delete": "DELETE", "ins": "INSERT", "bksp": "BACKSPACE", "backspace": "BACKSPACE",
    "pgup": "PAGEUP", "pageup": "PAGEUP", "pgdn": "PAGEDOWN", "pagedown": "PAGEDOWN",
    "space": "SPACE", "tab": "TAB", "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "home": "HOME", "end": "END", "print": "SYSRQ", "printscreen": "SYSRQ", "prtsc": "SYSRQ",
    "capslock": "CAPSLOCK", "menu": "COMPOSE",
    "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE", ";": "SEMICOLON",
    "'": "APOSTROPHE", "`": "GRAVE", "\\": "BACKSLASH", ",": "COMMA", ".": "DOT", "/": "SLASH",
    "plus": "EQUAL", "minus": "MINUS",
    "playpause": "PLAYPAUSE", "play": "PLAYPAUSE", "next": "NEXTSONG", "prev": "PREVIOUSSONG",
    "previous": "PREVIOUSSONG", "stop": "STOPCD", "volup": "VOLUMEUP", "voldown": "VOLUMEDOWN",
    "mute": "MUTE", "micmute": "MICMUTE", "brightnessup": "BRIGHTNESSUP", "brightnessdown": "BRIGHTNESSDOWN",
    "calc": "CALC", "mail": "MAIL", "www": "WWW", "sleep": "SLEEP",
}

XDO = {
    "LEFTCTRL": "ctrl", "RIGHTCTRL": "ctrl", "LEFTSHIFT": "shift", "RIGHTSHIFT": "shift",
    "LEFTALT": "alt", "RIGHTALT": "ISO_Level3_Shift", "LEFTMETA": "super", "ENTER": "Return",
    "ESC": "Escape", "SPACE": "space", "BACKSPACE": "BackSpace", "DELETE": "Delete",
    "PLAYPAUSE": "XF86AudioPlay", "NEXTSONG": "XF86AudioNext", "PREVIOUSSONG": "XF86AudioPrev",
    "STOPCD": "XF86AudioStop", "VOLUMEUP": "XF86AudioRaiseVolume", "VOLUMEDOWN": "XF86AudioLowerVolume",
    "MUTE": "XF86AudioMute", "MICMUTE": "XF86AudioMicMute", "SYSRQ": "Print", "PAGEUP": "Prior",
    "PAGEDOWN": "Next", "TAB": "Tab", "UP": "Up", "DOWN": "Down", "LEFT": "Left", "RIGHT": "Right",
    "HOME": "Home", "END": "End", "INSERT": "Insert",
}

# US-layout characters for "type" mode
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
    "_": "-", "+": "=", "{": "[", "}": "]", ":": ";", '"': "'", "~": "`", "|": "\\", "<": ",", ">": ".", "?": "/",
}


def parse_combo(combo: str) -> list[str]:
    """'Ctrl+Shift+T' -> ['LEFTCTRL', 'LEFTSHIFT', 'T']"""
    names = []
    parts = [p for p in combo.replace(" ", "").split("+")]
    # handle "ctrl++" (plus key)
    if combo.strip().endswith("++"):
        parts = [p for p in parts if p] + ["plus"]
    for raw in parts:
        if not raw:
            continue
        low = raw.lower()
        name = ALIASES.get(low) or ALIASES.get(raw) or raw.upper()
        names.append(name)
    return names


class Keyboard:
    def __init__(self) -> None:
        self._ui = None
        self._lock = threading.Lock()
        self.error = ""
        if UInput is not None:
            try:
                self._ui = UInput(name="ulanzi-linux virtual keyboard")
                time.sleep(0.2)  # let the compositor pick the device up
            except Exception as ex:  # noqa: BLE001
                self.error = (
                    f"uinput unavailable ({ex}). Install the udev rule from the README "
                    "so keystrokes work on Wayland."
                )
                log.warning(self.error)

    @property
    def backend(self) -> str:
        if self._ui:
            return "uinput"
        if shutil.which("xdotool"):
            return "xdotool"
        return "none"

    def _code(self, name: str) -> int:
        code = getattr(e, f"KEY_{name}", None)
        if code is None:
            raise ValueError(f"unknown key: {name}")
        return code

    def combo(self, combo: str) -> None:
        names = parse_combo(combo)
        if not names:
            return
        if self._ui:
            codes = [self._code(n) for n in names]
            with self._lock:
                for c in codes:
                    self._ui.write(e.EV_KEY, c, 1)
                    self._ui.syn()
                    time.sleep(0.008)
                for c in reversed(codes):
                    self._ui.write(e.EV_KEY, c, 0)
                    self._ui.syn()
                    time.sleep(0.008)
        elif shutil.which("xdotool"):
            subprocess.run(["xdotool", "key", "+".join(XDO.get(n, n.lower()) for n in names)], check=False)
        else:
            raise RuntimeError(self.error or "no keyboard backend available")

    def type_text(self, text: str) -> None:
        if not self._ui:
            if shutil.which("xdotool"):
                subprocess.run(["xdotool", "type", "--delay", "8", text], check=False)
                return
            raise RuntimeError(self.error or "no keyboard backend available")
        for ch in text:
            if ch == "\n":
                self.combo("enter")
            elif ch == " ":
                self.combo("space")
            elif ch == "\t":
                self.combo("tab")
            elif ch.isalpha() and ch.isascii():
                self.combo(f"shift+{ch}" if ch.isupper() else ch)
            elif ch.isdigit():
                self.combo(ch)
            elif ch in SHIFTED:
                self.combo(f"shift+{SHIFTED[ch]}")
            elif ch in ALIASES:
                self.combo(ch)
            else:
                log.debug("cannot type %r with uinput; skipped", ch)
