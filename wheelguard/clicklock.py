"""Click-Lock: hold the left button for a while and Wheelguard keeps clicking for you.

Pure like core.py: evdev events (type, code, value) and timestamps in, frames to write out. No I/O and
no clock of its own.

    press and hold the left button   the press goes through as usual (in "repeat" mode with `ignore_drag`,
                                     moving the pointer more than DRAG_COUNTS while held means you are
                                     dragging, and this press can no longer engage the lock)
    still held after `hold_s`        the lock engages: in "repeat" mode the button is released and then
                                     clicked over and over at `rate` clicks per second; in "hold" mode it
                                     simply stays down, like Windows' ClickLock
    let go                           ignored while locked, so the clicking (or holding) carries on
    click again                      the lock ends; that click itself is swallowed, so it never lands
    press any other mouse button     the lock ends too (an always-available way out); that press
                                     goes through as usual
    switch Click-Lock off            the lock ends at once (stop())
    safety timer                     a lock still running after `max_s` seconds ends by itself (stop()),
                                     as does one the daemon is told about: suspend, screen lock

A short click never engages anything: the timer only runs while the button is physically down.
"""
import math
from typing import List, Tuple

from evdev import ecodes as e

REPEAT = "repeat"
HOLD = "hold"
MODES = (REPEAT, HOLD)

DEFAULT_HOLD_MS = 5000
DEFAULT_RATE = 10
DRAG_COUNTS = 100           # REL_X/REL_Y counts the pointer may wander while held before it counts as a drag
DEFAULT_MAX_S = 600         # safety timer: a lock that is still running after 10 minutes stops by itself

Event = Tuple[int, int, int]
Frame = List[Event]


def _key(value) -> Frame:
    return [(e.EV_KEY, e.BTN_LEFT, value)]


