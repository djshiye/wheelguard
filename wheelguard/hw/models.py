"""The mice Wheelguard can configure, and what each one offers. Plain data, no I/O.

The GUI builds the Mouse page from a Model: which cards and controls to show, the DPI range, the
polling rates and how many DPI stages there are. Every model's codec uses the same flat setting keys
where the meaning is the same (dpi_x0, stage_count, active_stage, polling, ...), so the page, the tray
and the panel extension treat all mice alike.
"""
from dataclasses import dataclass
from typing import Dict, FrozenSet, Tuple


@dataclass(frozen=True)
class Polling:
    code: int           # the raw value the codec stores under "polling"
    label: str          # "1000 Hz"
    sub: str = ""       # second line in the drop-down ("Battery saver")


@dataclass(frozen=True)
class Model:
    key: str                    # stable id, also used in backup file names
    name: str                   # shown on the Mouse page header
    brand: str
    dpi_min: int
    dpi_max: int
    dpi_step: int
    max_stages: int
    polling: Tuple[Polling, ...]
    features: FrozenSet[str]    # see FEATURES
    wireless_text: str = "Wireless via dongle"
    usb_vendor: int = 0         # USB vendor id (the product id comes from the connected device)
    # False: Wheelguard reads the mouse (settings, battery) but changes nothing until someone with this model
    # confirms the readings are right. A model of a family Wheelguard already drives starts this way.
    confirmed: bool = True
    art: str = ""               # which drawing MouseArt.qml uses ("" = the model's own, by key)
    idle_range: Tuple[int, int] = (1, 15)   # "idle": the sleep timer's range in minutes (0 = never sleeps)

    @property
    def art_key(self) -> str:
        return self.art or self.key

    @property
    def polling_codes(self) -> Tuple[int, ...]:
        return tuple(p.code for p in self.polling)


# Feature flags the Mouse page understands.
FEATURES = (
    "xy",               # separate X and Y DPI per stage
    "motion_sync", "angle_snapping", "ripple", "glass", "lod",
    "buttons",          # button actions + per-button multiclick filters (Endgame)
    "sleep_deep",       # Endgame: sleep + deep sleep in minutes, LED-off-on-lift
    "idle",             # Razer: one sleep timer, 1-15 minutes
    "low_battery",      # Razer: low-battery warning threshold
    "status_only",      # Glorious: the mouse can't report its DPI stages or polling rate back, so the page
                        # shows only what it can read (battery, firmware and, with "idle", the sleep timer)
)

ENDGAME_XM2W = Model(
    key="endgame-xm2w-4k-v2",
    name="Endgame Gear XM2w 4k v2",
    brand="Endgame Gear",
    dpi_min=50, dpi_max=30000, dpi_step=50,
    max_stages=4,
    polling=(Polling(0x40, "125 Hz", "Office mode"), Polling(0x80, "1000 Hz", "Battery saver"),
             Polling(0x08, "1000 Hz", "Full speed"), Polling(0x04, "2000 Hz", "Full speed"),
             Polling(0x02, "4000 Hz", "Full speed")),
    usb_vendor=0x3367,
    features=frozenset(("xy", "motion_sync", "angle_snapping", "ripple", "glass", "lod", "buttons",
                        "sleep_deep")),
)

# Logitech stores the report interval in milliseconds in the onboard profile.
SUPERLIGHT = Model(
    key="logitech-pro-x-superlight",
    name="Logitech G Pro X Superlight",
    brand="Logitech",
    dpi_min=100, dpi_max=25600, dpi_step=50,
    max_stages=5,
    polling=(Polling(8, "125 Hz"), Polling(4, "250 Hz"), Polling(2, "500 Hz"), Polling(1, "1000 Hz")),
    features=frozenset(),
    wireless_text="Wireless via LIGHTSPEED receiver",
    usb_vendor=0x046D,
)

