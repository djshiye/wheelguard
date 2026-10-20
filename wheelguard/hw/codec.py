"""Config blob <-> settings, and settings edits -> the minimal set of group writes.

Layout and confidence levels: docs/endgame-protocol.md (sections 3, 4, 8, 9).

The blob is the 1024 bytes the mouse returns for `A1 12`. Everything that is not decoded here
(B[0x00], 0x02, 0x07, 0x0F-0x22, B[0x06], B[0x01], button words, the filter slots of the wheel
records, ...) is never changed: encode() patches a copy of the blob byte by byte, so those bytes are
echoed back exactly as read, and a decode -> encode round trip is the identity.

Edits are flat dicts {key: value} (see KEYS). The write path is: fresh blob -> apply edits ->
plan_writes(old, new) -> only the groups whose source bytes changed, in order 0x14, 0x15, 0x16.
"""
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

BLOB_LEN = 1024

# --- groups: payload byte i of the packet comes from blob offset SRC[i] (doc section 3.2 / 8)
SRC_14 = (0x07, 0x08, 0x09, 0x0A, 0x0B, 0x01, 0x0E, 0x0D) + tuple(range(0x23, 0x37))          # 28
SRC_15 = (0x0C, 0x05, 0x06, 0x04, 0x3D, 0x44, 0x4B, 0x52, 0x59, 0x03, 0x6F)                    # 11
SRC_16 = (tuple(range(0x37, 0x53)), tuple(range(0x53, 0x6F)))                                   # pages 1, 2
assert len(SRC_14) == 28 and len(SRC_15) == 11 and all(len(p) == 28 for p in SRC_16)

# Which changed blob bytes require which group (doc section 9, "group classification").
FILTER_BYTES = (0x3D, 0x44, 0x4B, 0x52, 0x59)           # multiclick filters of the five exposed buttons
TRIGGER_14 = frozenset((0x01, 0x07, 0x08, 0x09, 0x0A, 0x0B) + tuple(range(0x0D, 0x37)))
TRIGGER_15 = frozenset((0x03, 0x04, 0x05, 0x06, 0x0C, 0x6F) + FILTER_BYTES)
# 0x16 carries all button records, but the five filter bytes travel in 0x15 (the vendor tool's
# change detector classifies them there), so changing only those must not rewrite the button map.
TRIGGER_16 = frozenset(range(0x37, 0x6F)) - frozenset(FILTER_BYTES)

CMD_DPI, CMD_ADV, CMD_BUTTONS = 0x14, 0x15, 0x16

# --- field encodings
POLLING = ((125, 0x40), (1000, 0x80), (1000, 0x08), (2000, 0x04), (4000, 0x02))   # UI order (doc 4.1)
POLLING_CODES = tuple(code for _hz, code in POLLING)
LOD_MM = tuple(round(0.7 + 0.1 * i, 1) for i in range(11))      # code i -> mm (glass mode off)
GLASS_LOD = {1: 1.0, 2: 2.0}                                     # glass mode on: code -> mm
DPI_MIN, DPI_MAX = 50, 30000      # DPI_MAX is unverified against the sensor's real limit
FILTER_MAX = 25
FILTER_DEFAULT = 8
GX_SPEED, GX_SAFE = 0xF1, 0xF0    # left/right only (SPDT modes)
SLEEP_MIN, SLEEP_MAX = 1, 120

# (type, code) actions the app understands for button records 0..4 (doc 4.4)
ACTIONS = (
    ((0x00, 0x01), "Left click"),
    ((0x00, 0x02), "Right click"),
    ((0x00, 0x04), "Middle click"),
    ((0x00, 0x08), "Back"),
    ((0x00, 0x10), "Forward"),
    ((0x01, 0x01), "Wheel up"),
    ((0x01, 0xFF), "Wheel down"),
)
ACTION_NAMES = {(t << 8) | c: name for (t, c), name in ACTIONS}
LEFT_CLICK = 0x0001
BUTTON_NAMES = ("Left button", "Right button", "Middle click", "Back (side)", "Forward (side)")
N_BUTTONS = 5

_DPI_BASE, _DPI_STRIDE = 0x23, 5
_BTN_BASE, _BTN_STRIDE = 0x37, 7
_FILTER_OFFS = (0x3D, 0x44, 0x4B, 0x52, 0x59)


def snap_dpi(v: int) -> int:
    """Clamp to the supported range and round the way the vendor tool does (10, or 50 above 10000)."""
    v = max(DPI_MIN, min(DPI_MAX, int(v)))
    q = 50 if v > 10000 else 10
    return max(DPI_MIN, min(DPI_MAX, int(round(v / q)) * q))


