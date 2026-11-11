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
    # the rest of the Razer family (models.RAZER_LINKS)
    **{(0x1532, pid): (models.MODELS[link.model].name, "cable" if link.wired else "dongle")
       for pid, link in models.RAZER_LINKS.items() if pid not in (0x00BE, 0x00BF)},
    # the rest of the Logitech family (models.LOGI_LINKS)
    **{(0x046D, pid): (models.MODELS[link.model].name, "cable" if link.wired else "receiver")
       for pid, link in models.LOGI_LINKS.items() if pid not in (0x4093, 0xC094)},
}
KNOWN_MICE = tuple(KNOWN)
AUTO = "auto"
_ID = re.compile(r"^([0-9a-fA-F]{4}):([0-9a-fA-F]{4})$")

# Mice the user allowed with "Allow this mouse": the root helper (build-aux/wheelguard-allow-mouse) keeps one
# rule line per mouse here. The file is world-readable; only its vendor/product pairs are taken from it.
ALLOWED_RULES = "/etc/udev/rules.d/71-wheelguard-allowed-mice.rules"
_RULE = re.compile(r'ATTRS\{id/vendor\}=="([0-9a-f]{4})", ATTRS\{id/product\}=="([0-9a-f]{4})"')
MAX_ALLOWED = 16                # the helper never writes more


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
    m = _ID.fullmatch(value) if isinstance(value, str) else None
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else None


def format_choice(vendor, product):
    return f"{vendor:04x}:{product:04x}"


def allowed_ids(path=ALLOWED_RULES):
    """The "vvvv:pppp" ids of the mice the user allowed, in file order ([] when there is no file)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(65536)
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        m = _RULE.search(line) if not line.lstrip().startswith("#") else None
        if m and f"{m.group(1)}:{m.group(2)}" not in out:
            out.append(f"{m.group(1)}:{m.group(2)}")
    return out[:MAX_ALLOWED]


def allowed_mice(path=ALLOWED_RULES):
    """allowed_ids() as (vendor, product) pairs, for the daemon's Automatic search."""
    return [parse_choice(i) for i in allowed_ids(path)]
