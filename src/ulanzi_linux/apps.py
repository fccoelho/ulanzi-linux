"""Discovery of installed desktop applications (.desktop files) and their icons."""

from __future__ import annotations

import configparser
import os
from functools import lru_cache
from pathlib import Path


def _data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    dirs = [home] + [Path(d) for d in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if d]
    for extra in ("/var/lib/flatpak/exports/share", str(Path.home() / ".local/share/flatpak/exports/share"), "/var/lib/snapd/desktop"):
        if Path(extra) not in dirs:
            dirs.append(Path(extra))
    return dirs


@lru_cache(maxsize=1)
def list_apps() -> list[dict]:
    seen: dict[str, dict] = {}
    for base in _data_dirs():
        appdir = base / "applications"
        if not appdir.is_dir():
            continue
        for f in appdir.rglob("*.desktop"):
            app_id = str(f.relative_to(appdir)).replace("/", "-")
            if app_id in seen:
                continue
            cp = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except Exception:  # noqa: BLE001
                continue
            if e.get("Type", "Application") != "Application":
                continue
            if e.get("NoDisplay", "false").lower() == "true" or e.get("Hidden", "false").lower() == "true":
                continue
            seen[app_id] = {
                "id": app_id,
                "name": e.get("Name", app_id),
                "icon": e.get("Icon", ""),
                "exec": e.get("Exec", ""),
                "path": str(f),
            }
    return sorted(seen.values(), key=lambda a: a["name"].lower())


ICON_EXTS = (".svg", ".png", ".xpm")
SIZES = ("scalable", "512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48")


@lru_cache(maxsize=512)
def find_icon(name: str) -> str | None:
    if not name:
        return None
    if name.startswith("/"):
        return name if Path(name).exists() else None
    themes = ["hicolor", "breeze", "Papirus", "Adwaita", "Yaru", "gnome", "oxygen"]
    for base in _data_dirs():
        icons = base / "icons"
        if icons.is_dir():
            for theme in themes + [d.name for d in icons.iterdir() if d.is_dir() and d.name not in themes]:
                tdir = icons / theme
                if not tdir.is_dir():
                    continue
                for size in SIZES:
                    for cat in ("apps", "applications", "places", "devices", "categories", "mimetypes", "actions", "status"):
                        for ext in ICON_EXTS:
                            for p in (tdir / size / cat / f"{name}{ext}", tdir / cat / size / f"{name}{ext}"):
                                if p.exists():
                                    return str(p)
        pix = base / "pixmaps"
        for ext in ICON_EXTS:
            if (pix / f"{name}{ext}").exists():
                return str(pix / f"{name}{ext}")
    return None
