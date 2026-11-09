"""Notice that the computer was suspended (or that the daemon was frozen for a long time).

While the machine sleeps CLOCK_MONOTONIC stands still but CLOCK_BOOTTIME keeps counting, so the gap
between the two growing means "we slept". A loop that stopped running for a long time without a
sleep (a stalled process) is caught as well, because the daemon wakes up at least every couple of
seconds. Either way a running Click-Lock must not outlive it.
"""
import time

JUMP_S = 3.0        # more than this much sleeping between two looks counts as a suspend
STALL_S = 15.0      # the loop normally runs every <= 2 s; this long without a look is a stall


def _boottime():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class SuspendWatch:
    def __init__(self, boottime=_boottime, monotonic=time.monotonic):
        self._boot, self._mono = boottime, monotonic
        self._last_mono = monotonic()
        self._last_slept = boottime() - self._last_mono

    def poll(self):
        """True when the machine slept (or the loop stalled) since the previous call."""
        mono = self._mono()
        slept = self._boot() - mono
        jumped = slept - self._last_slept > JUMP_S
        stalled = mono - self._last_mono > STALL_S
        self._last_mono, self._last_slept = mono, slept
        return jumped or stalled
