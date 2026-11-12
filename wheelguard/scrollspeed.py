"""Scroll speed: how far one notch of the wheel scrolls, as a percentage of what the mouse sends.

Pure like core.py: frames of evdev events (type, code, value) in, frames out. It works on what leaves the
bounce filter, so a blocked tick is never scaled back into existence.

A wheel notch reaches the desktop twice in one frame: REL_WHEEL (whole notches, +1/-1) for older
programs, and REL_WHEEL_HI_RES (120 per notch) for everything current, which is what decides how far a
page moves. So the high-resolution value is multiplied (150% turns 120 into 180), with the fraction that
doesn't fit carried over to the next tick, and the whole notches are counted again from the result: at
200% an old program gets two notches per notch, at 50% one for every two. Only the vertical wheel is
changed; a tilt wheel is left alone. At 100% nothing is touched at all.
"""
from evdev import ecodes as e

DEFAULT = 100
RANGE = (50, 300)       # percent. Below 50 a single notch would be too small for the desktop to act on.
NOTCH = 120             # REL_WHEEL_HI_RES units in one notch


class ScrollSpeed:
    def __init__(self, percent=DEFAULT):
        self.percent = DEFAULT
        self._part = 0          # hundredths of a high-resolution unit not sent yet
        self._notch = 0         # high-resolution units sent that haven't made a whole notch yet
        self.configure(percent)

    def configure(self, percent):
        percent = int(percent or DEFAULT)
        if percent != self.percent:
            self.percent = percent
            self.reset()

    @property
    def active(self):
        return self.percent != DEFAULT

    def reset(self):
        """Forget the carried-over fractions (the speed changed, or the mouse went away)."""
        self._part = self._notch = 0

    def _scale(self, hires):
        """`hires` units from the mouse -> (units to send, whole notches to send)."""
        if (hires > 0) != (self._notch > 0 or self._part > 0) and (self._notch or self._part):
            self.reset()                        # the direction changed: start clean, the first tick back counts fully
        total = hires * self.percent + self._part
        out = int(total / 100)                  # towards zero, so both directions behave the same
        self._part = total - out * 100
        self._notch += out
        notches = int(self._notch / NOTCH)
        self._notch -= notches * NOTCH
        return out, notches

    def frame(self, events):
        """One frame (without its SYN_REPORT) with its vertical wheel events rescaled. A frame without any
        comes back as it is; one left empty comes back empty (the caller drops it)."""
        wheel = hires = None
        for etype, code, value in events:
            if etype == e.EV_REL:
                if code == e.REL_WHEEL:
                    wheel = value
                elif code == e.REL_WHEEL_HI_RES:
                    hires = value
        if wheel is None and hires is None:
            return events
        out_hires, out_wheel = self._scale(hires if hires is not None else wheel * NOTCH)
        rest = [ev for ev in events
                if not (ev[0] == e.EV_REL and ev[1] in (e.REL_WHEEL, e.REL_WHEEL_HI_RES))]
        if out_hires:
            rest.append((e.EV_REL, e.REL_WHEEL_HI_RES, out_hires))
        if out_wheel:
            rest.append((e.EV_REL, e.REL_WHEEL, out_wheel))
        return rest

    def frames(self, frames):
        """Every frame through frame(); frames left empty are dropped."""
        if self.percent == DEFAULT:
            return frames
        return [f for f in map(self.frame, frames) if f]
