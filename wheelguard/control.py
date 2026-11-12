"""Daemon state shared by the input path and the IPC server: live config, filter, counters, status.

No device or socket access here, so it can be driven from tests. Everything runs on the daemon's
single thread. Events for subscribers go out through the `publish` callable (set by the IPC server).
"""
import atexit
import logging
import os
import time

from evdev import ecodes as e

from . import __version__
from . import config as C
from . import mice, scan
from .clicklock import ClickLock
from .debounce import ClickDebounce
from .core import DROP, EMIT, PASS, Decision, WheelFilter
from .history import History
from .frames import flush_frames, process_frame, tick_sign
from .power import SuspendWatch
from .scrollspeed import ScrollSpeed

log = logging.getLogger("wheelguard")
POLL_S = 2.0  # config.json mtime poll interval
BUTTON_NAMES = {e.BTN_LEFT: "left", e.BTN_RIGHT: "right", e.BTN_MIDDLE: "middle",
                e.BTN_SIDE: "back", e.BTN_EXTRA: "forward"}
PROTECTION_KEYS = ("pre_ms", "hold_ms", "lock_ms")
_NO_DECISIONS = ()


def _new_stats(since):
    return {"ticks": 0, "passed": 0, "dropped": 0, "debounced": 0,
            "debounced_by": dict.fromkeys(BUTTON_NAMES.values(), 0), "since": since}
MICE_MIN_GAP_S = 3.0    # the input devices are never scanned more often than this
HOUSEKEEPING_S = 0.25   # with nothing held or timed, handle_flush() only looks around this often


