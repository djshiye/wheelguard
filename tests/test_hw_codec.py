"""Tests for the hardware layer (wheelguard.hw): codec, allow-list, write gate, session. No Qt, no hardware."""
import errno
import tempfile
import unittest
from pathlib import Path

from wheelguard.hw import codec, endgame, hidraw, mock, session
from wheelguard.hw.endgame import EndgameDevice, Rejected, WriteBlocked, build_packet, check_packet

BLOB = mock.default_blob()


def edited_offsets(a: bytes, b: bytes):
    return {i for i in range(len(a)) if a[i] != b[i]}


class CodecTests(unittest.TestCase):
    def test_roundtrip_is_identity(self):
        self.assertEqual(codec.encode(BLOB, {}), BLOB)
        self.assertEqual(codec.encode(BLOB, codec.flat(codec.decode(BLOB))), BLOB)

    def test_roundtrip_on_odd_blobs(self):
        odd = bytearray(BLOB)
        odd[0x05] = 0x33          # unknown polling code
        odd[0x04] = 0x80          # sleep disabled with 0 minutes
        odd[0x3D] = 0xF0
        odd[0x23] = 7             # odd DPI flag
        odd = bytes(odd)
        self.assertEqual(codec.encode(odd, codec.flat(codec.decode(odd))), odd)

    def test_decode_defaults(self):
        s = codec.decode(BLOB)
        self.assertEqual(s.dpi_x, (400, 800, 1600, 3200))
        self.assertEqual((s.stage_count, s.active_stage, s.polling), (4, 1, 0x02))
        self.assertEqual(s.filters, (8,) * 5)
        self.assertEqual(s.buttons, (0x0001, 0x0002, 0x0004, 0x0008, 0x0010))
        self.assertTrue(s.sleep_on and s.deep_on and not s.led_off and s.motion_sync)

    def test_each_edit_touches_only_its_bytes(self):
        cases = {
            "dpi_x2": (1200, {0x23 + 10 + 1, 0x23 + 10 + 2, 0x23 + 10}),     # value + flag (X != Y)
            "dpi_y0": (900, {0x23 + 3, 0x23 + 4, 0x23}),
            "stage_count": (2, {0x0E}),
            "active_stage": (3, {0x0D}),
            "polling": (0x40, {0x05}),
            "motion_sync": (False, {0x0C}),
            "angle_snapping": (True, {0x0A}),
            "ripple": (True, {0x0B}),
            "glass": (True, {0x6F}),
            "lod": (7, {0x09}),
            "led_off": (True, {0x08}),
            "sleep_on": (False, {0x04}),
            "sleep_min": (15, {0x04}),
            "deep_on": (False, {0x03}),
            "deep_min": (90, {0x03}),
            "filter0": (codec.GX_SPEED, {0x3D}),
            "filter1": (codec.GX_SAFE, {0x44}),
            "filter2": (20, {0x4B}),
            "filter3": (0, {0x52}),
            "filter4": (25, {0x59}),
            "button3": (0x0101, {0x37 + 21, 0x37 + 22}),
            "button4": (0x01FF, {0x37 + 28, 0x37 + 29}),
        }
        for key, (val, offs) in cases.items():
            with self.subTest(key):
                new = codec.encode(BLOB, {key: val})
                self.assertEqual(edited_offsets(BLOB, new) - offs, set())
                self.assertEqual(codec.flat(codec.decode(new))[key], val)

    def test_dpi_flag_follows_x_vs_y(self):
        b = codec.encode(BLOB, {"dpi_x1": 1000})
        self.assertEqual(b[0x23 + 5], 1)
        b = codec.encode(b, {"dpi_y1": 1000})
        self.assertEqual(b[0x23 + 5], 0)

    def test_sleep_bits_are_independent(self):
        b = codec.encode(BLOB, {"sleep_on": False})
        self.assertEqual(b[0x04], 0x80 | 5)             # minutes survive while disabled
        b = codec.encode(b, {"sleep_min": 20})
        self.assertEqual(b[0x04], 0x80 | 20)
        self.assertEqual(codec.encode(b, {"sleep_on": True})[0x04], 20)

    def test_button_edit_keeps_the_two_words(self):
        b = bytearray(BLOB)
        b[0x37 + 2:0x37 + 6] = bytes((1, 2, 3, 4))
        new = codec.encode(bytes(b), {"button0": 0x0002})
        self.assertEqual(new[0x37 + 2:0x37 + 6], bytes((1, 2, 3, 4)))

    def test_bad_values_rejected(self):
        for key, val in (("dpi_x0", 10), ("dpi_x0", 40000), ("stage_count", 5), ("stage_count", 0),
                         ("active_stage", 4), ("polling", 0x10), ("lod", 11), ("sleep_min", 0),
                         ("deep_min", 121), ("filter2", 26), ("filter2", codec.GX_SPEED),
                         ("filter0", 30), ("button0", 0x0909), ("nonsense", 1)):
            with self.subTest(key=key, val=val), self.assertRaises(ValueError):
                codec.encode(BLOB, {key: val})

    def test_validate_settings(self):
        with self.assertRaises(ValueError):       # no left click left
            codec.validate_settings(codec.decode(codec.encode(BLOB, {"button0": 0x0002})))
        with self.assertRaises(ValueError):       # glass mode with a normal-mode LOD code
            codec.validate_settings(codec.decode(codec.encode(BLOB, {"glass": True})))
        codec.validate_settings(codec.decode(codec.encode(BLOB, {"glass": True, "lod": 2})))
        with self.assertRaises(ValueError):       # active stage beyond the count
            codec.validate_settings(codec.decode(codec.encode(BLOB, {"stage_count": 1})))

    def test_blob_problems(self):
        self.assertEqual(codec.blob_problems(BLOB), [])
        self.assertTrue(codec.blob_problems(bytes(1024)))
        self.assertTrue(codec.blob_problems(b"\xff" * 1024))
        self.assertTrue(codec.blob_problems(BLOB[:100]))

    def test_snap_dpi(self):
        self.assertEqual(codec.snap_dpi(1), 50)
        self.assertEqual(codec.snap_dpi(99999), 30000)
        self.assertEqual(codec.snap_dpi(1234), 1230)
        self.assertEqual(codec.snap_dpi(12340), 12350)

    def test_fw_text(self):
        self.assertEqual(codec.fw_text(0x0123), "1.23")
        self.assertEqual(codec.fw_text(0x00A5), "0x00A5")
        self.assertEqual(codec.fw_text(0), "")


