"""wheelguard daemon: grabs the mouse, drops bouncing wheel ticks, re-emits the rest."""
import argparse
import atexit
import errno
import fcntl
import glob
import logging
import os
import select
import signal
import struct
import sys
import time

import evdev
from evdev import UInput, UInputError, ecodes as e

from . import config as C
from . import mice, scan
from .control import POLL_S, Controller
from .core import DROP, EMIT, HOLD
from .hotplug import DeviceWatcher
from .ipc import AlreadyRunning, Server
from .screenwatch import ScreenWatch

log = logging.getLogger("wheelguard")
UI_NAME = "wheelguard Endgame Gear"     # unchanged for every mouse, so existing setups see the same virtual device
UI_PREFIX = "wheelguard"
SETTLE_S = 0.25     # after a /dev/input change, let udev finish (permissions, sibling nodes) before searching
EVIOCSCLOCKID = 0x400445A0      # _IOW('E', 0xa0, int): which clock the kernel stamps this device's events with
_EVENT = struct.Struct("llHHi")     # struct input_event: a timeval (ignored by uinput), type, code, value
_SYN_REPORT = _EVENT.pack(0, 0, e.EV_SYN, e.SYN_REPORT, 0)


# Mice Wheelguard knows, as (vendor, product) of their wheel-bearing input device (see mice.py for their
# names). The configured vendor/product (config.json) is tried first; these are the fallbacks, so any
# supported mouse gets the scroll filter without editing the config.
KNOWN_MICE = mice.KNOWN_MICE
_SKIP_NAMES = ("Keyboard", "Consumer Control", "System Control")


def _ident(dev):
    info = getattr(dev, "info", None)
    return (info.vendor, info.product) if info else None


def _wanted(vendor, product, allowed=None):
    """Automatic's search order: the configured mouse, the supported mice, then the mice the user allowed
    ("Allow this mouse"). Allowing a mouse is the user's say-so, so Automatic takes it over like a supported one
    and still prefers a supported mouse when both are connected."""
    ids = [(vendor, product)]
    ids += [k for k in KNOWN_MICE if k not in ids]
    ids += [k for k in (mice.allowed_mice() if allowed is None else allowed) if k not in ids]
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


def discover(vendor, product, mouse=None):
    """(device, error): the wheel-bearing event node of the configured mouse or, failing that, of the
    first supported mouse found (KNOWN_MICE order); or a reason. `mouse` ("vvvv:pppp") pins one mouse
    instead (default: the config's "mouse" setting; "auto" = the behaviour above)."""
    pinned = mice.parse_choice(scan.selected() if mouse is None else mouse)
    ids = [pinned] if pinned else _wanted(vendor, product)
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
                and e.REL_WHEEL in caps.get(e.EV_REL, []) and not scan.keyboard_like(*ident, caps)):
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
    return None, "selected mouse not found" if pinned else "mouse not found"


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
    button event can be among the lost ones, so the button state is then re-read from the device
    (`keys_now`, EVIOCGKEY) and the difference is replayed as a frame of its own.

    Two kernel details make this exact:
    - EVIOCGKEY also throws away every key event still queued for us (evdev_handle_get_val), because
      they are part of the state it returns. Key events we had already read in the same batch are part
      of it too, so they are left out until the next batch (call `next_batch()` before each read).
      Replaying them on top of the re-read would leave a button pressed whose release was thrown away
      (and swallow the next real press), or lose a press made during the re-read.
    - A read can end in the middle of a frame. The button state only takes in a frame once its
      SYN_REPORT arrives, so a frame cut off by an overflow can't leave a press recorded that was
      never sent.
    """

    def __init__(self, keys_now):
        self.keys_now = keys_now
        self.state = KeyState()         # what the frames handed out so far leave pressed
        self.frame = []
        self.skipping = False
        self.stale_keys = False         # key events from here to the end of this batch are in the re-read

    def next_batch(self):
        """A new read() from the device starts: its events are newer than any re-read so far."""
        self.stale_keys = False

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
                if not frame:
                    return []
                self.state.update(frame)
                return [frame]
            return []
        if self.skipping:
            return []
        if self.stale_keys and (etype == e.EV_KEY or (etype == e.EV_MSC and code == e.MSC_SCAN)):
            return []                       # already counted by the re-read (see the class doc)
        self.frame.append((etype, code, value))
        return []

    def _resync(self):
        try:
            actual = set(self.keys_now())
        except OSError:
            return []
        self.stale_keys = True
        fix = [(e.EV_KEY, code, 0) for code in sorted(self.state.down - actual)]
        fix += [(e.EV_KEY, code, 1) for code in sorted(actual - self.state.down)]
        if not fix:
            return []
        log.info("events dropped by the kernel; button state re-read from the mouse")
        self.state.update(fix)
        return [fix]


def use_monotonic_clock(dev):
    """Ask the kernel to stamp this device's events with CLOCK_MONOTONIC instead of the wall clock.

    Every timing decision (bounce windows, the Click-Lock hold, the Double-click fix) compares event times
    with each other and with our own clock. On the wall clock, a time correction would count as time
    passing: set back, it made real scrolling look like bounce for as long as the step; set forward, it
    engaged Click-Lock after a short press. Returns False if the kernel refuses; the caller then stamps
    the events itself with time.monotonic().
    """
    try:
        fcntl.ioctl(dev.fd, EVIOCSCLOCKID, struct.pack("i", time.CLOCK_MONOTONIC))
    except (OSError, TypeError, ValueError):
        return False
    return True


def pack_frames(frames):
    """The frames as the bytes /dev/uinput takes: each event and a SYN_REPORT after every frame."""
    parts = []
    for frame in frames:
        for etype, code, value in frame:
            parts.append(_EVENT.pack(0, 0, etype, code, value))
        parts.append(_SYN_REPORT)
    return b"".join(parts)


def write_frames(ui, frames):
    for frame in frames:
        for etype, code, value in frame:
            ui.write(etype, code, value)
        ui.syn()


class FrameWriter:
    """Writes frames to the virtual mouse with one system call for all of them, instead of one per event
    (a motion frame is three events: X, Y and the SYN_REPORT). uinput takes any number of events in one
    write. If the kernel ever refuses that, it goes back to one write per event for good. Either way every
    event of every frame is written: a batch is never dropped, because it may hold a button's release."""

    def __init__(self, ui):
        self.ui = ui
        self.batched = isinstance(ui, UInput) and isinstance(getattr(ui, "fd", None), int)

    def __call__(self, frames):
        if self.batched:
            data = pack_frames(frames)
            try:
                done = os.write(self.ui.fd, data)
            except OSError as ex:
                if ex.errno != errno.EINVAL:
                    raise
                # uinput refuses with EINVAL before taking anything, so the whole batch is still to be written
                log.warning("writing whole frames to the virtual mouse failed (%s); writing event by event", ex)
                self.batched = False
                write_frames(self.ui, frames)
                return
            done -= done % _EVENT.size                  # uinput takes whole events only
            while done < len(data):                     # a short write: the rest, one event at a time
                os.write(self.ui.fd, data[done:done + _EVENT.size])
                done += _EVENT.size
            return
        write_frames(self.ui, frames)


