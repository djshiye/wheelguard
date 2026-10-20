#!/usr/bin/env python3
"""Fake wheelguard daemon speaking PROTOCOL.md, for developing the GUI without the mouse.

    tools/mock_daemon.py --scenario connected      # realistic scroll bursts, ~9% bounces, real WheelFilter
    tools/mock_daemon.py --scenario disconnected   # daemon up, mouse asleep / unplugged
    tools/mock_daemon.py --scenario permission     # daemon up, udev rule missing
    tools/mock_daemon.py --scenario none           # no socket at all (just sleeps)

Uses a private socket by default so it never collides with the real service:
    wheelguard-settings --socket "$XDG_RUNTIME_DIR/wheelguard-mock.sock"
"""
import argparse
import json
import os
import random
import selectors
import signal
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), ".."))
from wheelguard.core import DROP, EMIT, HOLD, WheelFilter  # noqa: E402

VERSION = "1.0.0-mock"
DEVICE = "Endgame Gear Endgame Gear HS Dongle"
DEFAULT_CONFIG = {"enabled": True, "lock_ms": 300, "pre_ms": 60, "hold_ms": 25,
                  "vendor": "3367", "product": "1970", "retry_s": 2}
BOUNCE_RATE = 0.09


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
        if k == "enabled":
            if not isinstance(v, bool):
                return None, "enabled must be true or false"
        elif k in ("vendor", "product"):
            merged[k] = str(v)
            continue
        elif isinstance(v, bool) or not isinstance(v, (int, float)):
            return None, f"{k} must be a number"
        merged[k] = v
    for k in ("lock_ms", "pre_ms", "hold_ms"):
        if merged[k] > 2000:
            return None, f"{k} must be <= 2000"
        if merged[k] < 0:
            return None, f"{k} must be >= 0"
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
    def __init__(self, args):
        self.scenario = args.scenario
        self.cfg = dict(DEFAULT_CONFIG)
        self.stats = {"ticks": 0, "passed": 0, "dropped": 0, "since": time.time()}
        self.sel = selectors.DefaultSelector()
        self.clients = {}
        self.rng = random.Random(args.seed)
        self.source = TickSource(self.rng, args.rate)
        self.filter = WheelFilter(self.cfg["lock_ms"], self.cfg["pre_ms"], self.cfg["hold_ms"])
        self.mono0 = time.monotonic()
        self.wall0 = time.time()
        self.path = args.socket

    # --- protocol
    def status(self):
        connected = self.scenario == "connected"
        error = "permission denied: /dev/input/event2" if self.scenario == "permission" else None
        return {"type": "status", "version": VERSION, "connected": connected,
                "device": DEVICE if connected else None, "error": error,
                "config": dict(self.cfg), "stats": dict(self.stats)}

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
            merged, err = validate(self.cfg, msg.get("config"))
            if err:
                self.send(c, {"type": "error", "message": err})
                return
            self.cfg = merged
            self.filter = WheelFilter(merged["lock_ms"], merged["pre_ms"], merged["hold_ms"])
            self.send(c, {"type": "ok"})
            self.broadcast(self.status())
        elif cmd == "reload":
            self.send(c, {"type": "ok"})
        elif cmd == "reset_stats":
            self.stats.update(ticks=0, passed=0, dropped=0, since=time.time())
            self.send(c, {"type": "ok"})
            self.broadcast(self.status())
        else:
            self.send(c, {"type": "error", "message": f"unknown command: {cmd!r}"})

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
        if self.scenario != "connected":
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
    ap.add_argument("--scenario", choices=("connected", "disconnected", "permission", "none"),
                    default="connected")
    ap.add_argument("--socket", default=default_socket())
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--rate", type=float, default=1.0, help="tick speed multiplier (default 1)")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))   # let serve() unlink the socket
    try:
        Mock(args).serve()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