class PlanTests(unittest.TestCase):
    def plan(self, **edits):
        return [(g.cmd, g.page) for g in codec.plan_writes(BLOB, codec.encode(BLOB, edits))]

    def test_nothing_changed_nothing_sent(self):
        self.assertEqual(self.plan(), [])

    def test_group_selection(self):
        self.assertEqual(self.plan(dpi_x0=500), [(0x14, 0)])
        self.assertEqual(self.plan(lod=5), [(0x14, 0)])
        self.assertEqual(self.plan(led_off=True), [(0x14, 0)])
        self.assertEqual(self.plan(polling=0x04), [(0x15, 0)])
        self.assertEqual(self.plan(glass=True, lod=1), [(0x14, 0), (0x15, 0)])
        self.assertEqual(self.plan(motion_sync=False), [(0x15, 0)])
        self.assertEqual(self.plan(sleep_min=9), [(0x15, 0)])
        self.assertEqual(self.plan(filter3=3), [(0x15, 0)])            # filters alone never rewrite the button map
        self.assertEqual(self.plan(button1=0x0004), [(0x16, 1), (0x16, 2)])

    def test_order_is_14_15_16(self):
        p = self.plan(button3=0x0101, polling=0x40, dpi_x3=2000, filter0=2)
        self.assertEqual(p, [(0x14, 0), (0x15, 0), (0x16, 1), (0x16, 2)])

    def test_payloads_are_slices_of_the_new_blob(self):
        new = codec.encode(BLOB, {"dpi_x0": 500, "polling": 0x04, "button4": 0x0101})
        plan = {(g.cmd, g.page): g.payload for g in codec.plan_writes(BLOB, new)}
        self.assertEqual(plan[(0x14, 0)][8:13], new[0x23:0x28])
        self.assertEqual(plan[(0x14, 0)][6], 4)
        self.assertEqual(plan[(0x15, 0)][1], 0x04)
        self.assertEqual(len(plan[(0x15, 0)]), 11)
        self.assertEqual(plan[(0x16, 1)], new[0x37:0x53])
        self.assertEqual(plan[(0x16, 2)], new[0x53:0x6F])

    def test_unmapped_bytes_are_echoed(self):
        new = codec.encode(BLOB, {"dpi_x0": 500})
        p = codec.group_payload(new, 0x14)
        self.assertEqual(p[0], BLOB[0x07])
        self.assertEqual(p[5], BLOB[0x01])
        for off in (0x00, 0x02, 0x0F, 0x22, 0x70, 0x400 - 1):
            self.assertEqual(new[off], BLOB[off])

    def test_merge_backup(self):
        edited = codec.encode(BLOB, {"dpi_x0": 500, "polling": 0x40, "button1": 0x0004, "filter2": 3})
        restored = codec.merge_backup(edited, BLOB)
        self.assertEqual(restored, BLOB)
        # bytes no group can carry stay as the mouse has them
        mouse = bytearray(edited)
        mouse[0x02] = 0x99
        self.assertEqual(codec.merge_backup(bytes(mouse), BLOB)[0x02], 0x99)