def service(ctl, server, timeout, extra=()):
    """One select() round over the IPC sockets plus `extra` fds; returns the readable extra fds."""
    timeout = POLL_S if timeout is None else min(timeout, POLL_S)
    readable, _, _ = select.select(list(extra) + server.fds(), [], [], timeout)
    server.service([fd for fd in readable if fd not in extra])
    ctl.poll_config()
    return [fd for fd in readable if fd in extra]


def idle(ctl, server, seconds, watcher=None):
    """Wait `seconds` (the retry interval) while still answering IPC clients.

    With a `watcher` (hotplug.DeviceWatcher) the wait ends early, SETTLE_S after an input device
    appears or becomes accessible, so a mouse that wakes up or is plugged in is picked up at once.
    It also ends at once when a client pauses or resumes (ctl.wake).
    """
    end = time.monotonic() + seconds
    extra = () if watcher is None else (watcher.fileno(),)
    while (left := end - time.monotonic()) > 0:
        if service(ctl, server, left, extra) and watcher.drain():
            ctl.invalidate_mice()
            end = min(end, time.monotonic() + SETTLE_S)
        if ctl.wake is True:
            return


def buttons_down(dev):
    """Keys and buttons the device reports as pressed right now (EVIOCGKEY). Any key counts, not only
    mouse buttons: a device that also has keyboard keys must not be grabbed while one is held, or the
    desktop would never see its release."""
    return list(dev.active_keys())


def wait_for_buttons_up(dev, ctl, server):
    """Don't grab the mouse while a button (or key) is held.

    Until the grab, the desktop reads the mouse directly. If it saw a press there and the grab then
    swallowed the release (it would go only to us), the desktop would keep that button pressed until
    Wheelguard lets go of the mouse: a stuck click that clicking again can't fix, because the new
    clicks arrive through the virtual mouse. This happens when the mouse wakes up or reconnects with a
    click. So wait (still answering IPC clients) until every button is up; the release then reaches
    the desktop the normal way. Returns False when a client paused the daemon meanwhile (nothing is grabbed).
    """
    logged = False
    while buttons_down(dev):
        if getattr(ctl, "paused", False) is True:
            return False
        if not logged:
            log.info("waiting for the mouse buttons to be released before taking over the mouse")
            logged = True
        if service(ctl, server, POLL_S, (dev.fd,)):
            drain(dev)                     # not grabbed yet: the desktop gets these events itself
    return True


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
        if wait_for_buttons_up(dev, ctl, server) is False:
            return False
        drain(dev)
        dev.grab()
        if not buttons_down(dev):
            return True
        log.info("a mouse button went down while taking over the mouse; waiting for its release")
        dev.ungrab()


