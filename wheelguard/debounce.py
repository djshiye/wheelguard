"""Click debounce ("Double-click fix"): drop the second press of a worn switch's chatter.

Pure like core.py and clicklock.py: evdev events (type, code, value) and timestamps in, events out.

A switch that is wearing out can send press, release, press, release for a single physical click, a few
milliseconds apart. With a window of N ms, a press of the left, right or middle button that arrives less
than N ms after that same button's previous release is dropped, and so is the release that belongs to
it, so the desktop sees one clean click. The window is measured from the last release that went
through, so a long burst of chatter cannot keep extending it.

Safety: a release is only ever dropped when its own press was dropped, so a button the desktop has been
told is pressed is always released. That bookkeeping keeps running even after the window is set to 0
("off") or the device goes away (reset()).
"""
from typing import List, Tuple

from evdev import ecodes as e

BUTTONS = (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE)
DEFAULT_ON_MS = 40

Event = Tuple[int, int, int]


class ClickDebounce:
    def __init__(self, window_ms=0):
        self.window = 0.0
        self.last_up = {}           # button -> time of its last release that went through
        self.suppressed = set()     # buttons whose press was dropped; their release is dropped too
        self.configure(window_ms)

    def configure(self, window_ms):
        self.window = max(0.0, float(window_ms or 0)) / 1000.0

    def reset(self):
        """The device went away: forget everything (the kernel releases the virtual buttons itself)."""
        self.last_up.clear()
        self.suppressed.clear()

    def process(self, events, t) -> Tuple[List[Event], int]:
        """One evdev frame (without its SYN_REPORT) at time t. Returns (events to pass on, presses dropped)."""
        if not any(ev[0] == e.EV_KEY and ev[1] in BUTTONS for ev in events):
            return list(events), 0
        out, dropped, swallowed = [], 0, False
        for ev in events:
            etype, code, value = ev
            if etype != e.EV_KEY or code not in BUTTONS:
                out.append(ev)
                continue
            if value == 1:
                last = self.last_up.get(code)
                if code in self.suppressed or (self.window > 0 and last is not None
                                               and 0 <= t - last < self.window):
                    if code not in self.suppressed:
                        dropped += 1
                    self.suppressed.add(code)
                    swallowed = True
                    continue
                out.append(ev)
            elif value == 0:
                if code in self.suppressed:
                    self.suppressed.discard(code)
                    swallowed = True
                    continue
                self.last_up[code] = t
                out.append(ev)
            else:                                   # autorepeat
                if code in self.suppressed:
                    swallowed = True
                    continue
                out.append(ev)
        if swallowed:                               # the scancode that went with a dropped button event
            out = [ev for ev in out if not (ev[0] == e.EV_MSC and ev[1] == e.MSC_SCAN)]
        return out, dropped
