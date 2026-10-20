"""Transport for the Endgame Gear vendor HID protocol (docs/endgame-protocol.md).

Every byte that reaches the device goes through EndgameDevice._set(), the single choke point. It
enforces:

  * an allow-list of commands (ALLOWED_CMDS) with the exact frame shape of each one, so the factory
    reset (0x13), the pairing commands (0x70/0x71/0x72) and anything unknown cannot be sent;
  * report IDs 0xA1 (set, 64 bytes) only, and 0xA1 / 0xA0 (get) with their fixed sizes;
  * the write gate: the group writes 0x14/0x15/0x16 are refused unless this session has read the
    mouse's identity (A1 0E) and it reported PID 0x1982, the only mouse the vendor tool supports;
  * sane payload contents (codec.validate_group).

Nothing here has been verified on hardware.
"""
import errno
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import codec
from .hidraw import PID_WIRED

REPORT_CMD = 0xA1          # 64-byte Feature report carrying commands and short replies
REPORT_BLOB = 0xA0         # 1041-byte Feature report carrying the config blob
CMD_LEN = 64
BLOB_REPORT_LEN = 1041
PAYLOAD_AT = 0x10

CMD_DONGLE_INFO = 0x0D
CMD_MOUSE_INFO = 0x0E
CMD_PROBE = 0x0F
CMD_READ_BLOB = 0x12
CMD_BATTERY = 0xB4
READ_CMDS = frozenset((CMD_DONGLE_INFO, CMD_MOUSE_INFO, CMD_BATTERY, CMD_READ_BLOB))
WRITE_CMDS = frozenset((codec.CMD_DPI, codec.CMD_ADV, codec.CMD_BUTTONS))
ALLOWED_CMDS = READ_CMDS | WRITE_CMDS | {CMD_PROBE}

# Never sendable, even if someone widens ALLOWED_CMDS by mistake. Named so refusals read clearly.
FORBIDDEN_CMDS = {0x13: "factory reset", 0x70: "pair dongle", 0x71: "pair default", 0x72: "read pairing data"}

# cmd -> (allowed sub bytes, fixed byte 3, allowed page bytes, payload length)
_SHAPES = {
    CMD_DONGLE_INFO: ((0x00,), 0x00, (0,), 0),
    CMD_MOUSE_INFO: ((0x00,), 0x00, (0,), 0),
    CMD_BATTERY: ((0x00,), 0x00, (0,), 0),
    CMD_READ_BLOB: ((0x00,), 0x00, (0,), 0),
    CMD_PROBE: ((0x01, 0x0F), 0x00, (0,), 0),
    codec.CMD_DPI: ((0x0F,), 0x1C, (0,), 28),
    codec.CMD_ADV: ((0x0F,), 0x0A, (0,), 11),
    codec.CMD_BUTTONS: ((0x0F,), 0x1C, (1, 2), 28),
}

PID_UNKNOWN_VALUES = (0x0000, 0xFFFF)    # what a link-down dongle might report for the mouse PID

# Waits between SetFeature and GetFeature, seconds: (dongle, wired). At least the vendor tool's.
DELAY = {
    CMD_DONGLE_INFO: (0.15, 0.15),
    CMD_MOUSE_INFO: (0.35, 0.15),
    CMD_BATTERY: (0.35, 0.30),
    CMD_READ_BLOB: (0.40, 0.11),
    CMD_PROBE: (0.30, 0.30),
    codec.CMD_DPI: (0.88, 0.50),
    codec.CMD_ADV: (0.88, 0.50),
    codec.CMD_BUTTONS: (0.88, 0.50),
}
PAGE_GAP = 0.10
BUSY_RETRIES = 3
SET_RETRIES = 3
_RETRY_ERRNOS = (errno.EIO, errno.EPIPE, errno.EPROTO, errno.ETIMEDOUT, errno.EOVERFLOW)


class Rejected(Exception):
    """The frame is not on the allow-list (or is malformed). Nothing was sent."""


