"""Auto-tune: listen to the wheel for about a minute and recommend the lightest protection preset that
would still have caught the stray ticks it saw.

The daemon's tick stream (PROTOCOL.md) reports every wheel tick with its direction and time. A bounce
shows up as a short A, B, A pattern: a tick, one tick the other way, then the first direction again. For
each such pattern we measure the gap before the odd tick (g1) and the gap after it (g2) and ask, for
each preset in gui/presets.py, whether the daemon's filter (core.WheelFilter) would have dropped it:

    dropped when  g1 < lock_ms  and  (g1 < pre_ms  or  g2 < hold_ms)

A deliberate reversal (A, A, B, B) never forms that pattern, so it is not counted. The analysis is pure
Python (`Analyzer`); `AutoTune` is the QObject the QML card talks to.
"""
from dataclasses import dataclass
from typing import List, NamedTuple, Optional, Tuple

from PySide6.QtCore import Property, QObject, Signal, Slot

from . import presets

TARGET_ACTIVE_S = 60.0          # "scroll normally for about a minute"
MIN_TICKS = 100                 # and at least this many ticks
ACTIVE_GAP_S = 1.0              # a longer pause isn't scrolling: it doesn't count towards the minute
STRAY_MAX_G1_MS = 400.0         # a stray follows its tick within the longest lock window
STRAY_MAX_G2_MS = 150.0         # and the original direction resumes soon after
COVERAGE = 0.9                  # recommend the lightest preset that catches at least this share
MAX_STRAYS = 2000

IDLE, COLLECTING, READY = "idle", "collecting", "ready"


class Stray(NamedTuple):
    g1_ms: float        # time from the tick before the odd one to the odd tick
    g2_ms: float        # time from the odd tick to the tick that resumed the first direction


def catches(preset: presets.Preset, stray: Stray) -> bool:
    """Would this preset's filter have dropped the stray?"""
    return stray.g1_ms < preset.lock_ms and (stray.g1_ms < preset.pre_ms or stray.g2_ms < preset.hold_ms)


@dataclass(frozen=True)
class Result:
    state: str = IDLE
    ticks: int = 0
    active_s: float = 0.0
    strays: int = 0
    progress: float = 0.0           # 0..1 towards a minute of scrolling and the minimum tick count
    recommended: int = -1           # index into presets.PRESETS once there is a recommendation
    caught: int = 0                 # strays the recommended preset would catch
    confidence: str = "none"        # "none" | "low" | "medium" | "high"
    median_gap_ms: float = 0.0      # median g1 of the strays


def recommend(strays: List[Stray]) -> Tuple[int, int]:
    """(preset index, strays it catches): the lightest preset reaching COVERAGE, else the one catching the most."""
    if not strays:
        return 0, 0
    counts = [sum(catches(p, s) for s in strays) for p in presets.PRESETS]
    for i, n in enumerate(counts):
        if n >= COVERAGE * len(strays):
            return i, n
    top = max(counts)
    if top == 0:                                    # nothing helps: the strongest preset is the closest
        return len(counts) - 1, 0
    best = counts.index(top)                        # the lightest of the best
    return best, counts[best]


def confidence_of(ticks: int, strays: int, caught: int) -> str:
    if strays >= 10:
        level = "high"
    elif strays >= 4 or (strays == 0 and ticks >= 300):
        level = "medium"
    else:
        level = "low"
    if strays and caught < COVERAGE * strays:       # even the strongest preset misses some: be modest
        level = {"high": "medium", "medium": "low"}.get(level, level)
    return level


