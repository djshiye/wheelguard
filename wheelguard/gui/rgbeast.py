"""RGBeast, the owner's lighting app, drives the GMMK Pro's lighting through its system daemon.

The keyboard keeps its debounce in the same report as its lighting (the properties frame). So that only
one program writes that report, Wheelguard hands debounce, and the profile and layer it switched to, to
RGBeast's daemon while RGBeast manages the keyboard, and writes the frame itself (with the board's
default lighting) only when RGBeast isn't installed or hasn't been turned on for the keyboard.

Plain strings over the system D-Bus (QtDBus): `ListDevices() -> s` (JSON) and
`SetKeyboardOptions(s id, s json) -> s`. The daemon checks every change with polkit; an active local
session is allowed without a prompt. Nothing here touches the keyboard.
"""
import json
from typing import Callable, Optional

SERVICE = "io.github.djshiye.RGBeast1"
PATH = "/io/github/djshiye/RGBeast1"
IFACE = "io.github.djshiye.RGBeast1.Manager"
LIST_TIMEOUT_MS = 3000
WRITE_TIMEOUT_MS = 15000        # a new debounce is three frames with CORE's pauses


def keyboard_id(devices_json, vendor: int, product: int) -> Optional[str]:
    """RGBeast's id for this board, when RGBeast drives it and lighting control is turned on."""
    try:
        devices = json.loads(devices_json) if isinstance(devices_json, str) else []
    except ValueError:
        return None
    want = f"gmmk:{vendor:04x}:{product:04x}"
    for d in devices if isinstance(devices, list) else []:
        if (isinstance(d, dict) and d.get("driver") == "gmmk" and str(d.get("id", "")).startswith(want)
                and not d.get("locked", False)):
            return str(d["id"])
    return None


def options_json(profile: int, layer: int, debounce_ms: int) -> str:
    """Profile and layer are 0-based here, 1-based on the board and in RGBeast."""
    return json.dumps({"profile": profile + 1, "layer": layer + 1, "debounce_ms": int(debounce_ms)},
                      separators=(",", ":"))


class RGBeastClient:
    """Blocking calls are made from Wheelguard's keyboard worker thread; `check` is asynchronous, for
    the UI thread."""

    def __init__(self, bus=None):
        from PySide6.QtDBus import QDBusConnection
        self._bus = bus if bus is not None else QDBusConnection.systemBus()
        self._watchers = set()

    def _message(self, member, args):
        from PySide6.QtDBus import QDBusMessage
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, member)
        msg.setArguments(args)
        msg.setAutoStartService(False)          # never start RGBeast just to ask
        return msg

    def _block(self, member, args, timeout_ms):
        """(value, error text)."""
        from PySide6.QtDBus import QDBus, QDBusMessage
        if not self._bus.isConnected():
            return None, "no system bus"
        reply = self._bus.call(self._message(member, args), QDBus.CallMode.Block, timeout_ms)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None, reply.errorMessage() or "RGBeast didn't answer"
        values = reply.arguments()
        return (values[0] if values else None), ""

    def managed(self, vendor: int, product: int) -> Optional[str]:
        value, _err = self._block("ListDevices", [], LIST_TIMEOUT_MS)
        return keyboard_id(value, vendor, product)

    def set_options(self, dev_id: str, profile: int, layer: int, debounce_ms: int) -> str:
        """"" when RGBeast took them, else why not."""
        _value, err = self._block("SetKeyboardOptions", [dev_id, options_json(profile, layer, debounce_ms)],
                                  WRITE_TIMEOUT_MS)
        return f"RGBeast couldn't change the keyboard: {err}" if err else ""

    def check(self, vendor: int, product: int, done: Callable[[Optional[str]], None]):
        """Ask without blocking; `done(id or None)` runs on the calling (UI) thread."""
        from PySide6.QtDBus import QDBusMessage, QDBusPendingCallWatcher
        if not self._bus.isConnected():
            done(None)
            return
        w = QDBusPendingCallWatcher(self._bus.asyncCall(self._message("ListDevices", []), LIST_TIMEOUT_MS))
        self._watchers.add(w)

        def finished(watcher):
            self._watchers.discard(watcher)
            reply = watcher.reply()
            watcher.deleteLater()
            ok = reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments()
            done(keyboard_id(reply.arguments()[0], vendor, product) if ok else None)

        w.finished.connect(finished)
