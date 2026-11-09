"""Wire handling for the wheelguard IPC protocol (see PROTOCOL.md). No Qt.

`ok` / `error` replies answer our commands strictly in order; `status` / `tick` are events.
"""
import datetime
import json
import math
from collections import deque
from typing import Any, Deque, List, NamedTuple, Optional

from .. import mice as mouse_ids

TICK_KINDS = ("emit", "drop", "hold", "pass")

# The config keys the app shows, with what it assumes before the daemon has said otherwise.
CONFIG_DEFAULTS = {"enabled": True, "pre_ms": 60, "hold_ms": 25, "lock_ms": 300,
                   "clicklock": False, "clicklock_ms": 5000, "clicklock_mode": "repeat",
                   "clicklock_rate": 10, "clicklock_max_s": 600,
                   "clicklock_ignore_drag": True, "debounce_ms": 0, "mouse": "auto"}
# Click-Lock slider range in the app and the panel (the daemon accepts 0.5 to 30 s).
CLICKLOCK_MS_RANGE = (1000, 15000)
CLICKLOCK_MS_STEP = 500
CLICKLOCK_MAX_S_CHOICES = (0, 60, 300, 600, 1800)    # "Stop automatically after": Never, 1, 5, 10, 30 min
DEBOUNCE_MS_RANGE = (10, 150)           # "Double-click fix" slider; 0 means off
DEBOUNCE_DEFAULT_ON_MS = 40             # what switching it on starts with
CLICKLOCK_MODES = ("repeat", "hold")    # the daemon's clicklock.MODES (kept evdev-free here)


class LineBuffer:
    """Accumulates bytes and yields complete newline-terminated lines (as bytes, without \\n)."""

    MAX_PENDING = 1 << 20

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> List[bytes]:
        self._buf += data
        lines: List[bytes] = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            line = bytes(self._buf[:i])
            del self._buf[:i + 1]
            if line.strip():
                lines.append(line)
        if len(self._buf) > self.MAX_PENDING:   # garbage without newlines: drop it
            self._buf.clear()
        return lines

    def clear(self):
        self._buf.clear()


def encode(msg: dict) -> bytes:
    return json.dumps(msg, separators=(",", ":")).encode() + b"\n"


def parse(line: bytes) -> Optional[dict]:
    """One JSON object per line; anything else is ignored (None)."""
    try:
        msg = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        return None
    return msg


class Tick(NamedTuple):
    t: float
    sign: int
    kind: str
    held: bool
    gap_ms: Optional[float]


def as_tick(msg: dict) -> Optional[Tick]:
    if msg.get("type") != "tick":
        return None
    kind = msg.get("kind")
    sign = msg.get("sign")
    if kind not in TICK_KINDS or sign not in (1, -1):
        return None
    try:
        t = float(msg.get("t", 0.0))
        gap = msg.get("gap_ms")
        gap = None if gap is None else float(gap)
    except (TypeError, ValueError):
        return None
    return Tick(t, int(sign), kind, bool(msg.get("held", False)), gap)


class DeviceState(NamedTuple):
    state: str            # "running" | "waiting" | "permission"
    device: str
    error: str
    label: str = ""       # what to show for the mouse: "Endgame Gear XM2w 4k v2 · dongle", else the device name