class AllowListTests(unittest.TestCase):
    def test_forbidden_commands_cannot_be_framed_or_sent(self):
        for cmd in (0x13, 0x70, 0x71, 0x72):
            for sub in (0x00, 0x0F, 0x01):
                pkt = bytearray(64)
                pkt[0], pkt[1], pkt[2] = 0xA1, cmd, sub
                with self.assertRaises(Rejected):
                    check_packet(bytes(pkt))
        for cmd in (0x13, 0x70, 0x71, 0x72):
            dev = mock.FakeHidraw()
            eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
            eg.mouse_info()                        # even with the write gate open
            with self.assertRaises(Rejected):
                eg._exchange(build_packet(cmd, fixed=0))
            self.assertTrue(all(p[1] != cmd for p in dev.sent))

    def test_no_forbidden_command_in_allow_list(self):
        self.assertEqual(endgame.ALLOWED_CMDS & set(endgame.FORBIDDEN_CMDS), set())
        self.assertEqual(endgame.ALLOWED_CMDS, {0x0D, 0x0E, 0x0F, 0xB4, 0x12, 0x14, 0x15, 0x16})

    def test_every_other_command_byte_is_rejected(self):
        for cmd in range(256):
            if cmd in endgame.ALLOWED_CMDS:
                continue
            pkt = bytearray(64)
            pkt[0], pkt[1] = 0xA1, cmd
            with self.assertRaises(Rejected, msg=hex(cmd)):
                check_packet(bytes(pkt))

    def test_report_ids(self):
        for rid in (0x00, 0x02, 0x03, 0x08, 0xA0, 0xA2, 0xFF):
            pkt = bytearray(64)
            pkt[0], pkt[1] = rid, 0x0E
            with self.assertRaises(Rejected):
                check_packet(bytes(pkt))
        for bad in ((0xA1, 65), (0xA0, 64), (0x03, 8), (0xA2, 64)):
            with self.assertRaises(Rejected):
                endgame.check_get(*bad)
        endgame.check_get(0xA1, 64)
        endgame.check_get(0xA0, 1041)

    def test_frame_shape_is_enforced(self):
        good = build_packet(0x14, sub=0x0F, payload=codec.group_payload(BLOB, 0x14))
        check_packet(good)
        for index, value in ((2, 0x01), (3, 0x1D), (4, 1), (5, 1), (6, 1), (9, 1), (15, 1), (63, 1)):
            bad = bytearray(good)
            bad[index] = value
            with self.assertRaises(Rejected, msg=f"byte {index}"):
                check_packet(bytes(bad))
        with self.assertRaises(Rejected):
            check_packet(good[:63])
        with self.assertRaises(Rejected):
            check_packet(good + b"\0")
        # page must be 1 or 2 for 0x16, and 0 for everything else
        for page in (0, 3, 255):
            with self.assertRaises(Rejected):
                check_packet(build_packet(0x16, sub=0x0F, page=page, payload=bytes(28)))
        with self.assertRaises(Rejected):
            check_packet(build_packet(0x12, page=1))
        # probe sub-commands: 0x01 and 0x0F only
        check_packet(build_packet(0x0F, sub=0x01))
        check_packet(build_packet(0x0F, sub=0x0F))
        with self.assertRaises(Rejected):
            check_packet(build_packet(0x0F, sub=0x02))
        # read commands carry no payload
        with self.assertRaises(Rejected):
            check_packet(build_packet(0x12, payload=b"\x01"))

    def test_bad_payload_contents_are_refused(self):
        dev = mock.FakeHidraw()
        eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
        eg.mouse_info()
        base = bytearray(codec.group_payload(BLOB, 0x15))
        for idx, val in ((1, 0x10), (3, 0), (3, 121), (4, 30), (6, 26), (10, 2)):
            p = bytearray(base)
            p[idx] = val
            with self.assertRaises(Rejected, msg=f"0x15[{idx}]={val}"):
                eg._exchange(build_packet(0x15, sub=0x0F, payload=bytes(p)))
        p14 = bytearray(codec.group_payload(BLOB, 0x14))
        p14[6] = 5
        with self.assertRaises(Rejected):
            eg._exchange(build_packet(0x14, sub=0x0F, payload=bytes(p14)))
        p14 = bytearray(codec.group_payload(BLOB, 0x14))
        p14[8 + 1], p14[8 + 2] = 0, 0           # stage 1 X = 0
        with self.assertRaises(Rejected):
            eg._exchange(build_packet(0x14, sub=0x0F, payload=bytes(p14)))
        self.assertEqual([p[1] for p in dev.sent], [0x0E])

    def test_hidraw_ioctl_numbers(self):
        self.assertEqual(hidraw.HIDIOCSFEATURE(64), 0xC0404806)
        self.assertEqual(hidraw.HIDIOCGFEATURE(1041), 0xC4114807)


