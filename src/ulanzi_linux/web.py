"""FastAPI application: REST API + WebSocket for live device events."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions as A
from . import protocol as P
from .apps import find_icon, list_apps
from .config import ICONS_DIR, Action, FontModel, Key, ObsSettings, Page, Profile, new_id
from .controller import Controller
from .images import render_glyph_icon, store_image

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"

ctl: Controller | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ctl
    ctl = Controller()
    ctl.start(asyncio.get_running_loop())
    yield
    ctl.stop()


app = FastAPI(title="Ulanzi Studio for Linux", lifespan=lifespan)


def C() -> Controller:
    assert ctl is not None
    return ctl


def _profile(pid: str) -> Profile:
    p = C().store.profiles.get(pid)
    if not p:
        raise HTTPException(404, "profile not found")
    return p


def _page(p: Profile, page_id: str) -> Page:
    page = p.page(page_id)
    if not page:
        raise HTTPException(404, "page not found")
    return page


def _save(p: Profile, page_id: str | None = None) -> dict:
    c = C()
    c.prune_folders(p)
    c.store.save_profile(p)
    c.refresh_if_visible(p.id, page_id)
    c.emit({"type": "profile_changed", "profile": p.id})
    return p.model_dump()


# --------------------------------------------------------------------- state
@app.get("/api/state")
def state():
    c = C()
    s = c.store
    return {
        "settings": s.settings.model_dump(),
        "profiles": [{"id": p.id, "name": p.name} for p in s.profiles.values()],
        "active_profile": s.settings.active_profile,
        "current_page": c.page_id,
        "device": c.device.status.as_dict(),
        "keyboard": {"backend": c.keyboard.backend, "error": c.keyboard.error},
        "layout": {"cols": P.COLS, "rows": P.ROWS, "buttons": P.BUTTON_COUNT, "info_index": P.INFO_WINDOW_INDEX},
        "info_modes": [[m.value, m.name] for m in P.InfoMode],
    }


@app.get("/api/actions")
def get_actions():
    return A.catalogue()


# ------------------------------------------------------------------ profiles
@app.get("/api/profiles/{pid}")
def get_profile(pid: str):
    return _profile(pid).model_dump()


@app.post("/api/profiles")
def create_profile(name: str = Body("New Profile", embed=True)):
    c = C()
    with c.store.lock:
        p = Profile(name=name)
        c.store.profiles[p.id] = p
        c.store.save_profile(p)
    c.emit({"type": "profiles"})
    return p.model_dump()


@app.post("/api/profiles/{pid}/duplicate")
def duplicate_profile(pid: str):
    c = C()
    with c.store.lock:
        src = _profile(pid)
        data = src.model_dump()
        data["id"] = new_id()
        data["name"] = f"{src.name} copy"
        p = Profile.model_validate(data)
        c.store.profiles[p.id] = p
        c.store.save_profile(p)
    c.emit({"type": "profiles"})
    return p.model_dump()


class ProfilePatch(BaseModel):
    name: str | None = None
    info_mode: int | None = None
    info_background: str | None = None
    info_action: Action | None = None
    clear_info_action: bool = False
    default_font: FontModel | None = None


@app.patch("/api/profiles/{pid}")
def patch_profile(pid: str, patch: ProfilePatch):
    c = C()
    with c.store.lock:
        p = _profile(pid)
        for f in ("name", "info_mode", "info_background", "info_action", "default_font"):
            v = getattr(patch, f)
            if v is not None:
                setattr(p, f, v)
        if patch.clear_info_action:
            p.info_action = None
        out = _save(p)
    c.emit({"type": "profiles"})
    return out


@app.delete("/api/profiles/{pid}")
def delete_profile(pid: str):
    c = C()
    with c.store.lock:
        if len(c.store.profiles) <= 1:
            raise HTTPException(400, "cannot delete the last profile")
        _profile(pid)
        c.store.delete_profile(pid)
        if c.store.settings.active_profile == pid:
            c.activate_profile(next(iter(c.store.profiles)))
    c.emit({"type": "profiles"})
    return {"ok": True}


@app.post("/api/profiles/{pid}/activate")
def activate_profile(pid: str):
    _profile(pid)
    C().activate_profile(pid)
    return {"ok": True}


@app.get("/api/profiles/{pid}/export")
def export_profile(pid: str):
    p = _profile(pid)
    fname = "".join(ch if ch.isalnum() else "_" for ch in p.name) or "profile"
    return JSONResponse(
        C().store.export_profile(p),
        headers={"Content-Disposition": f'attachment; filename="{fname}.ulanzi.json"'},
    )


@app.post("/api/profiles/import")
async def import_profile(file: UploadFile = File(...)):
    import json

    try:
        data = json.loads(await file.read())
        p = C().store.import_profile(data)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"invalid profile file: {e}") from e
    C().emit({"type": "profiles"})
    return p.model_dump()


# --------------------------------------------------------------------- pages
@app.post("/api/profiles/{pid}/pages")
def add_page(pid: str, name: str | None = Body(None, embed=True)):
    with C().store.lock:
        p = _profile(pid)
        page = Page(name=name or f"Page {len(p.pages) + 1}")
        p.pages.append(page)
        out = _save(p, page_id="__none__")
    return {"profile": out, "page": page.id}


@app.patch("/api/profiles/{pid}/pages/{page_id}")
def rename_page(pid: str, page_id: str, name: str = Body(..., embed=True)):
    with C().store.lock:
        p = _profile(pid)
        _page(p, page_id).name = name
        return _save(p, page_id="__none__")


@app.delete("/api/profiles/{pid}/pages/{page_id}")
def delete_page(pid: str, page_id: str):
    with C().store.lock:
        p = _profile(pid)
        if len(p.pages) <= 1:
            raise HTTPException(400, "a profile needs at least one page")
        p.pages = [pg for pg in p.pages if pg.id != page_id]
        return _save(p)


@app.post("/api/profiles/{pid}/pages/reorder")
def reorder_pages(pid: str, order: list[str] = Body(..., embed=True)):
    with C().store.lock:
        p = _profile(pid)
        by_id = {pg.id: pg for pg in p.pages}
        p.pages = [by_id[i] for i in order if i in by_id] + [pg for pg in p.pages if pg.id not in order]
        return _save(p, page_id="__none__")


# ---------------------------------------------------------------------- keys
@app.put("/api/profiles/{pid}/pages/{page_id}/keys/{index}")
def put_key(pid: str, page_id: str, index: int, key: Key):
    if not 0 <= index < P.BUTTON_COUNT:
        raise HTTPException(400, "invalid key index")
    c = C()
    with c.store.lock:
        p = _profile(pid)
        page = _page(p, page_id)
        c.ensure_folder(p, page_id, key)
        key.state = min(key.state, len(key.states) - 1)
        page.keys[str(index)] = key
        return _save(p, page_id)


@app.delete("/api/profiles/{pid}/pages/{page_id}/keys/{index}")
def delete_key(pid: str, page_id: str, index: int):
    with C().store.lock:
        p = _profile(pid)
        _page(p, page_id).keys.pop(str(index), None)
        return _save(p, page_id)


class MoveReq(BaseModel):
    src: int
    dst: int
    copy_: bool = False
    dst_page: str | None = None


@app.post("/api/profiles/{pid}/pages/{page_id}/keys/move")
def move_key(pid: str, page_id: str, req: MoveReq):
    with C().store.lock:
        p = _profile(pid)
        src_page = _page(p, page_id)
        dst_page = _page(p, req.dst_page or page_id)
        a = src_page.keys.get(str(req.src))
        b = dst_page.keys.get(str(req.dst))
        if a is None:
            return p.model_dump()
        if req.copy_:
            dst_page.keys[str(req.dst)] = Key.model_validate(a.model_dump())
        else:
            dst_page.keys[str(req.dst)] = a
            if b is not None:
                src_page.keys[str(req.src)] = b
            else:
                src_page.keys.pop(str(req.src), None)
        out = _save(p)
        C().refresh_if_visible(p.id)
        return out


# ---------------------------------------------------------------- navigation
@app.post("/api/navigate")
def navigate(page_id: str = Body(..., embed=True)):
    c = C()
    c.open_page(page_id, push=any(f.id == page_id for f in c.store.active.folders))
    return {"ok": True}


@app.post("/api/press/{index}")
def simulate_press(index: int, long: bool = False):
    """Run a key's action as if it was pressed on the deck."""
    c = C()
    if index == P.INFO_WINDOW_INDEX:
        act = c.store.active.info_action
    else:
        key = c._key_for(index)
        act = (key.long_press if long else key.action) if key else None
    if not act:
        raise HTTPException(404, "no action on this key")
    c.trigger(index, act, long=long)
    return {"ok": True}


