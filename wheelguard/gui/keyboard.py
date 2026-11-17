"""QObject backend for the Keyboard page: a Glorious GMMK Pro's or GMMK 2's performance settings, profiles, key
remapping and macros (wheelguard/hw/gmmk.py). Lighting lives in RGBeast; while RGBeast manages the
keyboard, debounce (which shares a report with the lighting) goes through RGBeast (rgbeast.py).

The board can't report its settings, so Wheelguard keeps what it last sent in
~/.config/wheelguard/keyboard.json and shows that. Every change is written at once, on one worker
thread, in the same order and with the same pauses as Glorious CORE. A change is only stored once the
board took it.

A board model nobody has confirmed with Wheelguard yet starts locked: the page shows it, but changes
nothing until the owner unlocks it (and so confirms they want to try it on real hardware).
"""
import json
import os
import queue
import select
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..hw import gmbk, gmmk, gmmk_eternal, gmmk_he, gmmk_mock
from ..hw.hidraw import AccessDenied, HidrawDevice
from .appsettings import config_dir
from .rgbeast import RGBeastClient

FILE = "keyboard.json"
POLL_MS = 4000
POLLING_CHOICES = (125, 250, 500, 1000)         # the order the page shows them in (GMMK frames)


def polling_choices(model: Optional[gmmk.KeyboardModel]) -> Tuple[int, ...]:
    """The rates the page offers for a board, slowest first."""
    if model is None:
        return POLLING_CHOICES
    return tuple(sorted(gmmk.PROTOCOLS[model.protocol].polling))

# A job is a list of (frame, pause). An item may also be (step, 0): `step(send)` runs on the worker
# thread, may send frames with `send(frame, pause)`, and returns "" or why it failed.
Job = List[Tuple[object, float]]


class _StepFailed(Exception):
    pass


# ---------------------------------------------------------------- stored state
def _empty_board(model: Optional[gmmk.KeyboardModel] = None) -> dict:
    return {
        "unlocked": False,
        "polling": gmmk.DEFAULT_POLLING,
        "profile": 0,
        "layer": 0,
        "debounce": [model.default_debounce if model else gmmk.DEFAULT_DEBOUNCE] * gmmk.PROFILES,
        "sleep": model.default_sleep if model else gmmk.DEFAULT_SLEEP,   # lights-off timer, minutes (255 never)
        "analog": dict(gmmk_he.DEFAULT_ANALOG),  # GMMK 3 HE only: actuation point and rapid trigger
        "keys": [[{} for _ in range(gmmk.LAYERS)] for _ in range(gmmk.PROFILES)],
    }


def _clean_board(raw, model: gmmk.KeyboardModel) -> dict:
    b = _empty_board(model)
    if not isinstance(raw, dict):
        return b
    b["unlocked"] = raw.get("unlocked") is True
    if raw.get("polling") in polling_choices(model):
        b["polling"] = raw["polling"]
    analog = raw.get("analog")
    if isinstance(analog, dict):
        try:
            gmmk_he.check_analog(analog)
            b["analog"] = {k: analog[k] for k in gmmk_he.DEFAULT_ANALOG}
        except (ValueError, KeyError):
            pass
    for k, n in (("profile", gmmk.PROFILES), ("layer", gmmk.LAYERS)):
        if isinstance(raw.get(k), int) and 0 <= raw[k] < n:
            b[k] = raw[k]
    deb = raw.get("debounce")
    if (model.protocol != "gmbk" and isinstance(deb, list) and len(deb) == gmmk.PROFILES
            and all(d in gmmk.DEBOUNCE_MS for d in deb)):
        b["debounce"] = list(deb)
    if raw.get("sleep") in gmmk.SLEEP_MINUTES:
        b["sleep"] = raw["sleep"]
    keys = raw.get("keys")
    index = gmmk.KEY_INDEX[model.keymap]
    locked = gmmk.locked_keys(model)
    if isinstance(keys, list) and len(keys) == gmmk.PROFILES:
        for p in range(gmmk.PROFILES):
            if not isinstance(keys[p], list) or len(keys[p]) != gmmk.LAYERS:
                continue
            for layer in range(gmmk.LAYERS):
                layer_keys = keys[p][layer]
                if isinstance(layer_keys, dict):
                    b["keys"][p][layer] = {k: v for k, v in layer_keys.items()
                                           if k in index and k not in locked and isinstance(v, dict)
                                           and v.get("kind") != "default"}
    return b