def run_session(dev, ctl, server, tick_log, watcher=None, lockwatch=None):
    """Grab dev and filter until it disappears (OSError) or we are stopped.

    Returns True when it ended because the mouse choice changed or the daemon was paused (the caller
    looks again at once). With a `watcher` (hotplug.DeviceWatcher) a change in /dev/input marks the mouse
    list stale; with a `lockwatch` (ScreenWatch) the screen locking ends a running Click-Lock."""
    try:
        ui = UInput.from_device(dev, name=UI_NAME)  # virtual device first, only then grab
    except (OSError, UInputError):
        dev.close()
        raise
    grabbed = False
    kernel_stamps = use_monotonic_clock(dev)    # before anything is read: the stamps then match time.monotonic()

    out = KeyState()                    # what the virtual mouse has been told is pressed
    emit = FrameWriter(ui)

    def write(frames):
        emit(frames)
        for frame in frames:
            out.update(frame)

    try:
        if grab_when_buttons_up(dev, ctl, server) is False:
            return True                     # paused while waiting for a held button: nothing was grabbed
        grabbed = True
        cfg = ctl.cfg
        pinned = mice.parse_choice(cfg.get("mouse"))
        if pinned and pinned != _ident(dev):
            # the choice changed between discover() and the grab (set_config only flags a running session)
            log.info("mouse choice changed, switching")
            return True
        log.info("filtering %s (%s), lock=%gms pre=%gms hold=%gms, click-lock %s", dev.name, dev.path,
                 cfg["lock_ms"], cfg["pre_ms"], cfg["hold_ms"],
                 f"after {cfg['clicklock_ms'] / 1000:g}s ({cfg['clicklock_mode']})" if cfg["clicklock"] else "off")
        ctl.reset_filter()
        ctl.restart_requested = False
        ctl.set_device(True, dev.name, None, ident=_ident(dev))
        # Nothing is held: grab_when_buttons_up() checked that after the grab. Not asked again here,
        # because asking (EVIOCGKEY) would throw away a click queued since then (see FrameReader).
        reader = FrameReader(dev.active_keys)
        while True:
            if ctl.paused is True:
                return True
            if ctl.restart_requested is True:
                ctl.restart_requested = False
                log.info("mouse choice changed, switching")
                ctl.set_device(False, None, "switching mouse")
                return True
            timeout = None
            now = time.monotonic()
            if (deadline := ctl.deadline) is not None:
                timeout = min(max(deadline - now, 0.0), ctl.cfg["hold_ms"] / 1000.0)
            if (deadline := ctl.clicklock_deadline) is not None:
                timeout = min(timeout if timeout is not None else POLL_S, max(deadline - now, 0.0))
            if (deadline := ctl.debounce_deadline) is not None:
                timeout = min(timeout if timeout is not None else POLL_S, max(deadline - now, 0.0))
            extra = [dev.fd]
            if watcher is not None:
                extra.append(watcher.fileno())
            if lockwatch is not None:
                lockwatch.start()           # no-op while it runs; brings back one that ended (rate-limited)
            lock_fd = None if lockwatch is None else lockwatch.fileno()
            if lock_fd is not None:
                extra.append(lock_fd)
            readable = service(ctl, server, timeout, extra)
            if watcher is not None and watcher.fileno() in readable and watcher.drain():
                ctl.invalidate_mice()
            if lock_fd is not None and lock_fd in readable and lockwatch.drain():
                ctl.stop_clicklock("the screen locked")
            if dev.fd in readable:
                reader.next_batch()
                read_at = time.monotonic()
                for ev in dev.read():
                    for frame in reader.feed(ev.type, ev.code, ev.value):
                        tick_log(ctl.handle_frame(frame, ev.timestamp() if kernel_stamps else read_at, write))
            tick_log(ctl.handle_flush(time.monotonic(), write))
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
    watcher = DeviceWatcher.open()
    lockwatch = ScreenWatch()
    atexit.register(lockwatch.close)
    last_err = None
    try:
        while True:
            ctl.wake = False
            if ctl.paused is True:
                ctl.set_device(False, None, "paused")
                last_err = None
                idle(ctl, server, POLL_S, watcher)
                continue
            lockwatch.drain()               # nothing to end while no mouse is held; also notices a watcher that ended
            lockwatch.start()               # no-op while it runs; restarts one that ended, at most every 30 s
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
                    if run_session(dev, ctl, server, tick_log, watcher, lockwatch):
                        continue                    # the mouse choice changed or paused: look again at once
                except (OSError, UInputError) as ex:
                    msg = describe(ex)
                    log.warning("session ended: %s; retrying", msg)
                    ctl.set_device(False, None, msg)
                    last_err = msg
            idle(ctl, server, ctl.cfg["retry_s"], watcher)
    except KeyboardInterrupt:
        pass
    finally:
        if watcher is not None:
            watcher.close()
        lockwatch.close()
        server.close()