# Razer HyperPolling codes (command 00:40), as OpenRazer's set_polling_rate2 sends them. 250 Hz (0x20)
# is listed too, so a mouse set to it in Synapse doesn't make the whole page read-only.
DEATHADDER_V4_PRO = Model(
    key="razer-deathadder-v4-pro",
    name="Razer DeathAdder V4 Pro",
    brand="Razer",
    dpi_min=100, dpi_max=45000, dpi_step=50,
    max_stages=5,
    polling=(Polling(0x40, "125 Hz"), Polling(0x20, "250 Hz"), Polling(0x10, "500 Hz"), Polling(0x08, "1000 Hz"),
             Polling(0x04, "2000 Hz"), Polling(0x02, "4000 Hz"), Polling(0x01, "8000 Hz")),
    features=frozenset(("xy", "idle", "low_battery")),
    wireless_text="Wireless via HyperSpeed dongle",
    usb_vendor=0x1532,
)



# ---------------------------------------------------------------- the Razer family
# Recent Razer mice speak the DeathAdder V4 Pro's protocol (razer.py) with a few per-model differences, taken
# from how the OpenRazer driver handles each product id: the polling-rate command ("hyper" = 00:40 like the
# DeathAdder V4 Pro, "classic" = 00:05 with 125/500/1000 Hz), how long the mouse needs before its reply is
# ready, and the DPI range. Only models that OpenRazer drives with transaction id 0x1F and the same DPI stage,
# battery, sleep and low-battery commands are listed. They start unconfirmed: read-only until checked.

@dataclass(frozen=True)
class RazerLink:
    pid: int
    model: str                  # Model.key
    wired: bool
    polling: str                # "hyper" | "classic"
    wait_s: float               # OpenRazer's wait before reading the reply
    rates: Tuple[int, ...]      # Hz this connection offers


_WAIT_NEW, _WAIT_VIPER = 0.031, 0.0599      # RAZER_NEW_MOUSE_RECEIVER / RAZER_VIPER_MOUSE_RECEIVER wait
_CLASSIC = (125, 500, 1000)
_HYPER = (125, 500, 1000, 2000, 4000, 8000)
_RAZER_RATES = {125: Polling(0x40, "125 Hz"), 250: Polling(0x20, "250 Hz"), 500: Polling(0x10, "500 Hz"),
                1000: Polling(0x08, "1000 Hz"), 2000: Polling(0x04, "2000 Hz"), 4000: Polling(0x02, "4000 Hz"),
                8000: Polling(0x01, "8000 Hz")}


def _razer(key, name, dpi_max, art, links, wireless_text="Wireless via HyperSpeed dongle"):
    rates = sorted({r for *_x, rs in links for r in rs})
    model = Model(key=key, name=name, brand="Razer", dpi_min=100, dpi_max=dpi_max, dpi_step=50, max_stages=5,
                  polling=tuple(_RAZER_RATES[r] for r in rates), features=frozenset(("xy", "idle", "low_battery")),
                  wireless_text=wireless_text, usb_vendor=0x1532, confirmed=False, art=art)
    return model, [RazerLink(pid, key, wired, polling, wait, rs) for pid, wired, polling, wait, rs in links]