def fw_text(word: int) -> str:
    """Firmware word -> text. Hex digits read as decimal / 100 (0x0123 -> '1.23'); doc 3.1."""
    s = f"{word:x}"
    if word and s.isdigit():
        return f"{int(s) / 100:.2f}"
    return f"0x{word:04X}" if word else ""


# --- decoded settings

@dataclass(frozen=True)
class MouseSettings:
    """The user-visible settings, decoded from a blob. Raw values are kept where the blob has
    more states than the UI (polling code, filter bytes, sleep byte, DPI flag)."""
    dpi_x: Tuple[int, ...]
    dpi_y: Tuple[int, ...]
    stage_count: int
    active_stage: int
    polling: int            # raw code, see POLLING
    motion_sync: bool
    angle_snapping: bool
    ripple: bool
    glass: bool
    lod: int                # raw code; meaning depends on glass
    led_off: bool           # "turn the LED off on lift-off"
    sleep_on: bool
    sleep_min: int
    deep_on: bool
    deep_min: int
    filters: Tuple[int, ...]   # raw bytes, left, right, middle, back, forward
    buttons: Tuple[int, ...]   # (type << 8) | code, same order


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def decode(blob: bytes) -> MouseSettings:
    if len(blob) != BLOB_LEN:
        raise ValueError(f"config blob must be {BLOB_LEN} bytes, got {len(blob)}")
    b = blob
    return MouseSettings(
        dpi_x=tuple(_u16(b, _DPI_BASE + _DPI_STRIDE * k + 1) for k in range(4)),
        dpi_y=tuple(_u16(b, _DPI_BASE + _DPI_STRIDE * k + 3) for k in range(4)),
        stage_count=b[0x0E],
        active_stage=b[0x0D],
        polling=b[0x05],
        motion_sync=b[0x0C] == 1,
        angle_snapping=b[0x0A] == 1,
        ripple=b[0x0B] == 1,
        glass=b[0x6F] == 1,
        lod=b[0x09],
        led_off=b[0x08] == 0,
        sleep_on=not b[0x04] & 0x80,
        sleep_min=b[0x04] & 0x7F,
        deep_on=not b[0x03] & 0x80,
        deep_min=b[0x03] & 0x7F,
        filters=tuple(b[o] for o in _FILTER_OFFS),
        buttons=tuple((b[_BTN_BASE + _BTN_STRIDE * r] << 8) | b[_BTN_BASE + _BTN_STRIDE * r + 1]
                      for r in range(N_BUTTONS)),
    )


def flat(s: MouseSettings) -> Dict[str, int]:
    """Flat key -> int/bool view used for edits and for the UI."""
    d: Dict[str, object] = {}
    for k in range(4):
        d[f"dpi_x{k}"] = s.dpi_x[k]
        d[f"dpi_y{k}"] = s.dpi_y[k]
    for k in range(N_BUTTONS):
        d[f"filter{k}"] = s.filters[k]
        d[f"button{k}"] = s.buttons[k]
    for name in ("stage_count", "active_stage", "polling", "motion_sync", "angle_snapping", "ripple",
                 "glass", "lod", "led_off", "sleep_on", "sleep_min", "deep_on", "deep_min"):
        d[name] = getattr(s, name)
    return d


KEYS = tuple(flat(decode(bytes(BLOB_LEN))).keys())


# --- edits

def _check_key_value(key: str, v) -> None:
    if key not in KEYS:
        raise ValueError(f"unknown setting: {key}")
    if key.startswith("dpi_"):
        if not DPI_MIN <= v <= DPI_MAX:
            raise ValueError(f"DPI must be {DPI_MIN}-{DPI_MAX}")
    elif key == "stage_count":
        if not 1 <= v <= 4:
            raise ValueError("stage count must be 1-4")
    elif key == "active_stage":
        if not 0 <= v <= 3:
            raise ValueError("active stage must be 0-3")
    elif key == "polling":
        if v not in POLLING_CODES:
            raise ValueError("unknown polling rate")
    elif key == "lod":
        if not 0 <= v <= 10:
            raise ValueError("lift-off code out of range")
    elif key in ("sleep_min", "deep_min"):
        if not SLEEP_MIN <= v <= SLEEP_MAX:
            raise ValueError("timeout must be 1-120 minutes")
    elif key.startswith("filter"):
        left_right = key in ("filter0", "filter1")
        if not (0 <= v <= FILTER_MAX or (left_right and v in (GX_SPEED, GX_SAFE))):
            raise ValueError("multiclick filter must be 0-25 (GX modes: left and right only)")
    elif key.startswith("button"):
        if v not in ACTION_NAMES:
            raise ValueError("unsupported button action")