class WriteGateTests(unittest.TestCase):
    def group(self):
        return codec.GroupWrite(0x15, 0, codec.group_payload(BLOB, 0x15))

    def test_blocked_before_identification(self):
        dev = mock.FakeHidraw()
        eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
        self.assertFalse(eg.write_enabled)
        with self.assertRaises(WriteBlocked):
            eg.write_group(self.group())
        self.assertEqual(dev.sent, [])

    def test_blocked_for_other_pids(self):
        for pid in (0x1981, 0x1983, 0x1970, 0x0000, 0xFFFF):
            dev = mock.FakeHidraw(mouse_pid=pid)
            eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
            eg.mouse_info()
            with self.assertRaises(WriteBlocked, msg=hex(pid)):
                eg.write_group(self.group())
            self.assertEqual([p[1] for p in dev.sent], [0x0E])

    def test_open_for_the_supported_mouse(self):
        dev = mock.FakeHidraw()
        eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
        eg.mouse_info()
        self.assertTrue(eg.write_enabled)
        eg.write_group(self.group())
        self.assertEqual(dev.sent[-1][1], 0x15)

    def test_gate_closes_again_if_reidentification_fails(self):
        dev = mock.FakeHidraw()
        eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
        eg.mouse_info()
        dev.mode = "asleep"
        with self.assertRaises(endgame.StatusError):
            eg.mouse_info()
        self.assertFalse(eg.write_enabled)

    def test_all_three_groups_gated(self):
        dev = mock.FakeHidraw(mouse_pid=0x1983)
        eg = EndgameDevice(dev, wired=False, sleep=lambda s: None)
        for gw in codec.plan_writes(BLOB, codec.encode(BLOB, {"dpi_x0": 500, "polling": 0x40, "button1": 4})):
            with self.assertRaises(WriteBlocked):
                eg.write_group(gw)
        self.assertEqual(dev.sent, [])


