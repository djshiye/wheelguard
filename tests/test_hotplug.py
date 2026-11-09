import os
import struct
import tempfile
import time
import unittest

from wheelguard import daemon, hotplug
from wheelguard.hotplug import DeviceWatcher, relevant


def record(name, mask=hotplug.IN_CREATE):
    raw = name.encode() + b"\0" * (16 - len(name))
    return struct.pack("iIII", 1, mask, 0, len(raw)) + raw


class Relevant(unittest.TestCase):
    def test_event_node(self):
        self.assertTrue(relevant(record("event7")))

    def test_other_nodes_ignored(self):
        self.assertFalse(relevant(record("mouse0") + record("js0")))

    def test_event_after_other(self):
        self.assertTrue(relevant(record("by-id") + record("event12", hotplug.IN_ATTRIB)))

    def test_truncated(self):
        self.assertFalse(relevant(b"\x01\x00"))


class Unavailable(unittest.TestCase):
    def test_missing_directory_falls_back_to_polling(self):
        self.assertIsNone(DeviceWatcher.open("/nonexistent/wheelguard-test"))

    def test_no_inotify(self):
        class NoLibc:
            pass
        self.assertIsNone(DeviceWatcher.open(libc=NoLibc()))


class RealInotify(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.w = DeviceWatcher.open(self.dir)
        self.assertIsNotNone(self.w)

    def tearDown(self):
        self.w.close()

    def touch(self, name):
        with open(os.path.join(self.dir, name), "w"):
            pass

    def test_nothing_pending(self):
        self.assertFalse(self.w.drain())

    def test_new_event_node(self):
        self.touch("event3")
        self.assertTrue(self.w.drain())
        self.assertFalse(self.w.drain())        # consumed

    def test_permission_change(self):
        self.touch("event4")
        self.w.drain()
        os.chmod(os.path.join(self.dir, "event4"), 0o600)   # what udev's uaccess ACL looks like
        self.assertTrue(self.w.drain())

    def test_unrelated_node(self):
        self.touch("mouse1")
        self.assertFalse(self.w.drain())


class FakeServer:
    def fds(self):
        return []

    def service(self, readable):
        pass


class FakeCtl:
    def poll_config(self):
        pass


class Idle(unittest.TestCase):
    def test_wakes_soon_after_a_device_appears(self):
        d = tempfile.mkdtemp()
        w = DeviceWatcher.open(d)
        self.addCleanup(w.close)
        with open(os.path.join(d, "event9"), "w"):
            pass
        t0 = time.monotonic()
        daemon.idle(FakeCtl(), FakeServer(), 5.0, w)
        took = time.monotonic() - t0
        self.assertLess(took, 1.0)
        self.assertGreaterEqual(took, daemon.SETTLE_S * 0.9)

    def test_without_watcher_waits_the_full_interval(self):
        t0 = time.monotonic()
        daemon.idle(FakeCtl(), FakeServer(), 0.3)
        self.assertGreaterEqual(time.monotonic() - t0, 0.29)

    def test_quiet_directory_waits_the_full_interval(self):
        w = DeviceWatcher.open(tempfile.mkdtemp())
        self.addCleanup(w.close)
        t0 = time.monotonic()
        daemon.idle(FakeCtl(), FakeServer(), 0.4, w)
        self.assertGreaterEqual(time.monotonic() - t0, 0.39)


if __name__ == "__main__":
    unittest.main()
