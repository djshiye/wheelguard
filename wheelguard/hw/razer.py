"""Razer mice: transport, codec and driver. See docs/razer-protocol.md.

The protocol is Razer's 90-byte Feature report, as implemented by the OpenRazer kernel driver for the
DeathAdder V4 Pro (PIDs 0x00BE wired, 0x00BF wireless) and, with the per-model differences listed in
models.RAZER_LINKS (polling-rate command, reply wait, DPI range), for the rest of the Razer family. Every byte that reaches the device goes through
RazerDevice._exchange(), which enforces an allow-list of (command class, command id, size) and checks
every write's arguments. Nothing else can be sent: no device-mode switch, no firmware commands, no
lighting, no unknown command.

Wheelguard keeps what it read in a small fixed record (the "blob", BLOB_LEN bytes, layout below) so
the session's generic diff / backup / verify flow works the same way as for the other mice. The
record never goes to the mouse: plan_writes() turns a changed record into the matching commands.

Not verified on hardware.
"""
import time
from dataclasses import dataclass
from functools import partial
from typing import Callable, Dict, List, Optional, Tuple

from .drivers import Driver
from .endgame import LinkError, Rejected, StatusError, WriteBlocked
from .errors import Battery, HwError, Snapshot
from .hidraw import RAZER_DA_V4_PRO_WIRED, RAZER_PIDS
from .models import DEATHADDER_V4_PRO as MODEL
from .models import MODELS, RAZER_LINKS, Model, RazerLink

# ---------------------------------------------------------------- transport

REPORT_LEN = 90
TRANSACTION_ID = 0x1F          # what OpenRazer uses for this mouse
VARSTORE = 0x01
WAIT_S = 0.031                 # OpenRazer's RAZER_NEW_MOUSE_RECEIVER_WAIT_US for this mouse
ATTEMPTS = 5                   # like OpenRazer: retry a bad or failed reply after 10 ms
RETRY_S = 0.010

ST_BUSY, ST_OK, ST_FAILURE, ST_TIMEOUT, ST_UNSUPPORTED = 1, 2, 3, 4, 5

# (class, id) -> data_size of the request
GET_FIRMWARE = (0x00, 0x81)
GET_POLLING = (0x00, 0xC0)          # "hyper" polling (HyperPolling mice)
GET_POLLING_CLASSIC = (0x00, 0x85)  # "classic" polling: 1000 / 500 / 125 Hz
GET_STAGES = (0x04, 0x86)
GET_BATTERY = (0x07, 0x80)
GET_CHARGING = (0x07, 0x84)
GET_IDLE = (0x07, 0x83)
GET_LOW_BATTERY = (0x07, 0x81)
SET_POLLING = (0x00, 0x40)
SET_POLLING_CLASSIC = (0x00, 0x05)
SET_DPI = (0x04, 0x05)
SET_STAGES = (0x04, 0x06)
SET_IDLE = (0x07, 0x03)
SET_LOW_BATTERY = (0x07, 0x01)

READS = {GET_FIRMWARE: 0x02, GET_POLLING: 0x01, GET_POLLING_CLASSIC: 0x01, GET_STAGES: 0x26, GET_BATTERY: 0x02,
         GET_CHARGING: 0x02, GET_IDLE: 0x02, GET_LOW_BATTERY: 0x01}
WRITES = {SET_POLLING: 0x02, SET_POLLING_CLASSIC: 0x01, SET_DPI: 0x07, SET_STAGES: 0x26, SET_IDLE: 0x02,
          SET_LOW_BATTERY: 0x01}
HYPER_ONLY, CLASSIC_ONLY = {GET_POLLING, SET_POLLING}, {GET_POLLING_CLASSIC, SET_POLLING_CLASSIC}
# "classic" codes as the mouse sends them <-> the "hyper" codes Wheelguard keeps in its record (models.Polling)
CLASSIC_TO_HYPER = {0x08: 0x40, 0x02: 0x10, 0x01: 0x08}
HYPER_TO_CLASSIC = {v: k for k, v in CLASSIC_TO_HYPER.items()}
RATE_OF = {0x40: 125, 0x20: 250, 0x10: 500, 0x08: 1000, 0x04: 2000, 0x02: 4000, 0x01: 8000}   # code -> Hz
ALLOWED = {**READS, **WRITES}

