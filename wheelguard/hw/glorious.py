"""Glorious mice: transports, codecs and drivers for two protocols. See docs/glorious-mouse-protocol.md.

Both protocols were decoded from the Glorious CORE app (read as text, never run) and are not verified on
hardware. Every model starts unconfirmed (models.GLORIOUS_FAMILY), so Wheelguard reads and never writes.

"v3", the Model O3 / D3 (vendor 3794): Feature report 3, 64 bytes, `03 BB cmd len data...`, one command per
setting. The app reads back the firmware, the battery, the device name and the power-saving (sleep) timer;
it has no command that reads the DPI stages, the lift-off distance or the polling rate.

"v2pro", the Sinowealth wireless mice (vendor 258a): Feature report 0, 65 bytes, a status byte in every
reply. The app reads back only the firmware and the battery; every setting is write-only.

So the record Wheelguard keeps (the "blob", as for the other mice) holds only what the mouse can report:
the sleep timer on the O3 / D3, nothing on the Sinowealth mice. A setting that can't be read back can't
be checked after a write, so its command is not on the allow-list either. Every byte that reaches a
device goes through V3Device._exchange() / V2ProDevice._exchange(), which check the request against the
allow-list first: lighting, buttons, macros, DPI, polling, lift-off, debounce, motion sync, profiles,
firmware, pairing and the dock can't be sent.
"""
import time
from dataclasses import dataclass
from functools import partial
from typing import Callable, Dict, List, Optional, Tuple

from .drivers import Driver
from .endgame import LinkError, Rejected, StatusError, WriteBlocked
from .errors import Battery, Snapshot
from .models import (GLORIOUS_D3, GLORIOUS_LINKS, GLORIOUS_O3, GLORIOUS_O3_DONGLE, MODELS, GloriousLink, Model)

# ================================================================ v3: Model O3 / D3

V3_REPORT_ID, V3_REPORT_LEN = 0x03, 64
V3_MOUSE = 0xBB                 # the "mouse device id" the app puts in byte 1 of every request
V3_WAIT_WIRED_S, V3_WAIT_DONGLE_S = 0.05, 0.10      # the app's waits before GET_FEATURE
V3_ATTEMPTS = 3
V3_PAYLOAD = 3                  # where the app reads a reply's data (unverified: see the doc)

V3_FIRMWARE, V3_BATTERY, V3_GET_POWER, V3_INFO = 0x80, 0x82, 0x84, 0x99
V3_SET_POWER = 0x04
V3_READS = {V3_FIRMWARE: 0, V3_BATTERY: 0, V3_GET_POWER: 0, V3_INFO: 1}     # cmd -> data length
V3_WRITES = {V3_SET_POWER: 8}
V3_ALLOWED = {**V3_READS, **V3_WRITES}
V3_WIRED_ONLY = frozenset((0xA310, 0xA320))         # the wired O3 / D3: no battery

MINUTE_MS = 60000
SLEEP_MAX_MIN = 60              # the app offers 0 (off) to 60 minutes
LOWPOWER_MS = 60000             # the app always sends this as the second value


def _be32(b: bytes) -> int:
    return int.from_bytes(bytes(b[:4]), "big")


def v3_build(cmd: int, data: bytes = b"") -> bytes:
    """A request report. Does not validate: _exchange() does that for everything that is sent."""
    r = bytearray(V3_REPORT_LEN)
    r[0], r[1], r[2], r[3] = V3_REPORT_ID, V3_MOUSE, cmd, len(data)
    r[4:4 + len(data)] = data
    return bytes(r)