def _put_dpi(ba: bytearray, k: int, axis: int, v: int) -> None:
    base = _DPI_BASE + _DPI_STRIDE * k
    o = base + 1 + 2 * axis
    ba[o], ba[o + 1] = v & 0xFF, v >> 8
    ba[base] = 1 if _u16(ba, base + 1) != _u16(ba, base + 3) else 0     # the flag follows X != Y


def encode(blob: bytes, edits: Dict[str, object]) -> bytes:
    """Copy of blob with only the edited fields patched. Unedited bytes are untouched."""
    if len(blob) != BLOB_LEN:
        raise ValueError(f"config blob must be {BLOB_LEN} bytes, got {len(blob)}")
    ba = bytearray(blob)
    current = flat(decode(blob))
    for key, v in edits.items():
        if key in current and current[key] == v:
            continue            # not an edit: leave the bytes (and any value this app doesn't know) alone
        v = int(v) if not isinstance(v, bool) else v
        _check_key_value(key, int(v))
        if key.startswith("dpi_"):
            _put_dpi(ba, int(key[5:]), 0 if key[4] == "x" else 1, v)
        elif key.startswith("filter"):
            ba[_FILTER_OFFS[int(key[6:])]] = v
        elif key.startswith("button"):
            o = _BTN_BASE + _BTN_STRIDE * int(key[6:])
            ba[o], ba[o + 1] = v >> 8, v & 0xFF          # type/code only; the two words are kept
        elif key == "stage_count":
            ba[0x0E] = v
        elif key == "active_stage":
            ba[0x0D] = v
        elif key == "polling":
            ba[0x05] = v
        elif key == "motion_sync":
            ba[0x0C] = 1 if v else 0
        elif key == "angle_snapping":
            ba[0x0A] = 1 if v else 0
        elif key == "ripple":
            ba[0x0B] = 1 if v else 0
        elif key == "glass":
            ba[0x6F] = 1 if v else 0
        elif key == "lod":
            ba[0x09] = v
        elif key == "led_off":
            ba[0x08] = 0 if v else 1
        elif key in ("sleep_on", "deep_on"):
            o = 0x04 if key == "sleep_on" else 0x03
            ba[o] = (ba[o] & 0x7F) | (0 if v else 0x80)
        elif key in ("sleep_min", "deep_min"):
            o = 0x04 if key == "sleep_min" else 0x03
            ba[o] = (ba[o] & 0x80) | v
    return bytes(ba)


