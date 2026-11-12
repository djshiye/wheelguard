#!/usr/bin/env python3
"""Fake wheelguard daemon speaking PROTOCOL.md, for developing the GUI without the mouse.

    tools/mock_daemon.py --scenario connected      # realistic scroll bursts, ~9% bounces, real WheelFilter
    tools/mock_daemon.py --scenario disconnected   # daemon up, mouse asleep / unplugged
    tools/mock_daemon.py --scenario permission     # daemon up, udev rule missing
    tools/mock_daemon.py --scenario unsupported    # daemon up, only a mouse that isn't a supported model
    tools/mock_daemon.py --scenario unsupported-noaccess   # the same, and the service may not open it
    tools/mock_daemon.py --scenario allowed        # Automatic protecting a mouse the user allowed
    tools/mock_daemon.py --scenario none           # no socket at all (just sleeps)

Uses a private socket by default so it never collides with the real service:
    wheelguard-settings --socket "$XDG_RUNTIME_DIR/wheelguard-mock.sock"
"""
import argparse
import datetime
import json
import os
import random
import re
import selectors
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
from wheelguard import __version__ as VERSION  # noqa: E402
from wheelguard.core import DROP, HOLD, WheelFilter  # noqa: E402

DEVICE = "Endgame Gear Endgame Gear HS Dongle"
DEFAULT_CONFIG = {"enabled": True, "lock_ms": 300, "pre_ms": 60, "hold_ms": 25,
                  "vendor": "3367", "product": "1970", "retry_s": 2,
                  "clicklock": False, "clicklock_ms": 5000, "clicklock_mode": "repeat", "clicklock_rate": 10,
                  "clicklock_max_s": 600, "clicklock_ignore_drag": True, "clicklock_drag_counts": 400,
                  "clicklock_button": "left", "debounce_ms": 0, "scroll_speed": 100, "mouse": "auto"}
OVERRIDE_KEYS = ("enabled", "pre_ms", "hold_ms", "lock_ms", "clicklock")
MICE = [   # what the mock reports as connected (mice with a scroll wheel), supported ones first
    {"name": "Endgame Gear XM2w 4k v2", "device": "Endgame Gear HS Dongle", "vendor": "3367", "product": "1970",
     "supported": True, "connection": "dongle"},
    {"name": "Logitech G Pro X Superlight", "device": "Logitech USB Receiver", "vendor": "046d", "product": "4093",
     "supported": True, "connection": "receiver"},
    {"name": "Razer Viper Mini", "device": "Razer Viper Mini", "vendor": "1532", "product": "008a",
     "supported": False, "connection": None},
]
OTHER_MOUSE = {"name": "Logitech PRO X 2", "device": "Logitech PRO X 2", "vendor": "046d", "product": "409f",
               "supported": False, "connection": None}
MODEL, CONNECTION = "Endgame Gear XM2w 4k v2", "dongle"
BOUNCE_RATE = 0.09


def make_history(kind, rng):
    """Fake daily totals for the wheel-health chart: "steady", "wear" (bouncing more this week), "short" or "empty"."""
    today = datetime.date.today()
    days = []
    count = {"steady": 60, "wear": 60, "short": 3, "empty": 0}[kind]
    for n in range(count - 1, -1, -1):
        ticks = rng.randint(1500, 6000)
        rate = rng.uniform(0.015, 0.035)
        if kind == "wear" and n < 7:
            rate = rng.uniform(0.07, 0.11) - (0.0025 * n)
        if n % 9 == 4:
            continue                                   # a day the mouse wasn't used
        days.append({"date": (today - datetime.timedelta(days=n)).isoformat(), "ticks": ticks,
                     "dropped": round(ticks * rate)})
    return days


def default_socket():
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.path.join(run, "wheelguard-mock.sock")


