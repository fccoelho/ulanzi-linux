"""Action catalogue (mirrors the Ulanzi Studio action list) and executors.

Each action declares the fields of its property inspector; the web UI builds
its forms from this catalogue, so adding an action here is all that's needed.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .controller import Controller

log = logging.getLogger(__name__)


@dataclass
class ActionDef:
    type: str
    name: str
    category: str
    icon: str  # Font Awesome name used for the default key icon
    color: str  # default key background colour
    fields: list[dict] = field(default_factory=list)
    states: int = 1  # 2 = toggle action with two key states
    state_names: list[str] = field(default_factory=list)
    run: Callable[["Controller", dict, int], int | None] | None = None
    description: str = ""

    def meta(self) -> dict:
        return {
            "type": self.type, "name": self.name, "category": self.category, "icon": self.icon,
            "color": self.color, "fields": self.fields, "states": self.states,
            "state_names": self.state_names, "description": self.description,
        }


REGISTRY: dict[str, ActionDef] = {}


def action(**kw):
    def deco(fn):
        d = ActionDef(run=fn, **kw)
        REGISTRY[d.type] = d
        return fn

    return deco


# ---------------------------------------------------------------- helpers
def spawn(cmd: list[str] | str, shell: bool = False, env: dict | None = None) -> None:
    """Start a detached process that survives the server."""
    full_env = os.environ.copy()
    full_env.update(env or {})
    if shutil.which("systemd-run") and not shell:
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect", *cmd]
    subprocess.Popen(
        cmd, shell=shell, env=full_env, start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def run_quiet(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=10)


def set_clipboard(text: str) -> bool:
    for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, input=text, text=True, timeout=5)
            return True
    for q in ("qdbus6", "qdbus"):
        if shutil.which(q):
            r = run_quiet([q, "org.kde.klipper", "/klipper", "setClipboardContents", text])
            if r.returncode == 0:
                return True
    return False


# ------------------------------------------------------------------ System
@action(type="hotkey", name="Hotkey", category="System", icon="keyboard", color="#3b5bdb",
        description="Send a keyboard shortcut.",
        fields=[{"key": "keys", "label": "Hotkey", "type": "hotkey", "placeholder": "ctrl+shift+t"}])
def _hotkey(ctx, s, st):
    ctx.keyboard.combo(s.get("keys", ""))


@action(type="hotkey_switch", name="Hotkey Switch", category="System", icon="arrow-right-arrow-left",
        color="#3b5bdb", states=2, state_names=["State A", "State B"],
        description="Alternate between two shortcuts on each press.",
        fields=[{"key": "keys_a", "label": "Hotkey (state A)", "type": "hotkey"},
                {"key": "keys_b", "label": "Hotkey (state B)", "type": "hotkey"}])
def _hotkey_switch(ctx, s, st):
    ctx.keyboard.combo(s.get("keys_b" if st else "keys_a", ""))
    return 1 - st


@action(type="open", name="Open", category="System", icon="folder-open", color="#e67700",
        description="Open a file, folder or application path.",
        fields=[{"key": "path", "label": "File / folder / program", "type": "text", "placeholder": "~/Documents"}])
def _open(ctx, s, st):
    p = os.path.expanduser(s.get("path", ""))
    if os.access(p, os.X_OK) and os.path.isfile(p):
        spawn([p])
    else:
        spawn(["xdg-open", p])


@action(type="launch_app", name="Launch App", category="System", icon="rocket", color="#e67700",
        description="Launch an installed application.",
        fields=[{"key": "app", "label": "Application", "type": "app"}])
def _launch(ctx, s, st):
    app = s.get("app", "")
    if app.endswith(".desktop"):
        app_id = app[: -len(".desktop")]
    else:
        app_id = app
    if shutil.which("gtk-launch"):
        spawn(["gtk-launch", app_id])
    elif shutil.which("gio"):
        from .apps import list_apps

        path = next((a["path"] for a in list_apps() if a["id"] == app), None)
        if path:
            spawn(["gio", "launch", path])


@action(type="website", name="Website", category="System", icon="globe", color="#1971c2",
        description="Open a URL in the default browser.",
        fields=[{"key": "url", "label": "URL", "type": "text", "placeholder": "https://"}])
def _website(ctx, s, st):
    url = s.get("url", "").strip()
    if url and "://" not in url:
        url = "https://" + url
    if shutil.which("xdg-open"):
        spawn(["xdg-open", url])
    else:
        webbrowser.open(url)


@action(type="text", name="Text", category="System", icon="font", color="#5f3dc4",
        description="Type or paste a piece of text.",
        fields=[{"key": "text", "label": "Text", "type": "textarea"},
                {"key": "mode", "label": "Input method", "type": "select", "default": "paste",
                 "options": [["paste", "Paste via clipboard (any layout / Unicode)"],
                             ["type", "Type key by key (US layout, ASCII)"]]},
                {"key": "enter", "label": "Press Enter afterwards", "type": "toggle"}])
def _text(ctx, s, st):
    text = s.get("text", "")
    if s.get("mode", "paste") == "paste" and set_clipboard(text):
        time.sleep(0.05)
        ctx.keyboard.combo("ctrl+v")
    else:
        ctx.keyboard.type_text(text)
    if s.get("enter"):
        ctx.keyboard.combo("enter")


@action(type="command", name="Run Command", category="System", icon="terminal", color="#2b8a3e",
        description="Run a shell command.",
        fields=[{"key": "command", "label": "Command", "type": "textarea", "placeholder": "notify-send hello"},
                {"key": "terminal", "label": "Run in a terminal window", "type": "toggle"}])
def _command(ctx, s, st):
    cmd = s.get("command", "")
    if not cmd.strip():
        return
    if s.get("terminal"):
        for term in ("konsole", "gnome-terminal", "xfce4-terminal", "kitty", "alacritty", "xterm"):
            if shutil.which(term):
                hold = f"{cmd}; echo; read -p 'Press Enter to close...'"
                spawn([term, "-e", "bash", "-lc", hold])
                return
    spawn(cmd, shell=True)


@action(type="multimedia", name="Multimedia", category="System", icon="play", color="#c2255c",
        description="Media playback and volume keys.",
        fields=[{"key": "op", "label": "Function", "type": "select", "default": "playpause",
                 "options": [["playpause", "Play / Pause"], ["next", "Next track"], ["prev", "Previous track"],
                             ["stop", "Stop"], ["volup", "Volume up"], ["voldown", "Volume down"],
                             ["mute", "Mute output"]]}])
def _media(ctx, s, st):
    ctx.keyboard.combo(s.get("op", "playpause"))


@action(type="mic_mute", name="Microphone Mute", category="System", icon="microphone", color="#c92a2a",
        states=2, state_names=["Unmuted", "Muted"],
        description="Toggle the default microphone (PipeWire/PulseAudio).")
def _mic(ctx, s, st):
    if shutil.which("wpctl"):
        run_quiet(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "toggle"])
        out = run_quiet(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"]).stdout
        return 1 if "MUTED" in out else 0
    run_quiet(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "toggle"])
    return 1 - st


@action(type="volume", name="Volume", category="System", icon="volume-high", color="#c2255c",
        description="Set or change output volume.",
        fields=[{"key": "op", "label": "Operation", "type": "select", "default": "up",
                 "options": [["up", "Increase"], ["down", "Decrease"], ["set", "Set to"]]},
                {"key": "value", "label": "Amount (%)", "type": "number", "default": 5, "min": 0, "max": 150}])
def _volume(ctx, s, st):
    v = int(s.get("value", 5))
    op = s.get("op", "up")
    arg = f"{v / 100:.2f}" + {"up": "+", "down": "-", "set": ""}[op]
    if shutil.which("wpctl"):
        run_quiet(["wpctl", "set-volume", "-l", "1.5", "@DEFAULT_AUDIO_SINK@", arg])
    else:
        sign = {"up": "+", "down": "-", "set": ""}[op]
        run_quiet(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{sign}{v}%"])


@action(type="lock_screen", name="Lock Screen", category="System", icon="lock", color="#495057",
        description="Lock the desktop session.")
def _lock(ctx, s, st):
    spawn(["loginctl", "lock-session"])


@action(type="screenshot", name="Screenshot", category="System", icon="camera", color="#495057",
        description="Press the Print Screen key.")
def _shot(ctx, s, st):
    ctx.keyboard.combo("print")


# -------------------------------------------------------------- Navigation
@action(type="folder", name="Create Folder", category="Navigation", icon="folder", color="#f08c00",
        description="Open a folder of keys. Key 1 inside the folder returns.",
        fields=[{"key": "folder", "label": "Folder", "type": "folder"}])
def _folder(ctx, s, st):
    if s.get("folder"):
        ctx.open_page(s["folder"], push=True)


@action(type="back", name="Back", category="Navigation", icon="arrow-left", color="#f08c00",
        description="Leave the current folder.")
def _back(ctx, s, st):
    ctx.go_back()


@action(type="page_next", name="Next Page", category="Navigation", icon="angles-right", color="#0c8599",
        description="Go to the next page.")
def _pnext(ctx, s, st):
    ctx.step_page(+1)


@action(type="page_prev", name="Previous Page", category="Navigation", icon="angles-left", color="#0c8599",
        description="Go to the previous page.")
def _pprev(ctx, s, st):
    ctx.step_page(-1)


@action(type="page_goto", name="Go to Page", category="Navigation", icon="file-lines", color="#0c8599",
        description="Jump to a specific page.",
        fields=[{"key": "page", "label": "Page", "type": "page"}])
def _pgoto(ctx, s, st):
    if s.get("page"):
        ctx.open_page(s["page"])


@action(type="profile_switch", name="Switch Profile", category="Navigation", icon="layer-group",
        color="#0c8599", description="Activate another profile.",
        fields=[{"key": "profile", "label": "Profile", "type": "profile"}])
def _profile(ctx, s, st):
    if s.get("profile"):
        ctx.activate_profile(s["profile"])


@action(type="brightness", name="Brightness", category="Navigation", icon="sun", color="#e8590c",
        description="Change the deck's screen brightness.",
        fields=[{"key": "op", "label": "Operation", "type": "select", "default": "up",
                 "options": [["up", "Increase"], ["down", "Decrease"], ["set", "Set to"]]},
                {"key": "value", "label": "Amount (%)", "type": "number", "default": 10, "min": 0, "max": 100}])
def _bright(ctx, s, st):
    v = int(s.get("value", 10))
    cur = ctx.store.settings.brightness
    new = {"up": cur + v, "down": cur - v, "set": v}[s.get("op", "up")]
    ctx.set_brightness(max(5, min(100, new)))


# -------------------------------------------------------------- Multi action
@action(type="multi_action", name="Multi Action", category="Multi Action", icon="list-check",
        color="#6741d9", description="Run several actions in sequence.",
        fields=[{"key": "steps", "label": "Actions", "type": "steps"}])
def _multi(ctx, s, st):
    ctx.run_steps(s.get("steps", []))


@action(type="multi_action_switch", name="Multi Action Switch", category="Multi Action",
        icon="shuffle", color="#6741d9", states=2, state_names=["State A", "State B"],
        description="Two sequences of actions that alternate on each press.",
        fields=[{"key": "steps_a", "label": "Actions (state A)", "type": "steps"},
                {"key": "steps_b", "label": "Actions (state B)", "type": "steps"}])
def _multi_switch(ctx, s, st):
    ctx.run_steps(s.get("steps_b" if st else "steps_a", []))
    return 1 - st


@action(type="delay", name="Delay", category="Multi Action", icon="hourglass-half", color="#6741d9",
        description="Wait (useful inside Multi Action).",
        fields=[{"key": "ms", "label": "Milliseconds", "type": "number", "default": 500, "min": 0, "max": 60000}])
def _delay(ctx, s, st):
    time.sleep(int(s.get("ms", 500)) / 1000)


# ---------------------------------------------------------------------- OBS
def _obs(ctx):
    try:
        import obsws_python as obs
    except ImportError as ex:
        raise RuntimeError("Install the OBS extra: uv sync --extra obs") from ex
    o = ctx.store.settings.obs
    return obs.ReqClient(host=o.host, port=o.port, password=o.password or None, timeout=3)


@action(type="obs_scene", name="Scene", category="OBS Studio", icon="clapperboard", color="#212529",
        description="Switch the program scene.",
        fields=[{"key": "scene", "label": "Scene name", "type": "text"}])
def _obs_scene(ctx, s, st):
    _obs(ctx).set_current_program_scene(s.get("scene", ""))


@action(type="obs_record", name="Record", category="OBS Studio", icon="circle", color="#212529",
        states=2, state_names=["Idle", "Recording"], description="Toggle recording.")
def _obs_rec(ctx, s, st):
    c = _obs(ctx)
    c.toggle_record()
    time.sleep(0.3)
    return 1 if c.get_record_status().output_active else 0


@action(type="obs_stream", name="Stream", category="OBS Studio", icon="tower-broadcast", color="#212529",
        states=2, state_names=["Offline", "Live"], description="Toggle streaming.")
def _obs_stream(ctx, s, st):
    c = _obs(ctx)
    c.toggle_stream()
    time.sleep(0.3)
    return 1 if c.get_stream_status().output_active else 0


@action(type="obs_mute", name="Mute Source", category="OBS Studio", icon="volume-xmark", color="#212529",
        states=2, state_names=["Unmuted", "Muted"], description="Toggle an audio source.",
        fields=[{"key": "source", "label": "Input name", "type": "text", "placeholder": "Mic/Aux"}])
def _obs_mute(ctx, s, st):
    c = _obs(ctx)
    c.toggle_input_mute(s.get("source", ""))
    return 1 if c.get_input_mute(s.get("source", "")).input_muted else 0


@action(type="obs_virtualcam", name="Virtual Camera", category="OBS Studio", icon="video", color="#212529",
        states=2, state_names=["Off", "On"], description="Toggle the virtual camera.")
def _obs_vcam(ctx, s, st):
    c = _obs(ctx)
    c.toggle_virtual_cam()
    return 1 - st


def catalogue() -> list[dict]:
    return [d.meta() for d in REGISTRY.values()]


def execute(ctx: "Controller", act: dict | Any, state: int = 0) -> int | None:
    """Run an action. Returns the new key state for toggle actions."""
    if act is None:
        return None
    t = act["type"] if isinstance(act, dict) else act.type
    s = act.get("settings", {}) if isinstance(act, dict) else act.settings
    d = REGISTRY.get(t)
    if not d or not d.run:
        raise ValueError(f"unknown action type: {t}")
    log.info("run action %s %s", t, shlex.quote(str(s))[:120])
    return d.run(ctx, s or {}, state)
