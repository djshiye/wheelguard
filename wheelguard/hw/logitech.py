"""Logitech G Pro X Superlight (first generation): codec and driver. See docs/logitech-protocol.md.

The mouse keeps its settings in onboard profiles (HID++ feature 0x8100): flash sectors with a fixed
layout and a CRC. The mouse's own DPI button cycles through the profile's five DPI slots, and the
profile also holds the report rate. Wheelguard edits the profile the mouse is using, the same thing
G HUB, libratbag (Piper) and Solaar write, and leaves every byte it doesn't understand (G-shift
buttons, macros, lighting, name, power settings, ...) exactly as read. The five main buttons can be
set to a mouse click or "next DPI stage"; a button set to anything else (a key, a macro) is shown as
custom and is never rewritten unless the user picks a new action for it.

The blob is that profile sector followed by one byte: the DPI slot in use right now (the mouse's
"current DPI index", which the DPI button changes and which isn't stored in the sector). Changing
only the active stage therefore never writes flash.

A mouse that was never configured has no valid profile directory and runs on its read-only factory
(ROM) profile. The first Apply then does what libratbag does: it writes the edited copy of that
profile to user sector 1, writes a directory that enables it, and selects it.

Not verified on hardware.
"""
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import hidpp
from .drivers import Driver
from .endgame import LinkError, Rejected, StatusError, WriteBlocked
from .errors import Battery, HwError, MouseAsleep, Snapshot
from .hidraw import LOGI_PIDS, LOGI_SUPERLIGHT_WIRED, LOGI_SUPERLIGHT_WIRELESS
from .models import SUPERLIGHT as MODEL

# ---------------------------------------------------------------- profile layout (libratbag's
# hidpp20_internal_profile; Solaar's OnboardProfile): offsets into the sector
P_REPORT_RATE = 0       # report interval in ms
P_DEFAULT_DPI = 1       # DPI slot the profile starts in
P_SHIFT_DPI = 2         # DPI-shift ("sniper") slot, left alone
P_DPI = 3               # five little-endian u16; 0 = slot unused
N_SLOTS = 5
P_BUTTONS = 0x20        # 16 button bindings, 4 bytes each (then 16 G-shift bindings at 0x60, left alone)
N_BUTTONS = 5           # left, right, middle, back, forward: the Superlight's five buttons
ROM_BASE = 0x0100       # ROM profiles live at sectors 0x0101...
MIN_SECTOR = 64
MAX_SECTOR = 4096
KNOWN_FORMATS = (1, 2, 3, 4, 5)     # profile formats libratbag can parse; others stay read-only

DPI_MIN, DPI_MAX = MODEL.dpi_min, MODEL.dpi_max
POLLING_CODES = MODEL.polling_codes
NEW_SLOT_DPI = 800

# Button actions. The codes are the ones the Endgame Gear codec uses for the same clicks, so the page
# treats every mouse alike; NEXT_DPI is new. 0 = "something this app doesn't set" (shown as custom).
LEFT, RIGHT, MIDDLE, BACK, FORWARD, NEXT_DPI = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010, 0x0300
ACTION_LIST = ((LEFT, "Left click"), (RIGHT, "Right click"), (MIDDLE, "Middle click"), (BACK, "Back"),
           (FORWARD, "Forward"), (NEXT_DPI, "Next DPI stage"))
ACTION_CODES = tuple(c for c, _n in ACTION_LIST)
BUTTON_NAMES = ("Left button", "Right button", "Middle click", "Back (side)", "Forward (side)")
# The 4-byte bindings (libratbag hidpp20.h, Solaar's Button): 0x80 = send, subtype 1 = mouse buttons
# (big-endian bit mask); 0x90 = function, 5 = cycle the DPI stages.
_BIND_MOUSE, _BIND_FUNCTION, _FN_CYCLE_DPI = 0x80, 0x90, 0x05

KEYS = tuple([f"dpi_x{k}" for k in range(N_SLOTS)] + [f"dpi_y{k}" for k in range(N_SLOTS)]
             + ["stage_count", "active_stage", "polling"] + [f"button{k}" for k in range(N_BUTTONS)])