def changed_keys(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    return [k for k in KEYS if old.get(k) != new.get(k)]


def validate_settings(s: MouseSettings) -> None:
    """Whole-configuration rules that single-field checks can't see. Raises ValueError."""
    if LEFT_CLICK not in s.buttons:
        raise ValueError("At least one button must stay Left click.")
    if not 1 <= s.stage_count <= 4 or s.active_stage >= s.stage_count:
        raise ValueError("The active DPI stage must be one of the enabled stages.")
    if s.glass:
        if s.lod not in GLASS_LOD:
            raise ValueError("Glass mode only supports the 1.0 mm and 2.0 mm lift-off settings.")
    elif not 0 <= s.lod <= 10:
        raise ValueError("Lift-off setting out of range.")


def blob_problems(blob: bytes) -> List[str]:
    """Reasons the blob doesn't look like a settings block of this mouse (writes are refused)."""
    probs: List[str] = []
    if len(blob) != BLOB_LEN:
        return [f"expected {BLOB_LEN} bytes, got {len(blob)}"]
    if not any(blob) or all(x == 0xFF for x in blob):
        probs.append("settings block is empty")
    if not 1 <= blob[0x0E] <= 4:
        probs.append(f"DPI stage count {blob[0x0E]} is out of range")
    if blob[0x0D] > 3:
        probs.append(f"active DPI stage {blob[0x0D]} is out of range")
    return probs


# --- group payloads / write plan

@dataclass(frozen=True)
class GroupWrite:
    cmd: int
    page: int          # 0 except for 0x16 (1, 2)
    payload: bytes

    @property
    def label(self) -> str:
        return {CMD_DPI: "sensor", CMD_ADV: "advanced", CMD_BUTTONS: "buttons"}[self.cmd]


def group_payload(blob: bytes, cmd: int, page: int = 0) -> bytes:
    if cmd == CMD_DPI:
        return bytes(blob[i] for i in SRC_14)
    if cmd == CMD_ADV:
        return bytes(blob[i] for i in SRC_15)
    if cmd == CMD_BUTTONS and page in (1, 2):
        return bytes(blob[i] for i in SRC_16[page - 1])
    raise ValueError(f"no such group: cmd {cmd:#x} page {page}")


def _differs(old: bytes, new: bytes, offsets: Iterable[int]) -> bool:
    return any(old[i] != new[i] for i in offsets)


def plan_writes(old: bytes, new: bytes) -> List[GroupWrite]:
    """The minimal group writes that turn `old` into `new`, in the vendor tool's order 14 -> 15 -> 16."""
    plan: List[GroupWrite] = []
    if _differs(old, new, TRIGGER_14):
        plan.append(GroupWrite(CMD_DPI, 0, group_payload(new, CMD_DPI)))
    if _differs(old, new, TRIGGER_15):
        plan.append(GroupWrite(CMD_ADV, 0, group_payload(new, CMD_ADV)))
    if _differs(old, new, TRIGGER_16):
        for page in (1, 2):
            plan.append(GroupWrite(CMD_BUTTONS, page, group_payload(new, CMD_BUTTONS, page)))
    return plan


def merge_backup(fresh: bytes, backup: bytes) -> bytes:
    """`fresh` with every byte the three group writes can carry replaced by the backup's value.
    Bytes outside the payloads (nothing can write them) stay as they are on the mouse."""
    ba = bytearray(fresh)
    for src in (SRC_14, SRC_15, *SRC_16):
        for i in src:
            ba[i] = backup[i]
    return bytes(ba)


def describe_changes(old: Dict[str, object], new: Dict[str, object]) -> List[str]:
    """Human-readable list of what changed, one entry per user-level setting (for the action bar)."""
    keys = changed_keys(old, new)
    out: List[str] = []
    seen = set()
    for k in keys:
        if k.startswith("dpi_"):
            label = f"DPI stage {int(k[5:]) + 1}"
        elif k.startswith("filter"):
            label = BUTTON_NAMES[int(k[6:])] + " filter"
        elif k.startswith("button"):
            label = BUTTON_NAMES[int(k[6:])] + " action"
        else:
            label = {
                "stage_count": "Stage count", "active_stage": "Active stage", "polling": "Polling rate",
                "motion_sync": "Motion Sync", "angle_snapping": "Angle snapping", "ripple": "Ripple control",
                "glass": "Glass mode", "lod": "Lift-off distance", "led_off": "LED on lift-off",
                "sleep_on": "Sleep", "sleep_min": "Sleep", "deep_on": "Deep sleep", "deep_min": "Deep sleep",
            }[k]
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


# --- semantic guard for outgoing write payloads (used by the transport's choke point)

def validate_group(cmd: int, page: int, payload: bytes) -> None:
    """Last line of defence: refuse payloads that are obviously not a sane settings group.
    Fields the app doesn't edit are only checked where an out-of-range value could hurt. Raises ValueError."""
    def bad(msg):
        raise ValueError(f"{msg} (group {cmd:#04x})")

    if cmd == CMD_DPI:
        if len(payload) != 28:
            bad("payload must be 28 bytes")
        p = payload
        if p[1] > 1 or p[3] > 1 or p[4] > 1:
            bad("flag byte out of range")
        if p[2] > 10:
            bad("lift-off code out of range")
        if not 1 <= p[6] <= 4 or p[7] > 3:
            bad("DPI stage count / active stage out of range")
        for k in range(p[6]):
            rec = p[8 + 5 * k:13 + 5 * k]
            x, y = rec[1] | rec[2] << 8, rec[3] | rec[4] << 8
            if not (10 <= x <= DPI_MAX and 10 <= y <= DPI_MAX):
                bad(f"DPI stage {k + 1} out of range")
    elif cmd == CMD_ADV:
        if len(payload) != 11:
            bad("payload must be 11 bytes")
        p = payload
        if p[0] > 1 or p[10] > 1:
            bad("flag byte out of range")
        if p[1] not in POLLING_CODES:
            bad(f"polling code {p[1]:#04x} is not one this app knows")
        for t in (p[3], p[9]):
            if not (t & 0x80 or SLEEP_MIN <= t <= SLEEP_MAX):
                bad("sleep timeout out of range")
        for i in (4, 5):
            if not (p[i] <= FILTER_MAX or p[i] in (GX_SPEED, GX_SAFE)):
                bad("multiclick filter out of range")
        for i in (6, 7, 8):
            if p[i] > FILTER_MAX:
                bad("multiclick filter out of range")
    elif cmd == CMD_BUTTONS:
        if page not in (1, 2) or len(payload) != 28:
            bad("button map page must be 1 or 2 with 28 bytes")
        for j in range(4):
            r = (page - 1) * 4 + j
            if r < N_BUTTONS:
                f = payload[7 * j + 6]
                ok = f <= FILTER_MAX or (r < 2 and f in (GX_SPEED, GX_SAFE))
                if not ok:
                    bad(f"button {r + 1} filter out of range")
    else:
        bad("not a write group")