DPI_MIN, DPI_MAX = MODEL.dpi_min, MODEL.dpi_max      # the DeathAdder V4 Pro's; other models pass theirs
MAX_STAGES = MODEL.max_stages                       # 5 on every Razer model listed
IDLE_MIN_S, IDLE_MAX_S = 60, 900
LOW_MIN, LOW_MAX = 0x0C, 0x3F
POLLING_CODES = MODEL.polling_codes


def crc(report: bytes) -> int:
    c = 0
    for b in report[2:88]:
        c ^= b
    return c


def build(cmd: Tuple[int, int], args: bytes = b"") -> bytes:
    """A request report. Does not validate: _exchange() does that for everything that is sent."""
    r = bytearray(REPORT_LEN)
    r[1] = TRANSACTION_ID
    r[5] = ALLOWED.get(cmd, len(args))
    r[6], r[7] = cmd
    r[8:8 + len(args)] = args
    r[88] = crc(r)
    return bytes(r)


def _u16(hi: int, lo: int) -> int:
    return (hi << 8) | lo


def link_for(pid: int) -> RazerLink:
    """How to talk to the mouse with this USB product id (the DeathAdder V4 Pro's when unknown)."""
    return RAZER_LINKS.get(pid) or RAZER_LINKS[RAZER_DA_V4_PRO_WIRED]


def check_request(r: bytes, m: Model = MODEL, link: Optional[RazerLink] = None) -> Tuple[int, int]:
    """Validate an outgoing report and return its (class, id). Raises Rejected. `m` gives the DPI range and
    polling codes, `link` which polling command this connection takes (both: the DeathAdder V4 Pro)."""
    if len(r) != REPORT_LEN:
        raise Rejected(f"Razer reports are {REPORT_LEN} bytes, got {len(r)}")
    cmd = (r[6], r[7])
    if cmd not in ALLOWED:
        raise Rejected(f"command {cmd[0]:02x}:{cmd[1]:02x} is not on the allow-list")
    style = link.polling if link is not None else "hyper"
    if cmd in (CLASSIC_ONLY if style == "hyper" else HYPER_ONLY):
        raise Rejected(f"command {cmd[0]:02x}:{cmd[1]:02x} isn't this mouse's polling-rate command")
    dpi_min, dpi_max, polling_codes = m.dpi_min, m.dpi_max, m.polling_codes
    if r[0] or r[1] != TRANSACTION_ID or r[2] or r[3] or r[4] or r[89]:
        raise Rejected("malformed report header")
    if r[5] != ALLOWED[cmd]:
        raise Rejected(f"command {cmd[0]:02x}:{cmd[1]:02x}: wrong data size {r[5]}")
    if r[88] != crc(r):
        raise Rejected("bad checksum")
    a = r[8:88]
    if any(a[r[5]:]):
        raise Rejected("data beyond the argument length")
    if cmd in READS:
        if any(a[1:]) or a[0] not in ((VARSTORE,) if cmd == GET_STAGES else (0,)):
            raise Rejected("read commands carry no arguments")
    elif cmd == SET_POLLING:
        if a[0] not in (0, 1) or a[1] not in polling_codes:
            raise Rejected("unknown polling rate")
    elif cmd == SET_POLLING_CLASSIC:
        if CLASSIC_TO_HYPER.get(a[0]) not in polling_codes:
            raise Rejected("unknown polling rate")
    elif cmd == SET_DPI:
        x, y = _u16(a[1], a[2]), _u16(a[3], a[4])
        if a[0] != VARSTORE or not (dpi_min <= x <= dpi_max and dpi_min <= y <= dpi_max) or a[5] or a[6]:
            raise Rejected("DPI out of range")
    elif cmd == SET_STAGES:
        active, count = a[1], a[2]
        if a[0] != VARSTORE or not 1 <= count <= MAX_STAGES or not 1 <= active <= count:
            raise Rejected("DPI stage count / active stage out of range")
        for i in range(count):
            rec = a[3 + 7 * i:10 + 7 * i]
            x, y = _u16(rec[1], rec[2]), _u16(rec[3], rec[4])
            if rec[0] != i or not (dpi_min <= x <= dpi_max and dpi_min <= y <= dpi_max) or rec[5] or rec[6]:
                raise Rejected(f"DPI stage {i + 1} out of range")
        if any(a[3 + 7 * count:]):
            raise Rejected("data after the last DPI stage")
    elif cmd == SET_IDLE:
        if not IDLE_MIN_S <= _u16(a[0], a[1]) <= IDLE_MAX_S:
            raise Rejected("sleep timer out of range")
    elif cmd == SET_LOW_BATTERY:
        if not LOW_MIN <= a[0] <= LOW_MAX:
            raise Rejected("low-battery threshold out of range")
    return cmd