def sector_crc_ok(sector: bytes) -> bool:
    return len(sector) >= 4 and hidpp.crc_ccitt(sector[:-2]) == ((sector[-2] << 8) | sector[-1])


def with_crc(sector: bytes) -> bytes:
    c = hidpp.crc_ccitt(sector[:-2])
    return bytes(sector[:-2]) + bytes((c >> 8, c & 0xFF))


def _dpi(b: bytes, k: int) -> int:
    v = b[P_DPI + 2 * k] | (b[P_DPI + 2 * k + 1] << 8)
    return 0 if v == 0xFFFF else v


def _count(b: bytes) -> int:
    n = 0
    while n < N_SLOTS and _dpi(b, n):
        n += 1
    return n


def _button(b: bytes, k: int) -> int:
    o = P_BUTTONS + 4 * k
    t, sub, hi, lo = b[o], b[o + 1], b[o + 2], b[o + 3]
    if t == _BIND_MOUSE and sub == 0x01 and ((hi << 8) | lo) in (LEFT, RIGHT, MIDDLE, BACK, FORWARD):
        return (hi << 8) | lo
    if t == _BIND_FUNCTION and sub == _FN_CYCLE_DPI:
        return NEXT_DPI
    return 0


def _binding(code: int) -> bytes:
    if code == NEXT_DPI:
        return bytes((_BIND_FUNCTION, _FN_CYCLE_DPI, 0x00, 0x00))     # what libratbag writes
    return bytes((_BIND_MOUSE, 0x01, code >> 8, code & 0xFF))


def decode(blob: bytes) -> Dict[str, object]:
    if not MIN_SECTOR + 1 <= len(blob) <= MAX_SECTOR + 1:
        raise ValueError(f"profile blob has an impossible length ({len(blob)})")
    b, live = blob[:-1], blob[-1]
    n = _count(b)
    d: Dict[str, object] = {}
    for k in range(N_SLOTS):
        d[f"dpi_x{k}"] = d[f"dpi_y{k}"] = _dpi(b, k)
    d["stage_count"] = n
    if live < n:
        d["active_stage"] = live
    else:
        d["active_stage"] = b[P_DEFAULT_DPI] if b[P_DEFAULT_DPI] < n else 0
    d["polling"] = b[P_REPORT_RATE]
    for k in range(N_BUTTONS):
        d[f"button{k}"] = _button(b, k)
    return d


def flat(settings: Dict[str, object]) -> Dict[str, object]:
    return dict(settings)


def empty() -> Dict[str, object]:
    return decode(bytes(MIN_SECTOR + 1))


def snap_dpi(v: int) -> int:
    v = max(DPI_MIN, min(DPI_MAX, int(v)))
    return max(DPI_MIN, min(DPI_MAX, int(round(v / MODEL.dpi_step)) * MODEL.dpi_step))


def check_key_value(key: str, v) -> None:
    if key not in KEYS:
        raise ValueError(f"unknown setting: {key}")
    if key.startswith("dpi_"):
        if not DPI_MIN <= v <= DPI_MAX:
            raise ValueError(f"DPI must be {DPI_MIN}-{DPI_MAX}")
    elif key == "stage_count":
        if not 1 <= v <= N_SLOTS:
            raise ValueError(f"stage count must be 1-{N_SLOTS}")
    elif key == "active_stage":
        if not 0 <= v < N_SLOTS:
            raise ValueError("active stage out of range")
    elif key == "polling":
        if v not in POLLING_CODES:
            raise ValueError("unknown polling rate")
    elif key.startswith("button"):
        if v not in ACTION_CODES:
            raise ValueError("unsupported button action")


def _put_dpi(ba: bytearray, k: int, v: int) -> None:
    ba[P_DPI + 2 * k], ba[P_DPI + 2 * k + 1] = v & 0xFF, v >> 8