def _clean_macros(raw) -> Dict[int, dict]:
    out: Dict[int, dict] = {}
    if not isinstance(raw, list):
        return out
    for m in raw:
        if not isinstance(m, dict):
            continue
        slot = m.get("slot")
        if not isinstance(slot, int) or not 1 <= slot <= gmmk.MAX_MACROS or slot in out:
            continue
        events = m.get("events")
        if not isinstance(events, list) or gmmk.macro_problem(events):
            continue
        name = str(m.get("name") or f"Macro {slot}")[:40]
        mode = m.get("mode") if m.get("mode") in gmmk.MACRO_MODES else "once"
        out[slot] = {"slot": slot, "name": name, "mode": mode,
                     "events": [{"type": e["type"], "code": e["code"], "delay": e["delay"]} for e in events]}
    return out


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.raw: dict = {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.raw = data
        except (OSError, ValueError):
            pass
        self.macros = _clean_macros(self.raw.get("macros"))

    def board(self, model: gmmk.KeyboardModel) -> dict:
        boards = self.raw.get("boards") if isinstance(self.raw.get("boards"), dict) else {}
        return _clean_board(boards.get(model.key), model)

    def known(self) -> bool:
        return isinstance(self.raw.get("boards"), dict) and bool(self.raw["boards"])

    def save(self, model: Optional[gmmk.KeyboardModel], board: Optional[dict]) -> bool:
        data = dict(self.raw)
        boards = dict(data.get("boards") if isinstance(data.get("boards"), dict) else {})
        if model is not None and board is not None:
            boards[model.key] = board
        data["boards"] = boards
        data["macros"] = [self.macros[s] for s in sorted(self.macros)]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".keyboard-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=1)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.path)
            except OSError:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            return False
        self.raw = data
        return True


# ---------------------------------------------------------------- the worker
class _Worker:
    """One thread that owns the board's command interface. Jobs are lists of (frame, delay)."""

    def __init__(self, sleep: Callable[[float], None], opener):
        self._sleep = sleep
        self._opener = opener
        self._q: "queue.Queue" = queue.Queue()
        self._t: Optional[threading.Thread] = None

    def submit(self, board: gmmk.Board, job: Job, done: Callable[[str], None]):
        if self._t is None:
            self._t = threading.Thread(target=self._run, name="wheelguard-keyboard", daemon=True)
            self._t.start()
        self._q.put((board, job, done))

    def _run(self):
        while True:
            item = self._q.get()
            if item is None:
                return
            board, job, done = item
            error = ""
            opened: List[gmmk.Transport] = []

            def send(frame, delay, opened=opened, board=board):   # bound now: one job per loop
                if not opened:
                    opened.append(gmmk.Transport(board, self._sleep, self._opener))
                opened[0].send(frame, delay)

            try:
                try:
                    for frame, delay in job:
                        if callable(frame):
                            why = frame(send)
                            if why:
                                raise _StepFailed(why)
                        else:
                            send(frame, delay)
                finally:
                    if opened:
                        opened[0].close()
            except _StepFailed as e:
                error = str(e)
            except AccessDenied:
                error = "Wheelguard isn't allowed to change this keyboard's settings."
            except ValueError as e:
                error = str(e)
            except OSError as e:
                error = "The keyboard was unplugged." if e.errno == 19 else "The keyboard didn't take the change."
            done(error)

    def stop(self, wait_s: float = 20.0):
        if self._t is not None:
            self._q.put(None)
            self._t.join(wait_s)


class _Events:
    """Reads the board's event interface (profile/layer switched with Fn on the board itself)."""

    def __init__(self, path: str, on_profile: Callable[[int, int], None], parse=None):
        self._stop = threading.Event()
        self._path = path
        self._on_profile = on_profile
        self._parse = parse or self.parse
        self._t = threading.Thread(target=self._run, name="wheelguard-keyboard-events", daemon=True)
        self._t.start()

    @staticmethod
    def parse(data: bytes) -> Optional[Tuple[int, int]]:
        """(profile, layer), 0-based, from a "profile changed" event, else None."""
        if len(data) >= 4 and data[0] == 4 and data[1] == 241:
            p, layer = data[2] - 1, data[3] - 1
            if 0 <= p < gmmk.PROFILES and 0 <= layer < gmmk.LAYERS:
                return p, layer
        return None

    def _run(self):
        try:
            fd = os.open(self._path, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
        except OSError:
            return
        try:
            while not self._stop.is_set():
                r, _w, _x = select.select([fd], [], [], 0.5)
                if not r:
                    continue
                try:
                    data = os.read(fd, 64)
                except BlockingIOError:
                    continue
                except OSError:
                    return
                hit = self._parse(data)
                if hit is not None:
                    self._on_profile(*hit)
        finally:
            os.close(fd)

    def stop(self):
        self._stop.set()


def macro_text(events, limit: int = 6) -> str:
    """ "Ctrl+C, Ctrl+V": each key with the modifiers held while it went down."""
    held: List[int] = []
    parts: List[str] = []
    short = {224: "Ctrl", 228: "Ctrl", 225: "Shift", 229: "Shift", 226: "Alt", 230: "Alt", 227: "Super", 231: "Super"}
    for e in events:
        code = e["code"]
        if code in gmmk.MODIFIER_USAGES:
            if e["type"] == "press":
                held.append(code)
            elif code in held:
                held.remove(code)
            continue
        if e["type"] == "press":
            mods = []
            for h in held:
                if short[h] not in mods:
                    mods.append(short[h])
            parts.append("+".join(mods + [gmmk.USAGE_NAMES.get(code, "?")]))
    if not parts:       # only modifiers
        parts = [short[e["code"]] for e in events if e["type"] == "press"]
    text = ", ".join(parts[:limit])
    return text + (" …" if len(parts) > limit else "")


# ---------------------------------------------------------------- the controller
class KeyboardController(QObject):
    changed = Signal()              # anything the page shows
    keysChanged = Signal()
    macrosChanged = Signal()
    _done = Signal(str, object)     # worker -> UI thread: error ("" = ok), on-success callback
    _event = Signal(int, int)

    def __init__(self, parent=None, store_path: Optional[Path] = None, discover=None, opener=None,
                 sleep=None, rgbeast=None, poll: bool = True,
                 can_open: Optional[Callable[[str], bool]] = None):
        super().__init__(parent)
        mock = gmmk_mock.mock_mode()
        if mock and store_path is None:
            store_path = Path(tempfile.mkdtemp(prefix="wheelguard-kb-mock-")) / FILE
        self._discover = discover or ((lambda: gmmk_mock.boards(mock)) if mock else gmmk.discover)
        opener = opener or (gmmk_mock.FakeBoardDevice if mock else HidrawDevice)
        self._worker = _Worker(sleep or ((lambda s: time.sleep(s * 0.1)) if mock else time.sleep), opener)
        # RGBeast's daemon: None in mock runs and when Qt has no D-Bus.
        if rgbeast is None and not mock:
            try:
                rgbeast = RGBeastClient()
            except ImportError:
                rgbeast = None
        self._rgbeast = rgbeast
        self._rgb_id: Optional[str] = None        # RGBeast's id for the board while it manages it
        self._store = Store(store_path or config_dir() / FILE)
        self._board_hw: Optional[gmmk.Board] = None
        self._model: Optional[gmmk.KeyboardModel] = None
        self._b: dict = _empty_board()
        self._busy = 0
        self._message = ""
        self._message_ok = False
        if can_open is None:
            can_open = ((lambda _p: mock != "denied") if mock
                        else (lambda p: os.access(p, os.R_OK | os.W_OK)))
        self._can_open = can_open
        self._events: Optional[_Events] = None
        self._done.connect(self._finish)
        self._event.connect(self._board_switched)
        self.rescan()
        self._timer = None
        if poll:
            self._timer = QTimer(self)
            self._timer.setInterval(POLL_MS)
            self._timer.timeout.connect(self.rescan)
            self._timer.start()

    # ---- discovery
    @Slot()
    def rescan(self):
        try:
            boards = self._discover()
        except OSError:
            boards = []
        hw = boards[0] if boards else None
        self._check_rgbeast(hw)
        if hw == self._board_hw:
            return
        self._board_hw = hw
        if self._events is not None:
            self._events.stop()
            self._events = None
        if hw is not None:
            if hw.model != self._model:
                self._model = hw.model
                self._b = self._store.board(hw.model)
            if hw.event_path and not hw.qmk:
                parse = gmmk_he.parse_event if hw.model.protocol == "he" else _Events.parse
                self._events = _Events(hw.event_path, lambda p, layer: self._event.emit(p, layer), parse)
            if not self._store.known():
                self._store.save(self._model, self._b)        # remembered: the sidebar keeps the page
        self._set_message("")
        self.changed.emit()
        self.keysChanged.emit()

    def _check_rgbeast(self, hw: Optional[gmmk.Board]):
        if hw is None or hw.qmk or self._rgbeast is None:
            self._rgbeast_answer(None)
            return
        self._rgbeast.check(hw.model.vendor, hw.pid, self._rgbeast_answer)

    def _rgbeast_answer(self, dev_id: Optional[str]):
        if dev_id != self._rgb_id:
            self._rgb_id = dev_id
            if dev_id and self._board_hw is not None and self._b is not None and not self.locked:
                # RGBeast just took the keyboard on: give it the debounce, profile and layer we set,
                # so its lighting frames keep them.
                self._send([(self._tell_rgbeast(self._b["profile"], self._b["layer"]), 0)], lambda: None)
            self.changed.emit()

    def _tell_rgbeast(self, profile: int, layer: int) -> Callable:
        """A job step: RGBeast notes the profile and layer Wheelguard switched to (and that profile's
        debounce). Never fails the job: the board has already switched."""
        rgb, pid, ms = self._rgbeast, self._board_hw.pid, self._b["debounce"][profile]
        vid = self._board_hw.model.vendor

        def step(_send):
            dev = rgb.managed(vid, pid) if rgb is not None else None
            if dev:
                rgb.set_options(dev, profile, layer, ms)
            return ""
        return step

    @Slot(int, int)
    def _board_switched(self, profile: int, layer: int):
        if (profile, layer) != (self._b["profile"], self._b["layer"]):
            self._b["profile"], self._b["layer"] = profile, layer
            self._store.save(self._model, self._b)
            if self._he() and self._state() == "ready" and not self.locked:
                # As CORE does: the analog settings are applied again on every switch.
                self._send([(gmmk_he.magnet_mode_frame(), gmmk_he.DELAY),
                            (gmmk_he.actuation_frame(profile, layer, self._b["analog"]), gmmk_he.DELAY)], lambda: None)
            self.changed.emit()
            self.keysChanged.emit()

    def shutdown(self):
        if self._timer is not None:
            self._timer.stop()
        if self._events is not None:
            self._events.stop()
        self._worker.stop()

    # ---- state for the page
    def _state(self) -> str:
        hw = self._board_hw
        if hw is None:
            return "none"
        if hw.qmk:
            return "qmk"
        if not hw.path:
            return "unknown"
        if not gmmk.SHORT_LEN <= hw.report_len <= 1024:
            return "odd"
        if not self._can_open(hw.path):
            return "noaccess"
        return "ready"

    @Property(str, notify=changed)
    def state(self):
        return self._state()

    @Property(bool, notify=changed)
    def present(self):
        return self._board_hw is not None

    @Property(bool, notify=changed)
    def known(self):
        """Show the page in the sidebar: a board is here, or has been here before."""
        return self._board_hw is not None or self._store.known()

    @Property(str, notify=changed)
    def name(self):
        return self._model.name if self._model else "Glorious keyboard"

    @Property(str, notify=changed)
    def shortName(self):
        """"GMMK Pro", "GMMK 2 65%": for sentences."""
        if self._model is None:
            return "keyboard"
        return self._model.name.replace("Glorious ", "").replace(" (ISO)", "")

    @Property(bool, notify=changed)
    def hasKnob(self):
        return self._model is not None and "Knob" in gmmk.KEY_INDEX[self._model.keymap]

    @Property("QVariantList", notify=changed)
    def layoutSize(self):
        """[width in key units, rows, F-key row set apart] for drawing the board."""
        w, rows, gap = gmmk.layout_size(self._model) if self._model else (16.0, 6, True)
        return [w, rows, gap]

    @Property(str, notify=changed)
    def layoutName(self):
        if self._model is None or self._model.form == "numpad":
            return ""
        return self._model.layout.upper()

    @Property(str, notify=changed)
    def firmware(self):
        return self._board_hw.firmware if self._board_hw else ""

    @Property(bool, notify=changed)
    def confirmed(self):
        return bool(self._model and self._model.confirmed)

    @Property(bool, notify=changed)
    def locked(self):
        return not (self._model and (self._model.confirmed or self._b["unlocked"]))

    @Property(bool, notify=changed)
    def canEdit(self):
        return self._state() == "ready" and not self.locked and self._busy == 0

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy > 0

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def messageOk(self):
        return self._message_ok

    @Property(int, notify=changed)
    def pollingIndex(self):
        return polling_choices(self._model).index(self._b["polling"])

    @Property("QVariantList", notify=changed)
    def pollingChoices(self):
        """[{"label": "1000 Hz", "sub": "1 ms"}, ...], slowest first."""
        out = []
        for hz in polling_choices(self._model):
            ms = 1000 / hz
            out.append({"label": f"{hz // 1000}K Hz" if hz >= 2000 else f"{hz} Hz",
                        "sub": f"{ms:g} ms" if ms >= 1 else f"{ms:.3g} ms"})
        return out

    @Property(bool, notify=changed)
    def hasDebounce(self):
        # The HE's magnetic keys don't bounce; the GMBK's debounce unit isn't known, so it keeps its own.
        return self._protocol() in ("gmmk", "eternal")

    @Property(bool, notify=changed)
    def hasAnalog(self):
        """Hall-effect keys: actuation point and rapid trigger."""
        return bool(self._model and self._model.protocol == "he")

    @Property(float, notify=changed)
    def actuation(self):
        return float(self._b["analog"]["actuation"])

    @Property(bool, notify=changed)
    def rapidTrigger(self):
        return bool(self._b["analog"]["rapid"])

    @Property(float, notify=changed)
    def sensitivity(self):
        return float(self._b["analog"]["sensitivity"])

    @Slot(float, bool, float)
    def setAnalog(self, actuation: float, rapid: bool, sensitivity: float):
        """GMMK 3 HE: every key's actuation point and rapid trigger (mm), for the active profile and layer, the
        way CORE applies them. Wheelguard re-applies them whenever the profile or layer changes."""
        if not self.hasAnalog:
            return
        a = {"actuation": round(actuation, 1), "rapid": bool(rapid), "sensitivity": round(sensitivity, 1)}
        try:
            gmmk_he.check_analog(a)
        except ValueError as e:
            self._set_message(str(e))
            return
        if a == self._b["analog"]:
            return

        def ok():
            self._b["analog"] = a
        self._send(gmmk_he.analog_job(self._b["profile"], self._b["layer"], a), ok)

    @Property(bool, notify=changed)
    def sharesLighting(self):
        """Debounce and standby ride in the lighting frame (GMMK Pro, GMMK 2, GMMK 3)."""
        return self._protocol() == "gmmk"

    @Property(bool, notify=changed)
    def followsBoard(self):
        """The board reports profile and layer switches made on it."""
        return bool(self._board_hw and self._board_hw.event_path)

    @Property(bool, notify=changed)
    def layerSwitches(self):
        """Picking a layer switches the board to it (the Eternal has no command for that)."""
        return self._protocol() != "eternal"

    @Property(int, notify=changed)
    def debounceDefault(self):
        return gmmk.DEBOUNCE_MS.index(self._model.default_debounce) if self.hasDebounce and self._model else 2

    @Property(int, notify=changed)
    def sleepDefault(self):
        return gmmk.SLEEP_MINUTES.index(self._model.default_sleep) if self._model else 1

    def _he(self) -> bool:
        return self._protocol() == "he"

    def _protocol(self) -> str:
        return self._model.protocol if self._model is not None else "gmmk"

    def _gmbk_settings(self, polling: int, minutes: int) -> Job:
        """GMBK: polling and standby travel together, in each profile's settings frame."""
        return [(gmbk.settings_frame(p, polling, minutes), gmbk.DELAY) for p in range(gmmk.PROFILES)]

    @Property(bool, notify=changed)
    def hasMacros(self):
        return self._model is None or self._model.macros

    @Property(bool, notify=changed)
    def hasSleep(self):
        return bool(self._model and self._model.sleep)

    @Property(int, notify=changed)
    def sleepIndex(self):
        return gmmk.SLEEP_MINUTES.index(self._b["sleep"])

    @Property(int, notify=changed)
    def debounceIndex(self):
        return gmmk.DEBOUNCE_MS.index(self._b["debounce"][self._b["profile"]])

    @Property(int, notify=changed)
    def profile(self):
        return self._b["profile"]

    @Property(int, notify=changed)
    def layer(self):
        return self._b["layer"]

    @Property(bool, notify=changed)
    def rgbeastSupports(self):
        """RGBeast can drive this board's lighting (so far the GMMK Pro only)."""
        return bool(self._model and self._model.form == "pro")

    @Property(bool, notify=changed)
    def rgbeastLighting(self):
        """RGBeast manages the keyboard's lighting, so debounce goes through it."""
        return self._rgb_id is not None

    def _layer_keys(self) -> Dict[str, dict]:
        return self._b["keys"][self._b["profile"]][self._b["layer"]]

    def _macro_names(self) -> Dict[int, str]:
        return {s: m["name"] for s, m in self._store.macros.items()}

    @Property("QVariantList", notify=keysChanged)
    def keys(self):
        if self._model is None:
            return []
        bound = self._layer_keys()
        names = self._macro_names()
        locked = gmmk.locked_keys(self._model)
        out = []
        for k in gmmk.layout(self._model):
            kid = "Enter" if k["id"] == "EnterLower" else k["id"]
            b = bound.get(kid)
            out.append({"id": kid, "piece": k["id"], "label": gmmk.key_label(k["id"]), "name": gmmk.key_name(kid),
                        "x": k["x"], "y": k["y"], "w": k["w"], "h": k["h"],
                        "action": gmmk.binding_text(b, names), "remapped": bool(b),
                        "locked": kid in locked})
        return out

    @Property(int, notify=keysChanged)
    def remappedCount(self):
        return len(self._layer_keys())

    @Slot(str, result="QVariant")
    def binding(self, key: str):
        return dict(self._layer_keys().get(key) or {"kind": "default"})

    # ---- catalogues for the key picker
    @Slot(int, result=int)
    def usageFromScan(self, native_scan_code: int) -> int:
        return gmmk.usage_from_scancode(native_scan_code)

    @Slot(int, result=str)
    def usageName(self, usage: int) -> str:
        return gmmk.USAGE_NAMES.get(usage, "")

    @Property("QVariantList", constant=True)
    def pickerKeys(self):
        return [{"code": code, "name": gmmk.USAGE_NAMES[code]} for _k, code in gmmk.PICKER_KEYS]

    @Property("QVariantList", constant=True)
    def mediaActions(self):
        order = (1, 7, 6, 5, 2, 3, 4, 0)
        return [{"code": c, "name": gmmk.MEDIA[c]} for c in order]

    @Property("QVariantList", constant=True)
    def mouseActions(self):
        return [{"code": c, "name": n} for c, n in gmmk.MOUSE.items()]

    @Property("QVariantList", notify=changed)
    def functionActions(self):
        codes = (2, 1) if self._protocol() == "eternal" else (2, 1, 4, 3)     # the Eternal has no layer keys
        return [{"code": c, "name": gmmk.FUNCTIONS[c]} for c in codes]

    @Property("QVariantList", notify=macrosChanged)
    def macros(self):
        out = []
        for slot in sorted(self._store.macros):
            m = self._store.macros[slot]
            presses = [e for e in m["events"] if e["type"] == "press"]
            text = macro_text(m["events"])
            out.append({"slot": slot, "name": m["name"], "mode": m["mode"], "steps": len(presses),
                        "text": text, "used": self._macro_used(slot)})
        return out

    @Property(bool, notify=macrosChanged)
    def macrosFull(self):
        return len(self._store.macros) >= gmmk.MAX_MACROS

    # ---- writing
    def _set_message(self, text: str, ok: bool = False):
        if (text, ok) != (self._message, self._message_ok):
            self._message, self._message_ok = text, ok
            self.changed.emit()

    def _send(self, job: Job, on_ok: Callable[[], None], ok_text: str = ""):
        if self._state() != "ready" or self.locked or self._board_hw is None:
            return
        self._busy += 1
        self._set_message("")
        self.changed.emit()
        self._worker.submit(self._board_hw, job, lambda err: self._done.emit(err, (on_ok, ok_text)))

    @Slot(str, object)
    def _finish(self, error: str, cb):
        on_ok, ok_text = cb
        self._busy = max(0, self._busy - 1)
        if error:
            self._set_message(error)
        else:
            on_ok()
            self._store.save(self._model, self._b)
            if ok_text:
                self._set_message(ok_text, True)
        self.changed.emit()
        self.keysChanged.emit()
        self.macrosChanged.emit()

    @Slot()
    def unlock(self):
        if self._model is None:
            return
        self._b["unlocked"] = True
        self._store.save(self._model, self._b)
        self.changed.emit()

    @Slot()
    def lock(self):
        if self._model is None:
            return
        self._b["unlocked"] = False
        self._store.save(self._model, self._b)
        self.changed.emit()

    @Slot(int)
    def setPolling(self, index: int):
        choices = polling_choices(self._model)
        if not 0 <= index < len(choices):
            return
        hz = choices[index]
        if hz == self._b["polling"]:
            return

        def ok():
            self._b["polling"] = hz
        if self._protocol() == "gmbk":
            self._send(self._gmbk_settings(hz, self._b["sleep"]), ok)
            return
        if self._protocol() == "eternal":
            self._send([(gmmk_eternal.polling_frame(hz), gmmk_eternal.DELAY)], ok)
            return
        if self._he():
            # The HE keeps a rate per profile: give all three the same one.
            self._send([(gmmk_he.polling_frame(p, hz), gmmk_he.DELAY) for p in range(gmmk.PROFILES)], ok)
            return
        if self._model is not None and self._model.polling_in_properties:
            # GMMK 3 Pro Wireless: the rate rides in the active profile and layer's properties frame.
            p, layer = self._b["profile"], self._b["layer"]
            frame = gmmk.properties_frame(p, layer, self._b["debounce"][p], sleep=self._sleep_byte(), polling=hz)
            self._send([(frame, gmmk.DELAY_LONG)], ok)
            return
        self._send([(gmmk.polling_frame(hz, self._sleep_byte()), gmmk.DELAY_LONG)], ok)

    def _sleep_byte(self) -> Optional[int]:
        """The GMMK 3 repeats its lights-off timer in the polling and properties frames."""
        return self._b["sleep"] if self._model is not None and self._model.sleep else None

    def _polling_byte(self) -> Optional[int]:
        """The GMMK 3 Pro Wireless keeps its polling rate in the properties frame: always send it there."""
        return self._b["polling"] if self._model is not None and self._model.polling_in_properties else None

    @Slot(int)
    def setSleep(self, index: int):
        """GMMK 3: lights-off timer. Written the way CORE writes it: in the polling frame and in every
        profile and layer's properties frame (each with that profile's debounce)."""
        if not self.hasSleep or not 0 <= index < len(gmmk.SLEEP_MINUTES):
            return
        minutes = gmmk.SLEEP_MINUTES[index]
        if minutes == self._b["sleep"]:
            return
        def set_minutes():
            self._b["sleep"] = minutes
        if self._he():
            self._send([(gmmk_he.sleep_frame(minutes, self._model.wireless), gmmk_he.DELAY)], set_minutes)
            return
        if self._protocol() == "gmbk":
            self._send(self._gmbk_settings(self._b["polling"], minutes), set_minutes)
            return
        if self._protocol() == "eternal":
            self._send([(gmmk_eternal.standby_frame(minutes), gmmk_eternal.DELAY)], set_minutes)
            return
        polling = self._polling_byte()
        job: Job = [] if polling else [(gmmk.polling_frame(self._b["polling"], minutes), gmmk.DELAY_LONG)]
        job += [(gmmk.properties_frame(p, layer, self._b["debounce"][p], sleep=minutes, polling=polling),
                 gmmk.DELAY_LONG) for p in range(gmmk.PROFILES) for layer in range(gmmk.LAYERS)]

        def ok():
            self._b["sleep"] = minutes
        self._send(job, ok)

    @Slot(int)
    def setDebounce(self, index: int):
        if not self.hasDebounce or not 0 <= index < len(gmmk.DEBOUNCE_MS):
            return
        ms, p = gmmk.DEBOUNCE_MS[index], self._b["profile"]
        if ms == self._b["debounce"][p]:
            return
        if self._protocol() == "eternal":
            def all_ok():            # one setting for the whole board
                self._b["debounce"] = [ms] * gmmk.PROFILES
            self._send([(gmmk_eternal.debounce_frame(ms), gmmk_eternal.DELAY)], all_ok)
            return
        rgb, pid, active_layer = self._rgbeast, self._board_hw.pid, self._b["layer"]
        vid, sleep = self._board_hw.model.vendor, self._sleep_byte()
        frames = [gmmk.properties_frame(p, layer, ms, sleep=sleep, polling=self._polling_byte())
                  for layer in range(gmmk.LAYERS)]

        def step(send):
            # While RGBeast manages the keyboard it writes this frame, with the lighting it shows.
            dev = rgb.managed(vid, pid) if rgb is not None else None
            if dev:
                return rgb.set_options(dev, p, active_layer, ms)
            for f in frames:            # otherwise with the board's default lighting
                send(f, gmmk.DELAY_LONG)
            return ""

        job: Job = [(step, 0)]

        def ok():
            self._b["debounce"][p] = ms
        self._send(job, ok)

    def _layer_job(self, p: int, layer: int, keys: Dict[str, dict], macros: Dict[int, dict]) -> Job:
        """Macros used by the layer, its key assignment (frames for any profile/layer); no commit."""
        assert self._model is not None
        if self._protocol() == "gmbk":
            frame = gmbk.keys_frame(self._model.layout, p, layer, keys, gmmk.encode_binding, gmmk.LOCKED_KEYS)
            return [(frame, gmbk.DELAY)]
        job: Job = []
        used = sorted({b["slot"] for b in keys.values() if b.get("kind") == "macro"})
        if used:
            modes = {s: macros[s]["mode"] for s in used}
            job.append((gmmk.macro_mode_frame(self._model, p, layer, keys, modes), gmmk.DELAY_KEYS))
            for s in used:
                job.append((gmmk.macro_frame(s, macros[s]["events"]), gmmk.DELAY_KEYS))
        job.append((gmmk.keymap_frame(self._model, p, layer, keys, list(macros)), gmmk.DELAY_KEYS))
        return job

    def _commit(self) -> Job:
        """After key or macro changes the Pro re-selects the active layer, as CORE does; the GMMK 2 doesn't."""
        if self._model is not None and not self._model.commit_keys:
            return []
        f = gmmk.profile_frame(self._b["profile"], self._b["layer"])
        return [(f, gmmk.DELAY_LONG), (f, gmmk.DELAY_LONG)]

    @Slot(int)
    def setProfile(self, index: int):
        if not 0 <= index < gmmk.PROFILES or index == self._b["profile"]:
            return

        def ok():
            self._b["profile"] = index
        if self._he():
            self._send(gmmk_he.analog_job(index, self._b["layer"], self._b["analog"]), ok)
            return
        if self._protocol() == "eternal":
            self._send([(gmmk_eternal.profile_frame(index), gmmk_eternal.DELAY)], ok)
            return
        self._send([(gmmk.profile_frame(index, self._b["layer"]), gmmk.DELAY_LONG),
                    (self._tell_rgbeast(index, self._b["layer"]), 0)], ok)

    @Slot(int)
    def setLayer(self, index: int):
        if not 0 <= index < gmmk.LAYERS or index == self._b["layer"] or self._model is None:
            return
        p = self._b["profile"]
        if self._he():
            def he_ok():
                self._b["layer"] = index
            self._send(gmmk_he.analog_job(p, index, self._b["analog"]), he_ok)
            return
        if self._protocol() == "eternal":
            # The Eternal has no command to switch layers: the page just shows that layer's keys.
            self._b["layer"] = index
            self._store.save(self._model, self._b)
            self.changed.emit()
            self.keysChanged.emit()
            return
        keys = self._b["keys"][p][index]
        f = gmmk.profile_frame(p, index)
        job = [(f, gmmk.DELAY_LONG)] + self._layer_job(p, index, keys, self._store.macros) + [(f, gmmk.DELAY_LONG)]
        job.append((self._tell_rgbeast(p, index), 0))

        def ok():
            self._b["layer"] = index
        self._send(job, ok)

    def _he_keys_job(self, p: int, layer: int, old: Dict[str, dict], new: Dict[str, dict]) -> Job:
        """GMMK 3 HE: one frame per key whose binding changed (a key back to normal gets its own usage)."""
        assert self._model is not None
        numbers = gmmk.KEY_INDEX[self._model.keymap]
        job: Job = []
        for key in sorted(set(old) | set(new)):
            if old.get(key) == new.get(key):
                continue
            b = new.get(key)
            if b is None:
                code = gmmk_he.default_keycode(key, gmmk.default_usage(self._model, key))
            else:
                problem = gmmk_he.binding_problem(b) or gmmk.binding_problem(b)
                if problem:
                    raise ValueError(f"{gmmk.key_name(key)}: {problem}")
                code = gmmk_he.keycode(b)
            job.append((gmmk_he.key_frame(p, layer, numbers[key], code), gmmk_he.DELAY_KEY))
        return job

    def _write_layer(self, new_keys: Dict[str, dict], ok_text: str = ""):
        p, layer = self._b["profile"], self._b["layer"]
        try:
            if self._he():
                job = self._he_keys_job(p, layer, self._layer_keys(), new_keys)
            elif self._protocol() == "eternal":
                job = gmmk_eternal.keys_job(self._model.layout, layer, self._layer_keys(), new_keys,
                                            lambda k: gmmk.default_usage(self._model, k))
            else:
                job = self._layer_job(p, layer, new_keys, self._store.macros) + self._commit()
        except ValueError as e:
            self._set_message(str(e))
            return

        def ok():
            self._b["keys"][p][layer] = new_keys
        self._send(job, ok, ok_text)

    @Slot(str, str, int, str)
    def bindKey(self, key: str, kind: str, code: int, mods: str):
        """kind: default | disabled | key | media | mouse | function | macro (code = macro slot)."""
        if (self._model is None or key in gmmk.locked_keys(self._model)
                or key not in gmmk.KEY_INDEX[self._model.keymap]):
            return
        if kind == "default":
            b = None
        elif kind == "macro":
            if not self._model.macros:
                return
            b = {"kind": "macro", "slot": code}
        elif kind == "key":
            b = {"kind": "key", "code": code}
            m = [x for x in mods.split(",") if x]
            if m:
                b["mods"] = [x for x in ("ctrl", "shift", "alt", "super") if x in m]
        elif kind == "disabled":
            b = {"kind": "disabled"}
        else:
            b = {"kind": kind, "code": code}
        if b is not None:
            problem = gmmk.binding_problem(b, list(self._store.macros))
            extra = {"gmbk": gmbk.binding_problem, "eternal": gmmk_eternal.binding_problem,
                     "he": gmmk_he.binding_problem}.get(self._protocol())
            problem = problem or (extra(b) if extra else "")
            if problem:
                self._set_message(problem)
                return
        keys = dict(self._layer_keys())
        if b is None:
            keys.pop(key, None)
        else:
            keys[key] = b
        if keys != self._layer_keys():
            self._write_layer(keys)

    @Slot()
    def resetLayer(self):
        if self._layer_keys():
            self._write_layer({}, "Every key on this layer is back to normal.")

    # ---- macros
    def _macro_used(self, slot: int) -> bool:
        return any(b.get("kind") == "macro" and b.get("slot") == slot
                   for prof in self._b["keys"] for layer in prof for b in layer.values())

    @Slot(int, str, str, str, result=int)
    def saveMacro(self, slot: int, name: str, mode: str, events_json: str) -> int:
        """Create (slot 0) or change a macro. Returns its slot, or 0 when it can't be saved."""
        try:
            events = json.loads(events_json)
        except ValueError:
            events = None
        events = events if isinstance(events, list) else []
        problem = gmmk.macro_problem(events)
        if mode not in gmmk.MACRO_MODES:
            problem = problem or "Unknown play mode."
        if slot == 0:
            free = [s for s in range(1, gmmk.MAX_MACROS + 1) if s not in self._store.macros]
            if not free:
                problem = problem or f"Wheelguard keeps at most {gmmk.MAX_MACROS} macros."
            slot = free[0] if free else 0
        elif slot not in self._store.macros:
            problem = problem or "That macro doesn't exist."
        if problem:
            self._set_message(problem)
            return 0
        macro = {"slot": slot, "name": (name.strip() or f"Macro {slot}")[:40], "mode": mode,
                 "events": [{"type": e["type"], "code": e["code"], "delay": e["delay"]} for e in events]}
        if not self._macro_used(slot):
            self._store.macros[slot] = macro
            self._store.save(self._model, self._b)
            self.macrosChanged.emit()
            self.keysChanged.emit()
            return slot
        # In use: the board needs the new steps (one frame, whatever layer uses it) and, for the play mode,
        # every layer that uses it.
        macros = dict(self._store.macros)
        macros[slot] = macro
        job: Job = [(gmmk.macro_frame(slot, macro["events"]), gmmk.DELAY_KEYS)]
        if self._model is not None and mode != self._store.macros[slot]["mode"]:
            for p in range(gmmk.PROFILES):
                for layer in range(gmmk.LAYERS):
                    keys = self._b["keys"][p][layer]
                    if any(b.get("kind") == "macro" and b.get("slot") == slot for b in keys.values()):
                        modes = {b["slot"]: macros[b["slot"]]["mode"] for b in keys.values()
                                 if b.get("kind") == "macro"}
                        job.append((gmmk.macro_mode_frame(self._model, p, layer, keys, modes), gmmk.DELAY_KEYS))
            job += self._commit()

        def ok():
            self._store.macros[slot] = macro
        self._send(job, ok)
        return slot

    @Slot(int)
    def deleteMacro(self, slot: int):
        if slot not in self._store.macros:
            return
        if not self._macro_used(slot):
            del self._store.macros[slot]
            self._store.save(self._model, self._b)
            self.macrosChanged.emit()
            return
        # Keys that play it go back to normal on every layer that has one, then the macro goes.
        macros = {s: m for s, m in self._store.macros.items() if s != slot}
        new_keys = [[{k: b for k, b in layer.items() if not (b.get("kind") == "macro" and b.get("slot") == slot)}
                     for layer in prof] for prof in self._b["keys"]]
        job: Job = []
        for p in range(gmmk.PROFILES):
            for layer in range(gmmk.LAYERS):
                if new_keys[p][layer] != self._b["keys"][p][layer]:
                    job += self._layer_job(p, layer, new_keys[p][layer], macros)
        job += self._commit()

        def ok():
            self._b["keys"] = new_keys
            del self._store.macros[slot]
        self._send(job, ok)

    def demo(self):
        """Screenshots: controls on, a macro, and a few keys doing other things (nothing is sent)."""
        if self._model is None:
            return
        self._b["unlocked"] = True
        ev = []
        for code in (6, 25):            # Ctrl+C, Ctrl+V
            ev += [{"type": "press", "code": 224, "delay": 30}, {"type": "press", "code": code, "delay": 30},
                   {"type": "release", "code": code, "delay": 30}, {"type": "release", "code": 224, "delay": 120}]
        self._store.macros = {1: {"slot": 1, "name": "Copy and paste", "mode": "once", "events": ev}}
        self._b["keys"][0][0] = {"CapsLock": {"kind": "key", "code": 224}, "F5": {"kind": "media", "code": 1},
                                 "F6": {"kind": "media", "code": 7}, "F12": {"kind": "macro", "slot": 1},
                                 "PrintScreen": {"kind": "key", "code": 4, "mods": ["super"]}}
        self._b["polling"] = 1000
        self._b["debounce"][0] = 8
        self._store.save(self._model, self._b)
        self.changed.emit()
        self.keysChanged.emit()
        self.macrosChanged.emit()

    @Slot(int, result="QVariant")
    def macro(self, slot: int):
        m = self._store.macros.get(slot)
        return json.loads(json.dumps(m)) if m else {}
