"""Finding mice with a scroll wheel among the input devices (needs evdev). Used for the mouse list in
the status and for the "Mouse" choice; the daemon's discover() does the actual picking."""
import glob
import os

import evdev
from evdev import ecodes as e

from . import mice

UI_PREFIX = "wheelguard"        # our own virtual mouse
SKIP_NAMES = ("Keyboard", "Consumer Control", "System Control")

LETTER_KEYS = frozenset(getattr(e, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")

_selected = mice.AUTO           # the "mouse" setting, kept here so discover() can see it (set by the Controller)


def set_selected(value):
    global _selected
    _selected = value if mice.parse_choice(value) else mice.AUTO


def selected():
    return _selected


def has_letter_keys(caps):
    """True for a node with keyboard letter keys (a keyboard, or a combo device that is one as well)."""
    return not LETTER_KEYS.isdisjoint(caps.get(e.EV_KEY, []))


def keyboard_like(vendor, product, caps):
    """True for a node that must never be grabbed because it may be a keyboard: one with letter keys
    that isn't a supported mouse (a keyboard with a trackpoint, a combo receiver...). The keyboard would
    stop working for the rest of the desktop.

    A supported mouse is exempt: some put their keyboard keys (for buttons bound to keys) on the same node
    as the wheel. The Logitech G Pro X Superlight behind its receiver has a single input node, "Logitech
    PRO X", with letter keys on it; skipping it left that mouse unprotected ("mouse not found"). Its keys
    still reach the desktop through the virtual mouse, like the buttons."""
    return has_letter_keys(caps) and mice.identify(vendor, product)[0] is None


def _sysfs_info(path):
    """(name, vendor, product) of an event node we may not open, from sysfs; None unless it has a wheel."""
    base = f"/sys/class/input/{os.path.basename(path)}/device"

    def attr(name):
        with open(f"{base}/{name}") as f:
            return f.read().strip()
    try:
        name = attr("name")
        if (any(n in name for n in SKIP_NAMES) or name.startswith(UI_PREFIX)
                or int(attr("capabilities/rel") or "0", 16) >> e.REL_WHEEL & 1 != 1):
            return None
        return name, int(attr("id/vendor"), 16), int(attr("id/product"), 16)
    except (OSError, ValueError):
        return None


def entry(name, vendor, product, accessible=True):
    model, connection = mice.identify(vendor, product)
    return {"name": model or mice.pretty_name(name) or "Mouse", "device": mice.pretty_name(name),
            "vendor": f"{vendor:04x}", "product": f"{product:04x}", "supported": model is not None,
            "connection": connection, "accessible": accessible}


def list_mice():
    """Every connected mouse with a scroll wheel, one entry per vendor/product, supported ones first.

    Keyboards and consumer-control nodes are skipped, and so is our own virtual mouse. A node we may
    not open is still listed (from sysfs) with "accessible": false, so the app can say why it can't be
    used. A mouse is accessible when at least one of its nodes can be opened.
    """
    found = {}                  # (vendor, product) -> [name, accessible]
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            info = _sysfs_info(path)
            if info:
                found.setdefault((info[1], info[2]), [info[0], False])
            continue
        except OSError:
            continue
        try:
            name = dev.name or ""
            caps = dev.capabilities()
            if (not any(n in name for n in SKIP_NAMES) and not name.startswith(UI_PREFIX)
                    and e.REL_WHEEL in caps.get(e.EV_REL, [])
                    and not keyboard_like(dev.info.vendor, dev.info.product, caps)):
                found[(dev.info.vendor, dev.info.product)] = [name, True]
        finally:
            dev.close()
    out = [entry(n, v, p, ok) for (v, p), (n, ok) in found.items()]
    out.sort(key=lambda m: (not m["supported"], m["name"].lower(), m["vendor"], m["product"]))
    return out
