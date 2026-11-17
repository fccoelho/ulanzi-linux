"""Ulanzi D200 / D200H (U-Studio) HID wire protocol.

Reverse-engineered by the community; see redphx/strmdck,
racerxdl/ulanzi-d200-linux and jcalado/companion-surface-d200.

Every report is 1024 bytes::

    [0x7c 0x7c][command: u16 BE][length: u32 LE][payload ... padded to 1016]

Large payloads (the button ZIP) continue in raw 1024-byte chunks without a
header. hidapi needs an extra leading report-ID byte (0x00) on writes.
"""

from __future__ import annotations

import io
import json
import os
import struct
import zipfile
from dataclasses import dataclass
from enum import IntEnum

PACKET_SIZE = 1024
HEADER_SIZE = 8
FIRST_CHUNK = PACKET_SIZE - HEADER_SIZE  # 1016
MAGIC = b"\x7c\x7c"

COLS = 5
ROWS = 3
BUTTON_COUNT = 13  # physical LCD keys
INFO_WINDOW_INDEX = 13  # the wide (2-cell) info display at col 3, row 2
ICON_SIZE = 196
INFO_WIDTH, INFO_HEIGHT = 458, 196


class Cmd(IntEnum):
    OUT_SET_BUTTONS = 0x0001
    OUT_GET_BASE = 0x0003
    OUT_SET_SMALL_WINDOW = 0x0006
    OUT_SET_BRIGHTNESS = 0x000A
    OUT_SET_LABEL_STYLE = 0x000B
    OUT_PARTIAL_UPDATE = 0x000D
    OUT_LOCK_SCREEN = 0x000F
    OUT_UNLOCK_SCREEN = 0x0010

    IN_BUTTON = 0x0101
    IN_BUTTON_2 = 0x0102
    IN_CONFIG_REQUEST = 0x010B
    IN_DEVICE_INFO = 0x0303


class InfoMode(IntEnum):
    """Modes of the wide information window (SmallViewMode)."""

    STATS = 0
    DIAL_CLOCK = 1
    BACKGROUND = 2
    DIGITAL_DATE_TIME_WEEKDAY = 200
    DIGITAL_TIME_WEEKDAY = 201
    DIGITAL_TIME_DATE = 202
    DIGITAL_TIME = 203


def index_to_key(index: int) -> str:
    """Device key index (row-major, 5 columns) -> manifest key "col_row"."""
    return f"{index % COLS}_{index // COLS}"


def build_packet(cmd: int, payload: bytes = b"", length: int | None = None) -> bytes:
    if len(payload) > FIRST_CHUNK:
        raise ValueError(f"payload too large for a single packet: {len(payload)}")
    buf = bytearray(PACKET_SIZE)
    buf[0:2] = MAGIC
    struct.pack_into(">H", buf, 2, cmd)
    struct.pack_into("<I", buf, 4, len(payload) if length is None else length)
    buf[HEADER_SIZE : HEADER_SIZE + len(payload)] = payload
    return bytes(buf)


def build_file_packets(cmd: int, data: bytes) -> list[bytes]:
    packets = [build_packet(cmd, data[:FIRST_CHUNK], length=len(data))]
    for off in range(FIRST_CHUNK, len(data), PACKET_SIZE):
        packets.append(data[off : off + PACKET_SIZE].ljust(PACKET_SIZE, b"\x00"))
    return packets


def payload_is_safe(data: bytes) -> bool:
    """Firmware bug: first byte of every continuation chunk must not be 0x00/0x7c."""
    return all(data[i] not in (0x00, 0x7C) for i in range(FIRST_CHUNK, len(data), PACKET_SIZE))


@dataclass
class Font:
    align: str = "bottom"  # top | middle | bottom
    color: str = "FFFFFF"
    family: str = "Source Han Sans SC"
    show_title: bool = True
    size: int = 10
    weight: int = 80

    def to_wire(self) -> dict:
        return {
            "Align": self.align,
            "Color": int(self.color.lstrip("#") or "FFFFFF", 16),
            "FontName": self.family,
            "ShowTitle": bool(self.show_title),
            "Size": int(self.size),
            "Weight": int(self.weight),
        }


