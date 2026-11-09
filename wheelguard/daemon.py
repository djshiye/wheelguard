"""wheelguard daemon: grabs the mouse, drops bouncing wheel ticks, re-emits the rest."""
import argparse
import errno
import glob
import logging
import os
import select
import signal
import sys
import time

import evdev
from evdev import UInput, UInputError, ecodes as e

from . import config as C
from .control import POLL_S, Controller
from .core import DROP, EMIT, HOLD
from .ipc import AlreadyRunning, Server

log = logging.getLogger("wheelguard")
UI_NAME = "wheelguard Endgame Gear"     # unchanged for every mouse, so existing setups see the same virtual device
UI_PREFIX = "wheelguard"


# Mice Wheelguard knows, as (vendor, product) of their wheel-bearing input device. The configured
# vendor/product (config.json) is tried first; these are the fallbacks, so any supported mouse gets the
# scroll filter without editing the config. The Superlight's wireless ID is the one hid-logitech-dj
# gives the mouse behind its receiver.
KNOWN_MICE = (
    (0x3367, 0x1970),       # Endgame Gear XM2w 4k v2, dongle
    (0x3367, 0x1982),       # Endgame Gear XM2w 4k v2, cable
    (0x1532, 0x00BF),       # Razer DeathAdder V4 Pro, dongle
    (0x1532, 0x00BE),       # Razer DeathAdder V4 Pro, cable
    (0x046D, 0x4093),       # Logitech G Pro X Superlight, LIGHTSPEED receiver
    (0x046D, 0xC094),       # Logitech G Pro X Superlight, cable
)
_SKIP_NAMES = ("Keyboard", "Consumer Control", "System Control")


def _wanted(vendor, product):
    ids = [(vendor, product)]
    ids += [k for k in KNOWN_MICE if k not in ids]
    return ids


def _sysfs_ids(path):
    base = f"/sys/class/input/{os.path.basename(path)}/device"

    def attr(name):
        with open(f"{base}/{name}") as f:
            return f.read().strip()
    return attr


def _sysfs_is_wheel_mouse(path, vendor, product):
    """Match an event node we may not open by its sysfs attributes (name, ids, REL bitmask)."""
    try:
        attr = _sysfs_ids(path)
        return (int(attr("id/vendor"), 16) == vendor and int(attr("id/product"), 16) == product
                and not any(n in attr("name") for n in _SKIP_NAMES)
                and int(attr("capabilities/rel") or "0", 16) >> e.REL_WHEEL & 1 == 1)
    except (OSError, ValueError):
        return False


def discover(vendor, product):
    """(device, error): the wheel-bearing event node of the configured mouse or, failing that, of the
    first supported mouse found (KNOWN_MICE order); or a reason."""
    ids = _wanted(vendor, product)
    denied = None
    best = None                 # (rank, device)
    # not evdev.list_devices(): that silently skips nodes we are not allowed to open
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            if denied is None and any(_sysfs_is_wheel_mouse(path, v, p) for v, p in ids):
                denied = path
            continue
        except OSError:
            continue
        name = dev.name or ""
        ident = (dev.info.vendor, dev.info.product)
        caps = dev.capabilities()
        if (ident in ids and not any(n in name for n in _SKIP_NAMES) and not name.startswith(UI_PREFIX)
                and e.REL_WHEEL in caps.get(e.EV_REL, [])):
            # prefer the configured mouse, then the node that also has the buttons
            rank = (ids.index(ident), 0 if e.BTN_LEFT in caps.get(e.EV_KEY, []) else 1)
            if best is None or rank < best[0]:
                if best is not None:
                    best[1].close()
                best = (rank, dev)
                continue
        dev.close()
    if best is not None:
        return best[1], None
    if denied:
        return None, f"permission denied: {denied}"
    return None, "mouse not found"


def describe(ex):
    """Human-readable reason for a failed session (OSError, or evdev's UInputError)."""
    if isinstance(ex, UInputError):
        if "cannot be opened for writing" in str(ex):
            return "permission denied: /dev/uinput"
        return str(ex)
    if isinstance(ex, PermissionError):
        return f"permission denied: {ex.filename or '/dev/uinput'}"
    if ex.errno == errno.ENODEV:
        return "mouse disconnected"
    return str(ex)


class TickLogger:
    def __init__(self, enabled):
        self.enabled = enabled

    def __call__(self, decisions):
        if not self.enabled:
            return
        for d in decisions:
            if not d.held and d.kind == EMIT and d.reason != "confirmed by second reversal":
                continue  # ordinary tick; only reversals are interesting
            gap = "first" if d.gap_ms is None else f"{d.gap_ms:.1f}ms"
            what = {EMIT: "RELEASE", DROP: "DROP", HOLD: "HOLD"}.get(d.kind)
            if what is None:
                continue
            print(f"{d.t:.3f} {what:7} {'up' if d.sign > 0 else 'down':4} "
                  f"gap={gap} ({d.reason})", flush=True)