def v3_check_request(r: bytes) -> int:
    """Validate an outgoing report and return its command. Raises Rejected."""
    if len(r) != V3_REPORT_LEN:
        raise Rejected(f"Glorious reports are {V3_REPORT_LEN} bytes, got {len(r)}")
    if r[0] != V3_REPORT_ID or r[1] != V3_MOUSE:
        raise Rejected("malformed report header")
    cmd = r[2]
    if cmd not in V3_ALLOWED:
        raise Rejected(f"command {cmd:#04x} is not on the allow-list")
    n = V3_ALLOWED[cmd]
    if r[3] != n:
        raise Rejected(f"command {cmd:#04x}: wrong data length {r[3]}")
    data = r[4:4 + n]
    if any(r[4 + n:]):
        raise Rejected("data beyond the argument length")
    if cmd == V3_INFO and data != b"\x01":
        raise Rejected("the device-info read takes the argument 01")
    elif cmd == V3_SET_POWER:
        sleep_ms, low_ms = _be32(data[0:4]), _be32(data[4:8])
        if sleep_ms % MINUTE_MS or not 0 <= sleep_ms <= SLEEP_MAX_MIN * MINUTE_MS:
            raise Rejected("sleep timer out of range")
        if low_ms != LOWPOWER_MS:
            raise Rejected("low-power timer out of range")
    return cmd


class V3Device:
    """One open hidraw node (the settings collection, usage page 0xFF00 / usage 1). `dev` provides
    set_feature(bytes) and get_feature(id, len)."""

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None] = time.sleep):
        self._dev = dev
        self.wired = wired
        self._sleep = sleep
        self.identified = False     # the write gate: the mouse answered a firmware read

    def _unwrap(self, raw: bytes) -> bytes:
        """The reply as the app reads it. Through the dongle the app drops two bytes after the report id."""
        raw = bytes(raw)
        return raw if self.wired else raw[:1] + raw[3:]

    def _exchange(self, r: bytes) -> bytes:
        cmd = v3_check_request(r)
        if cmd in V3_WRITES and not self.identified:
            raise WriteBlocked("writes are disabled: the mouse has not answered yet")
        for attempt in range(V3_ATTEMPTS):
            try:
                self._dev.set_feature(r)
                self._sleep(V3_WAIT_WIRED_S if self.wired else V3_WAIT_DONGLE_S)
                reply = self._unwrap(self._dev.get_feature(V3_REPORT_ID, V3_REPORT_LEN))
            except OSError as e:
                if attempt < V3_ATTEMPTS - 1:
                    continue
                raise LinkError(e.errno or 0, "Glorious report") from e
            if len(reply) >= V3_PAYLOAD + 8 and reply[0] == V3_REPORT_ID:
                return reply.ljust(V3_REPORT_LEN, b"\0")
        raise StatusError(cmd, 0)

    def request(self, cmd: int, data: bytes = b"") -> bytes:
        return self._exchange(v3_build(cmd, data))

    # ---- reads
    def firmware(self) -> str:
        a = self.request(V3_FIRMWARE)
        self.identified = True
        return f"{a[V3_PAYLOAD]}.{a[V3_PAYLOAD + 1]}.{a[V3_PAYLOAD + 2]}"

    def device_name(self) -> str:
        """The name the mouse gives itself ("MOUSE_O3_WIRELESS"); how the app tells the O3 and D3 apart."""
        a = self.request(V3_INFO, b"\x01")[V3_PAYLOAD:]
        return bytes(a[:a.index(0)] if 0 in a else a).decode("ascii", "replace").strip()

    def battery(self) -> Battery:
        a = self.request(V3_BATTERY)
        level, charging = a[V3_PAYLOAD + 5], a[V3_PAYLOAD + 4]
        if level > 100 or charging > 1:
            raise StatusError(V3_BATTERY, level)            # not a battery reply this app understands
        return Battery(level, charging == 1)

    def power_saving(self) -> Tuple[int, int]:
        """(sleep, low power) in milliseconds."""
        a = self.request(V3_GET_POWER)
        return _be32(a[V3_PAYLOAD:]), _be32(a[V3_PAYLOAD + 4:])

    # ---- writes
    def set_power_saving(self, sleep_ms: int, low_ms: int = LOWPOWER_MS) -> None:
        self.request(V3_SET_POWER, sleep_ms.to_bytes(4, "big") + low_ms.to_bytes(4, "big"))