_FAMILY = [
    _razer("razer-deathadder-v3-pro", "Razer DeathAdder V3 Pro", 35000, "razer-deathadder-v4-pro", [
        (0x00B6, True, "classic", _WAIT_NEW, _CLASSIC), (0x00B7, False, "classic", _WAIT_NEW, _CLASSIC),
        (0x00C2, True, "classic", _WAIT_NEW, _CLASSIC), (0x00C3, False, "classic", _WAIT_NEW, _CLASSIC)]),
    _razer("razer-deathadder-v3-hyperspeed", "Razer DeathAdder V3 HyperSpeed", 26000, "razer-deathadder-v4-pro", [
        (0x00C4, True, "classic", _WAIT_NEW, _CLASSIC), (0x00C5, False, "classic", _WAIT_NEW, _CLASSIC)]),
    _razer("razer-viper-v2-pro", "Razer Viper V2 Pro", 30000, "razer-viper", [
        (0x00A5, True, "classic", _WAIT_NEW, _CLASSIC), (0x00A6, False, "classic", _WAIT_NEW, _CLASSIC)]),
    _razer("razer-viper-v3-pro", "Razer Viper V3 Pro", 35000, "razer-viper", [
        (0x00C0, True, "classic", _WAIT_NEW, _CLASSIC), (0x00C1, False, "hyper", _WAIT_VIPER, _HYPER)]),
    _razer("razer-viper-v3-pro-se", "Razer Viper V3 Pro SE", 35000, "razer-viper", [
        (0x00DE, True, "classic", _WAIT_NEW, _CLASSIC), (0x00DF, False, "classic", _WAIT_VIPER, _CLASSIC)]),
    _razer("razer-viper-v3-hyperspeed", "Razer Viper V3 HyperSpeed", 30000, "razer-viper", [
        (0x00B8, False, "classic", _WAIT_VIPER, _CLASSIC)]),
    _razer("razer-viper-mini-se", "Razer Viper Mini Signature Edition", 30000, "razer-viper", [
        (0x009E, True, "hyper", _WAIT_VIPER, _CLASSIC), (0x009F, False, "hyper", _WAIT_VIPER, _HYPER)]),
    _razer("razer-basilisk-v3-pro", "Razer Basilisk V3 Pro", 30000, "razer-deathadder-v4-pro", [
        (0x00AA, True, "classic", _WAIT_NEW, _CLASSIC), (0x00AB, False, "classic", _WAIT_NEW, _CLASSIC)]),
    _razer("razer-basilisk-v3-pro-35k", "Razer Basilisk V3 Pro 35K", 35000, "razer-deathadder-v4-pro", [
        (0x00CC, True, "classic", _WAIT_NEW, _CLASSIC), (0x00CD, False, "classic", _WAIT_NEW, _CLASSIC),
        (0x00D6, True, "classic", _WAIT_NEW, _CLASSIC), (0x00D7, False, "classic", _WAIT_NEW, _CLASSIC)]),
    _razer("razer-cobra-pro", "Razer Cobra Pro", 30000, "razer-viper", [
        (0x00AF, True, "classic", _WAIT_NEW, _CLASSIC), (0x00B0, False, "classic", _WAIT_NEW, _CLASSIC)]),
]
RAZER_FAMILY: Tuple[Model, ...] = (DEATHADDER_V4_PRO,) + tuple(m for m, _l in _FAMILY)
RAZER_LINKS: Dict[int, RazerLink] = {
    0x00BE: RazerLink(0x00BE, DEATHADDER_V4_PRO.key, True, "hyper", _WAIT_NEW, _HYPER),
    0x00BF: RazerLink(0x00BF, DEATHADDER_V4_PRO.key, False, "hyper", _WAIT_NEW, _HYPER),
    **{link.pid: link for _m, links in _FAMILY for link in links},
}

# ---------------------------------------------------------------- the Logitech family
# Logitech G mice with HID++ 2.0 onboard profiles keep DPI stages and the report rate in the same profile
# layout as the Superlight (logitech.py checks the layout version the mouse reports and stays read-only on
# any other). Product ids from libratbag's device files (MIT): the id behind a LIGHTSPEED receiver, which
# hid-logitech-dj gives the mouse its own HID device for, and the id over the cable. Only mice with a 1000 Hz
# maximum are listed: faster ones store the rate elsewhere. They start unconfirmed: read-only until checked.

@dataclass(frozen=True)
class LogiLink:
    pid: int
    model: str                  # Model.key
    wired: bool


_LOGI_SYMMETRIC, _LOGI_ERGO = "logitech-pro-x-superlight", "logitech-ergo"