class ClickLock:
    def __init__(self, enabled=False, hold_ms=DEFAULT_HOLD_MS, mode=REPEAT, rate=DEFAULT_RATE,
                 max_s=0, ignore_drag=True):
        self.phys_down = False      # the user's finger is on the button
        self.virtual_down = False   # what the rest of the system has been told
        self.locked = False
        self.pressed_at = None      # when the current press started, while the hold timer runs
        self.stop_pending = False   # the lock was ended by a press; its release is still to come
        self.next_t = None          # repeat mode: when the button flips next
        self.locked_at = None       # when the running lock engaged (for the safety timer)
        self.timed_out = False      # the last lock ended because the safety timer ran out
        self.moved_x = self.moved_y = 0     # pointer motion since the current press (counts)
        self.configure(enabled, hold_ms, mode, rate, max_s, ignore_drag)

    def configure(self, enabled, hold_ms, mode, rate, max_s=0, ignore_drag=True):
        """Change the settings in place. Turning it off does not end a running lock: call stop()."""
        self.enabled = bool(enabled)
        self.hold = hold_ms / 1000.0
        self.mode = mode if mode in MODES else REPEAT
        self.half = 0.5 / max(1, rate)          # a click is down for half a period, up for the other half
        self.ignore_drag = bool(ignore_drag)
        self.max_s = max(0, max_s or 0)         # 0 = no safety timer
        if not self.enabled:
            self.pressed_at = None

    @property
    def active(self) -> bool:
        return self.locked

    @property
    def deadline(self):
        """When flush() has something to do next, or None."""
        if self.locked:
            limit = None if not self.max_s or self.locked_at is None else self.locked_at + self.max_s
            if limit is None:
                return self.next_t
            return limit if self.next_t is None else min(self.next_t, limit)
        if self.pressed_at is not None:
            return self.pressed_at + self.hold
        return None

    # ------------------------------------------------------------------ input
    def process(self, events, t) -> Tuple[List[Frame], Frame, bool]:
        """One evdev frame (without its SYN_REPORT) at time t.

        Returns (frames, rest, changed): frames to write first (each followed by a SYN_REPORT), the
        frame itself with the left-button event kept, rewritten or removed, and whether `active` changed.
        """
        frames, changed = self.flush(t)
        if self.pressed_at is not None and self.phys_down and self.ignore_drag and self.mode == REPEAT:
            self._track_motion(events)
        if self.locked and any(ev[0] == e.EV_KEY and ev[1] != e.BTN_LEFT and ev[2] == 1
                               and e.BTN_MOUSE <= ev[1] < e.BTN_JOYSTICK for ev in events):
            # Any other mouse button (right, middle, side) is an emergency exit: the lock ends at
            # once and the button is handed back to the finger. That press itself goes through.
            stop_frames, _ = self.stop()
            frames, changed = frames + stop_frames, True
        idx = next((i for i, ev in enumerate(events)
                    if ev[0] == e.EV_KEY and ev[1] == e.BTN_LEFT), None)
        if idx is None:
            return frames, list(events), changed
        value = events[idx][2]
        out = None                              # None = swallow the button event
        if value == 1:
            self.phys_down = True
            if self.locked:
                self.locked, self.next_t, self.stop_pending = False, None, True
                changed = True
                if self.mode == REPEAT and self.virtual_down:
                    out, self.virtual_down = 0, False   # stop clicking right now
            else:
                out, self.virtual_down = 1, True
                self.pressed_at = t if self.enabled else None
                self.moved_x = self.moved_y = 0
        elif value == 0:
            self.phys_down = False
            self.pressed_at = None
            if self.stop_pending:
                self.stop_pending = False
                if self.virtual_down:           # hold mode: this release lets go of the held button
                    out, self.virtual_down = 0, False
            elif not self.locked:
                out, self.virtual_down = 0, False
        else:
            out = value if not self.locked and not self.stop_pending else None   # autorepeat
        rest = list(events)
        if out is None:
            del rest[idx]
            rest = [ev for ev in rest if not (ev[0] == e.EV_MSC and ev[1] == e.MSC_SCAN)]
        else:
            rest[idx] = (e.EV_KEY, e.BTN_LEFT, out)
        return frames, rest, changed

    def _track_motion(self, events):
        """Accumulate pointer motion while the button is held; a drag cancels this press's hold timer."""
        for etype, code, value in events:
            if etype == e.EV_REL and code == e.REL_X:
                self.moved_x += value
            elif etype == e.EV_REL and code == e.REL_Y:
                self.moved_y += value
        if math.hypot(self.moved_x, self.moved_y) > DRAG_COUNTS:
            self.pressed_at = None

    def flush(self, now) -> Tuple[List[Frame], bool]:
        """Engage the lock when the hold time is up, and drive the repeat clicks."""
        frames: List[Frame] = []
        changed = False
        if self.locked and self.max_s and self.locked_at is not None and now >= self.locked_at + self.max_s:
            frames, _ = self.stop()                 # the safety timer: hand the button back
            self.timed_out = True
            return frames, True
        if (not self.locked and self.pressed_at is not None and self.phys_down
                and now >= self.pressed_at + self.hold):
            self.locked, self.pressed_at, changed = True, None, True
            self.locked_at, self.timed_out = now, False
            if self.mode == REPEAT:
                frames.append(_key(0))          # finish the click the user is holding
                self.virtual_down = False
                self.next_t = now + self.half
        elif self.locked and self.next_t is not None and now >= self.next_t:
            self.virtual_down = not self.virtual_down
            frames.append(_key(1 if self.virtual_down else 0))
            self.next_t += self.half
            if self.next_t <= now:              # we were stalled: carry on from now, never in a burst
                self.next_t = now + self.half
        return frames, changed

    def stop(self) -> Tuple[List[Frame], bool]:
        """End a running lock and give the button back to the user's finger."""
        was = self.locked
        self.locked, self.next_t, self.pressed_at, self.locked_at = False, None, None, None
        frames = []
        if not self.stop_pending and self.virtual_down != self.phys_down:   # a stopping click settles itself
            self.virtual_down = self.phys_down
            frames.append(_key(1 if self.phys_down else 0))
        return frames, was

    def reset(self):
        """The device went away: forget everything (the kernel releases its buttons itself)."""
        self.phys_down = self.virtual_down = self.locked = self.stop_pending = False
        self.pressed_at = self.next_t = self.locked_at = None