# ---------------------------------------------------------------- v3 codec
# The record Wheelguard keeps (never sent to the mouse as such):
#   0     magic 'G'          1   layout (3 = Model O3 / D3)
#   2-5   sleep timer, ms (u32 big-endian; 0 = never)
#   6-9   low-power timer, ms (u32 big-endian; the app always uses 60000)
#   10-15 zero
V3_BLOB_LEN = 16
MAGIC, V3_LAYOUT, V2PRO_LAYOUT = 0x47, 3, 2

V3_KEYS = ("idle_min",)


def v3_pack(sleep_ms: int, low_ms: int) -> bytes:
    b = bytearray(V3_BLOB_LEN)
    b[0], b[1] = MAGIC, V3_LAYOUT
    b[2:6] = min(sleep_ms, 0xFFFFFFFF).to_bytes(4, "big")
    b[6:10] = min(low_ms, 0xFFFFFFFF).to_bytes(4, "big")
    return bytes(b)


def _no_stages(d: Dict[str, object]) -> Dict[str, object]:
    d["stage_count"], d["active_stage"] = 0, 0      # nothing for the tray's DPI menu
    return d


def v3_decode(blob: bytes) -> Dict[str, object]:
    if len(blob) != V3_BLOB_LEN:
        raise ValueError(f"Glorious record must be {V3_BLOB_LEN} bytes, got {len(blob)}")
    minutes = round(_be32(blob[2:6]) / MINUTE_MS)
    return _no_stages({"idle_min": max(0, min(SLEEP_MAX_MIN, minutes))})


def v3_empty() -> Dict[str, object]:
    return v3_decode(v3_pack(0, LOWPOWER_MS))


def v3_check_key_value(key: str, v, m: Model = GLORIOUS_O3) -> None:
    if key not in V3_KEYS:
        raise ValueError(f"unknown setting: {key}")
    lo, hi = m.idle_range
    if not lo <= v <= hi:
        raise ValueError(f"sleep timer must be {lo}-{hi} minutes")


def v3_encode(blob: bytes, edits: Dict[str, object], m: Model = GLORIOUS_O3) -> bytes:
    """Copy of the record with only the edited fields changed."""
    current = v3_decode(blob)
    ba = bytearray(blob)
    for key, v in edits.items():
        if current.get(key) == v:
            continue
        v = int(v)
        v3_check_key_value(key, v, m)
        ba[2:6] = (v * MINUTE_MS).to_bytes(4, "big")
        ba[6:10] = LOWPOWER_MS.to_bytes(4, "big")
    return bytes(ba)


def v3_blob_problems(blob: bytes, m: Model = GLORIOUS_O3) -> List[str]:
    if len(blob) != V3_BLOB_LEN:
        return [f"expected {V3_BLOB_LEN} bytes, got {len(blob)}"]
    if blob[0] != MAGIC or blob[1] != V3_LAYOUT:
        return ["not a Wheelguard Glorious record"]
    probs = []
    sleep_ms = _be32(blob[2:6])
    if sleep_ms % MINUTE_MS or sleep_ms > m.idle_range[1] * MINUTE_MS:
        probs.append(f"sleep timer ({sleep_ms} ms) is not one this app knows")
    if _be32(blob[6:10]) != LOWPOWER_MS:
        probs.append("low-power timer is not the one this app writes")
    return probs


@dataclass(frozen=True)
class Op:
    kind: str            # "sleep"
    label: str
    record: bytes        # the new record; write() takes the values for this op from it


def v3_plan_writes(old: bytes, new: bytes) -> List[Op]:
    return [Op("sleep", "sleep", new)] if old[2:10] != new[2:10] else []


def changed_keys_of(keys):
    return lambda old, new: [k for k in keys if old.get(k) != new.get(k)]


