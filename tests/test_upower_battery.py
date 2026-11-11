"""Battery for any wireless mouse: matching UPower's batteries to the protected mouse, and what the panel,
tray and alerts then see (wheelguard/gui/upower.py, mouseview.py)."""
import os
import tempfile
import unittest
from pathlib import Path

from wheelguard.gui import upower
from wheelguard.gui.upower import Reading

try:
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    from wheelguard.gui import traymodel
    from wheelguard.gui.mouseview import MouseView
except ImportError:             # PySide6 isn't installed
    MouseView = None


def fake_sysfs(root, supplies=(), inputs=()):
    """supplies: (power_supply name, HID device dir name); inputs: (eventN, vendor, product, uniq)."""
    for name, hid in supplies:
        hid_dir = Path(root) / "devices" / "pci0000:00" / "usb1" / hid
        (hid_dir / "power_supply" / name).mkdir(parents=True)
        link = Path(root) / "class" / "power_supply" / name
        link.mkdir(parents=True)
        os.symlink(hid_dir, link / "device")
    for ev, vendor, product, uniq in inputs:
        d = Path(root) / "class" / "input" / ev / "device"
        (d / "id").mkdir(parents=True)
        (d / "id" / "vendor").write_text(f"{vendor:04x}\n")
        (d / "id" / "product").write_text(f"{product:04x}\n")
        (d / "uniq").write_text(uniq + "\n")


class Matching(unittest.TestCase):
    def test_kernel_battery_by_hid_ids(self):
        with tempfile.TemporaryDirectory() as root:
            fake_sysfs(root, supplies=[("hidpp_battery_0", "0003:046D:4099.0005")])
            self.assertEqual(upower.hid_ids_of_supply("hidpp_battery_0", root), (0x046D, 0x4099))
            self.assertTrue(upower.belongs_to("hidpp_battery_0", 0x046D, 0x4099, [], root))
            self.assertFalse(upower.belongs_to("hidpp_battery_0", 0x046D, 0x4093, [], root))
            self.assertIsNone(upower.hid_ids_of_supply("BAT0", root))
            for bad in ("", "..", "../x", "a/b"):
                self.assertIsNone(upower.hid_ids_of_supply(bad, root))

    def test_bluetooth_battery_by_address(self):
        native = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_0F"
        self.assertEqual(upower.bluetooth_address(native), "aa:bb:cc:dd:ee:0f")
        with tempfile.TemporaryDirectory() as root:
            fake_sysfs(root, inputs=[("event5", 0x046D, 0xB034, "aa:bb:cc:dd:ee:0f"),
                                     ("event6", 0x1234, 0x5678, "11:22:33:44:55:66"),
                                     ("event7", 0x046D, 0xB034, "")])
            addrs = upower.mouse_addresses(0x046D, 0xB034, root)
            self.assertEqual(addrs, ["aa:bb:cc:dd:ee:0f"])
            self.assertTrue(upower.belongs_to(native, 0x046D, 0xB034, addrs, root))
            self.assertFalse(upower.belongs_to("/org/bluez/hci0/dev_11_22_33_44_55_66", 0x046D, 0xB034, addrs, root))

    def test_reading(self):
        self.assertEqual(upower.to_reading(42.0, 2), Reading(42, False))
        self.assertEqual(upower.to_reading(80.4, 1), Reading(80, True))
        self.assertEqual(upower.to_reading(100.0, 4), Reading(100, False))
        self.assertIsNone(upower.to_reading(0.0, 0))            # unknown: no level reported yet
        self.assertIsNone(upower.to_reading(50.0, 2, present=False))
        self.assertIsNone(upower.to_reading(None, 2))
        self.assertIsNone(upower.to_reading(150.0, 2))

    def test_child_nodes(self):
        xml = ('<node name="/org/freedesktop/UPower/devices"><node name="battery_BAT0"/>'
               '<node name="mouse_hidpp_battery_0"/><node name="../evil"/><node/></node>')
        self.assertEqual(upower.child_nodes(xml), ["battery_BAT0", "mouse_hidpp_battery_0"])
        self.assertEqual(upower.child_nodes("not xml"), [])

    def test_find_battery_reads_only_what_it_needs(self):
        props = {"battery_BAT0": {"NativePath": "BAT0", "Percentage": 90.0, "State": 2},
                 "mouse_hidpp_battery_0": {"NativePath": "hidpp_battery_0", "Percentage": 15.0, "State": 2,
                                           "IsPresent": True}}
        asked = []

        def get(node, prop):
            asked.append((node, prop))
            return props[node].get(prop)
        with tempfile.TemporaryDirectory() as root:
            fake_sysfs(root, supplies=[("hidpp_battery_0", "0003:046D:4099.0005")])
            got = upower.find_battery(list(props), get, 0x046D, 0x4099, [], root)
            self.assertEqual(got, Reading(15, False))
            self.assertEqual([p for n, p in asked if n == "battery_BAT0"], ["NativePath"])
            self.assertIsNone(upower.find_battery(list(props), get, 0x1532, 0x00BF, [], root))


if MouseView is not None:
    class FakeMouse(QObject):
        trayChanged = Signal()
        infoChanged = Signal()
        stateChanged = Signal()
        online, battery, charging, stageLabels, activeStage, stageReason = False, -1, False, [], -1, ""
        stage_calls = []

        def setActiveStage(self, i):
            self.stage_calls.append(i)

        def refreshBattery(self):
            pass

    class FakeDaemon(QObject):
        stateChanged = Signal()
        state, ident = "running", "046d:4099"
        enabled, presetName, dropped = True, "Balanced", 0
        clickLock, clickLockMs, clickLockActive, clickLockMode = False, 5000, False, "repeat"


@unittest.skipIf(MouseView is None, "PySide6 not available")
class View(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self, reading):
        self.asked = []

        def battery(v, p):
            self.asked.append((v, p))
            return reading
        self.mouse, self.daemon = FakeMouse(), FakeDaemon()
        return MouseView(self.mouse, self.daemon, battery=battery, poll_ms=3600000)

    def test_unsupported_mouse_shows_its_upower_battery_only(self):
        view = self.make(Reading(17, False))
        self.assertEqual(self.asked, [(0x046D, 0x4099)])
        s = traymodel.state_dict(traymodel.snapshot(self.daemon, view))
        self.assertEqual((s["Online"], s["Battery"], s["BatteryLow"], s["StageLabels"]), (True, 17, True, []))
        view.setActiveStage(2)
        self.assertEqual(self.mouse.stage_calls, [])

    def test_own_driver_wins(self):
        view = self.make(Reading(17, False))
        self.mouse.online, self.mouse.battery, self.mouse.stageLabels = True, 64, ["800", "1600"]
        self.mouse.trayChanged.emit()
        self.asked.clear()
        view.poll()
        self.assertEqual(self.asked, [])                    # UPower isn't asked while the driver answers
        self.assertEqual((view.battery, view.stageLabels), (64, ["800", "1600"]))

    def test_nothing_known(self):
        view = self.make(None)
        self.assertFalse(view.online)
        self.daemon.state = "waiting"
        self.asked.clear()
        view.poll()
        self.assertEqual(self.asked, [])                    # nothing is being protected: nothing to ask

    def test_a_failing_service_is_ignored(self):
        def boom(_v, _p):
            raise RuntimeError("bus gone")
        view = MouseView(FakeMouse(), FakeDaemon(), battery=boom, poll_ms=3600000)
        self.assertFalse(view.online)


if __name__ == "__main__":
    unittest.main()
