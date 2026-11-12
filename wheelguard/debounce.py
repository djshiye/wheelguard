"""Click debounce ("Double-click fix"): merge the bounce of a worn switch into one clean press.

Pure like core.py and clicklock.py: evdev events (type, code, value) and timestamps in, events out.

A switch that is wearing out can send press, release, press, release for a single physical click, or
let go for a moment in the middle of a drag, a few milliseconds apart. So the debounce works on the
RELEASE: with a window of N ms, a release of the left, right, middle or a thumb (side) button is held back. If the
same button is pressed again less than N ms later, that was a bounce: the held release and the new
press are both dropped and the desktop simply sees the button stay down. Otherwise the release goes
through when the window ends (flush(), driven by `deadline`), N ms late at most. Presses are never
delayed, and a long drag with a bounce in it stays one drag.

Safety: a release is only ever dropped together with the press that follows it, so every press that
went through gets exactly one release that goes through. A held release is handed out by flush(), by
the next event of the same button once its window is over, and by drain() when the window is changed,
the device goes away or the daemon stops. That bookkeeping keeps running even after the window is set
to 0 ("off").
"""
from typing import List, Tuple

from evdev import ecodes as e

BUTTONS = (e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA)      # the two thumb buttons since 1.10.0
DEFAULT_ON_MS = 40

Event = Tuple[int, int, int]


class ClickDebounce:
    def __init__(self, window_ms=0):
        self.window = 0.0
        self.down = set()           # buttons the desktop has been told are pressed
        self.held = {}              # button -> time of its release that is being held back
        self.merged_codes = []      # the buttons whose bounce the last process() merged (for per-button counts)
        self.configure(window_ms)

    def configure(self, window_ms):
        """Change the window. Held releases stay held; the caller should drain() when it shrinks."""
        self.window = max(0.0, float(window_ms or 0)) / 1000.0

    def reset(self):
        """The device went away: forget everything (the kernel releases the virtual buttons itself)."""
        self.down.clear()
        self.held.clear()

    @property
    def deadline(self):
        """When flush() has something to do next (a held release is due), or None."""
        return min(self.held.values()) + self.window if self.held else None

    def flush(self, now) -> List[Tuple[float, List[Event]]]:
        """The held releases whose window is over at `now`, as [(release time, [event])], oldest first."""
        due = sorted((t, code) for code, t in self.held.items() if now < t or now - t >= self.window)
        out = []
        for t, code in due:
            del self.held[code]
            self.down.discard(code)
            out.append((t, [(e.EV_KEY, code, 0)]))
        return out

    def drain(self) -> List[Tuple[float, List[Event]]]:
        """Every held release, now (window changed, device gone, daemon stopping)."""
        return self.flush(float("inf"))

    def process(self, events, t) -> Tuple[List[Event], int]:
        """One evdev frame (without its SYN_REPORT) at time t. Returns (events to pass on, bounces merged).

        Releases that were already due come out first, ahead of this frame's own events.
        """
        out = [ev for _, evs in self.flush(t) for ev in evs]
        self.merged_codes = []
        if not any(ev[0] == e.EV_KEY and ev[1] in BUTTONS for ev in events):
            return out + list(events), 0
        merged, swallowed = 0, False
        for ev in events:
            etype, code, value = ev
            if etype != e.EV_KEY or code not in BUTTONS:
                out.append(ev)
                continue
            if value == 1:
                if code in self.held:                   # pressed again inside the window: a bounce
                    del self.held[code]
                    merged += 1
                    self.merged_codes.append(code)
                    swallowed = True
                    continue
                self.down.add(code)
                out.append(ev)
            elif value == 0:
                if code in self.down and self.window > 0:
                    if code in self.held:               # a second release; keep the first one's time
                        swallowed = True
                        continue
                    self.held[code] = t
                    swallowed = True
                    continue
                self.down.discard(code)
                out.append(ev)
            else:                                       # autorepeat
                out.append(ev)
        if swallowed:                                   # the scancode that went with a dropped button event
            out = [ev for ev in out if not (ev[0] == e.EV_MSC and ev[1] == e.MSC_SCAN)]
        return out, merged
