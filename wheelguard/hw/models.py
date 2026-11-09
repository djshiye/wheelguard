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
    sub: str = ""       # second line in the drop-down ("Power saving")


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
    polling=(Polling(0x40, "125 Hz", "Office mode"), Polling(0x80, "1000 Hz", "Power saving"),
             Polling(0x08, "1000 Hz", "No saving"), Polling(0x04, "2000 Hz", "No saving"),
             Polling(0x02, "4000 Hz", "No saving")),
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
)

# Razer HyperPolling codes (command 00:40). 250 Hz exists in the protocol but the read side of the
# reference driver can't report it for this mouse, so it isn't offered.
DEATHADDER_V4_PRO = Model(
    key="razer-deathadder-v4-pro",
    name="Razer DeathAdder V4 Pro",
    brand="Razer",
    dpi_min=100, dpi_max=45000, dpi_step=50,
    max_stages=5,
    polling=(Polling(0x40, "125 Hz"), Polling(0x10, "500 Hz"), Polling(0x08, "1000 Hz"),
             Polling(0x04, "2000 Hz"), Polling(0x02, "4000 Hz"), Polling(0x01, "8000 Hz")),
    features=frozenset(("xy", "idle", "low_battery")),
    wireless_text="Wireless via HyperSpeed dongle",
)

MODELS: Dict[str, Model] = {m.key: m for m in (ENDGAME_XM2W, SUPERLIGHT, DEATHADDER_V4_PRO)}
DEFAULT = ENDGAME_XM2W


def by_key(key: str) -> Model:
    return MODELS.get(key, DEFAULT)


def supported_names() -> Tuple[str, ...]:
    return tuple(m.name for m in MODELS.values())