def v3_describe_changes(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    return ["Sleep"] if old.get("idle_min") != new.get("idle_min") else []


# ================================================================ v2pro: the Sinowealth wireless mice

V2_REPORT_LEN = 65              # report id 0 + 64 bytes
V2_ATTEMPTS, V2_RETRY_S = 10, 0.04
V2_TARGET = 0x02                # byte 3 of a request; the app uses 00 for a firmware read through the dongle
ST_BUSY, ST_OK, ST_FAILED, ST_UNKNOWN, ST_NO_MOUSE = 0xA0, 0xA1, 0xA2, 0xA3, 0xA4

V2_FIRMWARE = (0x00, 0x81)
V2_BATTERY = (0x00, 0x83)
V2_READS = {V2_FIRMWARE: 3, V2_BATTERY: 2}          # (class, id) -> payload length byte
V2_WRITES: Dict[Tuple[int, int], int] = {}          # none: nothing this protocol stores can be read back
V2_ALLOWED = {**V2_READS, **V2_WRITES}


def v2_build(cmd: Tuple[int, int], target: int = V2_TARGET) -> bytes:
    """A request report (no payload: every command on the allow-list is a read)."""
    r = bytearray(V2_REPORT_LEN)
    r[3], r[4] = target, V2_ALLOWED.get(cmd, 0)
    r[5], r[6] = cmd
    return bytes(r)


def v2_check_request(r: bytes) -> Tuple[int, int]:
    """Validate an outgoing report and return its (class, id). Raises Rejected."""
    if len(r) != V2_REPORT_LEN:
        raise Rejected(f"Glorious reports are {V2_REPORT_LEN} bytes, got {len(r)}")
    cmd = (r[5], r[6])
    if cmd not in V2_ALLOWED:
        raise Rejected(f"command {cmd[0]:02x}/{cmd[1]:02x} is not on the allow-list")
    if r[0] or r[1] or r[2] or r[3] not in ((V2_TARGET, 0x00) if cmd == V2_FIRMWARE else (V2_TARGET,)):
        raise Rejected("malformed report header")
    if r[4] != V2_ALLOWED[cmd]:
        raise Rejected(f"command {cmd[0]:02x}/{cmd[1]:02x}: wrong length {r[4]}")
    if any(r[7:]):
        raise Rejected("read commands carry no arguments")
    return cmd


class V2ProDevice:
    """One open hidraw node (the settings collection, usage page 0xFFFF / usage 0)."""

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None] = time.sleep):
        self._dev = dev
        self.wired = wired
        self._sleep = sleep
        self.identified = False

    def _exchange(self, r: bytes) -> bytes:
        """Send one request and return the 64 reply bytes (status first) once the mouse says OK. Retries
        while it answers busy or failed, like the app means to (it never really checks)."""
        cmd = v2_check_request(r)
        if cmd in V2_WRITES and not self.identified:
            raise WriteBlocked("writes are disabled: the mouse has not answered yet")
        status, resend = 0, True
        for _attempt in range(V2_ATTEMPTS):
            try:
                if resend:
                    self._dev.set_feature(r)
                self._sleep(V2_RETRY_S)
                d = bytes(self._dev.get_feature(0x00, V2_REPORT_LEN))[1:]
            except OSError as e:
                raise LinkError(e.errno or 0, "Glorious report") from e
            if len(d) < 16:
                status, resend = 0, True
                continue
            status = d[0]
            if status == ST_OK and d[5] == cmd[1]:
                return d
            if status in (ST_NO_MOUSE, ST_UNKNOWN):
                break
            resend = status != ST_BUSY       # busy: ask for the reply again; failed (or a stale reply): resend
        raise StatusError((cmd[0] << 8) | cmd[1], status)

    def firmware(self) -> str:
        d = self._exchange(v2_build(V2_FIRMWARE, V2_TARGET if self.wired else 0x00))
        self.identified = True
        return ".".join(str(x) for x in d[6:10])

    def battery(self) -> Battery:
        d = self._exchange(v2_build(V2_BATTERY))
        level, charging = d[7], d[6]
        if level > 100 or charging > 1:
            raise StatusError((V2_BATTERY[0] << 8) | V2_BATTERY[1], level)
        return Battery(max(1, level), charging == 1)


# ---------------------------------------------------------------- v2pro codec
# The record: 0 magic 'G', 1 layout 2, 2-7 zero. Nothing this protocol stores can be read back.
V2_BLOB_LEN = 8