class WriteBlocked(Rejected):
    """A write was attempted but this session has not confirmed a supported mouse."""


class LinkError(Exception):
    """The ioctl failed (device gone, timed out, stalled)."""
    def __init__(self, err: int, what: str):
        super().__init__(f"{what}: {errno.errorcode.get(err, err)}")
        self.errno = err


class StatusError(Exception):
    """The device answered with a status other than 1."""
    def __init__(self, cmd: int, status: int):
        super().__init__(f"command {cmd:#04x} failed with status {status}")
        self.cmd, self.status = cmd, status


@dataclass(frozen=True)
class MouseInfo:
    pid: int
    fw_word: int

    @property
    def link_down(self) -> bool:
        return self.pid in PID_UNKNOWN_VALUES


def check_packet(pkt: bytes) -> int:
    """Validate an outgoing 64-byte command frame and return its command. Raises Rejected."""
    if len(pkt) != CMD_LEN:
        raise Rejected(f"command frames are {CMD_LEN} bytes, got {len(pkt)}")
    if pkt[0] != REPORT_CMD:
        raise Rejected(f"report ID {pkt[0]:#04x} is not allowed (only {REPORT_CMD:#04x} may be sent)")
    cmd = pkt[1]
    if cmd in FORBIDDEN_CMDS:
        raise Rejected(f"command {cmd:#04x} ({FORBIDDEN_CMDS[cmd]}) is forbidden")
    if cmd not in ALLOWED_CMDS or cmd not in _SHAPES:
        raise Rejected(f"command {cmd:#04x} is not on the allow-list")
    subs, fixed, pages, plen = _SHAPES[cmd]
    if pkt[2] not in subs:
        raise Rejected(f"command {cmd:#04x}: sub-command {pkt[2]:#04x} not allowed")
    if pkt[3] != fixed:
        raise Rejected(f"command {cmd:#04x}: byte 3 must be {fixed:#04x}")
    if pkt[4] or pkt[5] or any(pkt[7:PAYLOAD_AT]):
        raise Rejected(f"command {cmd:#04x}: reserved header bytes must be zero")
    if pkt[6] not in pages:
        raise Rejected(f"command {cmd:#04x}: page {pkt[6]} not allowed")
    if any(pkt[PAYLOAD_AT + plen:]):
        raise Rejected(f"command {cmd:#04x}: data beyond the {plen}-byte payload")
    return cmd


def check_get(report_id: int, length: int) -> None:
    if (report_id, length) not in ((REPORT_CMD, CMD_LEN), (REPORT_BLOB, BLOB_REPORT_LEN)):
        raise Rejected(f"GetFeature({report_id:#04x}, {length}) is not allowed")


def build_packet(cmd: int, sub: int = 0, fixed: Optional[int] = None, page: int = 0,
                 payload: bytes = b"") -> bytes:
    """Assemble a frame. Does not validate: _set() does that for everything that is sent."""
    pkt = bytearray(CMD_LEN)
    pkt[0], pkt[1], pkt[2] = REPORT_CMD, cmd, sub
    pkt[3] = _SHAPES[cmd][1] if fixed is None and cmd in _SHAPES else (fixed or 0)
    pkt[6] = page
    pkt[PAYLOAD_AT:PAYLOAD_AT + len(payload)] = payload
    return bytes(pkt)