def encode(blob: bytes, edits: Dict[str, object]) -> bytes:
    """Copy of the blob with only the edited fields changed; the sector CRC is recomputed when the
    sector changed. dpi_yN is ignored: this sensor has one DPI for both axes."""
    current = decode(blob)
    ba = bytearray(blob)
    sector_len = len(blob) - 1
    for key, v in edits.items():
        if current.get(key) == v:
            continue
        v = int(v)
        check_key_value(key, v)
        if key.startswith("dpi_x"):
            k = int(key[5:])
            if k < _count(ba[:sector_len]) or k < int(edits.get("stage_count", current["stage_count"])):
                _put_dpi(ba, k, v)
        elif key == "stage_count":
            for k in range(N_SLOTS):
                if k >= v:
                    _put_dpi(ba, k, 0)
                elif not _dpi(ba, k):
                    _put_dpi(ba, k, _dpi(ba, k - 1) if k else NEW_SLOT_DPI)
            if ba[P_DEFAULT_DPI] >= v:
                ba[P_DEFAULT_DPI] = 0
            if ba[sector_len] >= v and ba[sector_len] < N_SLOTS:
                ba[sector_len] = v - 1
        elif key == "active_stage":
            ba[sector_len] = v
        elif key == "polling":
            ba[P_REPORT_RATE] = v
        elif key.startswith("button"):
            o = P_BUTTONS + 4 * int(key[6:])
            ba[o:o + 4] = _binding(v)
    if any(k.startswith("button") and current.get(k) != v for k, v in edits.items()) \
            and LEFT not in [_button(ba[:sector_len], k) for k in range(N_BUTTONS)]:
        raise ValueError("At least one button must stay Left click.")
    if "active_stage" in edits and current["active_stage"] != edits["active_stage"] \
            and ba[sector_len] >= _count(ba[:sector_len]):
        # decode() hides a live index past the last stage, so the checks after encode can't see it
        raise ValueError("The active DPI stage must be one of the enabled stages.")
    if bytes(ba[:sector_len]) != bytes(blob[:sector_len]):
        ba[:sector_len] = with_crc(bytes(ba[:sector_len]))
    return bytes(ba)