class KeyState:
    """Which keys/buttons a stream of EV_KEY events leaves pressed."""

    def __init__(self):
        self.down = set()

    def update(self, frame):
        for etype, code, value in frame:
            if etype == e.EV_KEY:
                if value:
                    self.down.add(code)
                else:
                    self.down.discard(code)


class FrameReader:
    """Turns the mouse's raw evdev events into frames (lists of events, without their SYN_REPORT).

    After SYN_DROPPED (the kernel's buffer overflowed, e.g. at 4000-8000 Hz polling while the daemon
    was busy) everything up to the next SYN_REPORT is unreliable and discarded, as libevdev does. A
    button release can be among the lost events, so the button state is then re-read from the device
    (`keys_now`, EVIOCGKEY) and the difference is replayed as a frame of its own. Without this the
    virtual mouse could keep a button pressed until the next click. A press of a button that is already
    down (a duplicate after the re-read) is left out.
    """

    def __init__(self, keys_now):
        self.keys_now = keys_now
        self.state = KeyState()
        self.frame = []
        self.skipping = False

    def feed(self, etype, code, value):
        """One raw event; returns the frames it completes (usually none or one)."""
        if etype == e.EV_SYN:
            if code == e.SYN_DROPPED:
                self.frame, self.skipping = [], True
            elif code == e.SYN_REPORT:
                if self.skipping:
                    self.skipping = False
                    self.frame = []
                    return self._resync()
                frame, self.frame = self.frame, []
                return [frame] if frame else []
            return []
        if self.skipping:
            return []
        if etype == e.EV_KEY and value == 1 and code in self.state.down:
            return []                       # already pressed (replayed by a re-read)
        self.frame.append((etype, code, value))
        if etype == e.EV_KEY:
            self.state.update([(etype, code, value)])
        return []

    def _resync(self):
        try:
            actual = set(self.keys_now())
        except OSError:
            return []
        fix = [(e.EV_KEY, code, 0) for code in sorted(self.state.down - actual)]
        fix += [(e.EV_KEY, code, 1) for code in sorted(actual - self.state.down)]
        if not fix:
            return []
        log.info("events dropped by the kernel; button state re-read from the mouse")
        self.state.update(fix)
        return [fix]


def write_frames(ui, frames):
    for frame in frames:
        for etype, code, value in frame:
            ui.write(etype, code, value)
        ui.syn()


def service(ctl, server, timeout, extra=()):
    """One select() round over the IPC sockets plus `extra` fds; returns the readable extra fds."""
    timeout = POLL_S if timeout is None else min(timeout, POLL_S)
    readable, _, _ = select.select(list(extra) + server.fds(), [], [], timeout)
    server.service([fd for fd in readable if fd not in extra])
    ctl.poll_config()
    return [fd for fd in readable if fd in extra]


def idle(ctl, server, seconds):
    """Wait `seconds` (the retry interval) while still answering IPC clients."""
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        service(ctl, server, left)


def buttons_down(dev):
    """Mouse buttons the device reports as pressed right now (EVIOCGKEY)."""
    return [code for code in dev.active_keys() if e.BTN_MOUSE <= code < e.BTN_JOYSTICK]


def wait_for_buttons_up(dev, ctl, server):
    """Don't grab the mouse while a button is held.

    Until the grab, the desktop reads the mouse directly. If it saw a press there and the grab then
    swallowed the release (it would go only to us), the desktop would keep that button pressed until
    Wheelguard lets go of the mouse: a stuck click that clicking again can't fix, because the new
    clicks arrive through the virtual mouse. This happens when the mouse wakes up or reconnects with a
    click. So wait (still answering IPC clients) until every button is up; the release then reaches
    the desktop the normal way.
    """
    logged = False
    while buttons_down(dev):
        if not logged:
            log.info("waiting for the mouse buttons to be released before taking over the mouse")
            logged = True
        if service(ctl, server, POLL_S, (dev.fd,)):
            drain(dev)                     # not grabbed yet: the desktop gets these events itself


def drain(dev):
    """Read and forget every event queued for us so far.

    Until the grab the desktop reads the mouse too, so it has had these already. Replaying them
    through the virtual mouse after the grab would deliver them twice: a doubled click, scroll tick or
    cursor jump. Events pile up from the moment the mouse is opened (before the virtual mouse exists).
    """
    try:
        while True:
            count = 0
            for _ in dev.read():
                count += 1
            if not count:
                return
    except BlockingIOError:         # nothing (left) to read
        pass


