"""Click-Lock: hold the left button for a while and Wheelguard keeps clicking for you.

Pure like core.py: evdev events (type, code, value) and timestamps in, frames to write out. No I/O and
no clock of its own.

    press and hold the left button   the press goes through as usual
    still held after `hold_s`        the lock engages: in "repeat" mode the button is released and then
                                     clicked over and over at `rate` clicks per second; in "hold" mode it
                                     simply stays down, like Windows' ClickLock
    let go                           ignored while locked, so the clicking (or holding) carries on
    click again                      the lock ends; that click itself is swallowed, so it never lands
    switch Click-Lock off            the lock ends at once (stop())

A short click never engages anything: the timer only runs while the button is physically down.
"""
from typing import List, Tuple

from evdev import ecodes as e

REPEAT = "repeat"
HOLD = "hold"
MODES = (REPEAT, HOLD)

DEFAULT_HOLD_MS = 5000
DEFAULT_RATE = 10

Event = Tuple[int, int, int]
Frame = List[Event]


def _key(value) -> Frame:
    return [(e.EV_KEY, e.BTN_LEFT, value)]


class ClickLock:
    def __init__(self, enabled=False, hold_ms=DEFAULT_HOLD_MS, mode=REPEAT, rate=DEFAULT_RATE):
        self.phys_down = False      # the user's finger is on the button
        self.virtual_down = False   # what the rest of the system has been told
        self.locked = False
        self.pressed_at = None      # when the current press started, while the hold timer runs
        self.stop_pending = False   # the lock was ended by a press; its release is still to come
        self.next_t = None          # repeat mode: when the button flips next
        self.configure(enabled, hold_ms, mode, rate)

    def configure(self, enabled, hold_ms, mode, rate):
        """Change the settings in place. Turning it off does not end a running lock: call stop()."""
        self.enabled = bool(enabled)
        self.hold = hold_ms / 1000.0
        self.mode = mode if mode in MODES else REPEAT
        self.half = 0.5 / max(1, rate)          # a click is down for half a period, up for the other half
        if not self.enabled:
            self.pressed_at = None

    @property
    def active(self) -> bool:
        return self.locked

    @property
    def deadline(self):
        """When flush() has something to do next, or None."""
        if self.locked:
            return self.next_t
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

    def flush(self, now) -> Tuple[List[Frame], bool]:
        """Engage the lock when the hold time is up, and drive the repeat clicks."""
        frames: List[Frame] = []
        changed = False
        if (not self.locked and self.pressed_at is not None and self.phys_down
                and now >= self.pressed_at + self.hold):
            self.locked, self.pressed_at, changed = True, None, True
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
        self.locked, self.next_t, self.pressed_at = False, None, None
        frames = []
        if not self.stop_pending and self.virtual_down != self.phys_down:   # a stopping click settles itself
            self.virtual_down = self.phys_down
            frames.append(_key(1 if self.phys_down else 0))
        return frames, was

    def reset(self):
        """The device went away: forget everything (the kernel releases its buttons itself)."""
        self.phys_down = self.virtual_down = self.locked = self.stop_pending = False
        self.pressed_at = self.next_t = None
