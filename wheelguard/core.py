"""Pure bounce filter for scroll-wheel ticks: (timestamp_seconds, sign) in, decisions out.

No I/O and no clock of its own, so it can be driven from tests with made-up timestamps.
"""
from typing import List, NamedTuple, Optional

EMIT = "emit"
DROP = "drop"
HOLD = "hold"
PASS = "pass"   # filter disabled: tick forwarded untouched

DEFAULT_LOCK_MS = 300.0
DEFAULT_PRE_MS = 60.0
DEFAULT_HOLD_MS = 25.0


class Decision(NamedTuple):
    kind: str            # EMIT / DROP / HOLD (PASS when the filter is disabled)
    t: float             # timestamp of the tick this decision is about
    sign: int            # +1 / -1
    gap_ms: Optional[float]  # ms since the previous tick of any direction (None for the first)
    held: bool           # True: about an earlier tick that was being held, not the one just fed
    reason: str


class WheelFilter:
    def __init__(self, lock_ms=DEFAULT_LOCK_MS, pre_ms=DEFAULT_PRE_MS, hold_ms=DEFAULT_HOLD_MS):
        self.lock = lock_ms / 1000.0
        self.pre = pre_ms / 1000.0
        self.hold = hold_ms / 1000.0
        self.lock_dir = 0          # 0 = no lock
        self.last_t = 0.0          # time of the last emitted tick in the lock direction
        self.held = None           # (t, sign, gap_ms) of the held reversal, if any
        self.prev_t = None         # time of the previous tick seen (any outcome)

    @property
    def deadline(self):
        """Time at which the held tick must be released, or None if nothing is held."""
        return None if self.held is None else self.held[0] + self.hold

    def flush(self, now) -> List[Decision]:
        """Release the held tick if its hold has run out: emit it and switch the lock."""
        if self.held is None or now < self.held[0] + self.hold:
            return []
        t, sign, gap = self.held
        self.held = None
        self.lock_dir, self.last_t = sign, t
        return [Decision(EMIT, t, sign, gap, True, "hold expired, direction switched")]

    def feed(self, t, sign) -> List[Decision]:
        out = self.flush(t)
        gap = None if self.prev_t is None else (t - self.prev_t) * 1000.0
        self.prev_t = t

        if self.lock_dir == 0 or t - self.last_t >= self.lock:
            # The lock can expire while a hold is still pending (hold started late in the lock window).
            if self.held is not None:
                ht, hs, hg = self.held
                self.held = None
                if hs == sign:
                    out.append(Decision(EMIT, ht, hs, hg, True, "confirmed by second reversal"))
                else:
                    out.append(Decision(DROP, ht, hs, hg, True, "bounce (direction resumed)"))
            self.lock_dir, self.last_t = sign, t
            out.append(Decision(EMIT, t, sign, gap, False, "no lock"))
            return out

        if sign == self.lock_dir:
            if self.held is not None:
                ht, hs, hg = self.held
                self.held = None
                out.append(Decision(DROP, ht, hs, hg, True, "bounce (lock direction resumed)"))
            self.last_t = t
            out.append(Decision(EMIT, t, sign, gap, False, "lock direction"))
            return out

        # Reversal.
        if self.held is not None:
            ht, hs, hg = self.held
            self.held = None
            self.lock_dir, self.last_t = sign, t
            out.append(Decision(EMIT, ht, hs, hg, True, "confirmed by second reversal"))
            out.append(Decision(EMIT, t, sign, gap, False, "confirmed by second reversal"))
            return out
        if t - self.last_t < self.pre:
            out.append(Decision(DROP, t, sign, gap, False, "reversal right after lock tick"))
            return out
        self.held = (t, sign, gap)
        out.append(Decision(HOLD, t, sign, gap, False, "reversal held"))
        return out
