"""Frame-level glue between raw evdev frames and WheelFilter. Pure: events are (type, code, value)."""
from evdev import ecodes as e

from .core import EMIT, HOLD, WheelFilter

WHEEL_CODES = (e.REL_WHEEL, e.REL_WHEEL_HI_RES)


def tick_sign(events):
    """Direction (+1/-1) of the vertical wheel tick in a frame, or 0 if there is none."""
    wheel = hires = 0
    for etype, code, value in events:
        if etype != e.EV_REL:
            continue
        if code == e.REL_WHEEL:
            wheel = value
        elif code == e.REL_WHEEL_HI_RES:
            hires = value
    v = wheel or hires
    return (v > 0) - (v < 0)


def release_frame(sign):
    """Own frame (without the SYN_REPORT) for a held tick that is being let through."""
    return [(e.EV_REL, e.REL_WHEEL_HI_RES, 120 * sign), (e.EV_REL, e.REL_WHEEL, sign)]


def _wheel_events(events):
    return [ev for ev in events if ev[0] == e.EV_REL and ev[1] in WHEEL_CODES]


def _frames(wf, decisions):
    """Frames for held ticks that are let through now: the wheel events that were actually held back, so
    a high-resolution wheel's partial step comes out as the same partial step and not as a whole notch."""
    out = []
    for d in decisions:
        if d.held:
            held = wf.__dict__.pop("held_events", None)
            if d.kind == EMIT:
                out.append(held or release_frame(d.sign))
    return out


def process_frame(wf: WheelFilter, events, t):
    """Run one frame (events before its SYN_REPORT, stamped t) through the filter.

    Returns (frames, decisions): frames are lists of events to write, each followed by a
    SYN_REPORT, in order. A frame left empty by stripping is omitted.
    """
    sign = tick_sign(events)
    if not sign:
        return ([list(events)] if events else []), []
    decisions = wf.feed(t, sign)
    frames = _frames(wf, decisions)
    current = next(d for d in decisions if not d.held)
    if current.kind == HOLD:
        wf.held_events = _wheel_events(events)
    if current.kind == EMIT:
        frames.append(list(events))
    else:
        rest = [ev for ev in events if not (ev[0] == e.EV_REL and ev[1] in WHEEL_CODES)]
        if rest:
            frames.append(rest)
    return frames, decisions


def flush_frames(wf: WheelFilter, now):
    """Release an expired hold. Returns (frames, decisions) like process_frame."""
    decisions = wf.flush(now)
    return _frames(wf, decisions), decisions
