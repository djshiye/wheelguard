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
UI_NAME = "wheelguard Endgame Gear"
UI_PREFIX = "wheelguard"


def _sysfs_is_wheel_mouse(path, vendor, product):
    """Match an event node we may not open by its sysfs attributes (name, ids, REL bitmask)."""
    base = f"/sys/class/input/{os.path.basename(path)}/device"
    try:
        def attr(name):
            with open(f"{base}/{name}") as f:
                return f.read().strip()
        return (int(attr("id/vendor"), 16) == vendor and int(attr("id/product"), 16) == product
                and "Keyboard" not in attr("name")
                and int(attr("capabilities/rel") or "0", 16) >> e.REL_WHEEL & 1 == 1)
    except (OSError, ValueError):
        return False


def discover(vendor, product):
    """(device, error): the wheel-bearing (non-Keyboard) event node of the mouse, or a reason."""
    denied = None
    # not evdev.list_devices(): that silently skips nodes we are not allowed to open
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            if denied is None and _sysfs_is_wheel_mouse(path, vendor, product):
                denied = path
            continue
        except OSError:
            continue
        name = dev.name or ""
        if (dev.info.vendor == vendor and dev.info.product == product
                and "Keyboard" not in name and not name.startswith(UI_PREFIX)
                and e.REL_WHEEL in dev.capabilities().get(e.EV_REL, [])):
            return dev, None
        dev.close()
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


def run_session(dev, ctl, server, tick_log):
    """Grab dev and filter until it disappears (OSError) or we are stopped."""
    try:
        ui = UInput.from_device(dev, name=UI_NAME)  # virtual device first, only then grab
    except (OSError, UInputError):
        dev.close()
        raise
    grabbed = False

    def write(frames):
        write_frames(ui, frames)

    try:
        dev.grab()
        grabbed = True
        cfg = ctl.cfg
        log.info("filtering %s (%s), lock=%gms pre=%gms hold=%gms", dev.name, dev.path,
                 cfg["lock_ms"], cfg["pre_ms"], cfg["hold_ms"])
        ctl.reset_filter()
        ctl.set_device(True, dev.name, None)
        frame = []
        skipping = False  # after SYN_DROPPED, discard everything up to the next SYN_REPORT
        while True:
            deadline = ctl.deadline
            timeout = None
            if deadline is not None:
                timeout = min(max(deadline - time.time(), 0.0), ctl.cfg["hold_ms"] / 1000.0)
            if service(ctl, server, timeout, (dev.fd,)):
                for ev in dev.read():
                    if ev.type == e.EV_SYN:
                        if ev.code == e.SYN_DROPPED:
                            frame, skipping = [], True
                        elif ev.code == e.SYN_REPORT:
                            if skipping:
                                skipping = False
                            else:
                                tick_log(ctl.handle_frame(frame, ev.timestamp(), write))
                            frame = []
                    elif not skipping:
                        frame.append((ev.type, ev.code, ev.value))
            tick_log(ctl.handle_flush(time.time(), write))
    finally:
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