def v2_pack() -> bytes:
    return bytes((MAGIC, V2PRO_LAYOUT)) + bytes(V2_BLOB_LEN - 2)


def v2_decode(blob: bytes) -> Dict[str, object]:
    if len(blob) != V2_BLOB_LEN:
        raise ValueError(f"Glorious record must be {V2_BLOB_LEN} bytes, got {len(blob)}")
    return _no_stages({})


def v2_check_key_value(key: str, v, m: Optional[Model] = None) -> None:
    raise ValueError(f"unknown setting: {key}")


def v2_encode(blob: bytes, edits: Dict[str, object], m: Optional[Model] = None) -> bytes:
    for key, v in edits.items():
        v2_check_key_value(key, v)
    return bytes(blob)


def v2_blob_problems(blob: bytes, m: Optional[Model] = None) -> List[str]:
    if len(blob) != V2_BLOB_LEN:
        return [f"expected {V2_BLOB_LEN} bytes, got {len(blob)}"]
    if blob[0] != MAGIC or blob[1] != V2PRO_LAYOUT or any(blob[2:]):
        return ["not a Wheelguard Glorious record"]
    return []


# ================================================================ codecs

def _snap_dpi(v: int, m: Model) -> int:
    v = max(m.dpi_min, min(m.dpi_max, int(v)))
    return max(m.dpi_min, min(m.dpi_max, int(round(v / m.dpi_step)) * m.dpi_step))


class GloriousCodec:
    """One protocol's record functions for one model, as the object the session and the GUI expect."""

    def __init__(self, model: Model, protocol: str):
        self.model = model
        self.protocol = protocol
        v3 = protocol == "v3"
        self.BLOB_LEN = V3_BLOB_LEN if v3 else V2_BLOB_LEN
        self.KEYS = V3_KEYS if v3 else ()
        self.decode = v3_decode if v3 else v2_decode
        self.empty = v3_empty if v3 else (lambda: v2_decode(v2_pack()))
        self.changed_keys = changed_keys_of(self.KEYS)
        self.describe_changes = v3_describe_changes if v3 else (lambda _old, _new: [])
        self.plan_writes = v3_plan_writes if v3 else (lambda _old, _new: [])
        self.encode = partial(v3_encode if v3 else v2_encode, m=model)
        self.check_key_value = partial(v3_check_key_value if v3 else v2_check_key_value, m=model)
        self.blob_problems = partial(v3_blob_problems if v3 else v2_blob_problems, m=model)
        self.snap_dpi = partial(_snap_dpi, m=model)

    @staticmethod
    def flat(settings: Dict[str, object]) -> Dict[str, object]:
        return dict(settings)

    @staticmethod
    def validate_settings(s: Dict[str, object]) -> None:
        pass                    # the one setting (the sleep timer) is checked key by key

    @staticmethod
    def merge_backup(fresh: bytes, backup: bytes) -> bytes:
        return bytes(backup)


def _protocol_of(model: Model) -> str:
    return next(link.protocol for link in GLORIOUS_LINKS.values() if link.model == model.key)


CODECS = {m.key: GloriousCodec(m, _protocol_of(m)) for m in MODELS.values() if m.brand == "Glorious"}


def link_for(pid: int, protocol: str) -> GloriousLink:
    """The link for this USB product id; a protocol's first model over the cable when unknown."""
    link = GLORIOUS_LINKS.get(pid)
    if link is not None and link.protocol == protocol:
        return link
    return next(lk for lk in GLORIOUS_LINKS.values() if lk.protocol == protocol and lk.wired)


# ================================================================ drivers