def _logi(key, name, dpi_min, dpi_max, art, receiver=(), cable=()):
    model = Model(key=key, name=name, brand="Logitech", dpi_min=dpi_min, dpi_max=dpi_max, dpi_step=50,
                  max_stages=5, polling=SUPERLIGHT.polling, features=frozenset(),
                  wireless_text="Wireless via LIGHTSPEED receiver" if receiver else "Wired",
                  usb_vendor=0x046D, confirmed=False, art=art)
    return model, [LogiLink(p, key, False) for p in receiver] + [LogiLink(p, key, True) for p in cable]


_LOGI_FAMILY = [
    _logi("logitech-g-pro-wireless", "Logitech G Pro Wireless", 100, 25600, _LOGI_SYMMETRIC, (0x4079,), (0xC088,)),
    _logi("logitech-g-pro-hero", "Logitech G Pro Hero", 100, 25600, _LOGI_SYMMETRIC, (), (0xC08C,)),
    _logi("logitech-g305", "Logitech G305", 200, 12000, _LOGI_SYMMETRIC, (0x4074,)),
    _logi("logitech-g203", "Logitech G102 / G203", 200, 8000, _LOGI_SYMMETRIC, (), (0xC084, 0xC092, 0xC09D)),
    _logi("logitech-g303-shroud", "Logitech G303 Shroud Edition", 100, 25600, _LOGI_ERGO, (), (0xC097,)),
    _logi("logitech-g403-hero", "Logitech G403 Hero", 100, 25600, _LOGI_ERGO, (), (0xC08F,)),
    _logi("logitech-g502-hero", "Logitech G502 Hero", 100, 25600, _LOGI_ERGO, (), (0xC08B,)),
    _logi("logitech-g502-lightspeed", "Logitech G502 Lightspeed", 100, 25600, _LOGI_ERGO, (0x407F,), (0xC08D,)),
    _logi("logitech-g502-x", "Logitech G502 X", 100, 25600, _LOGI_ERGO, (), (0xC099,)),
    _logi("logitech-g502-x-lightspeed", "Logitech G502 X Lightspeed", 100, 25600, _LOGI_ERGO, (0x409F,), (0xC098,)),
    _logi("logitech-g502-x-plus", "Logitech G502 X Plus", 100, 25600, _LOGI_ERGO, (0x4099,), (0xC095,)),
    _logi("logitech-g604", "Logitech G604", 100, 25600, _LOGI_ERGO, (0x4085,)),
    _logi("logitech-g703-hero", "Logitech G703 Hero", 100, 25600, _LOGI_ERGO, (0x4086,), (0xC090,)),
    _logi("logitech-g705", "Logitech G705", 100, 8200, _LOGI_ERGO, (0x409D,), (0xC096,)),
    _logi("logitech-g903-hero", "Logitech G903 Hero", 100, 25600, _LOGI_SYMMETRIC, (0x4087,), (0xC091,)),
]
LOGI_FAMILY: Tuple[Model, ...] = (SUPERLIGHT,) + tuple(m for m, _l in _LOGI_FAMILY)
LOGI_LINKS: Dict[int, LogiLink] = {
    0x4093: LogiLink(0x4093, SUPERLIGHT.key, False),
    0xC094: LogiLink(0xC094, SUPERLIGHT.key, True),
    **{link.pid: link for _m, links in _LOGI_FAMILY for link in links},
}

# ---------------------------------------------------------------- the Glorious mice
# Two protocols, decoded from the Glorious CORE app (docs/glorious-mouse-protocol.md). "v3": the Model O3 / D3
# (vendor 3794), one command per setting on report 3. "v2pro": the Sinowealth wireless mice (vendor 258a) on
# report 0. Neither protocol has a command that reads the DPI stages or the polling rate back, so Wheelguard
# shows what it can read (battery, charging, firmware; the O3 / D3 sleep timer too) and changes nothing
# until someone with the mouse confirms the readings. Bootloader ids and the ids the app gives to two
# products (258a:2014, 3794:a100) are left out on purpose, and so is Bluetooth.

