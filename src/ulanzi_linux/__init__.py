"""Ulanzi Studio for Linux: a web app to manage Ulanzi D200 / D200H (U-Studio) decks."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

UDEV_RULES = """\
# Install as /etc/udev/rules.d/70-ulanzi.rules -- the number must be below 73,
# otherwise systemd's 73-seat-late.rules has already applied "uaccess" ACLs.
# Ulanzi D200 / D200H (U-Studio) stream controller: HID access for the logged-in user
SUBSYSTEM=="usb", ATTRS{idVendor}=="2207", ATTRS{idProduct}=="0019", MODE="0660", GROUP="plugdev", TAG+="uaccess"
KERNEL=="hidraw*", ATTRS{idVendor}=="2207", ATTRS{idProduct}=="0019", MODE="0660", GROUP="plugdev", TAG+="uaccess"
# Virtual keyboard used to send hotkeys / text (needed on Wayland)
KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", TAG+="uaccess", OPTIONS+="static_node=uinput"
"""


def main() -> None:
    ap = argparse.ArgumentParser(prog="ulanzi-linux", description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    run = sub.add_parser("serve", help="run the web app (default)")
    for p in (ap, run):
        p.add_argument("--host", default="127.0.0.1")
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--open", action="store_true", help="open the browser")
        p.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("udev-rules", help="print the udev rules to install")
    sub.add_parser("install-service", help="write a systemd --user unit to start on login")
    args = ap.parse_args()

    if args.cmd == "udev-rules":
        print(UDEV_RULES, end="")
        return
    if args.cmd == "install-service":
        install_service()
        return

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.5, webbrowser.open, [f"http://{args.host}:{args.port}/"]).start()

    import uvicorn

    uvicorn.run("ulanzi_linux.web:app", host=args.host, port=args.port, log_level="warning")


def install_service() -> None:
    import shutil
    import sys

    exe = shutil.which("ulanzi-linux") or f"{sys.executable} -m ulanzi_linux"
    unit = Path.home() / ".config/systemd/user/ulanzi-linux.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(f"""[Unit]
Description=Ulanzi Studio for Linux (stream deck manager)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart={exe} serve
Restart=on-failure
RestartSec=3

[Install]
WantedBy=graphical-session.target
""")
    print(f"Wrote {unit}\nEnable it with:\n  systemctl --user daemon-reload\n  systemctl --user enable --now ulanzi-linux")


if __name__ == "__main__":
    main()
