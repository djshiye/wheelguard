"""Finding mice with a scroll wheel among the input devices (needs evdev). Used for the mouse list in
the status and for the "Mouse" choice; the daemon's discover() does the actual picking."""
import glob
import os

import evdev
from evdev import ecodes as e

from . import mice

UI_PREFIX = "wheelguard"        # our own virtual mouse
SKIP_NAMES = ("Keyboard", "Consumer Control", "System Control")

_selected = mice.AUTO           # the "mouse" setting, kept here so discover() can see it (set by the Controller)


def set_selected(value):
    global _selected
    _selected = value if mice.parse_choice(value) else mice.AUTO


def selected():
    return _selected


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


def entry(name, vendor, product):
    model, connection = mice.identify(vendor, product)
    return {"name": model or mice.pretty_name(name) or "Mouse", "device": mice.pretty_name(name),
            "vendor": f"{vendor:04x}", "product": f"{product:04x}", "supported": model is not None,
            "connection": connection}


def list_mice():
    """Every connected mouse with a scroll wheel, one entry per vendor/product, supported ones first.

    Keyboards and consumer-control nodes are skipped, and so is our own virtual mouse. A node we may
    not open is still listed (from sysfs), so the app can say why it can't be used.
    """
    found = {}
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            info = _sysfs_info(path)
            if info:
                found.setdefault((info[1], info[2]), info[0])
            continue
        except OSError:
            continue
        try:
            name = dev.name or ""
            caps = dev.capabilities()
            if (not any(n in name for n in SKIP_NAMES) and not name.startswith(UI_PREFIX)
                    and e.REL_WHEEL in caps.get(e.EV_REL, [])):
                found[(dev.info.vendor, dev.info.product)] = name
        finally:
            dev.close()
    out = [entry(n, v, p) for (v, p), n in found.items()]
    out.sort(key=lambda m: (not m["supported"], m["name"].lower(), m["vendor"], m["product"]))
    return out