class EndgameDevice:
    """One open config channel. `dev` provides set_feature(bytes) and get_feature(report_id, length)."""

    def __init__(self, dev, wired: bool, sleep: Callable[[float], None] = time.sleep):
        self._dev = dev
        self.wired = wired
        self._sleep = sleep
        self._mouse_pid: Optional[int] = None

    # ---- the gate
    @property
    def write_enabled(self) -> bool:
        return self._mouse_pid == PID_WIRED

    @property
    def mouse_pid(self) -> Optional[int]:
        return self._mouse_pid

    # ---- the choke point
    def _set(self, pkt: bytes) -> int:
        cmd = check_packet(pkt)
        if cmd in WRITE_CMDS:
            if not self.write_enabled:
                raise WriteBlocked("writes are disabled: the mouse has not been identified as PID 0x1982")
            payload = pkt[PAYLOAD_AT:PAYLOAD_AT + _SHAPES[cmd][3]]
            try:
                codec.validate_group(cmd, pkt[6], payload)
            except ValueError as e:
                raise Rejected(str(e)) from e
        for attempt in range(SET_RETRIES + 1):
            try:
                self._dev.set_feature(pkt)
                return cmd
            except OSError as e:
                if e.errno in _RETRY_ERRNOS and attempt < SET_RETRIES:
                    self._sleep(0.05)
                    continue
                raise LinkError(e.errno or 0, "SetFeature") from e
        raise AssertionError("unreachable")

    def _get(self, report_id: int, length: int) -> bytes:
        check_get(report_id, length)
        try:
            return bytes(self._dev.get_feature(report_id, length))
        except OSError as e:
            raise LinkError(e.errno or 0, "GetFeature") from e

    def _exchange(self, pkt: bytes) -> bytes:
        """Send one command, wait the documented time, read the reply, require status 1 (retry on busy)."""
        cmd = self._set(pkt)
        delay = DELAY[cmd][1 if self.wired else 0]
        self._sleep(delay)
        rid, length = (REPORT_BLOB, BLOB_REPORT_LEN) if cmd == CMD_READ_BLOB else (REPORT_CMD, CMD_LEN)
        for attempt in range(BUSY_RETRIES + 1):
            reply = self._get(rid, length)
            status = reply[1] if len(reply) > 1 else 0
            if status == 1:
                return reply
            if status == 3 and attempt < BUSY_RETRIES:
                self._sleep(delay + 0.2 * (attempt + 1))
                continue
            raise StatusError(cmd, status)
        raise AssertionError("unreachable")

    # ---- operations
    def probe(self) -> None:
        """Dongle only: the vendor tool's link check before operations. 0F/01, then 0F/0F if that fails."""
        if self.wired:
            return
        try:
            self._exchange(build_packet(CMD_PROBE, sub=0x01))
            return
        except StatusError:
            pass
        self._exchange(build_packet(CMD_PROBE, sub=0x0F))

    def dongle_info(self) -> int:
        r = self._exchange(build_packet(CMD_DONGLE_INFO))
        return r[0x11] | (r[0x12] << 8)

    def mouse_info(self) -> MouseInfo:
        """Identify the mouse. This is the only place that can open the write gate."""
        self._mouse_pid = None
        r = self._exchange(build_packet(CMD_MOUSE_INFO))
        info = MouseInfo(pid=r[0x12] | (r[0x13] << 8), fw_word=r[0x17] | (r[0x18] << 8))
        self._mouse_pid = info.pid
        return info

    def battery(self) -> int:
        return min(100, self._exchange(build_packet(CMD_BATTERY))[0x10])

    def read_blob(self) -> bytes:
        r = self._exchange(build_packet(CMD_READ_BLOB))
        if len(r) < PAYLOAD_AT + codec.BLOB_LEN:
            raise StatusError(CMD_READ_BLOB, 0)
        return r[PAYLOAD_AT:PAYLOAD_AT + codec.BLOB_LEN]

    def write_group(self, gw: "codec.GroupWrite") -> None:
        """Send one group (or one page of the button map). Probes first in dongle mode, like the vendor
        tool does before each group (not between the two pages of the button map)."""
        if not self.write_enabled:      # refuse before any I/O, not only at the choke point
            raise WriteBlocked("writes are disabled: the mouse has not been identified as PID 0x1982")
        if not (gw.cmd == codec.CMD_BUTTONS and gw.page == 2):
            self.probe()
        self._exchange(build_packet(gw.cmd, sub=0x0F, page=gw.page, payload=gw.payload))
        if gw.cmd == codec.CMD_BUTTONS and gw.page == 1:
            self._sleep(PAGE_GAP)
