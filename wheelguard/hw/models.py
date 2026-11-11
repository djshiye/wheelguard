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

MODELS: Dict[str, Model] = {m.key: m for m in (ENDGAME_XM2W, SUPERLIGHT) + RAZER_FAMILY}
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
    """"the A, B and C, and N more Razer mice (read-only for now)" for messages: the full list is too long."""
    names = confirmed_names()
    text = ", ".join(names[:-1]) + f" {joiner} " + names[-1] if len(names) > 1 else names[0]
    more = sum(1 for m in MODELS.values() if not m.confirmed)
    return f"the {text}" + (f", plus {more} more Razer mice (read-only for now)" if more else "")