def grab_when_buttons_up(dev, ctl, server):
    """Grab the mouse at a moment when no button is held (see wait_for_buttons_up).

    A button can go down between the last check and the grab. The desktop saw that press, and the
    grab would send the release only to us: the stuck click again. So check once more after the grab
    and, if a button is down, let go of the mouse and wait for the release first.
    """
    while True:
        wait_for_buttons_up(dev, ctl, server)
        drain(dev)
        dev.grab()
        if not buttons_down(dev):
            return
        log.info("a mouse button went down while taking over the mouse; waiting for its release")
        dev.ungrab()


def run_session(dev, ctl, server, tick_log):
    """Grab dev and filter until it disappears (OSError) or we are stopped."""
    try:
        ui = UInput.from_device(dev, name=UI_NAME)  # virtual device first, only then grab
    except (OSError, UInputError):
        dev.close()
        raise
    grabbed = False

    out = KeyState()                    # what the virtual mouse has been told is pressed

    def write(frames):
        write_frames(ui, frames)
        for frame in frames:
            out.update(frame)

    try:
        grab_when_buttons_up(dev, ctl, server)
        grabbed = True
        cfg = ctl.cfg
        log.info("filtering %s (%s), lock=%gms pre=%gms hold=%gms, click-lock %s", dev.name, dev.path,
                 cfg["lock_ms"], cfg["pre_ms"], cfg["hold_ms"],
                 f"after {cfg['clicklock_ms'] / 1000:g}s ({cfg['clicklock_mode']})" if cfg["clicklock"] else "off")
        ctl.reset_filter()
        ctl.set_device(True, dev.name, None)
        reader = FrameReader(dev.active_keys)
        reader.state.down = set(dev.active_keys())   # normally empty (see grab_when_buttons_up)
        while True:
            timeout = None
            now = time.time()
            if (deadline := ctl.deadline) is not None:
                timeout = min(max(deadline - now, 0.0), ctl.cfg["hold_ms"] / 1000.0)
            if (deadline := ctl.clicklock_deadline) is not None:
                timeout = min(timeout if timeout is not None else POLL_S, max(deadline - now, 0.0))
            if service(ctl, server, timeout, (dev.fd,)):
                for ev in dev.read():
                    for frame in reader.feed(ev.type, ev.code, ev.value):
                        tick_log(ctl.handle_frame(frame, ev.timestamp(), write))
            tick_log(ctl.handle_flush(time.time(), write))
    finally:
        # Never leave a button pressed behind us (the kernel should release it when the virtual mouse
        # goes away, but say so explicitly as well).
        if out.down:
            try:
                write_frames(ui, [[(e.EV_KEY, code, 0) for code in sorted(out.down)]])
            except OSError:
                pass
        if grabbed:
            try:
                dev.ungrab()
            except OSError:
                pass
        ui.close()
        dev.close()


def _stop(signum, frame):
    raise SystemExit(0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Drop bouncing (wrong-direction) scroll-wheel ticks.")
    ap.add_argument("--lock-ms", type=float, help="direction lock expires after this long without ticks (default 300)")
    ap.add_argument("--pre-ms", type=float, help="reversals this soon after a lock-direction tick are dropped (default 60)")
    ap.add_argument("--hold-ms", type=float, help="other reversals are held this long before being let through (default 25)")
    ap.add_argument("--vendor", type=lambda s: int(s, 0), help="USB vendor id (default 0x3367)")
    ap.add_argument("--product", type=lambda s: int(s, 0), help="USB product id (default 0x1970)")
    ap.add_argument("--retry", type=float, dest="retry_s", help="seconds between device searches (default 2)")
    ap.add_argument("--log", action="store_true", help="print every dropped/held/released reversal with its gap")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    overrides = {}
    for k in C.DEFAULTS:
        if getattr(args, k, None) is not None:
            overrides[k] = getattr(args, k)
    try:
        ctl = Controller(overrides)
    except ValueError as ex:
        ap.error(str(ex))

    signal.signal(signal.SIGTERM, _stop)  # SIGINT already raises KeyboardInterrupt
    server = Server(ctl)
    try:
        server.start()
    except AlreadyRunning as ex:
        sys.exit(f"wheelguard: {ex}")
    except OSError as ex:
        sys.exit(f"wheelguard: cannot create socket {server.path}: {ex}")

    tick_log = TickLogger(args.log)
    last_err = None
    try:
        while True:
            cfg = ctl.cfg
            dev, err = discover(cfg["vendor"], cfg["product"])
            if dev is None:
                ctl.set_device(False, None, err)
                if err != last_err:
                    log.info("%s (%04x:%04x), waiting", err, cfg["vendor"], cfg["product"])
                    last_err = err
            else:
                last_err = None
                try:
                    run_session(dev, ctl, server, tick_log)
                except (OSError, UInputError) as ex:
                    msg = describe(ex)
                    log.warning("session ended: %s; retrying", msg)
                    ctl.set_device(False, None, msg)
                    last_err = msg
            idle(ctl, server, ctl.cfg["retry_s"])
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