class Controller:
    def __init__(self, overrides=None, config_path=None, publish=None, clock=None, power=None,
                 mouse_lister=None, history=None, wall=None):
        """`clock` times everything the filter does and must never jump: the monotonic clock, the same one
        the mouse's events are stamped with (see daemon.use_monotonic_clock). `wall` is the calendar clock,
        used only for the "since" shown with the counters. A test's `clock` stands in for both."""
        self.config_path = config_path or C.CONFIG_PATH
        self.overrides = dict(overrides or {})  # command-line flags; win over the file
        self.publish = publish or (lambda msg: None)
        self.wall = wall or clock or time.time
        self.clock = clock = clock or time.monotonic
        self.connected = False
        self.device = None
        self.ident = None                       # "vvvv:pppp" of the mouse being filtered
        self.model = self.connection = None     # friendly name and "dongle"/"receiver"/"cable" of a supported mouse
        self.error = "mouse not found"
        self.stats = _new_stats(self.wall())
        self.override = {}                      # a client's temporary settings (see set_override); never saved
        self.override_owner = None
        self.paused = False                     # the mouse is left alone until resumed (never saved)
        self.wake = False                       # something changed that the main loop should act on at once
        self.file_cfg = dict(C.DEFAULTS)
        try:
            self.file_cfg = C.load(self.config_path)
        except (OSError, ValueError) as ex:
            log.error("ignoring config: %s", ex)
        self.cfg = self._effective(self.file_cfg)  # ValueError: bad flags
        self.power = power or SuspendWatch()
        # the connected mice with a wheel, for the "Mouse" choice; the real daemon (no config_path given) scans
        self.mouse_lister = mouse_lister or (scan.list_mice if config_path is None else None)
        # daily totals for the wheel-health chart; the real daemon (no config_path given) keeps and saves them
        self.history = history
        if history is None and config_path is None:
            self.history = History()
            atexit.register(self.history.save, durable=True)    # also on SIGTERM (a SystemExit) and Ctrl-C
        self.mice = []
        self._mice_at = None
        self._mice_stale = False
        self.restart_requested = False      # the mouse choice changed under a running session
        scan.set_selected(self.cfg["mouse"])
        self.wf = self._new_filter()
        self.cl = ClickLock(*self._clicklock_args())
        self.deb = ClickDebounce(self.cfg["debounce_ms"])
        self.scroll = ScrollSpeed(self.cfg["scroll_speed"])     # applied to what leaves the filter (see _send)
        self._outbox = []        # frames released when the config changed under a pending hold
        self._outbox_decisions = []
        self._prev_t = None      # previous tick time, for gap_ms while passing through
        self._housekeeping_at = None    # when handle_flush() next does its slow checks with nothing pending
        self._mtime = self._stat_mtime()
        self._next_poll = clock() + POLL_S

    # ---------- status ----------
    def status(self):
        stats = dict(self.stats, debounced_by=dict(self.stats["debounced_by"]))
        return {"type": "status", "version": __version__, "connected": self.connected,
                "device": self.device, "ident": self.ident, "model": self.model, "connection": self.connection,
                "error": self.error, "config": C.to_wire(self.cfg), "override": dict(self.override),
                "paused": self.paused, "stats": stats, "mice": self.mice, "clicklock_active": self.cl.active}

    def history_days(self):
        """The daily totals for the `history` command, oldest first (empty without a history)."""
        return self.history.to_list() if self.history is not None else []

    def invalidate_mice(self):
        """The input devices changed (hotplug, a session started or ended): the next refresh rescans."""
        self._mice_stale = True

    def refresh_mice(self, max_age=0.0):
        """Rescan for mice with a scroll wheel unless the last scan is newer than max_age seconds (or
        than MICE_MIN_GAP_S, however often this is asked), or nothing changed since it (invalidate_mice)."""
        now = self.clock()
        if self.mouse_lister is None:
            return self.mice
        if self._mice_at is not None:
            age = now - self._mice_at
            if age < MICE_MIN_GAP_S or (age < max_age and not self._mice_stale):
                return self.mice
        self._mice_at = now
        self._mice_stale = False
        try:
            self.mice = self.mouse_lister()
        except OSError as ex:
            log.warning("scanning for mice failed: %s", ex)
        return self.mice

    def set_device(self, connected, device=None, error=None, ident=None):
        """`ident` is the (vendor, product) of the mouse being filtered, when there is one."""
        model, connection = mice.identify(*ident) if connected and ident else (None, None)
        changed = ((connected, device, error, model, connection)
                   != (self.connected, self.device, self.error, self.model, self.connection))
        self.connected, self.device, self.error = connected, device, error
        self.ident = mice.format_choice(*ident) if connected and ident else None
        self.model, self.connection = model, connection
        if not connected:
            self.reset_filter()
            if self.history is not None:
                self.history.save()                     # nothing is coming in: a good moment to write
        if changed:
            self.invalidate_mice()
            self.publish(self.status())

    # ---------- input path ----------
    def _new_filter(self):
        return WheelFilter(self.cfg["lock_ms"], self.cfg["pre_ms"], self.cfg["hold_ms"])

    def _clicklock_args(self):
        c = self.cfg
        return (c["clicklock"], c["clicklock_ms"], c["clicklock_mode"], c["clicklock_rate"],
                c["clicklock_max_s"], c["clicklock_ignore_drag"], c["clicklock_drag_counts"], c["clicklock_button"])

    def reset_filter(self):
        self.wf = self._new_filter()
        self.cl.reset()
        self.deb.reset()
        self.scroll.reset()
        self._outbox, self._outbox_decisions = [], []
        self._prev_t = None

    @property
    def deadline(self):
        """The wheel filter's hold deadline (None when nothing is held)."""
        return self.wf.deadline

    @property
    def clicklock_deadline(self):
        return self.cl.deadline

    @property
    def debounce_deadline(self):
        """When a held button release (Double-click fix) is due (None when nothing is held)."""
        return self.deb.deadline

    @property
    def next_deadline(self):
        """The earliest of the three deadlines above: when handle_flush() must run next (None: only when the
        mouse or a client says something). Asked for every report of the mouse, so the usual answer is quick."""
        best = None if self.wf.held is None else self.wf.deadline
        if self.cl.locked or self.cl.pressed_at is not None:
            due = self.cl.deadline
            if due is not None and (best is None or due < best):
                best = due
        if self.deb.held:
            due = self.deb.deadline
            if best is None or due < best:
                best = due
        return best

    def _debounced_releases(self, items):
        """Button releases the debounce let go of, through Click-Lock like any other click.
        Returns (frames, lock_changed)."""
        frames, changed = [], False
        for t, events in items:
            lock_frames, rest, ch = self.cl.process(events, t)
            frames += lock_frames + ([rest] if rest else [])
            changed = changed or ch
        return frames, changed

    def _take_outbox(self):
        frames, decisions = self._outbox, self._outbox_decisions
        self._outbox, self._outbox_decisions = [], []
        return frames, decisions

    def handle_frame(self, events, t, write):
        """Run one evdev frame through the filter (or straight through when disabled).

        `write(frames)` is called with the frames to emit before any counting or publishing, so
        the IPC side never delays the input path.
        """
        # Fast path: nearly every frame is plain pointer motion (up to 8000 a second), and unless a button
        # is being timed or held back, nothing here has anything to say about it.
        if not self._outbox and not self.deb.held and self.cl.pressed_at is None and not self.cl.locked:
            for ev in events:
                if ev[0] != e.EV_REL or ev[1] > e.REL_Y:
                    break
            else:
                if events:
                    write([events])
                return _NO_DECISIONS
        frames, decisions = self._take_outbox()
        up_frames, lock_changed = self._debounced_releases(self.deb.flush(t))   # releases that are due go first
        frames += up_frames
        events, fixed = self.deb.process(events, t)         # worn-switch chatter goes next, so nothing else sees it
        lock_frames, events, changed = self.cl.process(events, t)
        frames += lock_frames
        lock_changed = lock_changed or changed
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
            self._send(frames, write)
        self._record(decisions)
        if fixed:
            self.stats["debounced"] += fixed
            for code in self.deb.merged_codes:
                self.stats["debounced_by"][BUTTON_NAMES[code]] += 1
            self.publish(self.status())
        if lock_changed:
            self._clicklock_changed()
        return decisions

    def handle_flush(self, now, write):
        """Release an expired hold (and anything left over from a config change).

        Called after every report of the mouse. With nothing held back, timed or waiting to go out there is
        nothing to release, and the slow checks (was the computer suspended, is the history due for saving)
        are only made every HOUSEKEEPING_S."""
        if (self._housekeeping_at is not None and now < self._housekeeping_at and not self._outbox
                and self.wf.held is None and not self.deb.held
                and self.cl.pressed_at is None and not self.cl.locked):
            return _NO_DECISIONS
        self._housekeeping_at = now + HOUSEKEEPING_S
        if self.power.poll():
            self.stop_clicklock("the computer was suspended")
        frames, decisions = self._take_outbox()
        frames_up, lock_changed = self._debounced_releases(self.deb.flush(now))
        frames += frames_up
        lock_frames, changed = self.cl.flush(now)
        frames += lock_frames
        lock_changed = lock_changed or changed
        if self.cfg["enabled"]:
            f, d = flush_frames(self.wf, now)
            frames, decisions = frames + f, decisions + d
        if frames:
            self._send(frames, write)
        self._record(decisions)
        if self.history is not None:
            self.history.maybe_save()
        if lock_changed:
            self._clicklock_changed()
        return decisions

    def _send(self, frames, write):
        """Write frames that went through the full path, at the chosen scroll speed (plain pointer motion
        takes the fast path in handle_frame and never comes here)."""
        if self.scroll.active:
            frames = self.scroll.frames(frames)
            if not frames:
                return
        write(frames)

    def stop_clicklock(self, reason):
        """End a running Click-Lock from outside the input path (suspend, screen lock, a client's
        request). The button is handed back with the next frames written. Returns True if one was running."""
        frames, was_active = self.cl.stop()
        self._outbox += frames
        if was_active:
            log.info("click-lock released (%s)", reason)
            self.publish(self.status())
        return was_active

    def _clicklock_changed(self):
        if not self.cl.active and self.cl.timed_out:
            log.info("click-lock released (safety timer, %g s)", self.cl.max_s)
        else:
            log.info("click-lock %s", "engaged" if self.cl.active else "released")
        self.publish(self.status())

    def _record(self, decisions):
        ticks = dropped = 0
        for d in decisions:
            if not d.held:
                self.stats["ticks"] += 1
                ticks += 1
            if d.kind in (EMIT, PASS):
                self.stats["passed"] += 1
            elif d.kind == DROP:
                self.stats["dropped"] += 1
                dropped += 1
            self.publish({"type": "tick", "t": round(d.t, 3), "sign": d.sign, "kind": d.kind,
                          "held": d.held,
                          "gap_ms": None if d.gap_ms is None else round(d.gap_ms, 1)})
        if self.history is not None and (ticks or dropped):
            self.history.add(ticks, dropped)

    # ---------- config ----------
    def _stat_mtime(self):
        try:
            return os.stat(self.config_path).st_mtime_ns
        except OSError:
            return None

    def _effective(self, file_cfg):
        """The live config for file_cfg: the file, a client's temporary override on top, command-line flags
        on top of that. An override that no longer fits the file (hold_ms above a lowered lock_ms, say)
        is dropped rather than refusing the file. Raises ValueError."""
        base = file_cfg
        if self.override:
            try:
                base = C.normalize(self.override, file_cfg)
            except ValueError:
                log.info("temporary settings no longer fit the saved ones; dropped")
                self.override, self.override_owner = {}, None
        return C.normalize(self.overrides, base)

    def _apply(self, file_cfg):
        """Make file_cfg (plus a client's override and the command-line flags) the live config. Raises ValueError."""
        new = self._effective(file_cfg)
        self.file_cfg = file_cfg
        if new == self.cfg:
            return False
        old, self.cfg = self.cfg, new
        keys = ("enabled",) + C.MS_KEYS
        if any(old[k] != new[k] for k in keys):
            # Release a pending hold instead of losing the tick, then start from a clean filter.
            if self.wf.held is not None:
                f, d = flush_frames(self.wf, float("inf"))
                self._outbox += f                     # add: a Click-Lock release may be waiting there
                self._outbox_decisions += d
            self.wf = self._new_filter()
            self._prev_t = None
        if old["debounce_ms"] != new["debounce_ms"]:
            self.deb.configure(new["debounce_ms"])
            frames, _ = self._debounced_releases(self.deb.drain())     # let go of what was held back
            self._outbox += frames
        if old["scroll_speed"] != new["scroll_speed"]:
            self.scroll.configure(new["scroll_speed"])
        if any(old[k] != new[k] for k in C.CLICKLOCK_KEYS):
            if old["clicklock_button"] != new["clicklock_button"]:
                frames, was_active = self.cl.stop()       # a lock belongs to the button it started on
                self._outbox += frames
                self.cl.reset()                           # the old button is no longer ours: it passes through as it is
                if was_active:
                    log.info("click-lock released (its button changed)")
            self.cl.configure(*self._clicklock_args())
            if not new["clicklock"] or old["clicklock_mode"] != new["clicklock_mode"]:
                frames, was_active = self.cl.stop()       # switched off: hand the button back now
                self._outbox += frames
                if was_active:
                    log.info("click-lock released (switched off)")
        scan.set_selected(new["mouse"])
        if self.connected and any(old[k] != new[k] for k in ("mouse", "vendor", "product")):
            self.restart_requested = True
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
        if isinstance(partial, dict) and any(k in partial for k in PROTECTION_KEYS):
            # The three timings are one setting on screen. While a profile overrides them the user sees the
            # temporary values, so an edit of one saves all three as shown, never a mix with hidden ones.
            shown = {k: self.override[k] for k in PROTECTION_KEYS if k in self.override}
            partial = {**shown, **partial}
        try:
            new_file = C.normalize(partial, self.file_cfg)
            C.normalize(self.overrides, new_file)
        except ValueError as ex:
            return str(ex)
        try:
            C.save(new_file, self.config_path, fsync=False)     # this is the input thread: don't wait for the disk
        except OSError as ex:
            return f"cannot write {self.config_path}: {ex.strerror or ex}"
        self._mtime = self._stat_mtime()
        for k in partial:
            self.override.pop(k, None)          # a setting changed by hand wins over a temporary one
        if not self.override:
            self.override_owner = None
        self._apply(new_file)
        self.publish(self.status())
        return None

    def set_override(self, partial, owner=None):
        """Temporary settings on top of the saved ones (C.OVERRIDE_KEYS only), replacing any earlier set; {}
        ends them. Nothing is written to disk, and they end by themselves when `owner` (the client that sent
        them) disconnects, so a crashed app can never leave them behind. Returns None or an error message."""
        if not isinstance(partial, dict):
            return "config must be a JSON object"
        for k in partial:
            if k not in C.OVERRIDE_KEYS:
                return f"{k!r} cannot be set temporarily"
        try:
            full = C.normalize(partial, self.file_cfg)
            C.normalize(self.overrides, full)
        except ValueError as ex:
            return str(ex)
        new = {k: full[k] for k in partial}
        changed = new != self.override
        self.override, self.override_owner = new, (owner if new else None)
        if changed:
            self._apply(self.file_cfg)
            self.publish(self.status())
        return None

    def client_gone(self, owner):
        """A client disconnected: its temporary settings end with it."""
        if self.override and owner is self.override_owner:
            log.info("temporary settings ended (their client disconnected)")
            self.set_override({})

    def set_paused(self, on):
        """Pause: let go of the mouse (no filter, no Click-Lock, the desktop reads it directly) until resumed.
        Not saved: a restarted daemon always starts working."""
        if not isinstance(on, bool):
            return "on must be true or false"
        if on != self.paused:
            self.paused = on
            self.wake = True
            log.info("paused" if on else "resumed")
            self.publish(self.status())
        return None

    def reset_stats(self):
        self.stats = _new_stats(self.wall())
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
