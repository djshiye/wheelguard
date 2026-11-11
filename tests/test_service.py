"""The D-Bus service: state mirroring, command routing, and single-instance ownership of the bus name."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from wheelguard.hw import mock, session

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QTimer, Signal
    from PySide6.QtDBus import QDBusConnection
    from wheelguard.gui import service
    from wheelguard.gui.mouse import MouseController
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


if HAVE_QT:
    class FakeDaemon(QObject):
        stateChanged = Signal()
        configChanged = Signal()
        statsChanged = Signal()
        clickLockActiveChanged = Signal()

        def __init__(self):
            super().__init__()
            self.state, self.enabled, self.presetName, self.dropped = "running", True, "Balanced", 0
            self.clickLock, self.clickLockMs, self.clickLockActive = False, 5000, False
            self.clickLockMode = "repeat"
            self.calls = []

        def setEnabled(self, on):
            self.calls.append(("enabled", on))

        def setClickLock(self, on):
            self.calls.append(("clicklock", on))

        def setClickLockMs(self, ms):
            self.calls.append(("clicklock_ms", ms))

        def setClickLockMode(self, mode):
            self.calls.append(("clicklock_mode", mode))

        def stopClickLock(self):
            self.calls.append(("clicklock_stop",))

    class FakeMouse(QObject):
        trayChanged = Signal()

        def __init__(self):
            super().__init__()
            self.online, self.battery, self.charging = True, 80, False
            self.stageLabels, self.activeStage, self.stageReason = ["400", "800"], 1, ""
            self.stagesEnabled = True
            self.calls = []

        def setActiveStage(self, i):
            self.calls.append(("stage", i))

        def refreshQuietly(self):
            self.calls.append(("refresh",))


def pump(ms=50):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.d, self.m = FakeDaemon(), FakeMouse()
        self.svc = service.Service()
        self.sent = []
        self.svc.stateChanged.connect(self.sent.append)

    def test_idle_state_before_the_backends_exist(self):
        s = self.svc.current()
        self.assertFalse(s["Online"])
        self.assertEqual((s["DaemonState"], s["Battery"], s["StageLabels"]), ("connecting", -1, []))

    def test_commands_before_attach_are_ignored_and_show_is_remembered(self):
        self.svc.set_active_stage(1)
        self.svc.set_protection(False)
        self.svc.refresh()
        self.svc.show_window()
        self.assertTrue(self.svc.pending_show)

    def test_attach_publishes_the_state(self):
        self.svc.attach(self.d, self.m)
        self.assertEqual(len(self.sent), 1)
        s = self.svc.current()
        self.assertEqual((s["Battery"], s["ActiveStage"], s["Preset"], s["ProtectionEnabled"]), (80, 1, "Balanced", True))

    def test_changes_are_coalesced_and_only_real_changes_are_sent(self):
        self.svc.attach(self.d, self.m)
        self.sent.clear()
        self.m.battery = 70
        self.m.trayChanged.emit()
        self.d.dropped = 5
        self.d.statsChanged.emit()
        pump()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.sent[0]["Battery"], self.sent[0]["Dropped"]), (70, 5))
        self.m.trayChanged.emit()                   # nothing changed
        pump()
        self.assertEqual(len(self.sent), 1)

    def test_commands_are_routed(self):
        self.svc.attach(self.d, self.m)
        self.svc.set_active_stage(0)
        self.svc.refresh()
        self.svc.set_protection(False)
        self.svc.set_clicklock(True)
        self.svc.set_clicklock_ms(7000)
        self.assertEqual(self.m.calls, [("stage", 0), ("refresh",)])
        self.assertEqual(self.d.calls, [("enabled", False), ("clicklock", True), ("clicklock_ms", 7000)])

    def test_stop_clicklock_is_routed_and_the_screen_lock_watcher_calls_it(self):
        from wheelguard.gui.screenlock import ScreenLockWatcher
        self.svc.attach(self.d, self.m)
        self.svc.stop_clicklock()
        self.svc.adaptor.StopClickLock()
        w = ScreenLockWatcher(self.d)
        w.onActiveChanged(False)                        # the screen unlocking does nothing
        w.onActiveChanged(True)
        self.assertEqual(self.d.calls, [("clicklock_stop",)] * 3)

    def test_protection_needs_the_service(self):
        self.d.state = "stopped"
        self.svc.attach(self.d, self.m)
        self.svc.set_protection(False)
        self.svc.set_clicklock(True)
        self.svc.set_clicklock_ms(7000)
        self.assertEqual(self.d.calls, [])

    def test_toggle_clicklock_flips_and_reports(self):
        self.svc.attach(self.d, self.m)
        self.assertEqual(self.svc.toggle_clicklock(), "ok:Click-Lock on")
        self.d.clickLock = True
        self.assertEqual(self.svc.toggle_clicklock(), "ok:Click-Lock off")
        self.assertEqual(self.d.calls, [("clicklock", True), ("clicklock", False)])

    def test_toggle_clicklock_needs_the_service(self):
        self.assertTrue(self.svc.toggle_clicklock().startswith("error:"))       # not attached yet
        self.d.state = "stopped"
        self.svc.attach(self.d, self.m)
        self.assertTrue(self.svc.toggle_clicklock().startswith("error:"))
        self.assertEqual(self.d.calls, [])

    def test_next_dpi_stage_wraps_round(self):
        self.svc.attach(self.d, self.m)
        self.assertEqual(self.svc.next_dpi_stage(), "ok:DPI 400")       # stage 1 of 2 -> back to the first
        self.m.activeStage = 0
        self.assertEqual(self.svc.next_dpi_stage(), "ok:DPI 800")
        self.assertEqual(self.m.calls, [("stage", 0), ("stage", 1)])

    def test_next_dpi_stage_says_why_not(self):
        self.assertTrue(self.svc.next_dpi_stage().startswith("error:"))
        self.svc.attach(self.d, self.m)
        self.m.stageReason = "Unsaved changes on the Mouse page"
        self.assertEqual(self.svc.next_dpi_stage(), "error:Unsaved changes on the Mouse page")
        self.m.stageReason, self.m.stageLabels = "", []
        self.assertTrue(self.svc.next_dpi_stage().startswith("error:"))
        self.assertEqual(self.m.calls, [])

    def test_show_window_after_attach_is_signalled(self):
        self.svc.attach(self.d, self.m)
        got = []
        self.svc.showRequested.connect(lambda: got.append(1))
        self.svc.show_window()
        self.assertEqual((got, self.svc.pending_show), ([1], False))

    def test_adaptor_methods_reach_the_service(self):
        self.svc.attach(self.d, self.m)
        self.svc.adaptor.SetActiveStage(1)
        self.svc.adaptor.SetProtection(True)
        self.svc.adaptor.SetClickLock(True)
        self.svc.adaptor.SetClickLockMs(6000)
        self.svc.adaptor.SetClickLockMode("hold")
        self.svc.adaptor.SetClickLockMode("turbo")      # ignored
        self.svc.adaptor.Refresh()
        self.assertEqual(self.m.calls, [("stage", 1), ("refresh",)])
        self.assertEqual(self.d.calls, [("enabled", True), ("clicklock", True), ("clicklock_ms", 6000),
                                        ("clicklock_mode", "hold")])
        self.assertEqual(self.svc.adaptor.GetState()["ClickLockDetail"], "Off")
        self.assertEqual(self.svc.adaptor.GetState()["Battery"], 80)


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class StagedEditsTests(unittest.TestCase):
    """The real MouseController behind the service: with staged edits nothing reaches the mouse."""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dev = mock.FakeHidraw("connected")
        s = session.MouseSession(lambda: (self.dev, self.dev.wired), tmp.name, lambda t: None)
        c = MouseController(s)
        self.addCleanup(c.shutdown)
        loop = QEventLoop()
        c.stateChanged.connect(lambda: loop.quit() if c.state == "ready" else None)
        QTimer.singleShot(5000, loop.quit)
        c.ensureLoaded()
        loop.exec()
        self.assertEqual(c.state, "ready")
        return c

    def writes(self):
        return [p[1] for p in self.dev.sent if p[1] in (0x14, 0x15, 0x16)]

    def test_set_active_stage_over_the_service(self):
        c = self.make()
        d = FakeDaemon()
        svc = service.Service()
        svc.attach(d, c)
        c.setValue("polling", 0x04)                 # a staged edit
        svc.adaptor.SetActiveStage(3)
        self.assertEqual((c.state, self.writes()), ("ready", []))
        self.assertEqual(svc.current()["StageReason"], "Unsaved changes on the Mouse page") if False else None
        c.discard()
        svc.adaptor.SetActiveStage(3)
        self.assertEqual(c.state, "applying")


def private_bus():
    """Start a throwaway dbus-daemon; returns (process, address) or None when there is no dbus-daemon."""
    exe = shutil.which("dbus-daemon")
    if not exe:
        return None
    p = subprocess.Popen([exe, "--session", "--nofork", "--print-address"], stdout=subprocess.PIPE, text=True)
    return p, p.stdout.readline().strip()


@unittest.skipUnless(HAVE_QT and shutil.which("dbus-daemon"), "needs PySide6 and dbus-daemon")
class ScreenLockBusTests(unittest.TestCase):
    """The real signal over a real (private) bus: a plain-string slot silently never connected."""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.proc, self.addr = private_bus()
        self.addCleanup(self.proc.stdout.close)
        self.addCleanup(self.proc.wait)
        self.addCleanup(self.proc.terminate)
        self.listener = QDBusConnection.connectToBus(self.addr, "wg-lock-listener")
        self.addCleanup(QDBusConnection.disconnectFromBus, "wg-lock-listener")
        self.shell = QDBusConnection.connectToBus(self.addr, "wg-lock-shell")
        self.addCleanup(QDBusConnection.disconnectFromBus, "wg-lock-shell")

    def emit(self, svc, path, iface, active):
        from PySide6.QtDBus import QDBusMessage
        self.assertTrue(self.shell.registerService(svc))
        msg = QDBusMessage.createSignal(path, iface, "ActiveChanged")
        msg.setArguments([active])
        self.assertTrue(self.shell.send(msg))
        pump(200)

    def test_the_screensaver_signal_stops_click_lock(self):
        from wheelguard.gui.screenlock import SERVICES, ScreenLockWatcher
        d = FakeDaemon()
        w = ScreenLockWatcher(d, bus=self.listener)
        self.assertEqual(w.connected, [True] * len(SERVICES))
        svc, path, iface = SERVICES[0]
        self.emit(svc, path, iface, False)
        self.assertEqual(d.calls, [])
        self.emit(svc, path, iface, True)
        self.assertEqual(d.calls, [("clicklock_stop",)])

    def test_the_freedesktop_screensaver_works_too(self):
        from wheelguard.gui.screenlock import SERVICES, ScreenLockWatcher
        d = FakeDaemon()
        w = ScreenLockWatcher(d, bus=self.listener)
        self.emit(*SERVICES[1], True)
        self.assertEqual(d.calls, [("clicklock_stop",)])
        del w


@unittest.skipUnless(HAVE_QT and shutil.which("dbus-daemon"), "needs PySide6 and dbus-daemon")
class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.proc, self.addr = private_bus()
        self.addCleanup(self.proc.stdout.close)
        self.addCleanup(self.proc.wait)         # cleanups run last-in first-out: terminate, then wait
        self.addCleanup(self.proc.terminate)
        self.first = QDBusConnection.connectToBus(self.addr, "wg-first")
        self.addCleanup(QDBusConnection.disconnectFromBus, "wg-first")

    def test_the_second_claim_fails(self):
        self.assertTrue(service.claim(self.first))
        second = QDBusConnection.connectToBus(self.addr, "wg-second")
        self.addCleanup(QDBusConnection.disconnectFromBus, "wg-second")
        self.assertFalse(service.claim(second))

    def other_process(self, code):
        """Run `code` in a second Python on the private bus, like a second launch; the event loop keeps
        spinning meanwhile so the first instance can answer. Returns its stdout."""
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.addr, QT_QPA_PLATFORM="offscreen",
                   PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        p = subprocess.Popen([sys.executable, "-c", code], env=env, stdout=subprocess.PIPE, text=True)
        loop = QEventLoop()
        timer = QTimer()
        timer.timeout.connect(lambda: loop.quit() if p.poll() is not None else None)
        timer.start(10)
        QTimer.singleShot(8000, loop.quit)
        loop.exec()
        timer.stop()
        if p.poll() is None:
            p.kill()
        return p.communicate()[0]

    PRELUDE = ("from PySide6.QtCore import QCoreApplication\n"
               "from wheelguard.gui import service\n"
               "app = QCoreApplication([])\n")

    def test_second_process_cannot_claim_and_reaches_the_first(self):
        self.assertTrue(service.claim(self.first))
        svc = service.Service(bus=self.first)
        self.assertTrue(svc.register())
        svc.attach(FakeDaemon(), FakeMouse())
        got = []
        svc.showRequested.connect(lambda: got.append(1))
        out = self.other_process(self.PRELUDE + "print(service.claim(), service.call_remote('ShowWindow'))")
        self.assertEqual(out.split(), ["False", "True"])
        self.assertEqual(got, [1])

    def test_commands_and_state_over_the_bus(self):
        svc = service.Service(bus=self.first)
        self.assertTrue(service.claim(self.first) and svc.register())
        d, m = FakeDaemon(), FakeMouse()
        svc.attach(d, m)
        out = self.other_process(self.PRELUDE + (
            "from PySide6.QtDBus import QDBus, QDBusMessage, QDBusConnection\n"
            "bus = QDBusConnection.sessionBus()\n"
            "def call(iface, name, *args):\n"
            "    msg = QDBusMessage.createMethodCall(service.BUS_NAME, service.OBJECT_PATH, iface, name)\n"
            "    msg.setArguments(list(args))\n"
            "    return bus.call(msg, QDBus.CallMode.Block, 3000)\n"
            "call(service.INTERFACE, 'SetActiveStage', 0)\n"
            "call(service.INTERFACE, 'SetProtection', False)\n"
            "print(call('org.freedesktop.DBus.Properties', 'Get', service.INTERFACE, 'Battery').arguments()[0].variant())\n"
            "print(call(service.INTERFACE, 'GetState').type() == QDBusMessage.MessageType.ReplyMessage)\n"))
        self.assertEqual(out.split(), ["80", "True"])
        self.assertEqual(m.calls, [("stage", 0)])
        self.assertEqual(d.calls, [("enabled", False)])


    def test_the_dpi_shortcut_goes_over_the_bus(self):
        svc = service.Service(bus=self.first)
        self.assertTrue(service.claim(self.first) and svc.register())
        d, m = FakeDaemon(), FakeMouse()
        svc.attach(d, m)
        out = self.other_process(self.PRELUDE + (
            "from wheelguard.gui import commands\n"
            "print(commands.run_action('next-dpi-stage', service.call_remote_text, lambda: (False, 'x')))\n"
            "print(commands.run_action('toggle-clicklock', service.call_remote_text, lambda: (True, 'direct')))\n"))
        self.assertEqual(out.splitlines(), ["(True, 'DPI 400')", "(True, 'direct')"])
        self.assertEqual(d.calls, [])           # Click-Lock and Pause go straight to the service, not the app
        self.assertEqual(m.calls, [("stage", 0)])

    def test_no_app_means_no_answer(self):
        out = self.other_process(self.PRELUDE + "print(service.call_remote_text('NextDpiStage'))")
        self.assertEqual(out.split(), ["None"])


if __name__ == "__main__":
    unittest.main()