class RazerDevice:
    """One open interface-0 hidraw node. `dev` provides set_feature(bytes) and get_feature(id, len)."""

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None] = time.sleep, pid: int = 0):
        self._dev = dev
        self.wired = wired
        self._sleep = sleep
        self.link = link_for(pid or (RAZER_DA_V4_PRO_WIRED if wired else RAZER_PIDS[1]))
        self.model = MODELS[self.link.model]
        self.identified = False      # the write gate: a firmware read answered on a supported PID

    def _exchange(self, r: bytes) -> bytes:
        """Send one request and return the 80 argument bytes of a matching, successful reply."""
        cmd = check_request(r, self.model, self.link)
        if cmd in WRITES and not self.identified:
            raise WriteBlocked("writes are disabled: the mouse has not answered yet")
        status = 0
        for attempt in range(ATTEMPTS):
            if attempt:
                self._sleep(RETRY_S)
            try:
                self._dev.set_feature(b"\x00" + r)
                self._sleep(self.link.wait_s)
                reply = bytes(self._dev.get_feature(0x00, REPORT_LEN + 1))[1:]
            except OSError as e:
                if attempt < ATTEMPTS - 1:
                    continue
                raise LinkError(e.errno or 0, "Razer report") from e
            if len(reply) < REPORT_LEN or reply[2:4] != r[2:4] or reply[6] != r[6] or reply[7] != r[7]:
                status = 0
                continue                 # a reply to something else: ask again
            status = reply[0]
            if status in (ST_OK, ST_BUSY):     # OpenRazer: some commands answer "busy" but succeed
                return reply[8:88]
        raise StatusError((cmd[0] << 8) | cmd[1], status)

    def request(self, cmd: Tuple[int, int], args: bytes = b"") -> bytes:
        return self._exchange(build(cmd, args))

    # ---- reads
    def firmware(self) -> str:
        a = self.request(GET_FIRMWARE)
        self.identified = True
        return f"{a[0]}.{a[1]}"

    def stages(self) -> Tuple[int, List[Tuple[int, int]]]:
        """(active stage, 0-based; [(x, y), ...]) as stored on the mouse."""
        a = self.request(GET_STAGES, bytes((VARSTORE,)))
        count = min(a[2], MAX_STAGES)
        out = []
        for i in range(count):
            rec = a[3 + 7 * i:10 + 7 * i]
            out.append((_u16(rec[1], rec[2]), _u16(rec[3], rec[4])))
        return max(0, a[1] - 1), out

    def polling(self) -> int:
        """The polling rate as a "hyper" code (models.Polling), whichever command this mouse takes; 0 when
        the mouse answers a value this app doesn't know."""
        if self.link.polling == "classic":
            return CLASSIC_TO_HYPER.get(self.request(GET_POLLING_CLASSIC)[0], 0)
        return self.request(GET_POLLING)[1]

    def idle_seconds(self) -> int:
        a = self.request(GET_IDLE)
        return _u16(a[0], a[1])

    def low_battery(self) -> int:
        return self.request(GET_LOW_BATTERY)[0]

    def battery(self) -> Battery:
        level = self.request(GET_BATTERY)[1]
        try:
            charging: Optional[bool] = self.request(GET_CHARGING)[1] == 1
        except StatusError:
            charging = None
        return Battery(min(100, round(level * 100 / 255)), charging)

    # ---- writes
    def set_stages(self, active: int, stages: List[Tuple[int, int]]) -> None:
        args = bytearray((VARSTORE, active + 1, len(stages)))
        for i, (x, y) in enumerate(stages):
            args += bytes((i, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0))
        self.request(SET_STAGES, bytes(args))

    def set_dpi(self, x: int, y: int) -> None:
        self.request(SET_DPI, bytes((VARSTORE, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0)))

    def set_polling(self, code: int) -> None:
        if self.link.polling == "classic":
            if code not in HYPER_TO_CLASSIC:
                raise Rejected("this mouse offers 125, 500 and 1000 Hz only")
            self.request(SET_POLLING_CLASSIC, bytes((HYPER_TO_CLASSIC[code],)))
            return
        # OpenRazer sends this one twice for the DeathAdder V4 Pro, with 0x00 and then 0x01 first.
        self.request(SET_POLLING, bytes((0x00, code)))
        self.request(SET_POLLING, bytes((0x01, code)))

    def set_idle(self, seconds: int) -> None:
        self.request(SET_IDLE, bytes((seconds >> 8, seconds & 0xFF)))

    def set_low_battery(self, raw: int) -> None:
        self.request(SET_LOW_BATTERY, bytes((raw,)))


