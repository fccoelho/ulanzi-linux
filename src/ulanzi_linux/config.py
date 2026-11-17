"""Profiles, pages, keys and actions, persisted as JSON under ~/.config/ulanzi-linux."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import protocol as P

CONFIG_DIR = Path(os.environ.get("ULANZI_CONFIG_DIR", Path.home() / ".config" / "ulanzi-linux"))
PROFILES_DIR = CONFIG_DIR / "profiles"
ICONS_DIR = CONFIG_DIR / "icons"
SETTINGS_FILE = CONFIG_DIR / "settings.json"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class FontModel(BaseModel):
    align: str = "bottom"
    color: str = "FFFFFF"
    family: str = "Source Han Sans SC"
    show_title: bool = True
    size: int = 10
    weight: int = 80

    def to_proto(self) -> P.Font:
        return P.Font(**self.model_dump())


class Action(BaseModel):
    type: str
    settings: dict[str, Any] = Field(default_factory=dict)


class KeyState(BaseModel):
    icon: str | None = None  # file name inside ICONS_DIR
    title: str = ""
    font: FontModel | None = None  # None -> profile default
    icon_design: dict[str, Any] | None = None  # editor parameters, to re-edit later


class Key(BaseModel):
    action: Action | None = None
    long_press: Action | None = None
    states: list[KeyState] = Field(default_factory=lambda: [KeyState()])
    state: int = 0


class Page(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str = "Page"
    keys: dict[str, Key] = Field(default_factory=dict)  # "0".."12"
    parent: str | None = None  # set for folder pages


class Profile(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str = "Default"
    pages: list[Page] = Field(default_factory=lambda: [Page(name="Page 1")])
    folders: list[Page] = Field(default_factory=list)
    default_font: FontModel = Field(default_factory=FontModel)
    info_mode: int = int(P.InfoMode.DIAL_CLOCK)
    info_background: str | None = None
    info_action: Action | None = None

    def all_pages(self) -> list[Page]:
        return self.pages + self.folders

    def page(self, page_id: str) -> Page | None:
        return next((p for p in self.all_pages() if p.id == page_id), None)


class ObsSettings(BaseModel):
    host: str = "localhost"
    port: int = 4455
    password: str = ""


class Settings(BaseModel):
    active_profile: str | None = None
    brightness: int = 80
    sleep_minutes: int = 0  # 0 = never
    twelve_hour: bool = False
    long_press_ms: int = 500
    obs: ObsSettings = Field(default_factory=ObsSettings)


class Store:
    """Simple JSON store; all mutations go through the controller under a lock."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        for d in (PROFILES_DIR, ICONS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.settings = self._load_settings()
        self.profiles: dict[str, Profile] = {}
        for f in sorted(PROFILES_DIR.glob("*.json")):
            try:
                p = Profile.model_validate_json(f.read_text())
                self.profiles[p.id] = p
            except Exception:  # noqa: BLE001
                continue
        if not self.profiles:
            p = Profile(name="Default")
            self.profiles[p.id] = p
            self.save_profile(p)
        if self.settings.active_profile not in self.profiles:
            self.settings.active_profile = next(iter(self.profiles))
            self.save_settings()

    def _load_settings(self) -> Settings:
        if SETTINGS_FILE.exists():
            try:
                return Settings.model_validate_json(SETTINGS_FILE.read_text())
            except Exception:  # noqa: BLE001
                pass
        return Settings()

    def save_settings(self) -> None:
        _atomic_write(SETTINGS_FILE, self.settings.model_dump_json(indent=2))

    def save_profile(self, p: Profile) -> None:
        _atomic_write(PROFILES_DIR / f"{p.id}.json", p.model_dump_json(indent=2))

    def delete_profile(self, pid: str) -> None:
        self.profiles.pop(pid, None)
        (PROFILES_DIR / f"{pid}.json").unlink(missing_ok=True)

    @property
    def active(self) -> Profile:
        return self.profiles[self.settings.active_profile]  # type: ignore[index]

    def export_profile(self, p: Profile) -> dict:
        """Self-contained export including icon images (base64)."""
        import base64

        icons = set()
        for page in p.all_pages():
            for k in page.keys.values():
                icons.update(s.icon for s in k.states if s.icon)
        if p.info_background:
            icons.add(p.info_background)
        return {
            "format": "ulanzi-linux-profile/1",
            "profile": json.loads(p.model_dump_json()),
            "icons": {
                name: base64.b64encode((ICONS_DIR / name).read_bytes()).decode()
                for name in icons
                if (ICONS_DIR / name).exists()
            },
        }

    def import_profile(self, data: dict) -> Profile:
        import base64

        for name, b64 in data.get("icons", {}).items():
            safe = Path(name).name
            (ICONS_DIR / safe).write_bytes(base64.b64decode(b64))
        p = Profile.model_validate(data["profile"])
        p.id = new_id()
        self.profiles[p.id] = p
        self.save_profile(p)
        return p


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