class TransportTests(unittest.TestCase):
    def test_busy_status_is_retried(self):
        class Busy(mock.FakeHidraw):
            n = 0

            def get_feature(self, rid, length):
                r = bytearray(super().get_feature(rid, length))
                Busy.n += 1
                if Busy.n <= 2:
                    r[1] = 3
                return bytes(r)
        eg = EndgameDevice(Busy(), wired=True, sleep=lambda s: None)
        self.assertEqual(eg.battery(), 82)

    def test_set_feature_retries_on_eio_only(self):
        class Flaky(mock.FakeHidraw):
            fail = 2

            def set_feature(self, data):
                if Flaky.fail:
                    Flaky.fail -= 1
                    raise OSError(errno.EIO, "x")
                super().set_feature(data)
        self.assertEqual(EndgameDevice(Flaky(), wired=True, sleep=lambda s: None).battery(), 82)

        class Gone(mock.FakeHidraw):
            def set_feature(self, data):
                raise OSError(errno.ENODEV, "gone")
        with self.assertRaises(endgame.LinkError):
            EndgameDevice(Gone(), wired=True, sleep=lambda s: None).battery()

    def test_dongle_sends_probe_and_waits_longer(self):
        waits = []
        dev = mock.FakeHidraw()
        eg = EndgameDevice(dev, wired=False, sleep=waits.append)
        eg.probe()
        eg.mouse_info()
        eg.write_group(codec.GroupWrite(0x15, 0, codec.group_payload(BLOB, 0x15)))
        self.assertEqual([(p[1], p[2]) for p in dev.sent], [(0x0F, 0x01), (0x0E, 0), (0x0F, 0x01), (0x15, 0x0F)])
        self.assertIn(0.88, waits)

    def test_wired_skips_probe(self):
        dev = mock.FakeHidraw(mode="connected-wired")
        eg = EndgameDevice(dev, wired=True, sleep=lambda s: None)
        eg.probe()
        self.assertEqual(dev.sent, [])

    def test_blob_read(self):
        eg = EndgameDevice(mock.FakeHidraw(), wired=True, sleep=lambda s: None)
        self.assertEqual(eg.read_blob(), BLOB)