@dataclass(frozen=True)
class GloriousLink:
    pid: int
    model: str                  # Model.key
    wired: bool
    protocol: str               # "v3" | "v2pro"
    label: str = ""             # the name before the mouse says which it is (the O3 and D3 share a dongle)


GLORIOUS_V3_VENDOR, GLORIOUS_V2PRO_VENDOR = 0x3794, 0x258A
_GLORIOUS_O, _GLORIOUS_D = "endgame-xm2w-4k-v2", "razer-deathadder-v4-pro"     # symmetric / right-handed art
_G_RATES = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01, 2000: 0x20, 4000: 0x40, 8000: 0x80}    # v2pro codes


def _glorious_v3(key, name, art, wireless):
    rates = (125, 250, 500, 1000) + ((2000, 4000, 8000) if wireless else ())
    return Model(key=key, name=name, brand="Glorious", dpi_min=50, dpi_max=30000, dpi_step=50, max_stages=6,
                 polling=tuple(Polling(hz, f"{hz} Hz") for hz in rates),        # the command takes Hz
                 features=frozenset(("status_only", "idle")),
                 wireless_text="Wireless via 2.4 GHz dongle" if wireless else "Wired",
                 usb_vendor=GLORIOUS_V3_VENDOR, confirmed=False, art=art, idle_range=(0, 60))


def _glorious_v2pro(key, name, dpi_max, art, max_hz=1000):
    return Model(key=key, name=name, brand="Glorious", dpi_min=100, dpi_max=dpi_max, dpi_step=50, max_stages=6,
                 polling=tuple(Polling(code, f"{hz} Hz") for hz, code in _G_RATES.items() if hz <= max_hz),
                 features=frozenset(("status_only",)), wireless_text="Wireless via 2.4 GHz dongle",
                 usb_vendor=GLORIOUS_V2PRO_VENDOR, confirmed=False, art=art)


GLORIOUS_O3 = _glorious_v3("glorious-model-o3-wireless", "Glorious Model O3 Wireless", _GLORIOUS_O, True)
GLORIOUS_D3 = _glorious_v3("glorious-model-d3-wireless", "Glorious Model D3 Wireless", _GLORIOUS_D, True)
GLORIOUS_O3_DONGLE = 0xA300         # shared by the O3 and D3 Wireless: the mouse's name (command 0x99) decides
_GLORIOUS_V3 = [     # (model, [(pid, wired, label)])
    (GLORIOUS_O3, [(0xA312, True, ""), (GLORIOUS_O3_DONGLE, False, "Glorious Model O3 / D3 Wireless")]),
    (GLORIOUS_D3, [(0xA322, True, "")]),
    (_glorious_v3("glorious-model-o3", "Glorious Model O3", _GLORIOUS_O, False), [(0xA310, True, "")]),
    (_glorious_v3("glorious-model-d3", "Glorious Model D3", _GLORIOUS_D, False), [(0xA320, True, "")]),
]
_GLORIOUS_V2PRO = [     # (model, cable pid, dongle pid)
    (_glorious_v2pro("glorious-model-o-wireless", "Glorious Model O Wireless", 19000, _GLORIOUS_O), 0x2011, 0x2022),
    (_glorious_v2pro("glorious-model-o-minus-wireless", "Glorious Model O- Wireless", 19000, _GLORIOUS_O), 0x2013, 0x2024),
    (_glorious_v2pro("glorious-model-o-pro-wireless", "Glorious Model O Pro Wireless", 19000, _GLORIOUS_O), 0x2015, 0x2027),
    (_glorious_v2pro("glorious-model-d-wireless", "Glorious Model D Wireless", 19000, _GLORIOUS_D), 0x2012, 0x2023),
    # the cable id 2014 is also given to the "Model D2 Minus Wired" in the app: only the dongle is matched
    (_glorious_v2pro("glorious-model-d-minus-wireless", "Glorious Model D- Wireless", 19000, _GLORIOUS_D), None, 0x2025),
    (_glorious_v2pro("glorious-model-d-pro-wireless", "Glorious Model D Pro Wireless", 19000, _GLORIOUS_D), 0x2017, 0x2029),
    (_glorious_v2pro("glorious-series-one-pro-wireless", "Glorious Series One Pro Wireless", 19000, _GLORIOUS_O),
     0x2018, 0x2031),
    (_glorious_v2pro("glorious-model-o2-pro-wireless", "Glorious Model O2 Pro Wireless", 26000, _GLORIOUS_O), 0x2019, 0x2033),
    (_glorious_v2pro("glorious-model-d2-pro-wireless", "Glorious Model D2 Pro Wireless", 26000, _GLORIOUS_D), 0x201A, 0x2034),
    (_glorious_v2pro("glorious-model-o2-pro-4k8k", "Glorious Model O2 Pro 4K/8K Wireless", 26000, _GLORIOUS_O, 8000),
     0x201B, 0x2035),
    (_glorious_v2pro("glorious-model-d2-pro-4k8k", "Glorious Model D2 Pro 4K/8K Wireless", 26000, _GLORIOUS_D, 8000),
     0x201C, 0x2036),
    (_glorious_v2pro("glorious-series-2-pro-wireless", "Glorious Series 2 Pro Wireless", 26000, _GLORIOUS_O, 8000),
     0x201D, 0x2039),
]