@dataclass
class KeyView:
    """What one key shows for one state."""

    icon_png: bytes | None = None
    text: str = ""
    font: Font | None = None


@dataclass
class KeySpec:
    index: int
    views: list[KeyView]
    state: int = 0


def build_zip(
    keys: list[KeySpec],
    info_mode: int | None = InfoMode.DIAL_CLOCK,
    info_background_png: bytes | None = None,
) -> bytes:
    """Build the layout ZIP (manifest.json + Images/*.png) the firmware expects.

    ``info_mode`` None means "partial update without touching the info window".
    """
    manifest: dict[str, dict] = {}
    images: dict[str, bytes] = {}
    default_font = Font()

    for key in keys:
        params = []
        for s, view in enumerate(key.views or [KeyView()]):
            p: dict = {"Font": (view.font or default_font).to_wire()}
            if view.text:
                p["Text"] = view.text
            if view.icon_png:
                name = f"Images/k{key.index}_s{s}.png"
                images[name] = view.icon_png
                p["Icon"] = name
            params.append(p)
        manifest[index_to_key(key.index)] = {"State": key.state, "ViewParam": params}

    if info_mode is not None:
        entry: dict = {
            "State": 0,
            "SmallViewMode": int(info_mode),
            "ViewParam": [{"Font": default_font.to_wire(), "Text": ""}],
        }
        if int(info_mode) == InfoMode.BACKGROUND and info_background_png:
            images["Images/info_bg.png"] = info_background_png
            entry["ViewParam"][0]["Icon"] = "Images/info_bg.png"
        manifest[index_to_key(INFO_WINDOW_INDEX)] = entry

    manifest_bytes = json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode()
    dummy = b""
    for attempt in range(128):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            if dummy:
                zf.writestr("dummy.txt", dummy)
            zf.writestr("manifest.json", manifest_bytes)
            for name, data in images.items():
                zf.writestr(name, data)
        data = buf.getvalue()
        if payload_is_safe(data):
            return data
        dummy = os.urandom(4 + attempt * 3).hex().encode()
    raise RuntimeError("could not build a firmware-safe ZIP payload")


def encode_small_window(
    mode: int, cpu: int = 0, mem: int = 0, gpu: int = 0, *, twelve_hour: bool = False, now=None
) -> bytes:
    from datetime import datetime

    now = now or datetime.now()
    t = now.strftime("%H:%M:%S")
    date = now.strftime("%Y/%m/%d")
    wd = now.strftime("%a")
    suffix = {
        InfoMode.DIGITAL_DATE_TIME_WEEKDAY: f"{date} {wd}",
        InfoMode.DIGITAL_TIME_WEEKDAY: wd,
        InfoMode.DIGITAL_TIME_DATE: date,
    }.get(mode, "")
    fmt = "12H" if twelve_hour else "24H"
    return f"{int(mode)}|{cpu}|{mem}|{t}|{gpu}|{fmt}|{suffix}".encode()


@dataclass
class InputEvent:
    kind: str  # "button" | "encoder"
    index: int
    action: str  # press | release | left | right
    state: int


def parse_report(report: bytes) -> tuple[int, object] | None:
    """Returns (command, parsed) or None for unknown/garbage reports."""
    if len(report) < HEADER_SIZE or report[:2] != MAGIC:
        return None
    cmd = struct.unpack(">H", report[2:4])[0]
    length = struct.unpack("<I", report[4:8])[0]
    data = report[HEADER_SIZE : HEADER_SIZE + min(length, FIRST_CHUNK)]
    if cmd in (Cmd.IN_BUTTON, Cmd.IN_BUTTON_2):
        if len(data) < 4:
            data = report[HEADER_SIZE : HEADER_SIZE + 4]
        kind = "encoder" if data[2] == 0x02 else "button"
        action = {0x01: "press", 0x02: "left", 0x03: "right"}.get(data[3], "release")
        return cmd, InputEvent(kind=kind, index=data[1], action=action, state=data[0])
    if cmd == Cmd.IN_DEVICE_INFO:
        text = data.split(b"\x00", 1)[0].decode("utf-8", "replace")
        try:
            return cmd, json.loads(text)
        except ValueError:
            return cmd, text
    return cmd, data
