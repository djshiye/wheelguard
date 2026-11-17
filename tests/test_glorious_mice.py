"""The Glorious mice: every listed model is recognised everywhere it has to be, only the vendor-only settings
interface is found and granted, both transports send nothing that isn't on their allow-list, and every
model stays read-only until it is confirmed on real hardware."""
import dataclasses
import importlib.machinery
import importlib.util
import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock as umock

from wheelguard import mice
from wheelguard.hw import glorious, hidraw, mock, models, session
from wheelguard.hw.endgame import Rejected, StatusError, WriteBlocked

ROOT = Path(__file__).resolve().parent.parent
RULES = (ROOT / "build-aux" / "70-wheelguard.rules").read_text()

# Descriptors like the mice's interfaces (application collections only matter here).
V3_SETTINGS = bytes.fromhex("0600ff0901a101" "8503" "150026ff00" "7508" "953f" "0901" "b102" "c0")
V3_EVENTS = bytes.fromhex("0600ff0a00ffa101" "8504" "7508" "953f" "0901" "8102" "c0")
V2_SETTINGS = bytes.fromhex("06ffff0900a101" "150026ff00" "7508" "9540" "0900" "b102" "c0")
MOUSE = bytes.fromhex("05010902a1010901a100" "0509190129051500250175019505" "8102" "c0c0")
CONSUMER = bytes.fromhex("050c0901a101" "8502" "19002a3c02" "750c9501" "8100" "c0")
KEYBOARD = bytes.fromhex("05010906a101" "0507" "19e029e7" "75019508" "8102" "c0")
BOOTLOADERS = {(0x3794, p) for p in (0xA301, 0xA311, 0xA313, 0xA321, 0xA323, 0xA100)}
AMBIGUOUS = {(0x258A, 0x2014), (0x3794, 0xA100), (0x3794, 0xA101), (0x320F, 0x8888)}


