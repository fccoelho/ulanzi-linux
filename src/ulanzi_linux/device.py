"""HID transport for the Ulanzi deck: a single worker thread owns the handle."""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable

from . import protocol as P

log = logging.getLogger(__name__)

try:
    import hid  # cython-hidapi
except ImportError:  # pragma: no cover
    hid = None

ULANZI_VID = 0x2207
KNOWN_PIDS = {0x0019}  # D200 / D200H (U-Studio); others are matched by product name
ADB_DECOY = (0x18D1, 0xD002)


@dataclass
class DeviceStatus:
    connected: bool = False
    path: str = ""
    product: str = ""
    serial: str = ""
    vid: int = 0
    pid: int = 0
    info: dict | str | None = None
    hint: str = ""
    last_error: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "connected": self.connected,
            "path": self.path,
            "product": self.product,
            "serial": self.serial,
            "vid": f"{self.vid:04x}" if self.vid else "",
            "pid": f"{self.pid:04x}" if self.pid else "",
            "info": self.info,
            "hint": self.hint,
            "last_error": self.last_error,
        }


def _decoy_present() -> bool:
    """True when only the ADB-shaped 18d1:d002 half of the deck enumerated."""
    import glob

    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            vid = open(f"{d}/idVendor").read().strip()
            pid = open(f"{d}/idProduct").read().strip()
            prod = open(f"{d}/product").read().strip().lower() if _exists(f"{d}/product") else ""
        except OSError:
            continue
        if (int(vid, 16), int(pid, 16)) == ADB_DECOY and "ulanzi" in prod:
            return True
    return False


def _exists(p: str) -> bool:
    import os

    return os.path.exists(p)