def validate(cfg, patch):
    """PROTOCOL.md rules. Returns (merged, None) or (None, message)."""
    if not isinstance(patch, dict):
        return None, "config must be an object"
    merged = dict(cfg)
    for k, v in patch.items():
        if k not in DEFAULT_CONFIG:
            return None, f"unknown key: {k}"
        if k in ("enabled", "clicklock", "clicklock_ignore_drag"):
            if not isinstance(v, bool):
                return None, f"{k} must be true or false"
        elif k == "clicklock_mode":
            if v not in ("repeat", "hold"):
                return None, 'clicklock_mode must be "repeat" or "hold"'
            merged[k] = v
            continue
        elif k == "clicklock_button":
            if v not in ("left", "right"):
                return None, 'clicklock_button must be "left" or "right"'
            merged[k] = v
            continue
        elif k == "clicklock_drag_counts":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 50 <= v <= 20000:
                return None, "clicklock_drag_counts must be between 50 and 20000"
        elif k in ("vendor", "product"):
            merged[k] = str(v)
            continue
        elif k == "mouse":
            if not (v == "auto" or (isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{4}", v))):
                return None, 'mouse must be "auto" or the hex IDs of a mouse like "3367:1970"'
            merged[k] = v.lower()
            continue
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, f"{k} must be a number"
        merged[k] = v
    for k in ("lock_ms", "pre_ms", "hold_ms"):
        if merged[k] > 2000:
            return None, f"{k} must be <= 2000"
        if merged[k] < 0:
            return None, f"{k} must be >= 0"
    if not 500 <= merged["clicklock_ms"] <= 30000:
        return None, "clicklock_ms must be between 500 and 30000"
    if merged["clicklock_max_s"] != 0 and not 10 <= merged["clicklock_max_s"] <= 3600:
        return None, "clicklock_max_s must be 0 (never) or between 10 and 3600"
    if merged["debounce_ms"] != 0 and not 10 <= merged["debounce_ms"] <= 150:
        return None, "debounce_ms must be 0 (off) or between 10 and 150"
    if not 50 <= merged["scroll_speed"] <= 300 or merged["scroll_speed"] != int(merged["scroll_speed"]):
        return None, "scroll_speed must be a whole number between 50 and 300"
    if not 1 <= merged["clicklock_rate"] <= 30:
        return None, "clicklock_rate must be between 1 and 30"
    if not 0 < merged["hold_ms"] < merged["lock_ms"]:
        return None, "hold_ms must be > 0 and < lock_ms"
    if not 0 <= merged["pre_ms"] <= merged["lock_ms"]:
        return None, "pre_ms must be between 0 and lock_ms"
    return merged, None


class TickSource:
    """Random scrolling: bursts of ticks in one direction with pauses, plus encoder bounces."""

    def __init__(self, rng, speed=1.0):
        self.rng = rng
        self.speed = speed
        self.queue = []          # (due_monotonic, sign), sorted
        self.next_burst = time.monotonic() + 0.4

    def _schedule_burst(self, now):
        rng = self.rng
        sign = rng.choice((1, -1))
        n = rng.randint(3, 16)
        t = now
        for _ in range(n):
            t += rng.uniform(0.035, 0.11) / self.speed
            self.queue.append((t, sign))
            if rng.random() < BOUNCE_RATE:
                # A stray reversed tick right after the real one, sometimes two in a row.
                b = t + rng.uniform(0.008, 0.045)
                self.queue.append((b, -sign))
                if rng.random() < 0.3:
                    self.queue.append((b + rng.uniform(0.008, 0.03), -sign))
        if rng.random() < 0.25:   # occasionally a deliberate reversal after a short pause
            t += rng.uniform(0.12, 0.3)
            for _ in range(rng.randint(2, 5)):
                t += rng.uniform(0.04, 0.09)
                self.queue.append((t, -sign))
        self.queue.sort()
        self.next_burst = t + rng.uniform(0.5, 2.2) / self.speed

    def due(self, now):
        if now >= self.next_burst and not self.queue:
            self._schedule_burst(now)
        out = []
        while self.queue and self.queue[0][0] <= now:
            out.append(self.queue.pop(0))
        return out

    def next_wake(self, now):
        if self.queue:
            return self.queue[0][0]
        return self.next_burst


class Client:
    def __init__(self, conn):
        self.conn = conn
        self.buf = b""
        self.out = b""
        self.subscribed = False


class Mock:
    paused = False

    def __init__(self, args):
        self.scenario = args.scenario
        self.cfg = dict(DEFAULT_CONFIG)
        for item in args.set:
            key, _, raw = item.partition("=")
            self.cfg[key] = json.loads(raw)
        self.clicklock_active = args.clicklock_active
        if args.clicklock_active:
            self.cfg["clicklock"] = True
        left = args.debounced - args.debounced // 8
        self.stats = {"ticks": 0, "passed": 0, "dropped": 0, "debounced": args.debounced,
                      "debounced_by": {"left": left, "right": args.debounced - left, "middle": 0},
                      "since": time.time()}
        self.version = args.version or VERSION
        self.paused = args.paused
        self.file_cfg = dict(self.cfg)
        self.override = {}
        self.override_owner = None
        self.sel = selectors.DefaultSelector()
        self.clients = {}
        self.rng = random.Random(args.seed)
        self.source = TickSource(self.rng, args.rate)
        self.filter = WheelFilter(self.cfg["lock_ms"], self.cfg["pre_ms"], self.cfg["hold_ms"])
        self.mono0 = time.monotonic()
        self.wall0 = time.time()
        self.path = args.socket
        self.history = make_history(args.history, random.Random(args.seed))

    # --- protocol
    def status(self):
        connected = self.scenario in ("connected", "allowed") and not self.paused
        error = "permission denied: /dev/input/event2" if self.scenario == "permission" else None
        if self.paused:
            error = "paused"
        other = self.scenario == "allowed"
        device = OTHER_MOUSE["device"] if other else DEVICE
        ident = OTHER_MOUSE["vendor"] + ":" + OTHER_MOUSE["product"] if other else "3367:1970"
        return {"type": "status", "version": self.version, "connected": connected,
                "device": device if connected else None, "ident": ident if connected else None,
                "model": MODEL if connected and not other else None,
                "connection": CONNECTION if connected and not other else None,
                "error": error, "override": dict(self.override), "paused": self.paused,
                "config": dict(self.cfg), "mice": self.mice(), "stats": dict(self.stats),
                "clicklock_active": self.clicklock_active and self.cfg["clicklock"]}

    def mice(self):
        if self.scenario.startswith("unsupported"):
            return [dict(OTHER_MOUSE, accessible=self.scenario == "unsupported")]
        if self.scenario == "allowed":
            return [dict(OTHER_MOUSE, accessible=True)]
        if self.cfg["mouse"] == OTHER_MOUSE["vendor"] + ":" + OTHER_MOUSE["product"]:
            return MICE + [dict(OTHER_MOUSE, accessible=self.scenario != "permission")]     # the user picked it
        return MICE

    def send(self, c, msg):
        c.out += json.dumps(msg).encode() + b"\n"
        self.flush(c)

    def flush(self, c):
        try:
            while c.out:
                n = c.conn.send(c.out)
                c.out = c.out[n:]
        except BlockingIOError:
            if len(c.out) > 1 << 16:     # stalled client: disconnect, like the real daemon
                self.drop_client(c)
        except OSError:
            self.drop_client(c)

    def broadcast(self, msg):
        for c in list(self.clients.values()):
            if c.subscribed:
                self.send(c, msg)

    def drop_client(self, c):
        if c.conn.fileno() in self.clients:
            self.sel.unregister(c.conn)
            del self.clients[c.conn.fileno()]
        if self.override and self.override_owner is c:      # like the real daemon: they end with their client
            self.override, self.override_owner = {}, None
            self.apply_cfg()
            self.broadcast(self.status())
        try:
            c.conn.close()
        except OSError:
            pass

    def handle(self, c, msg):
        cmd = msg.get("cmd") if isinstance(msg, dict) else None
        if cmd == "status":
            self.send(c, self.status())
        elif cmd == "subscribe":
            c.subscribed = True
            self.send(c, self.status())
        elif cmd == "set":
            merged, err = validate(self.file_cfg, msg.get("config"))
            if err:
                self.send(c, {"type": "error", "message": err})
                return
            self.file_cfg = merged
            for k in msg.get("config"):
                self.override.pop(k, None)
            self.apply_cfg()
            self.send(c, {"type": "ok"})
            self.broadcast(self.status())
        elif cmd == "override":
            patch = msg.get("config")
            if not isinstance(patch, dict) or any(k not in OVERRIDE_KEYS for k in patch):
                self.send(c, {"type": "error", "message": "that cannot be set temporarily"})
                return
            _merged, err = validate(self.file_cfg, patch)
            if err:
                self.send(c, {"type": "error", "message": err})
                return
            self.override, self.override_owner = dict(patch), (c if patch else None)
            self.apply_cfg()
            self.send(c, {"type": "ok"})
            self.broadcast(self.status())
        elif cmd == "pause":
            if not isinstance(msg.get("on"), bool):
                self.send(c, {"type": "error", "message": "on must be true or false"})
                return
            self.paused = msg["on"]
            self.send(c, {"type": "ok"})
            self.broadcast(self.status())
        elif cmd == "history":
            self.send(c, {"type": "history", "days": self.history})
        elif cmd == "mice":
            self.send(c, {"type": "mice", "mice": self.mice()})
        elif cmd == "clicklock_stop":
            was, self.clicklock_active = self.clicklock_active, False
            self.send(c, {"type": "ok"})
            if was:
                self.broadcast(self.status())
        elif cmd == "reload":
            self.send(c, {"type": "ok"})
        elif cmd == "reset_stats":
            self.stats.update(ticks=0, passed=0, dropped=0, debounced=0, since=time.time(),
                              debounced_by={"left": 0, "right": 0, "middle": 0})
            self.send(c, {"type": "ok"})
            self.broadcast(self.status())
        else:
            self.send(c, {"type": "error", "message": f"unknown command: {cmd!r}"})

    def apply_cfg(self):
        self.cfg = dict(self.file_cfg, **self.override)
        self.filter = WheelFilter(self.cfg["lock_ms"], self.cfg["pre_ms"], self.cfg["hold_ms"])

    # --- ticks
    def emit_tick(self, kind, t, sign, held, gap):
        if kind != HOLD:
            self.stats["ticks"] += 1
            if kind == DROP:
                self.stats["dropped"] += 1
            else:
                self.stats["passed"] += 1
        wall = self.wall0 + (t - self.mono0)
        self.broadcast({"type": "tick", "t": round(wall, 4), "sign": sign, "kind": kind,
                        "held": held, "gap_ms": None if gap is None else round(gap, 1)})

    def feed(self, now, sign):
        if not self.cfg["enabled"]:
            self.emit_tick("pass", now, sign, False, None)
            return
        for d in self.filter.feed(now, sign):
            self.emit_tick(d.kind, d.t, d.sign, d.held, d.gap_ms)

    def tick_loop_step(self, now):
        if self.scenario != "connected" or self.paused:
            return
        if self.cfg["enabled"]:
            for d in self.filter.flush(now):
                self.emit_tick(d.kind, d.t, d.sign, d.held, d.gap_ms)
        for due, sign in self.source.due(now):
            self.feed(due, sign)

    def timeout(self, now):
        if self.scenario != "connected":
            return 1.0
        wake = self.source.next_wake(now)
        if self.filter.deadline is not None:
            wake = min(wake, self.filter.deadline)
        return max(0.0, min(1.0, wake - now))

    # --- main
    def serve(self):
        if self.scenario == "none":
            print("mock_daemon: scenario 'none', not listening", flush=True)
            while True:
                time.sleep(3600)
        if os.path.exists(self.path):
            os.unlink(self.path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o600)
        srv.listen(8)
        srv.setblocking(False)
        self.sel.register(srv, selectors.EVENT_READ, "accept")
        print(f"mock_daemon: scenario {self.scenario!r} on {self.path}", flush=True)
        try:
            while True:
                now = time.monotonic()
                for key, _ in self.sel.select(self.timeout(now)):
                    if key.data == "accept":
                        conn, _ = srv.accept()
                        conn.setblocking(False)
                        c = Client(conn)
                        self.clients[conn.fileno()] = c
                        self.sel.register(conn, selectors.EVENT_READ, "client")
                    else:
                        self.read_client(self.clients.get(key.fileobj.fileno()))
                self.tick_loop_step(time.monotonic())
        finally:
            srv.close()
            if os.path.exists(self.path):
                os.unlink(self.path)

    def read_client(self, c):
        if c is None:
            return
        try:
            data = c.conn.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self.drop_client(c)
            return
        c.buf += data
        while b"\n" in c.buf:
            line, c.buf = c.buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                self.handle(c, json.loads(line))
            except ValueError:
                self.send(c, {"type": "error", "message": "invalid JSON"})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=("connected", "disconnected", "permission", "unsupported",
                                                "unsupported-noaccess", "allowed", "none"),
                    default="connected")
    ap.add_argument("--socket", default=default_socket())
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0, help="tick speed multiplier (default 1)")
    ap.add_argument("--history", choices=("steady", "wear", "short", "empty"), default="steady",
                    help="the daily wheel totals to report (default steady; wear shows the wearing-wheel hint)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help="start with a config value, e.g. --set debounce_ms=40 (repeatable)")
    ap.add_argument("--debounced", type=int, default=0, help="start with this many double-click-fix clicks counted")
    ap.add_argument("--version", help="report this service version (to show the app's update prompt)")
    ap.add_argument("--paused", action="store_true", help="start paused (the service has let go of the mouse)")
    ap.add_argument("--clicklock-active", action="store_true",
                    help="pretend Click-Lock is on and clicking right now (for the app and panel UI)")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # let serve() unlink the socket
    try:
        Mock(args).serve()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
