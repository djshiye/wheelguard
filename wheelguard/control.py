"""Daemon state shared by the input path and the IPC server: live config, filter, counters, status.

No device or socket access here, so it can be driven from tests. Everything runs on the daemon's
single thread. Events for subscribers go out through the `publish` callable (set by the IPC server).
"""
import logging
import os
import time

from . import __version__
from . import config as C
from .core import DROP, EMIT, PASS, Decision, WheelFilter
from .frames import flush_frames, process_frame, tick_sign

log = logging.getLogger("wheelguard")
POLL_S = 2.0  # config.json mtime poll interval


class Controller:
    def __init__(self, overrides=None, config_path=None, publish=None, clock=time.time):
        self.config_path = config_path or C.CONFIG_PATH
        self.overrides = dict(overrides or {})  # command-line flags; win over the file
        self.publish = publish or (lambda msg: None)
        self.clock = clock
        self.connected = False
        self.device = None
        self.error = "mouse not found"
        self.stats = {"ticks": 0, "passed": 0, "dropped": 0, "since": clock()}
        self.file_cfg = dict(C.DEFAULTS)
        try:
            self.file_cfg = C.load(self.config_path)
        except (OSError, ValueError) as ex:
            log.error("ignoring config: %s", ex)
        self.cfg = C.normalize(self.overrides, self.file_cfg)  # ValueError: bad flags
        self.wf = self._new_filter()
        self._outbox = []        # frames released when the config changed under a pending hold
        self._outbox_decisions = []
        self._prev_t = None      # previous tick time, for gap_ms while passing through
        self._mtime = self._stat_mtime()
        self._next_poll = clock() + POLL_S

    # ---------- status ----------
    def status(self):
        return {"type": "status", "version": __version__, "connected": self.connected,
                "device": self.device, "error": self.error, "config": C.to_wire(self.cfg),
                "stats": dict(self.stats)}

    def set_device(self, connected, device=None, error=None):
        changed = (connected, device, error) != (self.connected, self.device, self.error)
        self.connected, self.device, self.error = connected, device, error
        if not connected:
            self.reset_filter()
        if changed:
            self.publish(self.status())

    # ---------- input path ----------
    def _new_filter(self):
        return WheelFilter(self.cfg["lock_ms"], self.cfg["pre_ms"], self.cfg["hold_ms"])

    def reset_filter(self):
        self.wf = self._new_filter()
        self._outbox, self._outbox_decisions = [], []
        self._prev_t = None

    @property
    def deadline(self):
        return self.wf.deadline

    def _take_outbox(self):
        frames, decisions = self._outbox, self._outbox_decisions
        self._outbox, self._outbox_decisions = [], []
        return frames, decisions

    def handle_frame(self, events, t, write):
        """Run one evdev frame through the filter (or straight through when disabled).

        `write(frames)` is called with the frames to emit before any counting or publishing, so
        the IPC side never delays the input path.
        """
        frames, decisions = self._take_outbox()
        if self.cfg["enabled"]:
            f, d = process_frame(self.wf, events, t)
        else:
            f, d = [list(events)] if events else [], []
            sign = tick_sign(events)
            if sign:
                gap = None if self._prev_t is None else (t - self._prev_t) * 1000.0
                self._prev_t = t
                d = [Decision(PASS, t, sign, gap, False, "filter disabled")]
        frames, decisions = frames + f, decisions + d
        if frames:
            write(frames)
        self._record(decisions)
        return decisions

    def handle_flush(self, now, write):
        """Release an expired hold (and anything left over from a config change)."""
        frames, decisions = self._take_outbox()
        if self.cfg["enabled"]:
            f, d = flush_frames(self.wf, now)
            frames, decisions = frames + f, decisions + d
        if frames:
            write(frames)
        self._record(decisions)
        return decisions

    def _record(self, decisions):
        for d in decisions:
            if not d.held:
                self.stats["ticks"] += 1
            if d.kind in (EMIT, PASS):
                self.stats["passed"] += 1
            elif d.kind == DROP:
                self.stats["dropped"] += 1
            self.publish({"type": "tick", "t": round(d.t, 3), "sign": d.sign, "kind": d.kind,
                          "held": d.held,
                          "gap_ms": None if d.gap_ms is None else round(d.gap_ms, 1)})

    # ---------- config ----------
    def _stat_mtime(self):
        try:
            return os.stat(self.config_path).st_mtime_ns
        except OSError:
            return None

    def _apply(self, file_cfg):
        """Make file_cfg (plus command-line overrides) the live config. Raises ValueError."""
        new = C.normalize(self.overrides, file_cfg)
        self.file_cfg = file_cfg
        if new == self.cfg:
            return False
        old, self.cfg = self.cfg, new
        keys = ("enabled",) + C.MS_KEYS
        if any(old[k] != new[k] for k in keys):
            # Release a pending hold instead of losing the tick, then start from a clean filter.
            if self.wf.held is not None:
                self._outbox, self._outbox_decisions = flush_frames(self.wf, float("inf"))
            self.wf = self._new_filter()
            self._prev_t = None
        log.info("config applied: %s", C.to_wire(new))
        return True

    def reload(self):
        """Re-read config.json. Returns None or an error message; the live config is kept on error."""
        self._mtime = self._stat_mtime()
        try:
            changed = self._apply(C.load(self.config_path))
        except (OSError, ValueError) as ex:
            log.error("config reload failed: %s", ex)
            return str(ex)
        if changed:
            self.publish(self.status())
        return None

    def set_config(self, partial):
        """Validate, merge, write atomically and apply. Returns None or an error message."""
        try:
            new_file = C.normalize(partial, self.file_cfg)
            C.normalize(self.overrides, new_file)
        except ValueError as ex:
            return str(ex)
        try:
            C.save(new_file, self.config_path)
        except OSError as ex:
            return f"cannot write {self.config_path}: {ex.strerror or ex}"
        self._mtime = self._stat_mtime()
        self._apply(new_file)
        self.publish(self.status())
        return None

    def reset_stats(self):
        self.stats.update(ticks=0, passed=0, dropped=0, since=self.clock())
        self.publish(self.status())

    def poll_config(self):
        """Call often; checks the config file's mtime about every POLL_S seconds."""
        now = self.clock()
        if now < self._next_poll:
            return
        self._next_poll = now + POLL_S
        if self._stat_mtime() != self._mtime:
            log.info("config.json changed, reloading")
            self.reload()