class SessionTests(unittest.TestCase):
    def make(self, mode="connected", **kw):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dev = mock.FakeHidraw(mode, **kw)
        return session.MouseSession(lambda: (self.dev, self.dev.wired), self.tmp.name, lambda s: None)

    def test_read(self):
        snap = self.make().read()
        self.assertEqual((snap.mouse_fw, snap.dongle_fw, snap.battery, snap.writable), ("1.05", "1.23", 82, True))
        self.assertEqual(snap.blob, BLOB)

    def test_read_never_sends_a_write_command(self):
        self.make().read()
        self.assertFalse({p[1] for p in self.dev.sent} & {0x13, 0x14, 0x15, 0x16, 0x70, 0x71, 0x72})

    def test_asleep(self):
        with self.assertRaises(session.MouseAsleep):
            self.make("asleep").read()

    def test_link_down_pid_zero_is_asleep(self):
        with self.assertRaises(session.MouseAsleep):
            self.make(mouse_pid=0).read()

    def test_unsupported_pid_blocks_everything(self):
        s = self.make("unsupported")
        with self.assertRaises(session.UnsupportedMouse) as cm:
            s.read()
        self.assertEqual(cm.exception.pid, 0x1983)
        with self.assertRaises(session.UnsupportedMouse):
            s.apply({"polling": 0x04})
        with self.assertRaises(session.UnsupportedMouse):
            s.restore()
        self.assertFalse({p[1] for p in self.dev.sent} & {0x12, 0x14, 0x15, 0x16})
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_apply_writes_only_changed_groups_and_backs_up_first(self):
        s = self.make()
        res = s.apply({"polling": 0x04})
        self.assertEqual(res.written, ["advanced"])
        self.assertEqual([p[1] for p in self.dev.sent if p[1] in (0x14, 0x15, 0x16)], [0x15])
        self.assertEqual(codec.decode(bytes(self.dev.blob)).polling, 0x04)
        self.assertEqual(edited_offsets(BLOB, bytes(self.dev.blob)), {0x05})
        files = list(Path(self.tmp.name).glob("1.05-*.bin"))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), BLOB)
        self.assertEqual(res.backup_created, str(files[0]))

    def test_backup_is_written_before_the_first_write(self):
        s = self.make()
        order = []
        orig_set = self.dev.set_feature

        def spy(data):
            if data[1] in (0x14, 0x15, 0x16):
                order.append(("write", len(list(Path(self.tmp.name).glob("*.bin")))))
            orig_set(data)
        self.dev.set_feature = spy
        s.apply({"dpi_x0": 500})
        self.assertEqual(order, [("write", 1)])

    def test_only_one_backup_per_device(self):
        s = self.make()
        s.apply({"polling": 0x04})
        s.apply({"polling": 0x40})
        self.assertEqual(len(list(Path(self.tmp.name).glob("*.bin"))), 1)

    def test_backup_failure_aborts_the_write(self):
        s = self.make()
        s._backup_dir = Path(self.tmp.name) / "file"
        (Path(self.tmp.name) / "file").write_text("not a directory")
        with self.assertRaises(session.HwError):
            s.apply({"polling": 0x04})
        self.assertEqual(bytes(self.dev.blob), BLOB)
        self.assertFalse({p[1] for p in self.dev.sent} & {0x14, 0x15, 0x16})

    def test_apply_preserves_changes_made_on_the_mouse_meanwhile(self):
        s = self.make()
        self.dev.blob[0x0D] = 3                 # the DPI button was pressed after the page loaded
        s.apply({"polling": 0x04})
        self.assertEqual(self.dev.blob[0x0D], 3)

    def test_no_change_sends_nothing(self):
        s = self.make()
        res = s.apply({"polling": 0x02})        # already 4000 Hz
        self.assertTrue(res.unchanged)
        self.assertFalse({p[1] for p in self.dev.sent} & {0x14, 0x15, 0x16})
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_full_apply_order_and_untouched_bytes(self):
        s = self.make()
        res = s.apply({"button3": 0x0101, "polling": 0x40, "dpi_x3": 2000, "filter0": codec.GX_SPEED})
        self.assertEqual([p[1] for p in self.dev.sent if p[1] in (0x14, 0x15, 0x16)], [0x14, 0x15, 0x16, 0x16])
        self.assertEqual([p[6] for p in self.dev.sent if p[1] == 0x16], [1, 2])
        changed = edited_offsets(BLOB, bytes(self.dev.blob))
        self.assertEqual(changed, {0x05, 0x3D, 0x32, 0x33, 0x34, 0x37 + 21, 0x37 + 22})
        for off in (0x00, 0x02, 0x07, *range(0x0F, 0x23), 0x70, 0x3FF, 0x67, 0x6E):
            self.assertEqual(self.dev.blob[off], BLOB[off])
        self.assertEqual(res.snapshot.blob, bytes(self.dev.blob))

    def test_refuses_to_leave_no_left_click(self):
        s = self.make()
        with self.assertRaises(session.HwError):
            s.apply({"button0": 0x0002})
        self.assertFalse({p[1] for p in self.dev.sent} & {0x14, 0x15, 0x16})

    def test_refuses_implausible_blob(self):
        s = self.make(blob=bytes(1024))
        snap = s.read()
        self.assertFalse(snap.writable)
        with self.assertRaises(session.HwError):
            s.apply({"polling": 0x04})
        self.assertFalse({p[1] for p in self.dev.sent} & {0x14, 0x15, 0x16})

    def test_failed_group_stops_the_sequence(self):
        s = self.make()
        orig = self.dev.set_feature

        def fail_on_15(data):
            orig(data)
            if data[1] == 0x15:
                self.dev._reply = bytes([0xA1, 2]) + bytes(62)
        self.dev.set_feature = fail_on_15
        with self.assertRaises(session.WriteFailed) as cm:
            s.apply({"dpi_x0": 500, "polling": 0x40, "button1": 4})
        self.assertEqual(cm.exception.written, ["sensor"])
        self.assertNotIn(0x16, [p[1] for p in self.dev.sent])

    def test_restore_goes_back_to_the_original_via_diff_only(self):
        s = self.make()
        s.apply({"polling": 0x40, "dpi_x0": 500})
        self.dev.sent.clear()
        res = s.restore()
        self.assertEqual(bytes(self.dev.blob), BLOB)
        self.assertEqual(res.written, ["sensor", "advanced"])
        self.assertNotIn(0x16, [p[1] for p in self.dev.sent])

    def test_restore_without_backup(self):
        with self.assertRaises(session.HwError):
            self.make().restore()

    def test_verify_mismatch_is_reported(self):
        s = self.make()
        orig = self.dev.set_feature

        def forgetful(data):
            orig(data)
            if data[1] == 0x15:
                self.dev.blob[0x05] = 0x02
        self.dev.set_feature = forgetful
        with self.assertRaises(session.VerifyMismatch) as cm:
            s.apply({"polling": 0x04})
        self.assertEqual(cm.exception.keys, ["polling"])

    def test_denied_and_missing(self):
        with self.assertRaises(session.NoAccess):
            session.MouseSession(lambda: mock.open_mock("denied")).read()
        with self.assertRaises(session.NoDevice):
            session.MouseSession(lambda: None).read()