# --------------------------------------------------------------------- icons
@app.post("/api/icons")
async def upload_icon(file: UploadFile = File(...), kind: str = Form("icon"), fit: str = Form("cover")):
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "image too large")
    try:
        name = store_image(data, "background" if kind == "background" else "icon", fit)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"unsupported image: {e}") from e
    return {"name": name}


@app.post("/api/icons/glyph")
def glyph_icon(name: str = Body(...), bg: str = Body("#3b5bdb"), fg: str = Body("#ffffff")):
    return {"name": render_glyph_icon(name, bg, fg)}


@app.get("/icons/{name}")
def get_icon(name: str):
    path = ICONS_DIR / Path(name).name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "max-age=31536000, immutable"})


@app.get("/api/apps")
def apps():
    return list_apps()


@app.get("/api/apps/icon")
def app_icon(name: str):
    path = find_icon(name)
    if not path:
        raise HTTPException(404)
    media = "image/svg+xml" if path.endswith(".svg") else None
    return FileResponse(path, media_type=media)


# ------------------------------------------------------------------ settings
class SettingsPatch(BaseModel):
    brightness: int | None = None
    sleep_minutes: int | None = None
    twelve_hour: bool | None = None
    long_press_ms: int | None = None
    obs: ObsSettings | None = None


@app.patch("/api/settings")
def patch_settings(patch: SettingsPatch):
    c = C()
    s = c.store.settings
    with c.store.lock:
        for f in ("sleep_minutes", "twelve_hour", "long_press_ms", "obs"):
            v = getattr(patch, f)
            if v is not None:
                setattr(s, f, v)
        c.store.save_settings()
    if patch.brightness is not None:
        c.set_brightness(max(0, min(100, patch.brightness)))
    if patch.sleep_minutes is not None:
        c.wake()
    return s.model_dump()


@app.post("/api/device/reconnect")
def reconnect():
    c = C()
    if c.device.connected:
        c.pool.submit(c._on_connect)
    return {"ok": True}


# ----------------------------------------------------------------- websocket
@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    q: asyncio.Queue = asyncio.Queue()
    c = C()
    c.listeners.add(q)
    try:
        while True:
            msg = await q.get()
            await sock.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        c.listeners.discard(q)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