# ---------------------------------------------------------------- codec

# The record Wheelguard keeps (never sent to the mouse as such):
#   0     magic 'R'           1   layout version (1)
#   2     active stage (0-based)        3   stage count (1-5)
#   4-23  five stages: X, Y (u16 big-endian each); unused stages are zero
#   24    polling code (see MODEL.polling)
#   25-26 sleep timer, seconds (u16 big-endian)
#   27    low-battery threshold, raw (0x0C-0x3F of 255)
#   28-31 zero
BLOB_LEN = 32
MAGIC, VERSION = 0x52, 1

# Low-battery warning: the mouse stores a fraction of 255; the page offers these.
LOW_BATTERY = ((0x0D, "5%"), (0x1A, "10%"), (0x26, "15%"), (0x33, "20%"), (0x3F, "25%"))
LOW_BATTERY_CODES = tuple(c for c, _l in LOW_BATTERY)
IDLE_MIN, IDLE_MAX = IDLE_MIN_S // 60, IDLE_MAX_S // 60

KEYS = tuple([f"dpi_x{k}" for k in range(MAX_STAGES)] + [f"dpi_y{k}" for k in range(MAX_STAGES)]
             + ["stage_count", "active_stage", "polling", "idle_min", "low_battery"])


def pack(active: int, stages: List[Tuple[int, int]], polling: int, idle_s: int, low: int) -> bytes:
    b = bytearray(BLOB_LEN)
    b[0], b[1], b[2], b[3] = MAGIC, VERSION, active, len(stages)
    for k, (x, y) in enumerate(stages[:MAX_STAGES]):
        b[4 + 4 * k:8 + 4 * k] = bytes((x >> 8, x & 0xFF, y >> 8, y & 0xFF))
    b[24] = polling
    b[25], b[26] = idle_s >> 8, idle_s & 0xFF
    b[27] = low
    return bytes(b)


def _stages(b: bytes) -> List[Tuple[int, int]]:
    return [(_u16(b[4 + 4 * k], b[5 + 4 * k]), _u16(b[6 + 4 * k], b[7 + 4 * k])) for k in range(min(b[3], MAX_STAGES))]


def decode(blob: bytes) -> Dict[str, object]:
    if len(blob) != BLOB_LEN:
        raise ValueError(f"Razer record must be {BLOB_LEN} bytes, got {len(blob)}")
    b = blob
    d: Dict[str, object] = {}
    for k in range(MAX_STAGES):
        o = 4 + 4 * k
        d[f"dpi_x{k}"] = _u16(b[o], b[o + 1])
        d[f"dpi_y{k}"] = _u16(b[o + 2], b[o + 3])
    d["stage_count"] = b[3]
    d["active_stage"] = b[2]
    d["polling"] = b[24]
    d["idle_min"] = max(IDLE_MIN, min(IDLE_MAX, round(_u16(b[25], b[26]) / 60)))
    d["low_battery"] = b[27]
    return d


def flat(settings: Dict[str, object]) -> Dict[str, object]:
    return dict(settings)


def empty() -> Dict[str, object]:
    return decode(pack(0, [], 0, 0, 0))


def snap_dpi(v: int, m: Model = MODEL) -> int:
    v = max(m.dpi_min, min(m.dpi_max, int(v)))
    return max(m.dpi_min, min(m.dpi_max, int(round(v / m.dpi_step)) * m.dpi_step))


def check_key_value(key: str, v, m: Model = MODEL) -> None:
    if key not in KEYS:
        raise ValueError(f"unknown setting: {key}")
    if key.startswith("dpi_"):
        if not m.dpi_min <= v <= m.dpi_max:
            raise ValueError(f"DPI must be {m.dpi_min}-{m.dpi_max}")
    elif key == "stage_count":
        if not 1 <= v <= MAX_STAGES:
            raise ValueError(f"stage count must be 1-{MAX_STAGES}")
    elif key == "active_stage":
        if not 0 <= v < MAX_STAGES:
            raise ValueError("active stage out of range")
    elif key == "polling":
        if v not in m.polling_codes:
            raise ValueError("unknown polling rate")
    elif key == "idle_min":
        if not IDLE_MIN <= v <= IDLE_MAX:
            raise ValueError(f"sleep timer must be {IDLE_MIN}-{IDLE_MAX} minutes")
    elif key == "low_battery":
        if not LOW_MIN <= v <= LOW_MAX:
            raise ValueError("low-battery threshold out of range")