def _glorious_links():
    links: Dict[int, GloriousLink] = {}
    for model, entries in _GLORIOUS_V3:
        for pid, wired, label in entries:
            links[pid] = GloriousLink(pid, model.key, wired, "v3", label)
    for model, cable, dongle in _GLORIOUS_V2PRO:
        for pid, wired in ((cable, True), (dongle, False)):
            if pid is not None:
                links[pid] = GloriousLink(pid, model.key, wired, "v2pro")
    return links


GLORIOUS_FAMILY: Tuple[Model, ...] = tuple(m for m, _l in _GLORIOUS_V3) + tuple(m for m, *_p in _GLORIOUS_V2PRO)
GLORIOUS_LINKS: Dict[int, GloriousLink] = _glorious_links()


def glorious_vendor(link: GloriousLink) -> int:
    return GLORIOUS_V3_VENDOR if link.protocol == "v3" else GLORIOUS_V2PRO_VENDOR


MODELS: Dict[str, Model] = {m.key: m for m in (ENDGAME_XM2W,) + LOGI_FAMILY + RAZER_FAMILY + GLORIOUS_FAMILY}
DEFAULT = ENDGAME_XM2W


def by_key(key: str) -> Model:
    return MODELS.get(key, DEFAULT)


def supported_names() -> Tuple[str, ...]:
    """Every model Wheelguard recognises, confirmed ones first."""
    return tuple(m.name for m in MODELS.values() if m.confirmed) + tuple(
        m.name for m in MODELS.values() if not m.confirmed)


def confirmed_names() -> Tuple[str, ...]:
    """The models whose settings Wheelguard changes (the short list for messages)."""
    return tuple(m.name for m in MODELS.values() if m.confirmed)


def supported_summary(joiner: str = "and") -> str:
    """"the A, B and C, plus N more Logitech and Razer mice (read-only for now)" for messages: the full
    list is too long."""
    names = confirmed_names()
    text = ", ".join(names[:-1]) + f" {joiner} " + names[-1] if len(names) > 1 else names[0]
    more = [m for m in MODELS.values() if not m.confirmed]
    brands = sorted({m.brand for m in more})
    brand_text = ", ".join(brands[:-1]) + " and " + brands[-1] if len(brands) > 1 else "".join(brands)
    return f"the {text}" + (f", plus {len(more)} more {brand_text} mice (read-only for now)" if more else "")