class Analyzer:
    """Feed it ticks; read `result()`. No Qt."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._ticks = 0
        self._active = 0.0
        self._strays: List[Stray] = []
        self._prev: Optional[Tuple[float, int]] = None          # (t, sign) of the last tick
        self._prev2: Optional[Tuple[float, int]] = None         # the one before it

    def feed(self, sign: int, kind: str, held: bool, t: float) -> bool:
        """One tick from the daemon. True when it was counted (held releases and bad data are not)."""
        if held or sign not in (1, -1) or kind not in ("emit", "drop", "hold", "pass"):
            return False        # a released held tick was already counted when it was first held
        prev = self._prev
        if prev is not None:
            if t < prev[0]:
                return False    # time ran backwards: ignore the tick
            gap = t - prev[0]
            if gap <= ACTIVE_GAP_S:
                self._active += gap
        self._ticks += 1
        if prev is not None and self._prev2 is not None:
            (t0, s0), (t1, s1) = self._prev2, prev
            if s1 != s0 and sign == s0:                         # A, B, A
                g1, g2 = (t1 - t0) * 1000.0, (t - t1) * 1000.0
                if g1 <= STRAY_MAX_G1_MS and g2 <= STRAY_MAX_G2_MS and len(self._strays) < MAX_STRAYS:
                    self._strays.append(Stray(g1, g2))
        self._prev2, self._prev = prev, (t, sign)
        return True

    def result(self) -> Result:
        n = len(self._strays)
        progress = min(1.0, min(self._active / TARGET_ACTIVE_S, 1.0) * 0.5 + min(self._ticks / MIN_TICKS, 1.0) * 0.5) \
            if self._ticks else 0.0
        enough = self._active >= TARGET_ACTIVE_S and self._ticks >= MIN_TICKS
        if not enough:
            return Result(COLLECTING if self._ticks else IDLE, self._ticks, self._active, n, progress)
        index, caught = recommend(self._strays)
        gaps = sorted(s.g1_ms for s in self._strays)
        median = gaps[len(gaps) // 2] if gaps else 0.0
        return Result(READY, self._ticks, self._active, n, 1.0, index, caught,
                      confidence_of(self._ticks, n, caught), median)


def describe(r: Result) -> str:
    """The plain-English line under the title."""
    if r.state != READY:
        left = max(0, int(round(TARGET_ACTIVE_S - r.active_s)))
        if r.ticks == 0:
            return "Scroll normally for about a minute and Wheelguard will suggest a strength."
        if r.active_s >= TARGET_ACTIVE_S:
            return "Nearly there: a little more scrolling to be sure."
        return f"Keep scrolling normally: about {left} more seconds."
    name = presets.PRESETS[r.recommended].name
    if r.strays == 0:
        return f"Your wheel didn't bounce while we listened. {name} is plenty."
    times = "once" if r.strays == 1 else f"{r.strays} times"
    text = f"Your wheel bounced {times}. {name} would have caught "
    text += "it." if r.strays == 1 else ("all of them." if r.caught == r.strays else f"{r.caught} of them.")
    if r.caught < r.strays:
        text += " Some strays were too slow for any preset; Advanced settings can go further."
    return text


class AutoTune(QObject):
    """`autoTune` in QML. start() begins a fresh listening session; ticks are fed from DaemonClient.tick."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._a = Analyzer()
        self._running = False
        self._r = Result()

    @Slot()
    def start(self):
        self._a.reset()
        self._running = True
        self._r = self._a.result()
        self.changed.emit()

    @Slot()
    def stop(self):
        """Stop listening; what was learned stays on screen until the next start() or reset()."""
        if self._running:
            self._running = False
            self.changed.emit()

    @Slot()
    def reset(self):
        self._a.reset()
        self._running = False
        self._r = self._a.result()
        self.changed.emit()

    @Slot(int, str, bool, float)
    def feed(self, sign: int, kind: str, held: bool, t: float):
        if not self._running or not self._a.feed(sign, kind, held, t):
            return
        r = self._a.result()
        if (r.state, r.recommended, r.strays, r.confidence, round(r.progress, 2)) != \
                (self._r.state, self._r.recommended, self._r.strays, self._r.confidence, round(self._r.progress, 2)) \
                or r.ticks % 10 == 0:
            self._r = r
            self.changed.emit()
        else:
            self._r = r

    running = Property(bool, lambda s: s._running, notify=changed)
    state = Property(str, lambda s: COLLECTING if s._running and s._r.state == IDLE
                     else (s._r.state if s._r.ticks else IDLE), notify=changed)
    progress = Property(float, lambda s: s._r.progress, notify=changed)
    ticks = Property(int, lambda s: s._r.ticks, notify=changed)
    activeSeconds = Property(float, lambda s: s._r.active_s, notify=changed)
    strays = Property(int, lambda s: s._r.strays, notify=changed)
    caught = Property(int, lambda s: s._r.caught, notify=changed)
    recommendedIndex = Property(int, lambda s: s._r.recommended, notify=changed)
    recommendedName = Property(str, lambda s: presets.PRESETS[s._r.recommended].name if s._r.recommended >= 0 else "",
                               notify=changed)
    confidence = Property(str, lambda s: s._r.confidence, notify=changed)
    needMore = Property(bool, lambda s: s._running and s._r.state != READY, notify=changed)
    medianGapMs = Property(float, lambda s: s._r.median_gap_ms, notify=changed)
    message = Property(str, lambda s: describe(s._r), notify=changed)