class DiscoveryTests(unittest.TestCase):
    def make_tree(self, nodes):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, hid_id, desc in nodes:
            d = Path(tmp.name) / name / "device"
            d.mkdir(parents=True)
            (d / "uevent").write_text(f"DRIVER=hid-generic\nHID_ID={hid_id}\nHID_NAME=x\n")
            (d / "report_descriptor").write_bytes(desc)
        return tmp.name

    CONFIG = bytes.fromhex("0601ff0902a101" "85a1" "75089505" "b103" "85a0" "b103" "c0")
    PLAIN = bytes.fromhex("05010902a101c0")

    def test_finds_only_the_config_channel(self):
        root = self.make_tree([
            ("hidraw0", "0003:00003367:00001970", self.PLAIN),
            ("hidraw1", "0003:00003367:00001970", self.CONFIG),
            ("hidraw2", "0003:00000816:0000246F", self.CONFIG),      # other vendor
            ("hidraw3", "0003:00003367:00001969", self.CONFIG),      # not a supported PID
        ])
        found = hidraw.discover(root, "/dev")
        self.assertEqual([(c.path, c.pid) for c in found], [("/dev/hidraw1", 0x1970)])

    def test_wired_wins(self):
        root = self.make_tree([("hidraw1", "0003:00003367:00001970", self.CONFIG),
                               ("hidraw5", "0003:00003367:00001982", self.CONFIG)])
        self.assertTrue(hidraw.discover(root)[0].wired)

    def test_descriptor_needs_both_reports(self):
        root = self.make_tree([("hidraw1", "0003:00003367:00001970", bytes.fromhex("0601ff0902a10185a1c0"))])
        self.assertEqual(hidraw.discover(root), [])

    def test_missing_sysfs(self):
        self.assertEqual(hidraw.discover("/nonexistent"), [])


class MockGuardTests(unittest.TestCase):
    def test_mock_is_off_without_the_env_var(self):
        import os
        old = os.environ.pop(mock.MOCK_ENV, None)
        try:
            self.assertIsNone(mock.mock_mode())
        finally:
            if old is not None:
                os.environ[mock.MOCK_ENV] = old

    def test_unknown_env_value_is_ignored(self):
        import os
        old = os.environ.get(mock.MOCK_ENV)
        os.environ[mock.MOCK_ENV] = "yes"
        try:
            self.assertIsNone(mock.mock_mode())
        finally:
            if old is None:
                del os.environ[mock.MOCK_ENV]
            else:
                os.environ[mock.MOCK_ENV] = old


if __name__ == "__main__":
    unittest.main()
