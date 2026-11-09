"""One-shot commands for keyboard shortcuts: `wheelguard-settings --toggle-clicklock` and `--next-dpi-stage`.

They talk to the running app over its D-Bus service, so they work with the window closed (the app keeps
running in the panel). Click-Lock can also go straight to the daemon's socket when the app isn't running.
The result is printed and shown as a small desktop notification, because a shortcut has no terminal.
Exit status: 0 done, 1 couldn't.
"""
import socket
from typing import Callable, Optional, Tuple

from . import protocol

TOGGLE_CLICKLOCK = "toggle-clicklock"
NEXT_DPI_STAGE = "next-dpi-stage"
METHODS = {TOGGLE_CLICKLOCK: "ToggleClickLock", NEXT_DPI_STAGE: "NextDpiStage"}
NOT_RUNNING = ("Wheelguard isn't running. Start it from the Applications menu, then try again "
               "(turn on \"Start in tray at login\" on the About page to have it ready after login).")
NO_SERVICE = "The Wheelguard service isn't running. Start it from the Wheelguard window."


def encode_result(ok: bool, text: str) -> str:
    """What the D-Bus methods return: "ok:Click-Lock on" or "error:..."."""
    return ("ok:" if ok else "error:") + text


def decode_result(raw: str) -> Tuple[bool, str]:
    if raw.startswith("ok:"):
        return True, raw[3:]
    return False, raw[6:] if raw.startswith("error:") else raw


def _read_until(sock: socket.socket, wanted: Tuple[str, ...]) -> Optional[dict]:
    lines = protocol.LineBuffer()
    while True:
        try:
            data = sock.recv(65536)
        except OSError:
            return None
        if not data:
            return None
        for line in lines.feed(data):
            msg = protocol.parse(line)
            if msg is not None and msg["type"] in wanted:
                return msg


def toggle_clicklock_direct(path: str, timeout: float = 2.0) -> Tuple[bool, str]:
    """Flip Click-Lock through the daemon's socket (PROTOCOL.md: status, then set)."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
        sock.sendall(protocol.encode({"cmd": "status"}))
        status = _read_until(sock, ("status",))
        cfg = protocol.status_config(status) if status else None
        if cfg is None:
            return False, NO_SERVICE
        on = not cfg["clicklock"]
        sock.sendall(protocol.encode({"cmd": "set", "config": {"clicklock": on}}))
        reply = _read_until(sock, ("ok", "error"))
        if reply is None:
            return False, NO_SERVICE
        if reply["type"] == "error":
            return False, str(reply.get("message", "The service refused the change."))
        return True, "Click-Lock on" if on else "Click-Lock off"
    except OSError:
        return False, NO_SERVICE
    finally:
        sock.close()


def run_action(action: str, remote: Callable[[str], Optional[str]], direct: Callable[[], Tuple[bool, str]]) -> Tuple[bool, str]:
    """Ask the running app; fall back to the daemon for Click-Lock; otherwise say what to do."""
    raw = remote(METHODS[action])
    if raw is not None:
        return decode_result(raw)
    if action == TOGGLE_CLICKLOCK:
        return direct()
    return False, NOT_RUNNING


def main(action: str, socket_path: Optional[str] = None) -> int:
    from PySide6.QtCore import QCoreApplication
    from . import service
    from .backend import default_socket_path
    from .batteryalert import notify
    app = QCoreApplication.instance() or QCoreApplication([])      # noqa: F841 - QtDBus wants one
    ok, text = run_action(action, service.call_remote_text,
                          lambda: toggle_clicklock_direct(socket_path or default_socket_path()))
    print(text, flush=True)
    notify(text)
    return 0 if ok else 1