class UlanziDevice:
    """Owns the HID connection. All I/O happens on the worker thread."""

    KEEPALIVE_S = 1.0

    def __init__(
        self,
        on_input: Callable[[P.InputEvent], None],
        on_connect: Callable[[], None],
        on_status: Callable[[DeviceStatus], None],
    ):
        self.on_input = on_input
        self.on_connect = on_connect
        self.on_status = on_status
        self.status = DeviceStatus()
        self._dev = None
        self._jobs: queue.Queue[tuple[Callable, Future]] = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ulanzi-hid", daemon=True)
        self._ignore_cfg_until = 0.0
        self.small_window: Callable[[], bytes] | None = None  # payload provider for keepalive

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def submit(self, fn: Callable[["UlanziDevice"], object]) -> Future:
        fut: Future = Future()
        self._jobs.put((fn, fut))
        return fut

    # Convenience wrappers (thread-safe; they enqueue work)
    def set_brightness(self, pct: int) -> Future:
        pct = max(0, min(100, int(pct)))
        return self.submit(lambda d: d._send(P.Cmd.OUT_SET_BRIGHTNESS, str(pct).encode()))

    def set_label_style(self, font: P.Font) -> Future:
        import json

        return self.submit(
            lambda d: d._send(P.Cmd.OUT_SET_LABEL_STYLE, json.dumps(font.to_wire()).encode())
        )

    def set_small_window(self, payload: bytes) -> Future:
        return self.submit(lambda d: d._send(P.Cmd.OUT_SET_SMALL_WINDOW, payload))

    def upload_layout(self, zip_data: bytes, partial: bool = False) -> Future:
        def job(d: "UlanziDevice"):
            if partial:
                d._write_many(P.build_file_packets(P.Cmd.OUT_PARTIAL_UPDATE, zip_data))
            else:
                d._barrier()
                d._ignore_cfg_until = time.monotonic() + 5
                d._write_many(P.build_file_packets(P.Cmd.OUT_SET_BUTTONS, zip_data))
                d._barrier()

        return self.submit(job)

    def lock_screen(self, locked: bool) -> Future:
        cmd = P.Cmd.OUT_LOCK_SCREEN if locked else P.Cmd.OUT_UNLOCK_SCREEN
        return self.submit(lambda d: d._send(cmd, b""))

    @property
    def connected(self) -> bool:
        return self._dev is not None

    # ---------------------------------------------------------------- internals
    def _send(self, cmd: int, payload: bytes) -> None:
        self._write(P.build_packet(cmd, payload))

    def _write(self, packet: bytes) -> None:
        if self._dev is None:
            raise RuntimeError("device not connected")
        n = self._dev.write(b"\x00" + packet)
        if n < 0:
            raise OSError("HID write failed")

    def _write_many(self, packets: list[bytes]) -> None:
        for p in packets:
            self._write(p)

    def _barrier(self, timeout: float = 3.0) -> None:
        """GETBASE round-trip: firmware answers 0x0303 when it is ready."""
        self._send(P.Cmd.OUT_GET_BASE, b"")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._dev.read(P.PACKET_SIZE, 50)
            if not data:
                continue
            parsed = P.parse_report(bytes(data))
            if parsed and parsed[0] == P.Cmd.IN_DEVICE_INFO:
                self.status.info = parsed[1]
                return
            self._dispatch(bytes(data))
        log.warning("GETBASE barrier timed out")

    def _dispatch(self, data: bytes) -> None:
        parsed = P.parse_report(data)
        if not parsed:
            return
        cmd, obj = parsed
        if cmd in (P.Cmd.IN_BUTTON, P.Cmd.IN_BUTTON_2):
            try:
                self.on_input(obj)  # type: ignore[arg-type]
            except Exception:
                log.exception("input handler failed")
        elif cmd == P.Cmd.IN_DEVICE_INFO:
            self.status.info = obj
        elif cmd == P.Cmd.IN_CONFIG_REQUEST and time.monotonic() > self._ignore_cfg_until:
            # Firmware UI restarted and lost its layout: push it again.
            log.info("device requested configuration; re-sending layout")
            self._ignore_cfg_until = time.monotonic() + 5
            threading.Thread(target=self.on_connect, daemon=True).start()

    def _find(self) -> dict | None:
        if hid is None:
            self.status.hint = "Python 'hidapi' module is not installed."
            return None
        candidates = []
        for info in hid.enumerate(ULANZI_VID, 0):
            name = (info.get("product_string") or "").lower()
            if info["product_id"] in KNOWN_PIDS or "ulanzi" in name:
                candidates.append(info)
        if not candidates:
            if _decoy_present():
                self.status.hint = (
                    "The deck enumerated only as 18d1:d002 (its unused ADB half). "
                    "Linux failed to enumerate the HID half. Re-plug it through a "
                    "USB 2.0 hub (a known workaround), then it will be detected."
                )
            else:
                self.status.hint = "No Ulanzi deck found. Plug the device in."
            return None
        candidates.sort(key=lambda i: (i.get("interface_number", 0) != 0,))
        return candidates[0]

    def _open(self) -> bool:
        info = self._find()
        if not info:
            return False
        try:
            dev = hid.device()
            dev.open_path(info["path"])
        except (OSError, IOError) as e:
            self.status.hint = (
                f"Found the deck but could not open it ({e}). Install the udev rule: "
                "run: uv run ulanzi-linux udev-rules | sudo tee /etc/udev/rules.d/70-ulanzi.rules, "
                "then: sudo udevadm control --reload-rules && sudo udevadm trigger, and re-plug."
            )
            return False
        self._dev = dev
        self.status = DeviceStatus(
            connected=True,
            path=info["path"].decode(errors="replace") if isinstance(info["path"], bytes) else str(info["path"]),
            product=info.get("product_string") or "Ulanzi Deck",
            serial=info.get("serial_number") or "",
            vid=info["vendor_id"],
            pid=info["product_id"],
        )
        log.info("connected to %s (%s)", self.status.product, self.status.path)
        return True

    def _close(self, err: str = "") -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
        self._dev = None
        self.status.connected = False
        self.status.last_error = err
        self.on_status(self.status)

    def _run(self) -> None:
        next_scan = 0.0
        next_keepalive = 0.0
        while not self._stop.is_set():
            if self._dev is None:
                # fail pending jobs quickly while disconnected
                while not self._jobs.empty():
                    _, fut = self._jobs.get_nowait()
                    fut.set_exception(RuntimeError("device not connected"))
                if time.monotonic() >= next_scan:
                    prev_hint = self.status.hint
                    if self._open():
                        self.on_status(self.status)
                        threading.Thread(target=self.on_connect, daemon=True).start()
                    elif self.status.hint != prev_hint:
                        self.on_status(self.status)
                    next_scan = time.monotonic() + 2.0
                self._stop.wait(0.25)
                continue
            try:
                # run queued jobs
                while True:
                    try:
                        fn, fut = self._jobs.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        fut.set_result(fn(self))
                    except Exception as e:  # noqa: BLE001
                        fut.set_exception(e)
                        if isinstance(e, OSError):
                            raise
                now = time.monotonic()
                if now >= next_keepalive and self.small_window:
                    self._send(P.Cmd.OUT_SET_SMALL_WINDOW, self.small_window())
                    next_keepalive = now + self.KEEPALIVE_S
                data = self._dev.read(P.PACKET_SIZE, 20)
                if data:
                    self._dispatch(bytes(data))
            except (OSError, IOError, ValueError) as e:
                log.warning("device I/O error: %s", e)
                self._close(str(e))
                next_scan = time.monotonic() + 1.0
        self._close()
