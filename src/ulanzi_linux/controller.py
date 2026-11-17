"""Glue between the deck, the stored profiles, actions and the web UI."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import actions as A
from . import protocol as P
from .config import Action, Key, KeyState, Page, Profile, Store
from .device import DeviceStatus, UlanziDevice
from .images import load_icon, render_glyph_icon
from .keyboard import Keyboard
from .stats import Stats

log = logging.getLogger(__name__)


class Controller:
    def __init__(self) -> None:
        self.store = Store()
        self.keyboard = Keyboard()
        self.stats = Stats()
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="action")
        self.device = UlanziDevice(self._on_input, self._on_connect, self._on_status)
        self.device.small_window = self._small_window_payload
        self.page_id = self.store.active.pages[0].id
        self.nav_stack: list[str] = []
        self._pressed: dict[int, float] = {}
        self._last_activity = time.monotonic()
        self.sleeping = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listeners: set[asyncio.Queue] = set()
        self._stop = threading.Event()

    # ------------------------------------------------------------ lifecycle
    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.device.start()
        threading.Thread(target=self._sleep_watch, daemon=True, name="sleep-watch").start()

    def stop(self) -> None:
        self._stop.set()
        self.device.stop()
        self.pool.shutdown(wait=False)

    # --------------------------------------------------------------- events
    def emit(self, msg: dict) -> None:
        if not self.loop:
            return

        def put():
            for q in list(self.listeners):
                if q.qsize() < 200:
                    q.put_nowait(msg)

        self.loop.call_soon_threadsafe(put)

    def _on_status(self, status: DeviceStatus) -> None:
        self.emit({"type": "status", "device": status.as_dict()})

    def _on_connect(self) -> None:
        try:
            s = self.store.settings
            self.device.set_brightness(s.brightness).result(5)
            self.push_page()
            self.sleeping = False
            self._last_activity = time.monotonic()
        except Exception as e:  # noqa: BLE001
            log.warning("initial sync failed: %s", e)
        self.emit({"type": "status", "device": self.device.status.as_dict()})

    def _on_input(self, ev: P.InputEvent) -> None:
        self._last_activity = time.monotonic()
        self.emit({"type": "input", "kind": ev.kind, "index": ev.index, "action": ev.action})
        if ev.kind != "button":
            return
        if self.sleeping:
            if ev.action == "release":
                self.wake()
            return
        key = self._key_for(ev.index)
        act = key.action if key else (self.store.active.info_action if ev.index == P.INFO_WINDOW_INDEX else None)
        long_act = key.long_press if key else None
        if ev.action == "press":
            self._pressed[ev.index] = time.monotonic()
            if not long_act and act:
                self.trigger(ev.index, act)
        elif ev.action == "release":
            t0 = self._pressed.pop(ev.index, None)
            if long_act:
                held = (time.monotonic() - t0) * 1000 if t0 else 0
                if held >= self.store.settings.long_press_ms:
                    self.trigger(ev.index, long_act, long=True)
                elif act:
                    self.trigger(ev.index, act)

    # -------------------------------------------------------------- actions
    def _key_for(self, index: int) -> Key | None:
        page = self.store.active.page(self.page_id)
        return page.keys.get(str(index)) if page else None

    def trigger(self, index: int, act: Action, long: bool = False) -> None:
        page_id, profile_id = self.page_id, self.store.active.id

        def job():
            key = self._key_for(index)
            state = key.state if key and not long else 0
            try:
                new_state = A.execute(self, act.model_dump(), state)
            except Exception as e:  # noqa: BLE001
                log.exception("action failed")
                self.emit({"type": "error", "message": f"{A.REGISTRY.get(act.type).name if act.type in A.REGISTRY else act.type}: {e}"})
                return
            if (
                new_state is not None and key is not None and not long
                and self.page_id == page_id and self.store.active.id == profile_id
                and new_state != key.state and len(key.states) > 1
            ):
                with self.store.lock:
                    key.state = new_state % len(key.states)
                    self.store.save_profile(self.store.active)
                self.push_keys([index])
                self.emit({"type": "key_state", "page": page_id, "index": index, "state": key.state})

        self.pool.submit(job)

    def run_steps(self, steps: list[dict]) -> None:
        for step in steps:
            A.execute(self, step, 0)

    # ---------------------------------------------------------- navigation
    def open_page(self, page_id: str, push: bool = False) -> None:
        if not self.store.active.page(page_id) or page_id == self.page_id:
            return
        if push:
            self.nav_stack.append(self.page_id)
        elif not any(p.id == page_id for p in self.store.active.folders):
            self.nav_stack.clear()
        self.page_id = page_id
        self.pool.submit(self._safe_push)
        self.emit({"type": "page", "profile": self.store.active.id, "page": page_id})

    def go_back(self) -> None:
        if self.nav_stack:
            self.page_id = self.nav_stack.pop()
        else:
            page = self.store.active.page(self.page_id)
            self.page_id = page.parent if page and page.parent else self.store.active.pages[0].id
        self.pool.submit(self._safe_push)
        self.emit({"type": "page", "profile": self.store.active.id, "page": self.page_id})

    def step_page(self, delta: int) -> None:
        pages = self.store.active.pages
        ids = [p.id for p in pages]
        i = ids.index(self.page_id) if self.page_id in ids else 0
        self.open_page(ids[(i + delta) % len(ids)])

    def activate_profile(self, profile_id: str) -> None:
        if profile_id not in self.store.profiles:
            return
        with self.store.lock:
            self.store.settings.active_profile = profile_id
            self.store.save_settings()
        self.nav_stack.clear()
        self.page_id = self.store.active.pages[0].id
        self.pool.submit(self._safe_push)
        self.emit({"type": "profile", "profile": profile_id, "page": self.page_id})

    # --------------------------------------------------------- device sync
    def _key_spec(self, index: int, key: Key | None, profile: Profile) -> P.KeySpec:
        if key is None:
            return P.KeySpec(index=index, views=[P.KeyView()])
        views = []
        for st in key.states:
            font = (st.font or profile.default_font).to_proto()
            views.append(P.KeyView(icon_png=load_icon(st.icon), text=st.title, font=font))
        return P.KeySpec(index=index, views=views, state=min(key.state, len(views) - 1))

    def push_page(self) -> None:
        if not self.device.connected:
            return
        profile = self.store.active
        page = profile.page(self.page_id) or profile.pages[0]
        specs = [self._key_spec(i, page.keys.get(str(i)), profile) for i in range(P.BUTTON_COUNT)]
        data = P.build_zip(specs, profile.info_mode, load_icon(profile.info_background))
        self.device.upload_layout(data).result(30)

    def push_keys(self, indexes: list[int]) -> None:
        if not self.device.connected:
            return
        profile = self.store.active
        page = profile.page(self.page_id)
        if not page:
            return
        specs = [self._key_spec(i, page.keys.get(str(i)), profile) for i in indexes]
        self.device.upload_layout(P.build_zip(specs, info_mode=None), partial=True).result(15)

    def refresh_if_visible(self, profile_id: str, page_id: str | None = None) -> None:
        if profile_id != self.store.active.id:
            return
        if self.store.active.page(self.page_id) is None:
            self.page_id = self.store.active.pages[0].id
            self.nav_stack.clear()
        if page_id is None or page_id == self.page_id:
            self.pool.submit(self._safe_push)

    def _safe_push(self) -> None:
        try:
            self.push_page()
        except Exception as e:  # noqa: BLE001
            self.emit({"type": "error", "message": f"device update failed: {e}"})

    def set_brightness(self, pct: int) -> None:
        with self.store.lock:
            self.store.settings.brightness = pct
            self.store.save_settings()
        if self.device.connected and not self.sleeping:
            self.device.set_brightness(pct)
        self.emit({"type": "settings", "settings": self.store.settings.model_dump()})

    def _small_window_payload(self) -> bytes:
        mode = self.store.active.info_mode
        cpu = mem = gpu = 0
        if mode == P.InfoMode.STATS:
            cpu, mem, gpu = self.stats.sample()
        return P.encode_small_window(mode, cpu, mem, gpu, twelve_hour=self.store.settings.twelve_hour)

    # ---------------------------------------------------------------- sleep
    def wake(self) -> None:
        self.sleeping = False
        self._last_activity = time.monotonic()
        if self.device.connected:
            self.device.set_brightness(self.store.settings.brightness)

    def _sleep_watch(self) -> None:
        while not self._stop.wait(5):
            mins = self.store.settings.sleep_minutes
            if mins and not self.sleeping and self.device.connected:
                if time.monotonic() - self._last_activity > mins * 60:
                    self.sleeping = True
                    self.device.set_brightness(0)

    # ------------------------------------------------------------- editing
    def ensure_folder(self, profile: Profile, parent_id: str, key: Key) -> None:
        """When a key gets a Folder action, create its sub-page with a Back key."""
        if not key.action or key.action.type != "folder":
            return
        fid = key.action.settings.get("folder")
        if fid and profile.page(fid):
            return
        folder = Page(name=(key.states[0].title or "Folder"), parent=parent_id)
        back_icon = render_glyph_icon("arrow-left", "#f08c00")
        folder.keys["0"] = Key(action=Action(type="back"), states=[KeyState(icon=back_icon, title="Back")])
        profile.folders.append(folder)
        key.action.settings["folder"] = folder.id

    def prune_folders(self, profile: Profile) -> None:
        while True:
            used = {
                k.action.settings.get("folder")
                for p in profile.all_pages()
                for k in p.keys.values()
                if k.action and k.action.type == "folder"
            }
            keep = [f for f in profile.folders if f.id in used]
            if len(keep) == len(profile.folders):
                return
            profile.folders = keep