def pretty_device_name(name: str) -> str:
    """Drop a doubled vendor prefix: "Endgame Gear Endgame Gear HS Dongle" -> "Endgame Gear HS Dongle"."""
    words = name.split()
    for n in range(len(words) // 2, 0, -1):
        if words[:n] == words[n:2 * n]:
            return " ".join(words[n:])
    return name


def device_label(status: dict) -> str:
    """Friendly text for the connected mouse: the supported model and how it is connected, else the raw name."""
    model, connection = status.get("model"), status.get("connection")
    if isinstance(model, str) and model.strip():
        if isinstance(connection, str) and connection.strip():
            return f"{model.strip()} \u00b7 {connection.strip()}"
        return model.strip()
    return pretty_device_name(status.get("device") or "")


def device_state(status: dict) -> DeviceState:
    """Map a status event to the GUI state. Permission is only inferred from the daemon's own error."""
    error = status.get("error") or ""
    device = pretty_device_name(status.get("device") or "")
    if not isinstance(error, str):
        error = str(error)
    if "permission" in error.lower():
        return DeviceState("permission", device, error, device)
    label = device_label(status)
    if status.get("connected"):
        return DeviceState("running", device, error, label)
    return DeviceState("waiting", device, error, label)


def status_config(status: dict) -> Optional[dict]:
    cfg = status.get("config")
    if not isinstance(cfg, dict):
        return None
    out = {}
    for k, default in CONFIG_DEFAULTS.items():
        v = cfg.get(k, default)
        if isinstance(default, bool):
            out[k] = bool(v)
        elif isinstance(default, str):
            out[k] = v if isinstance(v, str) else default
        else:
            out[k] = int(round(float(v)))
    return out


def status_mice(status: dict) -> List[dict]:
    """The connected mice with a scroll wheel from a status (or `mice`) message; malformed entries are dropped."""
    raw = status.get("mice")
    out = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        try:
            ident = mouse_ids.format_choice(int(str(m.get("vendor")), 16), int(str(m.get("product")), 16))
        except ValueError:
            continue
        name = m.get("name") if isinstance(m.get("name"), str) and m.get("name").strip() else "Mouse"
        conn = m.get("connection")
        out.append({"id": ident, "name": name.strip(), "supported": m.get("supported") is True,
                    "connection": conn if isinstance(conn, str) and conn else ""})
    return out


def mouse_options(mice: List[dict], selected: str) -> List[dict]:
    """The choices of the "Mouse" picker as [{"id", "label"}]: automatic, each connected mouse, and the saved
    choice when that mouse isn't connected right now (so the picker still says what is selected)."""
    opts = [{"id": mouse_ids.AUTO, "label": "Automatic (supported mice)"}]
    for m in status_mice({"mice": mice}):
        label = m["name"] + (f" \u00b7 {m['connection']}" if m["connection"] else "")
        opts.append({"id": m["id"], "label": label if m["supported"] else f"{label} (not tested)"})
    if selected != mouse_ids.AUTO and all(o["id"] != selected for o in opts):
        opts.append({"id": selected, "label": f"Saved mouse {selected} (not connected)"})
    return opts


def mouse_index(options: List[dict], selected: str) -> int:
    return next((i for i, o in enumerate(options) if o["id"] == selected), 0)


def clicklock_active(status: dict) -> bool:
    return status.get("clicklock_active") is True


def status_stats(status: dict) -> dict:
    s = status.get("stats") or {}
    ticks = int(s.get("ticks", 0) or 0)
    dropped = int(s.get("dropped", 0) or 0)
    return {"ticks": ticks, "dropped": dropped, "debounced": max(0, int(s.get("debounced", 0) or 0))}


def block_rate(ticks: int, dropped: int) -> float:
    return 100.0 * dropped / ticks if ticks > 0 else 0.0


# ---------------------------------------------------------------- wheel health over time
HISTORY_RANGES = (7, 30)        # the chart's choices, in days

# "Your wheel is bouncing more than usual": the last 7 days against the days before them. Deliberately
# conservative: plenty of ticks on both sides, several days of data, a clear rise in absolute and relative
# terms, and a margin of three standard errors so that an ordinary week-to-week wobble never triggers it.
WEAR_RECENT_DAYS = 7
WEAR_BASELINE_DAYS = 28
WEAR_MIN_RECENT_TICKS = 1000
WEAR_MIN_BASELINE_TICKS = 3000
WEAR_MIN_RECENT_DAYS = 2
WEAR_MIN_BASELINE_DAYS = 5
WEAR_MIN_RATIO = 1.5
WEAR_MIN_RISE = 0.01            # absolute rise in block rate (1 percentage point)
WEAR_SIGMAS = 3.0


def history_days(msg: dict) -> List[dict]:
    """The days of a `history` reply as [{"date": date, "ticks", "dropped"}] oldest first; bad rows are dropped."""
    raw = msg.get("days")
    days = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            d = datetime.date.fromisoformat(row.get("date"))
        except (TypeError, ValueError):
            continue
        ticks, dropped = row.get("ticks"), row.get("dropped")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in (ticks, dropped)):
            continue
        ticks, dropped = int(ticks), int(dropped)
        old = days.get(d, (0, 0))
        days[d] = (old[0] + ticks, old[1] + min(dropped, ticks))
    return [{"date": d, "ticks": t, "dropped": x} for d, (t, x) in sorted(days.items())]


def history_series(days: List[dict], count: int, today: datetime.date) -> List[dict]:
    """The last `count` days up to and including `today`, oldest first, gaps filled with zeros.
    `rate` is the block rate in percent, or None for a day without ticks."""
    by_date = {d["date"]: d for d in days}
    out = []
    for n in range(count - 1, -1, -1):
        day = today - datetime.timedelta(days=n)
        row = by_date.get(day)
        ticks, dropped = (row["ticks"], row["dropped"]) if row else (0, 0)
        out.append({"date": day.isoformat(), "ticks": ticks, "dropped": dropped,
                    "rate": block_rate(ticks, dropped) if ticks > 0 else None})
    return out


def has_history(series: List[dict]) -> bool:
    """Enough to draw a chart: at least two days with ticks."""
    return sum(1 for d in series if d["ticks"] > 0) >= 2


def wear_hint(days: List[dict], today: datetime.date) -> bool:
    """True when the last 7 days' block rate is clearly above the weeks before (see the WEAR_* limits)."""
    recent_from = today - datetime.timedelta(days=WEAR_RECENT_DAYS - 1)
    base_from = recent_from - datetime.timedelta(days=WEAR_BASELINE_DAYS)
    r_ticks = r_drop = r_days = b_ticks = b_drop = b_days = 0
    for d in days:
        if d["ticks"] <= 0:
            continue
        if recent_from <= d["date"] <= today:
            r_ticks, r_drop, r_days = r_ticks + d["ticks"], r_drop + d["dropped"], r_days + 1
        elif base_from <= d["date"] < recent_from:
            b_ticks, b_drop, b_days = b_ticks + d["ticks"], b_drop + d["dropped"], b_days + 1
    if (r_ticks < WEAR_MIN_RECENT_TICKS or b_ticks < WEAR_MIN_BASELINE_TICKS
            or r_days < WEAR_MIN_RECENT_DAYS or b_days < WEAR_MIN_BASELINE_DAYS):
        return False
    recent, base = r_drop / r_ticks, b_drop / b_ticks
    margin = WEAR_SIGMAS * math.sqrt(max(base * (1 - base), 1e-4) / r_ticks)
    return (recent - base >= WEAR_MIN_RISE and recent >= WEAR_MIN_RATIO * base
            and recent - base > margin)


class CommandQueue:
    """FIFO of commands awaiting an ok/error reply."""

    def __init__(self):
        self._q: Deque[str] = deque()

    def push(self, name: str):
        self._q.append(name)

    def resolve(self, msg: dict) -> Optional[tuple]:
        """For an ok/error message: (command name, error message or None). None for other types."""
        t = msg.get("type")
        if t not in ("ok", "error"):
            return None
        name = self._q.popleft() if self._q else "?"
        return (name, None) if t == "ok" else (name, str(msg.get("message", "Unknown error")))

    def clear(self):
        self._q.clear()

    def __len__(self):
        return len(self._q)