def load_helper():
    path = str(ROOT / "build-aux" / "wheelguard-glorious-check")
    loader = importlib.machinery.SourceFileLoader("wheelguard_glorious_check", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def ids():
    return {(models.glorious_vendor(link), pid) for pid, link in models.GLORIOUS_LINKS.items()}


class Table(unittest.TestCase):
    def test_every_link_is_known_found_and_granted(self):
        for pid, link in models.GLORIOUS_LINKS.items():
            with self.subTest(pid=f"{pid:04x}"):
                model, vid = models.MODELS[link.model], models.glorious_vendor(link)
                self.assertEqual(model.brand, "Glorious")
                self.assertEqual(model.usb_vendor, vid)
                self.assertEqual(mice.identify(vid, pid), (link.label or model.name, "cable" if link.wired else "dongle"))
                self.assertEqual(hidraw.Candidate("", vid, pid, "", "glorious-" + link.protocol).wired, link.wired)
                self.assertIn(f'KERNEL=="event*", ATTRS{{idVendor}}=="{vid:04x}", ATTRS{{idProduct}}=="{pid:04x}", '
                              'ENV{ID_INPUT_MOUSE}=="1", ENV{ID_INPUT_KEYBOARD}!="1", TAG+="uaccess"', RULES)

    def test_hidraw_nodes_go_through_the_helper_only(self):
        granted = set()
        for line in RULES.splitlines():
            if "glorious-check" not in line or line.startswith("#"):
                continue
            self.assertNotIn("uaccess", line)
            self.assertTrue(line.startswith('SUBSYSTEM=="hidraw", '))
            vid = int(re.search(r'idVendor\}=="([0-9a-f]{4})"', line).group(1), 16)
            pids = re.search(r'idProduct\}=="([0-9a-f|]+)"', line).group(1).split("|")
            granted |= {(vid, int(p, 16)) for p in pids}
        self.assertEqual(granted, ids())
        self.assertIn('SUBSYSTEM=="hidraw", ENV{WHEELGUARD_GLORIOUS}=="1", TAG+="uaccess"', RULES)
        # no other line hands out a Glorious mouse's hidraw nodes
        for line in RULES.splitlines():
            if 'SUBSYSTEM=="hidraw"' in line and "uaccess" in line:
                self.assertNotRegex(line, r'idVendor\}=="(3794|258a)"')

    def test_never_a_bootloader_or_an_id_the_app_gives_twice(self):
        self.assertFalse(ids() & (BOOTLOADERS | AMBIGUOUS))
        for vid, pid in BOOTLOADERS | AMBIGUOUS:
            self.assertEqual(mice.identify(vid, pid), (None, None))
            self.assertNotIn(f'idVendor}}=="{vid:04x}", ATTRS{{idProduct}}=="{pid:04x}"', RULES)
        self.assertFalse(set(models.GLORIOUS_LINKS) & (set(models.RAZER_LINKS) | set(models.LOGI_LINKS)))

    def test_every_model_is_read_only_and_has_a_codec(self):
        family = [m for m in models.MODELS.values() if m.brand == "Glorious"]
        self.assertEqual(len(family), 16)
        for m in family:
            self.assertFalse(m.confirmed)
            self.assertIn("status_only", m.features)
            self.assertIs(session.codec_for(m.key).model, m)
            self.assertIn(m.art_key, ("endgame-xm2w-4k-v2", "razer-deathadder-v4-pro"))   # existing drawings
        self.assertEqual(len(models.confirmed_names()), 3)

    def test_the_shared_dongle_is_named_for_both(self):
        self.assertEqual(mice.identify(0x3794, 0xA300), ("Glorious Model O3 / D3 Wireless", "dongle"))
        self.assertEqual(mice.identify(0x3794, 0xA322), ("Glorious Model D3 Wireless", "cable"))


class Helper(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()

    def test_vendor_only_settings_nodes(self):
        for desc in (V3_SETTINGS, V3_SETTINGS + V3_EVENTS, V2_SETTINGS):
            self.assertTrue(self.h.vendor_only(desc))

    def test_nodes_that_carry_input_are_never_granted(self):
        for desc in (V3_SETTINGS + MOUSE, MOUSE + V3_SETTINGS, V2_SETTINGS + CONSUMER, KEYBOARD + V2_SETTINGS,
                     V3_EVENTS, MOUSE, KEYBOARD, CONSUMER, b""):
            self.assertFalse(self.h.vendor_only(desc), desc.hex())

    def test_main_reads_sysfs_and_prints_the_flag(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        node = Path(tmp.name) / "hidraw3"
        (node / "device").mkdir(parents=True)
        for desc, want in ((V2_SETTINGS, "WHEELGUARD_GLORIOUS=1\n"), (V2_SETTINGS + CONSUMER, "")):
            (node / "device" / "report_descriptor").write_bytes(desc)
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(self.h.main(["x", str(node)]), 0)
            self.assertEqual(out.getvalue(), want)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.h.main(["x", str(node / "missing")]), 0)
        self.assertEqual(out.getvalue(), "")

    def test_same_walk_as_discovery(self):
        for desc in (V3_SETTINGS + MOUSE, V2_SETTINGS + CONSUMER, V3_SETTINGS + V3_EVENTS):
            self.assertEqual(self.h.application_pages(desc), hidraw.application_pages(desc))


class Discovery(unittest.TestCase):
    def test_only_vendor_only_settings_nodes_wired_first(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        nodes = (("hidraw0", "0003:00003794:0000A300", V3_SETTINGS),           # O3 / D3 dongle: found
                 ("hidraw1", "0003:00003794:0000A300", MOUSE + V3_SETTINGS),   # shares a node with input: never
                 ("hidraw2", "0003:0000258A:00002019", V2_SETTINGS),           # O2 Pro on its cable: found
                 ("hidraw3", "0003:0000258A:00002019", CONSUMER),
                 ("hidraw4", "0003:0000258A:00002014", V2_SETTINGS),           # the ambiguous id: never
                 ("hidraw5", "0003:00003794:00002019", V2_SETTINGS),           # right pid, wrong vendor
                 ("hidraw6", "0003:00003794:0000A313", V3_SETTINGS),           # bootloader
                 ("hidraw7", "0003:0000258A:00002022", V3_SETTINGS))           # the other protocol's collection
        for name, hid_id, desc in nodes:
            hid = root / "devices" / name / "hid"
            hid.mkdir(parents=True)
            (hid / "uevent").write_text(f"HID_ID={hid_id}\n")
            (hid / "report_descriptor").write_bytes(desc)
            (root / "class" / name).mkdir(parents=True)
            os.symlink(hid, root / "class" / name / "device")
        found = hidraw.discover_glorious(str(root / "class"), "/dev")
        self.assertEqual([(c.path, c.pid, c.kind, c.wired) for c in found],
                         [("/dev/hidraw2", 0x2019, "glorious-v2pro", True), ("/dev/hidraw0", 0xA300, "glorious-v3", False)])
        self.assertEqual([c.sysname for c in hidraw.discover_all(str(root / "class"), "/dev")], ["hidraw2", "hidraw0"])


class V3AllowList(unittest.TestCase):
    def test_reads_pass(self):
        for cmd in (glorious.V3_FIRMWARE, glorious.V3_BATTERY, glorious.V3_GET_POWER):
            self.assertEqual(glorious.v3_check_request(glorious.v3_build(cmd)), cmd)
        glorious.v3_check_request(glorious.v3_build(glorious.V3_INFO, b"\x01"))

    def test_every_other_command_is_refused(self):
        # lighting 14/94, buttons 18, DPI 0F/10, lift-off 09, polling 0D/0E, debounce 06/07, motion sync 15,
        # serial 81, dock E0-E2, dongle EE: none of them, nor anything else, can be sent
        for cmd in range(256):
            if cmd in glorious.V3_ALLOWED:
                continue
            with self.subTest(cmd=f"{cmd:#04x}"):
                with self.assertRaises(Rejected):
                    glorious.v3_check_request(glorious.v3_build(cmd, bytes(8)))
                with self.assertRaises(Rejected):
                    glorious.v3_check_request(glorious.v3_build(cmd))

    def test_sleep_timer_range(self):
        def power(sleep_ms, low_ms=60000):
            return glorious.v3_build(glorious.V3_SET_POWER, sleep_ms.to_bytes(4, "big") + low_ms.to_bytes(4, "big"))
        for ms in (0, 60000, 7 * 60000, 60 * 60000):
            glorious.v3_check_request(power(ms))
        for ms, low in ((61 * 60000, 60000), (5000, 60000), (90000, 60000), (60000, 50), (60000, 0)):
            with self.assertRaises(Rejected):
                glorious.v3_check_request(power(ms, low))

    def test_malformed_requests(self):
        good = glorious.v3_build(glorious.V3_FIRMWARE)
        for bad in (good[:63], good + b"\0", b"\x04" + good[1:], good[:1] + b"\xaa" + good[2:],
                    good[:3] + b"\x01" + good[4:], good[:20] + b"\x01" + good[21:],
                    glorious.v3_build(glorious.V3_INFO, b"\x02"), glorious.v3_build(glorious.V3_GET_POWER, b"\x01")):
            with self.assertRaises(Rejected):
                glorious.v3_check_request(bad)

    def test_writes_wait_for_the_firmware_read(self):
        dev = mock.FakeGloriousV3("glorious-o3-wired")
        g = glorious.V3Device(dev, True, lambda _t: None)
        with self.assertRaises(WriteBlocked):
            g.set_power_saving(5 * 60000)
        self.assertEqual(dev.sent, [])
        g.firmware()
        g.set_power_saving(5 * 60000)
        self.assertEqual(dev.sent[-1][:12], bytes.fromhex("03bb0408" "000493e0" "0000ea60"))
        self.assertEqual(dev.sleep_ms, 300000)


class V2ProAllowList(unittest.TestCase):
    def test_reads_pass(self):
        self.assertEqual(glorious.v2_check_request(glorious.v2_build(glorious.V2_FIRMWARE)), (0x00, 0x81))
        self.assertEqual(glorious.v2_check_request(glorious.v2_build(glorious.V2_FIRMWARE, 0x00)), (0x00, 0x81))
        self.assertEqual(glorious.v2_check_request(glorious.v2_build(glorious.V2_BATTERY)), (0x00, 0x83))
        r = glorious.v2_build(glorious.V2_FIRMWARE)
        self.assertEqual(r[:7], bytes.fromhex("00000002030081"))
        self.assertEqual(len(r), 65)

    def test_every_other_command_is_refused(self):
        # settings 01/xx (DPI, active stage, lift-off, polling, motion sync), lighting 02/xx, buttons 03/00,
        # macros 04/xx, profile 00/05, sleep 00/07, debounce 00/08: nothing but the two reads
        for cls in range(8):
            for cid in range(256):
                if (cls, cid) in glorious.V2_ALLOWED:
                    continue
                r = bytearray(glorious.v2_build(glorious.V2_FIRMWARE))
                r[4], r[5], r[6] = 2, cls, cid
                with self.assertRaises(Rejected):
                    glorious.v2_check_request(bytes(r))
        self.assertEqual(glorious.V2_WRITES, {})

    def test_malformed_requests(self):
        good = glorious.v2_build(glorious.V2_BATTERY)
        for bad in (good[:64], b"\x01" + good[1:], good[:3] + b"\x00" + good[4:], good[:4] + b"\x03" + good[5:],
                    good[:7] + b"\x01" + good[8:], good[:30] + b"\x05" + good[31:]):
            with self.assertRaises(Rejected):
                glorious.v2_check_request(bad)

    def test_retries_while_busy_and_resends_after_a_failure(self):
        dev = mock.FakeGloriousV2Pro(busy=3)
        g = glorious.V2ProDevice(dev, False, lambda _t: None)
        self.assertEqual(g.battery().percent, 73)
        self.assertEqual(len(dev.sent), 1)                  # busy: only the reply was asked for again
        dev.busy = 20
        with self.assertRaises(StatusError) as cm:
            g.battery()
        self.assertEqual(cm.exception.status, glorious.ST_BUSY)


class Codec(unittest.TestCase):
    V3 = session.codec_for("glorious-model-o3-wireless")
    V2 = session.codec_for("glorious-model-o2-pro-wireless")

    def test_v3_round_trip(self):
        blob = glorious.v3_pack(7 * 60000, 60000)
        self.assertEqual(len(blob), self.V3.BLOB_LEN)
        self.assertEqual(self.V3.decode(blob), {"idle_min": 7, "stage_count": 0, "active_stage": 0})
        self.assertEqual(self.V3.blob_problems(blob), [])
        new = self.V3.encode(blob, {"idle_min": 0})
        self.assertEqual(self.V3.decode(new)["idle_min"], 0)
        self.assertEqual(self.V3.encode(new, {"idle_min": 7}), blob)
        self.assertEqual(self.V3.encode(blob, {"idle_min": 7}), blob)
        self.assertEqual([op.kind for op in self.V3.plan_writes(blob, new)], ["sleep"])
        self.assertEqual(self.V3.plan_writes(blob, blob), [])
        self.assertEqual(self.V3.describe_changes(self.V3.decode(blob), self.V3.decode(new)), ["Sleep"])
        self.assertEqual(self.V3.changed_keys(self.V3.decode(blob), self.V3.decode(new)), ["idle_min"])
        self.assertEqual(self.V3.empty()["idle_min"], 0)

    def test_v3_ranges(self):
        self.V3.check_key_value("idle_min", 60)
        for key, v in (("idle_min", 61), ("idle_min", -1), ("dpi_x0", 800), ("polling", 1000), ("lod", 1)):
            with self.assertRaises(ValueError):
                self.V3.check_key_value(key, v)
        with self.assertRaises(ValueError):
            self.V3.encode(glorious.v3_pack(0, 60000), {"idle_min": 90})
        self.assertTrue(self.V3.blob_problems(glorious.v3_pack(90000, 60000)))        # not whole minutes
        self.assertTrue(self.V3.blob_problems(glorious.v3_pack(61 * 60000, 60000)))
        self.assertTrue(self.V3.blob_problems(glorious.v3_pack(60000, 100)))
        self.assertTrue(self.V3.blob_problems(bytes(16)))
        self.assertTrue(self.V3.blob_problems(bytes(8)))

    def test_v2pro_record_holds_nothing_writable(self):
        blob = glorious.v2_pack()
        self.assertEqual(self.V2.decode(blob), {"stage_count": 0, "active_stage": 0})
        self.assertEqual(self.V2.blob_problems(blob), [])
        self.assertEqual(self.V2.KEYS, ())
        self.assertEqual(self.V2.plan_writes(blob, blob), [])
        for key in ("dpi_x0", "polling", "idle_min"):
            with self.assertRaises(ValueError):
                self.V2.encode(blob, {key: 1})
        self.assertTrue(self.V2.blob_problems(blob[:7]))
        self.assertTrue(self.V2.blob_problems(b"R" + blob[1:]))


class ReadOnlyUntilConfirmed(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.dir = t.name
        mock._singletons.clear()
        self.addCleanup(mock._singletons.clear)

    def sess(self, mode):
        return mock.open_mock(mode)[0], session.MouseSession(lambda: mock.open_mock(mode), self.dir, lambda _t: None)

    def test_o3_reads_battery_and_sleep(self):
        dev, s = self.sess("glorious-o3")
        snap = s.read()
        self.assertEqual((snap.model, snap.mouse_fw, snap.battery, snap.charging, snap.wired),
                         ("glorious-model-o3-wireless", "1.4.2", 64, False, False))
        self.assertEqual(session.codec_for(snap.model).decode(snap.blob)["idle_min"], 7)
        self.assertFalse(snap.writable)
        self.assertIn("hasn't been tested on the Glorious Model O3 Wireless", snap.problem)
        self.assertEqual({r[2] for r in dev.sent}, {0x80, 0x82, 0x84, 0x99})

    def test_the_shared_dongle_tells_the_d3_apart(self):
        _dev, s = self.sess("glorious-d3")
        self.assertEqual(s.read().model, "glorious-model-d3-wireless")

    def test_on_the_cable_it_charges(self):
        dev, s = self.sess("glorious-o3-wired")
        snap = s.read()
        self.assertEqual((snap.wired, snap.charging, snap.mouse_pid), (True, True, 0xA312))
        self.assertNotIn(0x99, {r[2] for r in dev.sent})        # its own id: no need to ask its name

    def test_nothing_is_written(self):
        dev, s = self.sess("glorious-o3")
        s.read()
        with self.assertRaises(session.HwError):
            s.apply({"idle_min": 10})
        self.assertNotIn(glorious.V3_SET_POWER, {r[2] for r in dev.sent})
        self.assertEqual(dev.sleep_ms, 7 * 60000)
        self.assertEqual(list(Path(self.dir).iterdir()), [])     # not even a backup

    def test_drivers_refuse_writes_themselves(self):
        drv = session.make_driver(mock.open_mock("glorious-o3"), lambda _t: None)
        drv.identify(lambda *_a: None, 0, 1)
        self.assertFalse(drv.write_enabled)
        with self.assertRaises(WriteBlocked):
            drv.write(glorious.Op("sleep", "sleep", glorious.v3_pack(60000, 60000)))
        drv = session.make_driver(mock.open_mock("glorious-o2pro"), lambda _t: None)
        drv.identify(lambda *_a: None, 0, 1)
        self.assertFalse(drv.write_enabled)
        with self.assertRaises(WriteBlocked):
            drv.write(glorious.Op("sleep", "sleep", glorious.v2_pack()))

    def test_the_write_path_once_confirmed(self):
        key = "glorious-model-o3-wireless"
        with umock.patch.dict(models.MODELS, {key: dataclasses.replace(models.MODELS[key], confirmed=True)}):
            dev, s = self.sess("glorious-o3")
            snap = s.read()
            self.assertTrue(snap.writable, snap.problem)
            res = s.apply({"idle_min": 15})
            self.assertEqual(res.written, ["sleep"])
            self.assertEqual(dev.sleep_ms, 15 * 60000)
            self.assertIsNotNone(res.backup_created)
            self.assertEqual(session.codec_for(key).decode(res.snapshot.blob)["idle_min"], 15)
            with self.assertRaises(session.HwError):
                s.apply({"idle_min": 61})
            drv = session.make_driver(mock.open_mock("glorious-o3"), lambda _t: None)
            drv.identify(lambda *_a: None, 0, 1)
            with self.assertRaises(Rejected):
                drv.write(glorious.Op("sleep", "sleep", glorious.v3_pack(90000, 60000)))

    def test_wired_only_models_have_no_battery(self):
        dev = mock.FakeGloriousV3("glorious-o3-wired")
        drv = session.make_driver((dev, True, "glorious-v3", 0xA310), lambda _t: None)
        self.assertEqual(drv.model.key, "glorious-model-o3")
        sent = len(dev.sent)
        with self.assertRaises(StatusError):
            drv.battery()
        self.assertEqual(len(dev.sent), sent)

    def test_v2pro_reads_battery_only(self):
        dev, s = self.sess("glorious-o2pro")
        snap = s.read()
        self.assertEqual((snap.model, snap.mouse_fw, snap.battery, snap.charging),
                         ("glorious-model-o2-pro-wireless", "2.0.3.1", 73, False))
        self.assertFalse(snap.writable)
        self.assertIn("can't report its settings back", snap.problem)
        self.assertEqual({(r[5], r[6]) for r in dev.sent}, {(0x00, 0x81), (0x00, 0x83)})
        self.assertEqual(dev.sent[0][3], 0x00)          # the firmware read through the dongle, as the app does
        self.assertEqual(s.read_battery().percent, 73)

    def test_v2pro_asleep(self):
        _dev, s = self.sess("glorious-o2pro-asleep")
        with self.assertRaises(session.MouseAsleep):
            s.read()


class Page(unittest.TestCase):
    PAGE = (ROOT / "wheelguard" / "gui" / "qml" / "Wg" / "MousePage.qml").read_text()

    def test_status_only_mice_get_no_dpi_or_polling_section(self):
        self.assertIn('has("status_only") ? []', self.PAGE)
        self.assertIn("root.sections.length > 1", self.PAGE)
        self.assertIn('mouse.brand === "Glorious"', self.PAGE)


if __name__ == "__main__":
    unittest.main()
