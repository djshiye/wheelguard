"""One-shot commands for keyboard shortcuts: `wheelguard-settings --toggle-clicklock`, `--toggle-pause` and
`--next-dpi-stage`.

Click-Lock and Pause talk straight to the service's socket: no app needed and no toolkit loaded, so the key
answers at once. The DPI stage lives on the mouse, so that one asks the running app over its D-Bus service
(it works with the window closed; the app keeps running in the panel).
The result is printed and shown as a small desktop notification, because a shortcut has no terminal.
Exit status: 0 done, 1 couldn't.
"""
import os
import socket
from typing import Callable, Optional, Tuple

from . import protocol

TOGGLE_CLICKLOCK = "toggle-clicklock"
NEXT_DPI_STAGE = "next-dpi-stage"
TOGGLE_PAUSE = "toggle-pause"
METHODS = {TOGGLE_CLICKLOCK: "ToggleClickLock", NEXT_DPI_STAGE: "NextDpiStage"}
PAUSED = "Wheelguard paused. It has let go of your mouse."
RESUMED = "Wheelguard resumed"
NOT_RUNNING = ("Wheelguard isn't running. Start it from the Applications menu, then try again "
               "(turn on \"Start in tray at login\" on the Settings page to have it ready after login).")
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


def _ask(path: str, timeout: float, decide):
    """Connect, read the status, send what `decide(status)` returns as (command, done text), wait for the
    reply. Returns (ok, text)."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(path)
        sock.sendall(protocol.encode({"cmd": "status"}))
        status = _read_until(sock, ("status",))
        if status is None or protocol.status_config(status) is None:
            return False, NO_SERVICE
        command, text = decide(status)
        sock.sendall(protocol.encode(command))
        reply = _read_until(sock, ("ok", "error"))
        if reply is None:
            return False, NO_SERVICE
        if reply["type"] == "error":
            return False, str(reply.get("message", "The service refused the change."))
        return True, text
    except OSError:
        return False, NO_SERVICE
    finally:
        sock.close()


def toggle_clicklock_direct(path: str, timeout: float = 2.0) -> Tuple[bool, str]:
    """Flip Click-Lock through the daemon's socket (PROTOCOL.md: status, then set)."""
    def decide(status):
        on = not protocol.status_config(status)["clicklock"]
        return {"cmd": "set", "config": {"clicklock": on}}, "Click-Lock on" if on else "Click-Lock off"
    return _ask(path, timeout, decide)


def toggle_pause_direct(path: str, timeout: float = 2.0) -> Tuple[bool, str]:
    """Pause or resume the service (PROTOCOL.md: status, then pause). Works without the app: this is the way
    out if the mouse ever misbehaves."""
    def decide(status):
        on = status.get("paused") is not True
        return {"cmd": "pause", "on": on}, PAUSED if on else RESUMED
    return _ask(path, timeout, decide)


def run_action(action: str, remote: Callable[[str], Optional[str]], direct: Callable[[], Tuple[bool, str]]) -> Tuple[bool, str]:
    """Click-Lock and Pause go straight to the service (no app needed, nothing heavy to load). The DPI stage
    lives on the mouse, which only the running app talks to: ask it, or say what to do."""
    if action in (TOGGLE_CLICKLOCK, TOGGLE_PAUSE):
        return direct()
    raw = remote(METHODS[action])
    if raw is not None:
        return decode_result(raw)
    return False, NOT_RUNNING


def socket_path(override: Optional[str] = None) -> str:
    """The service's socket (the same rule as backend.default_socket_path, without loading Qt)."""
    if override:
        return override
    env = os.environ.get("WHEELGUARD_SOCKET")
    if env:
        return env
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "wheelguard.sock")


def main(action: str, socket_path_override: Optional[str] = None) -> int:
    from . import notifycli
    path = socket_path(socket_path_override)
    if action == TOGGLE_PAUSE:
        ok, text = toggle_pause_direct(path)
    elif action == TOGGLE_CLICKLOCK:
        ok, text = toggle_clicklock_direct(path)
    else:
        from PySide6.QtCore import QCoreApplication
        from . import service
        app = QCoreApplication.instance() or QCoreApplication([])      # noqa: F841 - QtDBus wants one
        ok, text = run_action(action, service.call_remote_text, lambda: (False, NOT_RUNNING))
    print(text, flush=True)
    notifycli.send(text)
    return 0 if ok else 1