def changed_keys(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    return [k for k in KEYS if old.get(k) != new.get(k)]


def validate_settings(s: Dict[str, object]) -> None:
    n = int(s["stage_count"])
    if not 1 <= n <= N_SLOTS or not 0 <= int(s["active_stage"]) < n:
        raise ValueError("The active DPI stage must be one of the enabled stages.")
    for k in range(n):
        if not DPI_MIN <= s[f"dpi_x{k}"] <= DPI_MAX:
            raise ValueError(f"DPI stage {k + 1} must be {DPI_MIN}-{DPI_MAX}.")


def blob_problems(blob: bytes) -> List[str]:
    if not MIN_SECTOR + 1 <= len(blob) <= MAX_SECTOR + 1:
        return [f"unexpected profile size ({len(blob) - 1} bytes)"]
    b = blob[:-1]
    probs = []
    if not sector_crc_ok(b):
        probs.append("the profile's checksum doesn't match")
    if all(x == 0xFF for x in b[:-2]) or not any(b[:-2]):
        probs.append("the profile is empty")
        return probs
    if b[P_REPORT_RATE] not in POLLING_CODES:
        probs.append(f"report rate {b[P_REPORT_RATE]} ms is not one this app knows")
    n = _count(b)
    if n == 0:
        probs.append("the profile has no DPI stages")
    for k in range(n):
        if not DPI_MIN <= _dpi(b, k) <= DPI_MAX:
            probs.append(f"DPI stage {k + 1} ({_dpi(b, k)}) is out of range")
    if b[P_DEFAULT_DPI] >= N_SLOTS:
        probs.append("the default DPI stage is out of range")
    return probs


@dataclass(frozen=True)
class Op:
    kind: str           # "mode" | "sector" | "directory" | "select" | "dpi_index"
    label: str
    sector: int = 0
    data: bytes = b""
    index: int = 0


def plan_writes(old: bytes, new: bytes) -> List[Op]:
    """Writes for a mouse that already runs on a user profile (the driver adjusts the rest)."""
    plan: List[Op] = []
    if old[:-1] != new[:-1]:
        plan.append(Op("sector", "profile", data=new[:-1]))
    if old[-1] != new[-1] and new[-1] < N_SLOTS:
        plan.append(Op("dpi_index", "DPI stage", index=new[-1]))
    return plan


def merge_backup(fresh: bytes, backup: bytes) -> bytes:
    if len(fresh) != len(backup):
        raise ValueError("the backup is for a different profile size")
    return bytes(backup)


def describe_changes(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    out: List[str] = []
    for k in changed_keys(old, new):
        if k.startswith("dpi_"):
            label = f"DPI stage {int(k[5:]) + 1}"
        elif k.startswith("button"):
            label = BUTTON_NAMES[int(k[6:])] + " action"
        else:
            label = {"stage_count": "Stage count", "active_stage": "Active stage", "polling": "Polling rate"}[k]
        if label not in out:
            out.append(label)
    return out


def directory(sector_size: int, profiles: int = 1) -> bytes:
    """A profile directory listing user profiles 1..profiles with only profile 1 enabled, like
    libratbag's write_dict after it copied the factory profile to profile 1."""
    d = bytearray(b"\xff" * sector_size)
    o = 0
    for i in range(max(1, min(profiles, 15))):
        d[o:o + 4] = bytes((0x00, i + 1, 0x01 if i == 0 else 0x00, 0x00))
        o += 4
    d[o:o + 4] = b"\xff\xff\x00\x00"
    return with_crc(bytes(d))


def directory_entries(sector: bytes) -> List[tuple]:
    """[(sector address, enabled)] from a valid directory sector."""
    out = []
    for o in range(0, len(sector) - 2 - 3, 4):
        addr = (sector[o] << 8) | sector[o + 1]
        if addr == 0xFFFF:
            break
        out.append((addr, bool(sector[o + 2])))
    return out


class Codec:
    KEYS = KEYS
    ACTION_LIST = ACTION_LIST
    BUTTON_NAMES = BUTTON_NAMES
    decode = staticmethod(decode)
    flat = staticmethod(flat)
    empty = staticmethod(empty)
    encode = staticmethod(encode)
    snap_dpi = staticmethod(snap_dpi)
    check_key_value = staticmethod(check_key_value)
    validate_settings = staticmethod(validate_settings)
    blob_problems = staticmethod(blob_problems)
    changed_keys = staticmethod(changed_keys)
    describe_changes = staticmethod(describe_changes)
    plan_writes = staticmethod(plan_writes)
    merge_backup = staticmethod(merge_backup)


# ---------------------------------------------------------------- driver

class LogitechDriver(Driver):
    model = MODEL
    codec = Codec

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None], pid: int = 0,
                 clock: Optional[Callable[[], float]] = None):
        super().__init__(dev, wired, sleep)
        self.pid = pid or (LOGI_SUPERLIGHT_WIRED if wired else LOGI_SUPERLIGHT_WIRELESS)
        kw = {"clock": clock} if clock else {}
        self.hp = hidpp.HidppDevice(dev, sleep, **kw)
        self.identified = False
        self.sector_size = 0
        self.sector_count = 0
        self.mode = 0
        self.dir_valid = False
        self.current = 0            # sector of the profile the mouse uses
        self.problem = ""           # why writes are refused although the mouse is supported
        self.profile_count = 1

    @property
    def write_enabled(self) -> bool:
        return self.identified and self.pid in LOGI_PIDS and not self.problem

    def identify(self, progress, f0, f1) -> Snapshot:
        span = f1 - f0
        snap = Snapshot(self.wired, "", "", self.pid, model=MODEL.key)
        try:
            progress(f0, "Contacting the mouse…" if self.wired else "Contacting the receiver…")
            self.hp.ping()
            progress(f0 + span * 0.4, "Identifying the mouse…")
            fw = self.hp.firmware()
            snap = Snapshot(self.wired, "", fw, self.pid, model=MODEL.key)
            if not self.hp.has(hidpp.ONBOARD_PROFILES):
                raise HwError("This mouse doesn't offer onboard profiles, so its settings can't be edited.", snap)
            info = self.hp.onboard_info()
        except self.link_errors as e:
            raise self.unreachable(snap, e) from e
        memory_model, profile_format, macro_format, count, _oob, _buttons, sectors = info[:7]
        size = (info[7] << 8) | info[8]
        self.sector_count, self.sector_size, self.profile_count = sectors, size, count
        if memory_model != 1 or macro_format != 1 or profile_format not in KNOWN_FORMATS or not MIN_SECTOR <= size <= MAX_SECTOR \
                or not 1 <= count <= 15:
            self.problem = "the mouse uses an onboard memory layout this app doesn't know"
        self.hp.sector_size = size
        self.identified = True
        self.hp.write_enabled = self.pid in LOGI_PIDS and not self.problem
        return snap

    def link_check(self) -> None:
        self.hp.ping()

    def watch_stage(self, should_stop, seconds: float) -> Optional[int]:
        """The current DPI index, then whatever the mouse announces while we listen: onboard profiles
        event 1 ("DPI index changed", data byte 0), as Solaar handles it. Only read requests are sent."""
        if should_stop():
            return None
        if not self.hp.feature(hidpp.ONBOARD_PROFILES):
            return None
        live = self.hp.dpi_index()
        seen = self.hp.wait_event(hidpp.ONBOARD_PROFILES, 1, should_stop, seconds)
        if seen is None and should_stop():
            return None             # cut short: the index read before listening may be stale
        index = seen[0] if seen is not None else live
        return index if index < N_SLOTS else None

    def battery(self) -> Battery:
        b = self.hp.battery()
        if b is None:
            raise StatusError(hidpp.BATTERY_STATUS, 0)
        return Battery(*b)

    def read_blob(self) -> bytes:
        hp, size = self.hp, self.sector_size
        if not size:
            raise StatusError(hidpp.ONBOARD_PROFILES, 0)
        self.mode = hp.onboard_mode()
        try:
            d = hp.read_sector(0x0000, size)
        except StatusError:
            d = None                       # some firmware refuses to read a directory never written
        self.dir_valid = d is not None and sector_crc_ok(d) and bool(directory_entries(d))
        # "Never written" only when the directory is blank (erased flash), as Solaar checks, never
        # just because a read failed or a CRC didn't match: guessing wrong would overwrite a saved
        # profile with a factory copy.
        blank = d is None or not d.strip(b"\xff") or not d.strip(b"\x00")
        enabled = {a for a, on in directory_entries(d) if on} if self.dir_valid else set()
        cur = hp.current_profile()
        if cur == 0:
            # Some firmware may report a 0-based number. Only trust that when it can't be wrong.
            cur = next(iter(enabled)) if len(enabled) == 1 else 0
            if not cur and not self.problem:
                self.problem = "the mouse didn't say clearly which profile is active"
        if not self.dir_valid and not cur & ROM_BASE and not self.problem:
            if d is not None and not blank:
                self.problem = ("the mouse's profile list couldn't be read reliably, so Wheelguard "
                                "won't change it")
            elif cur != 1:
                self.problem = (f"the mouse reports profile {cur} as active but has no profile list, "
                                "so Wheelguard can't be sure which profile to edit")
        if not self.dir_valid and not self.problem:
            # The factory path writes sector 1. Never do that over a profile that is already saved
            # there (the directory may just have failed to read).
            try:
                saved = sector_crc_ok(hp.read_sector(0x0001, size))
            except StatusError:
                saved = False
            if saved:
                self.problem = ("the mouse has a saved profile but its profile list couldn't be read, "
                                "so Wheelguard won't change it")
            elif not cur & ROM_BASE:
                cur = ROM_BASE | 1         # nothing saved yet: the mouse runs its first factory profile
        if not cur:
            cur = ROM_BASE | 1             # read-only: show the factory profile, write nothing
        self.current = cur
        sector = hp.read_sector(cur, size)
        if cur & ROM_BASE and not sector_crc_ok(sector):
            sector = with_crc(sector)      # some ROM profiles carry no CRC; the copy we write will
        try:
            live = hp.dpi_index()
        except StatusError:
            live = 0xFF
        if self.problem:
            pass                           # already read-only; the first reason is the one shown
        elif cur & ROM_BASE and self.dir_valid:
            self.problem = ("the mouse is using one of its built-in profiles; select a profile saved on "
                            "the mouse to edit it here")
        elif not cur & ROM_BASE and not 1 <= cur < self.sector_count:
            self.problem = f"the active profile is in an unexpected place (sector {cur:#06x})"
        elif not cur & ROM_BASE and cur not in enabled:
            self.problem = (f"the mouse reports profile {cur} as active, but it isn't in its profile list, "
                            "so Wheelguard can't be sure which profile to edit")
        return sector + bytes((live,))

    @property
    def factory(self) -> bool:
        """The mouse runs on its ROM profile and nothing is saved yet: the first write sets it up."""
        return bool(self.current & ROM_BASE) and not self.dir_valid

    def plan_writes(self, old: bytes, new: bytes) -> List[Op]:
        plan = plan_writes(old, new)
        if not plan:
            return plan
        out: List[Op] = []
        if self.mode != hidpp.MODE_ONBOARD:
            out.append(Op("mode", "onboard mode"))
        for op in plan:
            if op.kind == "sector" and self.factory:
                out += [Op("sector", "profile", sector=1, data=op.data),
                        Op("directory", "profile list", sector=0,
                           data=directory(self.sector_size, self.profile_count)),
                        Op("select", "profile selection", sector=1)]
            elif op.kind == "sector":
                out.append(Op("sector", "profile", sector=self.current, data=op.data))
            else:
                out.append(op)
        # Writing the profile in use can make the firmware reload it and fall back to its default
        # stage; libratbag sets the current stage again afterwards, so do the same.
        live, n = new[-1], decode(new)["stage_count"]
        if any(op.kind == "sector" for op in out) and not any(op.kind == "dpi_index" for op in out) and live < n:
            out.append(Op("dpi_index", "DPI stage", index=live))
        return out

    def backup_prefix(self, fw: str) -> str:
        # The profile sector is part of the name: a backup only ever goes back to the profile it came from.
        # A factory mouse's first write puts its ROM profile into sector 1, so its backup is filed
        # under sector 1: that keeps "Restore original" pointing at the factory settings afterwards.
        sector = 1 if self.factory else self.current
        return f"{super().backup_prefix(fw)}-p{sector:04x}"

    def write(self, op: Op) -> None:
        hp = self.hp
        if not self.write_enabled:
            raise WriteBlocked("writes are disabled: the mouse has not been identified as a G Pro X Superlight")
        if op.kind == "mode":
            hp.set_onboard_mode()
            self.mode = hidpp.MODE_ONBOARD
        elif op.kind in ("sector", "directory"):
            if op.kind == "sector":
                probs = blob_problems(op.data + b"\x00")
                if probs or not 1 <= op.sector < self.sector_count:
                    raise Rejected("refusing to write this profile: " + ("; ".join(probs) or "bad sector"))
            elif op.sector != 0 or not sector_crc_ok(op.data) or not directory_entries(op.data):
                raise Rejected("refusing to write a malformed profile directory")
            hp.writable_sectors = {op.sector}
            try:
                hp.write_sector(op.sector, op.data)
            finally:
                hp.writable_sectors = set()
            if op.kind == "directory":
                self.dir_valid = True
        elif op.kind == "select":
            hp.writable_sectors = {op.sector}
            try:
                hp.set_current_profile(op.sector)
            finally:
                hp.writable_sectors = set()
            self.current = op.sector
        elif op.kind == "dpi_index":
            hp.set_dpi_index(op.index)

    def op_text(self, op: Op) -> str:
        return {"mode": "Switching the mouse to its onboard profile…",
                "sector": "Writing the profile to the mouse…",
                "directory": "Saving the profile list…",
                "select": "Selecting the new profile…",
                "dpi_index": "Switching DPI stage…"}[op.kind]

    def unreachable(self, snap, e) -> HwError:
        if isinstance(e, LinkError) and e.errno == 19:
            return super().unreachable(snap, e)
        if not self.wired:
            return MouseAsleep("The receiver can't reach the mouse.", snap)
        return HwError("The mouse isn't responding.", snap)

    def state_problem(self) -> str:
        return self.problem
