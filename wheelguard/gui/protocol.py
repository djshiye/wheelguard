"""Wire handling for the wheelguard IPC protocol (see PROTOCOL.md). No Qt.

`ok` / `error` replies answer our commands strictly in order; `status` / `tick` are events.
"""
import json
from collections import deque
from typing import Any, Deque, List, NamedTuple, Optional

TICK_KINDS = ("emit", "drop", "hold", "pass")


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


def pretty_device_name(name: str) -> str:
    """Drop a doubled vendor prefix: "Endgame Gear Endgame Gear HS Dongle" -> "Endgame Gear HS Dongle"."""
    words = name.split()
    for n in range(len(words) // 2, 0, -1):
        if words[:n] == words[n:2 * n]:
            return " ".join(words[n:])
    return name


def device_state(status: dict) -> DeviceState:
    """Map a status event to the GUI state. Permission is only inferred from the daemon's own error."""
    error = status.get("error") or ""
    device = pretty_device_name(status.get("device") or "")
    if not isinstance(error, str):
        error = str(error)
    if "permission" in error.lower():
        return DeviceState("permission", device, error)
    if status.get("connected"):
        return DeviceState("running", device, error)
    return DeviceState("waiting", device, error)


def status_config(status: dict) -> Optional[dict]:
    cfg = status.get("config")
    if not isinstance(cfg, dict):
        return None
    out = {}
    for k, default in (("enabled", True), ("pre_ms", 60), ("hold_ms", 25), ("lock_ms", 300)):
        v = cfg.get(k, default)
        out[k] = bool(v) if k == "enabled" else int(round(float(v)))
    return out


def status_stats(status: dict) -> dict:
    s = status.get("stats") or {}
    ticks = int(s.get("ticks", 0) or 0)
    dropped = int(s.get("dropped", 0) or 0)
    return {"ticks": ticks, "dropped": dropped}


def block_rate(ticks: int, dropped: int) -> float:
    return 100.0 * dropped / ticks if ticks > 0 else 0.0


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