def encode(blob: bytes, edits: Dict[str, object], m: Model = MODEL) -> bytes:
    """Copy of the record with only the edited fields changed."""
    current = decode(blob)
    ba = bytearray(blob)
    for key, v in edits.items():
        if current.get(key) == v:
            continue
        v = int(v)
        check_key_value(key, v, m)
        if key.startswith("dpi_"):
            k, axis = int(key[5:]), 0 if key[4] == "x" else 1
            o = 4 + 4 * k + 2 * axis
            ba[o], ba[o + 1] = v >> 8, v & 0xFF
        elif key == "stage_count":
            ba[3] = v
            for k in range(MAX_STAGES):       # a new stage starts as a copy of the one before it
                o = 4 + 4 * k
                if k < v and not any(ba[o:o + 4]):
                    ba[o:o + 4] = ba[o - 4:o] if k else bytes((0x03, 0x20, 0x03, 0x20))
        elif key == "active_stage":
            ba[2] = v
        elif key == "polling":
            ba[24] = v
        elif key == "idle_min":
            ba[25], ba[26] = (v * 60) >> 8, (v * 60) & 0xFF
        elif key == "low_battery":
            ba[27] = v
    return bytes(ba)


def changed_keys(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    return [k for k in KEYS if old.get(k) != new.get(k)]


def validate_settings(s: Dict[str, object], m: Model = MODEL) -> None:
    n = int(s["stage_count"])
    if not 1 <= n <= MAX_STAGES or not 0 <= int(s["active_stage"]) < n:
        raise ValueError("The active DPI stage must be one of the enabled stages.")
    for k in range(n):
        if not (m.dpi_min <= s[f"dpi_x{k}"] <= m.dpi_max and m.dpi_min <= s[f"dpi_y{k}"] <= m.dpi_max):
            raise ValueError(f"DPI stage {k + 1} must be {m.dpi_min}-{m.dpi_max}.")


def blob_problems(blob: bytes, m: Model = MODEL) -> List[str]:
    if len(blob) != BLOB_LEN:
        return [f"expected {BLOB_LEN} bytes, got {len(blob)}"]
    probs = []
    if blob[0] != MAGIC or blob[1] != VERSION:
        probs.append("not a Wheelguard Razer record")
    if not 1 <= blob[3] <= MAX_STAGES:
        probs.append(f"DPI stage count {blob[3]} is out of range")
    elif blob[2] >= blob[3]:
        probs.append(f"active DPI stage {blob[2] + 1} is out of range")
    else:
        for k, (x, y) in enumerate(_stages(blob)):
            if not (m.dpi_min <= x <= m.dpi_max and m.dpi_min <= y <= m.dpi_max):
                probs.append(f"DPI stage {k + 1} ({x} × {y}) is out of range")
    if blob[24] not in m.polling_codes:
        probs.append(f"polling code {blob[24]:#04x} is not one this app knows")
    if not IDLE_MIN_S <= _u16(blob[25], blob[26]) <= IDLE_MAX_S:
        probs.append("sleep timer is out of range")
    if not LOW_MIN <= blob[27] <= LOW_MAX:
        probs.append("low-battery threshold is out of range")
    return probs


@dataclass(frozen=True)
class Op:
    kind: str            # "stages" | "polling" | "idle" | "low_battery"
    label: str
    record: bytes        # the new record; write() takes the values for this op from it


def plan_writes(old: bytes, new: bytes) -> List[Op]:
    plan: List[Op] = []
    if old[2:24] != new[2:24]:
        plan.append(Op("stages", "sensitivity", new))
    if old[24] != new[24]:
        plan.append(Op("polling", "polling rate", new))
    if old[25:27] != new[25:27]:
        plan.append(Op("idle", "sleep", new))
    if old[27] != new[27]:
        plan.append(Op("low_battery", "low-battery warning", new))
    return plan


def merge_backup(fresh: bytes, backup: bytes) -> bytes:
    """Everything this app can write comes from the backup."""
    return bytes(backup)


def describe_changes(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    out: List[str] = []
    for k in changed_keys(old, new):
        if k.startswith("dpi_"):
            label = f"DPI stage {int(k[5:]) + 1}"
        else:
            label = {"stage_count": "Stage count", "active_stage": "Active stage", "polling": "Polling rate",
                     "idle_min": "Sleep", "low_battery": "Low-battery warning"}[k]
        if label not in out:
            out.append(label)
    return out


# ---------------------------------------------------------------- driver

class RazerCodec:
    """The module-level functions above for one model, as the object the session and the GUI expect."""
    BLOB_LEN = BLOB_LEN
    KEYS = KEYS
    decode = staticmethod(decode)
    flat = staticmethod(flat)
    empty = staticmethod(empty)
    changed_keys = staticmethod(changed_keys)
    describe_changes = staticmethod(describe_changes)
    plan_writes = staticmethod(plan_writes)
    merge_backup = staticmethod(merge_backup)

    def __init__(self, model: Model):
        self.model = model
        self.encode = partial(encode, m=model)
        self.snap_dpi = partial(snap_dpi, m=model)
        self.check_key_value = partial(check_key_value, m=model)
        self.validate_settings = partial(validate_settings, m=model)
        self.blob_problems = partial(blob_problems, m=model)


Codec = RazerCodec(MODEL)                   # the DeathAdder V4 Pro's
CODECS = {m.key: Codec if m is MODEL else RazerCodec(m) for m in MODELS.values() if m.brand == "Razer"}


class RazerDriver(Driver):
    model = MODEL
    codec = Codec

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None], pid: int = 0):
        super().__init__(dev, wired, sleep)
        self.pid = pid or (RAZER_DA_V4_PRO_WIRED if wired else RAZER_PIDS[1])
        self.rz = RazerDevice(dev, wired, sleep, self.pid)
        self.model = self.rz.model          # the connected model, not the class default
        self.codec = CODECS[self.model.key]

    @property
    def write_enabled(self) -> bool:
        return self.rz.identified and self.pid in RAZER_PIDS and self.model.confirmed

    def state_problem(self) -> str:
        if not self.model.confirmed:
            return "it hasn't been tested on the " + self.model.name + " yet, so it only reads the settings for now"
        return ""

    def identify(self, progress, f0, f1) -> Snapshot:
        progress(f0, "Contacting the mouse…" if self.wired else "Contacting the dongle…")
        try:
            fw = self.rz.firmware()
        except self.link_errors as e:
            raise self.unreachable(Snapshot(self.wired, "", "", self.pid, model=self.model.key), e) from e
        progress(f0 + (f1 - f0) * 0.6, "Identifying the mouse…")
        return Snapshot(self.wired, "", fw, self.pid, model=self.model.key)

    def link_check(self) -> None:
        self.rz.firmware()

    def battery(self) -> Battery:
        return self.rz.battery()

    def read_blob(self) -> bytes:
        active, stages = self.rz.stages()
        if not stages:
            raise StatusError(GET_STAGES[0] << 8 | GET_STAGES[1], 0)
        return pack(min(active, len(stages) - 1), stages, self.rz.polling(), self.rz.idle_seconds(),
                    self.rz.low_battery())

    def write(self, op: Op) -> None:
        if not self.write_enabled:
            raise WriteBlocked("writes are disabled: the mouse has not been identified")
        b = op.record
        problems = self.codec.blob_problems(b)
        if problems:
            raise Rejected("refusing to write an invalid settings record: " + "; ".join(problems))
        if op.kind == "polling" and RATE_OF.get(b[24]) not in self.rz.link.rates:
            raise Rejected("this connection doesn't offer that polling rate")
        if op.kind == "stages":
            stages = _stages(b)
            self.rz.set_stages(b[2], stages)
            self.rz.set_dpi(*stages[b[2]])       # make the active stage's DPI take effect now
        elif op.kind == "polling":
            self.rz.set_polling(b[24])
        elif op.kind == "idle":
            self.rz.set_idle(_u16(b[25], b[26]))
        elif op.kind == "low_battery":
            self.rz.set_low_battery(b[27])

    def unreachable(self, snap, e) -> HwError:
        err = super().unreachable(snap, e)
        if isinstance(e, StatusError) and e.status == ST_UNSUPPORTED:
            return HwError("The mouse doesn't support this command (its firmware may be newer than "
                           "this app knows).", snap)
        return err
