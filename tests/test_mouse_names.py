import tempfile
import unittest
from pathlib import Path

from wheelguard import daemon, mice
from wheelguard.control import Controller
from wheelguard.gui import protocol
from wheelguard.hw import models


class Names(unittest.TestCase):
    def test_every_known_mouse_has_a_supported_model_name_and_connection(self):
        names = set(models.supported_names())
        for ident in mice.KNOWN_MICE:
            model, connection = mice.identify(*ident)
            self.assertIn(model, names)
            self.assertIn(connection, ("dongle", "receiver", "cable"))
        self.assertEqual(daemon.KNOWN_MICE, mice.KNOWN_MICE)

    def test_each_connection(self):
        self.assertEqual(mice.identify(0x3367, 0x1970), ("Endgame Gear XM2w 4k v2", "dongle"))
        self.assertEqual(mice.identify(0x3367, 0x1982), ("Endgame Gear XM2w 4k v2", "cable"))
        self.assertEqual(mice.identify(0x1532, 0x00BF), ("Razer DeathAdder V4 Pro", "dongle"))
        self.assertEqual(mice.identify(0x046D, 0x4093), ("Logitech G Pro X Superlight", "receiver"))
        self.assertEqual(mice.identify(0x046D, 0xC094), ("Logitech G Pro X Superlight", "cable"))
        self.assertEqual(mice.identify(0x1234, 0x5678), (None, None))

    def test_the_virtual_device_name_is_unchanged(self):
        self.assertEqual(daemon.UI_NAME, "wheelguard Endgame Gear")


class StatusFields(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ctl = Controller(config_path=Path(tmp.name) / "c.json", publish=lambda m: None)

    def test_supported_mouse(self):
        self.ctl.set_device(True, "Endgame Gear Endgame Gear HS Dongle", None, ident=(0x3367, 0x1970))
        st = self.ctl.status()
        self.assertEqual((st["model"], st["connection"]), ("Endgame Gear XM2w 4k v2", "dongle"))
        self.assertEqual(st["device"], "Endgame Gear Endgame Gear HS Dongle")      # still reported

    def test_unknown_mouse_and_no_mouse(self):
        self.ctl.set_device(True, "Acme Mouse", None, ident=(0x1111, 0x2222))
        st = self.ctl.status()
        self.assertEqual((st["model"], st["connection"], st["device"]), (None, None, "Acme Mouse"))
        self.ctl.set_device(True, "Acme Mouse", None)
        self.assertIsNone(self.ctl.status()["model"])
        self.ctl.set_device(False, None, "mouse not found")
        st = self.ctl.status()
        self.assertEqual((st["model"], st["connection"]), (None, None))

    def test_a_status_event_goes_out_when_only_the_model_changes(self):
        events = []
        self.ctl.publish = events.append
        self.ctl.set_device(True, "X", None, ident=(0x3367, 0x1970))
        self.ctl.set_device(True, "X", None, ident=(0x3367, 0x1982))
        self.assertEqual([e["connection"] for e in events], ["dongle", "cable"])


class GuiLabel(unittest.TestCase):
    def status(self, **kw):
        return dict({"connected": True, "device": "Endgame Gear Endgame Gear HS Dongle"}, **kw)

    def test_friendly_label(self):
        st = self.status(model="Endgame Gear XM2w 4k v2", connection="dongle")
        ds = protocol.device_state(st)
        self.assertEqual(ds.label, "Endgame Gear XM2w 4k v2 · dongle")
        self.assertEqual(ds.device, "Endgame Gear HS Dongle")
        self.assertEqual(ds.state, "running")

    def test_model_without_connection(self):
        self.assertEqual(protocol.device_label(self.status(model="Razer DeathAdder V4 Pro")),
                         "Razer DeathAdder V4 Pro")

    def test_fallbacks(self):
        self.assertEqual(protocol.device_label(self.status()), "Endgame Gear HS Dongle")      # old daemon
        self.assertEqual(protocol.device_label(self.status(model=None, connection=None, device="Acme Mouse")),
                         "Acme Mouse")
        self.assertEqual(protocol.device_label(self.status(model="", connection=5)), "Endgame Gear HS Dongle")
        self.assertEqual(protocol.device_label({}), "")


if __name__ == "__main__":
    unittest.main()
