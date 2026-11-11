"""Battery for any wireless mouse, from UPower: the level Linux already knows for the mouse being protected.

Wheelguard's own drivers read the battery of the mice it supports. For every other mouse this asks UPower
(the system's power service, the one GNOME's battery menu uses), read-only, over the system D-Bus: no device
access, no extra permissions, nothing is ever written. It covers mice whose kernel driver reports a battery
(most Logitech mice, wired through a receiver or over Bluetooth) and Bluetooth mice that report one to BlueZ.

UPower lists every battery on the computer, so the one that belongs to the protected mouse is matched by its
identity, never guessed: a kernel battery by the vendor and product ids of the HID device it hangs off, a
Bluetooth battery by the mouse's Bluetooth address (the input device's "uniq"). No match, no battery.
"""
import glob
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Tuple

SERVICE = "org.freedesktop.UPower"
DEVICES_PATH = "/org/freedesktop/UPower/devices"
DEVICE_IFACE = "org.freedesktop.UPower.Device"
TIMEOUT_MS = 300                # UPower answers in well under a millisecond; never stall the app on it
STATE_CHARGING = 1             # UPower's State: 1 charging, 2 discharging, 4 fully charged, 0 unknown
_HID_ID = re.compile(r"^[0-9A-Fa-f]{4}:([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})\.[0-9A-Fa-f]+$")
_BLUEZ = re.compile(r"/dev_([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})$")
_NODE = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Reading:
    percent: int                # 0-100
    charging: bool


def hid_ids_of_supply(native: str, sysfs: str = "/sys") -> Optional[Tuple[int, int]]:
    """(vendor, product) of the HID device a kernel battery belongs to ("hidpp_battery_0" ->
    /sys/class/power_supply/hidpp_battery_0/device -> .../0003:046D:4093.0005), or None."""
    if not native or "/" in native or native in (".", ".."):
        return None
    try:
        path = os.path.realpath(os.path.join(sysfs, "class", "power_supply", native, "device"))
    except (OSError, ValueError):
        return None
    for _ in range(3):              # the HID device itself, or close above it
        m = _HID_ID.match(os.path.basename(path))
        if m:
            return int(m.group(1), 16), int(m.group(2), 16)
        path = os.path.dirname(path)
    return None


def bluetooth_address(native: str) -> str:
    """"aa:bb:cc:dd:ee:ff" from a BlueZ device path ("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"), else ""."""
    m = _BLUEZ.search(native or "")
    return m.group(1).replace("_", ":").lower() if m else ""


def mouse_addresses(vendor: int, product: int, sysfs: str = "/sys") -> List[str]:
    """The "uniq" (a Bluetooth address, for a Bluetooth mouse) of the input devices with these ids."""
    out = []
    for dev in sorted(glob.glob(os.path.join(sysfs, "class", "input", "event*", "device"))):
        try:
            with open(os.path.join(dev, "id", "vendor")) as f:
                v = int(f.read().strip(), 16)
            with open(os.path.join(dev, "id", "product")) as f:
                p = int(f.read().strip(), 16)
            if (v, p) != (vendor, product):
                continue
            with open(os.path.join(dev, "uniq")) as f:
                u = f.read().strip().lower()
        except (OSError, ValueError):
            continue
        if re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", u) and u not in out:
            out.append(u)
    return out


def belongs_to(native: str, vendor: int, product: int, addresses: Iterable[str], sysfs: str = "/sys") -> bool:
    """True when the UPower device with this NativePath is the battery of the mouse (vendor, product)."""
    addr = bluetooth_address(native)
    if addr:
        return addr in set(addresses)
    return hid_ids_of_supply(native, sysfs) == (vendor, product)


def to_reading(percentage, state, present=True) -> Optional[Reading]:
    """UPower's Percentage/State as a Reading; None when it doesn't know the level."""
    try:
        pct = float(percentage)
        st = int(state)
    except (TypeError, ValueError):
        return None
    if not present or st == 0 or not 0 <= pct <= 100:      # state 0 = unknown: no level reported yet
        return None
    return Reading(int(round(pct)), st == STATE_CHARGING)


def child_nodes(introspection_xml: str) -> List[str]:
    try:
        root = ET.fromstring(introspection_xml)
    except ET.ParseError:
        return []
    return [n.get("name") for n in root.findall("node") if _NODE.match(n.get("name") or "")]


def find_battery(nodes: List[str], get: Callable[[str, str], object], vendor: int, product: int,
                 addresses: Iterable[str], sysfs: str = "/sys") -> Optional[Reading]:
    """The protected mouse's battery among UPower's devices. `get(node, property)` reads one property (None on
    failure); only NativePath is read for the others."""
    addresses = list(addresses)
    for node in nodes:
        native = get(node, "NativePath")
        if isinstance(native, str) and belongs_to(native, vendor, product, addresses, sysfs):
            return to_reading(get(node, "Percentage"), get(node, "State"), get(node, "IsPresent") is not False)
    return None


class UPowerClient:
    """The D-Bus side (QtDBus, system bus). Plain strings and variants only: no QDBusArgument decoding."""

    def __init__(self, bus=None):
        from PySide6.QtDBus import QDBusConnection
        self._bus = bus if bus is not None else QDBusConnection.systemBus()

    def _call(self, path, iface, member, args):
        from PySide6.QtDBus import QDBus, QDBusMessage
        msg = QDBusMessage.createMethodCall(SERVICE, path, iface, member)
        msg.setArguments(args)
        reply = self._bus.call(msg, QDBus.CallMode.Block, TIMEOUT_MS)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return None
        return reply.arguments()[0]

    def nodes(self) -> List[str]:
        if not self._bus.isConnected():
            return []
        xml = self._call(DEVICES_PATH, "org.freedesktop.DBus.Introspectable", "Introspect", [])
        return child_nodes(xml) if isinstance(xml, str) else []

    def get(self, node: str, prop: str):
        v = self._call(f"{DEVICES_PATH}/{node}", "org.freedesktop.DBus.Properties", "Get", [DEVICE_IFACE, prop])
        return v.variant() if hasattr(v, "variant") else v

    def battery(self, vendor: int, product: int, sysfs: str = "/sys") -> Optional[Reading]:
        return find_battery(self.nodes(), self.get, vendor, product, mouse_addresses(vendor, product, sysfs), sysfs)