class _GloriousDriver(Driver):
    protocol = ""

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None], pid: int = 0):
        self.link = link_for(pid, self.protocol)
        super().__init__(dev, self.link.wired, sleep)
        self.pid = self.link.pid
        self.model = MODELS[self.link.model]
        self.codec = CODECS[self.model.key]

    @property
    def write_enabled(self) -> bool:
        return self.g.identified and self.pid in GLORIOUS_LINKS and self.model.confirmed and bool(self.codec.KEYS)

    def link_check(self) -> None:
        self.g.firmware()

    def _snap(self, fw: str = "") -> Snapshot:
        return Snapshot(self.wired, "", fw, self.pid, model=self.model.key)

    def _check_write(self, op) -> None:
        if not self.write_enabled:
            raise WriteBlocked("writes are disabled: the mouse has not been identified")
        problems = self.codec.blob_problems(op.record)
        if problems:
            raise Rejected("refusing to write an invalid settings record: " + "; ".join(problems))


class GloriousV3Driver(_GloriousDriver):
    """Glorious Model O3 / D3 (wired, or through the 2.4 GHz dongle they share)."""
    protocol = "v3"
    model = GLORIOUS_O3
    codec = CODECS[GLORIOUS_O3.key]

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None], pid: int = 0):
        super().__init__(dev, wired, sleep, pid)
        self.g = V3Device(dev, self.wired, sleep)

    def state_problem(self) -> str:
        if not self.model.confirmed:
            return ("it hasn't been tested on the " + self.model.name + " yet, so it only reads the battery and the "
                    "sleep timer for now (this mouse can't report its DPI stages or polling rate back)")
        return ""

    def identify(self, progress, f0, f1) -> Snapshot:
        progress(f0, "Contacting the mouse…" if self.wired else "Contacting the dongle…")
        try:
            fw = self.g.firmware()
            if self.pid == GLORIOUS_O3_DONGLE:         # the O3 and the D3 share it: the mouse says which it is
                progress(f0 + (f1 - f0) * 0.5, "Identifying the mouse…")
                name = self.g.device_name().upper()
                if "D3" in name:
                    self.model = MODELS[GLORIOUS_D3.key]
                elif "O3" in name:
                    self.model = MODELS[GLORIOUS_O3.key]
                self.codec = CODECS[self.model.key]
        except self.link_errors as e:
            raise self.unreachable(self._snap(), e) from e
        return self._snap(fw)

    def battery(self) -> Battery:
        if self.pid in V3_WIRED_ONLY:
            raise StatusError(V3_BATTERY, 0)          # no battery to ask about
        return self.g.battery()

    def read_blob(self) -> bytes:
        return v3_pack(*self.g.power_saving())

    def write(self, op: Op) -> None:
        self._check_write(op)
        if op.kind == "sleep":
            b = op.record
            self.g.set_power_saving(_be32(b[2:6]), _be32(b[6:10]))


class GloriousV2ProDriver(_GloriousDriver):
    """The Sinowealth Glorious wireless mice (Model O / O- / O Pro / D / D- / D Pro, Series One Pro, O2 Pro /
    D2 Pro, 4K/8K, Series 2 Pro). Battery and firmware only: nothing else can be read back."""
    protocol = "v2pro"
    model = MODELS["glorious-model-o-wireless"]
    codec = CODECS["glorious-model-o-wireless"]

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None], pid: int = 0):
        super().__init__(dev, wired, sleep, pid)
        self.g = V2ProDevice(dev, self.wired, sleep)

    def state_problem(self) -> str:
        text = "this mouse can't report its settings back, so Wheelguard only shows its battery"
        return text + ("" if self.model.confirmed else " (and it hasn't been tested on the " + self.model.name + " yet)")

    def identify(self, progress, f0, f1) -> Snapshot:
        progress(f0, "Contacting the mouse…" if self.wired else "Contacting the dongle…")
        fw = ""
        try:
            fw = self.g.firmware()
            if not self.wired:
                progress(f0 + (f1 - f0) * 0.5, "Looking for the mouse…")
                self.g.battery()            # status A4: the dongle answers but can't find the mouse
        except self.link_errors as e:
            raise self.unreachable(self._snap(fw), e) from e
        return self._snap(fw)

    def battery(self) -> Battery:
        return self.g.battery()

    def read_blob(self) -> bytes:
        return v2_pack()

    def write(self, op) -> None:
        self._check_write(op)
        raise Rejected("nothing on this mouse can be written")
