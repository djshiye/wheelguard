"""Which mice Wheelguard knows by name. Plain data, no I/O (the daemon and the app both use it)."""
import re

from .hw import models

# (vendor, product) of a wheel-bearing input device -> (friendly model name, how it is connected).
# The Superlight's wireless ID is the one hid-logitech-dj gives the mouse behind its receiver.
KNOWN = {
    (0x3367, 0x1970): (models.ENDGAME_XM2W.name, "dongle"),
    (0x3367, 0x1982): (models.ENDGAME_XM2W.name, "cable"),
    (0x1532, 0x00BF): (models.DEATHADDER_V4_PRO.name, "dongle"),
    (0x1532, 0x00BE): (models.DEATHADDER_V4_PRO.name, "cable"),
    (0x046D, 0x4093): (models.SUPERLIGHT.name, "receiver"),
    (0x046D, 0xC094): (models.SUPERLIGHT.name, "cable"),
}
KNOWN_MICE = tuple(KNOWN)
AUTO = "auto"
_ID = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$")


def identify(vendor, product):
    """(model name, connection) of a supported mouse, or (None, None)."""
    return KNOWN.get((vendor, product), (None, None))


def pretty_name(name):
    """Drop a doubled vendor prefix: "Endgame Gear Endgame Gear HS Dongle" -> "Endgame Gear HS Dongle"."""
    words = (name or "").split()
    for n in range(len(words) // 2, 0, -1):
        if words[:n] == words[n:2 * n]:
            return " ".join(words[n:])
    return " ".join(words)


def parse_choice(value):
    """"3367:1970" -> (0x3367, 0x1970); None for "auto" (or anything that is not an ID pair)."""
    m = _ID.match(value) if isinstance(value, str) else None
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


def format_choice(vendor, product):
    return f"{vendor:04x}:{product:04x}"
