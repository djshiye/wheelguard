import datetime
import json
import os
import socket
import tempfile
import threading
import unittest

from wheelguard.gui import protocol

try:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
    from wheelguard.gui import backend
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

TODAY = datetime.date(2026, 11, 9)


def day(n_ago, ticks, dropped):
    return {"date": TODAY - datetime.timedelta(days=n_ago), "ticks": ticks, "dropped": dropped}


def steady(first, last, ticks=1000, rate=0.02):
    """One entry per day from `first` to `last` days ago (inclusive) at a constant block rate."""
    return [day(n, ticks, round(ticks * rate)) for n in range(first, last - 1, -1)]


class Parsing(unittest.TestCase):
    def test_reply_is_cleaned_and_sorted(self):
        msg = {"type": "history", "days": [
            {"date": "2026-11-09", "ticks": 10, "dropped": 1},
            {"date": "2026-11-07", "ticks": 20.0, "dropped": 2},
            {"date": "2026-11-07", "ticks": 5, "dropped": 9},           # repeated date: added up, clamped
            {"date": "nope", "ticks": 1, "dropped": 0},
            {"date": "2026-11-08", "ticks": -1, "dropped": 0},
            {"date": "2026-11-08", "ticks": True, "dropped": 0},
            {"date": "2026-11-08", "ticks": 1},
            {"date": 20261108, "ticks": 1, "dropped": 0},
            None, "x", 4]}
        got = protocol.history_days(msg)
        self.assertEqual([(d["date"].isoformat(), d["ticks"], d["dropped"]) for d in got],
                         [("2026-11-07", 25, 7), ("2026-11-09", 10, 1)])
        self.assertEqual(protocol.history_days({}), [])
        self.assertEqual(protocol.history_days({"days": "x"}), [])


class Series(unittest.TestCase):
    def test_zero_filled_and_oldest_first(self):
        days = [day(0, 200, 10), day(2, 100, 0)]
        s = protocol.history_series(days, 7, TODAY)
        self.assertEqual(len(s), 7)
        self.assertEqual((s[0]["date"], s[-1]["date"]), ("2026-11-03", "2026-11-09"))
        self.assertEqual(s[-1]["rate"], 5.0)
        self.assertEqual(s[-3]["rate"], 0.0)
        self.assertEqual(s[-2]["rate"], protocol.NO_RATE)                # a day without ticks has no rate (-1: None would reach QML as undefined)
        self.assertEqual(s[-2]["ticks"], 0)

    def test_old_days_are_left_out_of_a_short_range(self):
        s = protocol.history_series([day(20, 100, 5)], 7, TODAY)
        self.assertTrue(all(d["ticks"] == 0 for d in s))

    def test_enough_history_for_a_chart(self):
        self.assertFalse(protocol.has_history(protocol.history_series([], 30, TODAY)))
        self.assertFalse(protocol.has_history(protocol.history_series([day(0, 50, 1)], 30, TODAY)))
        self.assertTrue(protocol.has_history(protocol.history_series([day(0, 50, 1), day(3, 50, 1)], 30, TODAY)))


class WearHint(unittest.TestCase):
    def test_clear_rise_shows_the_hint(self):
        days = steady(30, 7, rate=0.02) + steady(6, 0, rate=0.08)       # 2% for weeks, then 8% this week
        self.assertTrue(protocol.wear_hint(days, TODAY))

    def test_steady_wheel_does_not(self):
        self.assertFalse(protocol.wear_hint(steady(30, 0, rate=0.05), TODAY))

    def test_ordinary_wobble_does_not(self):
        days = steady(30, 7, rate=0.040) + steady(6, 0, rate=0.047)
        self.assertFalse(protocol.wear_hint(days, TODAY))

    def test_a_small_absolute_rise_does_not_even_when_relatively_big(self):
        days = steady(30, 7, ticks=5000, rate=0.002) + steady(6, 0, ticks=5000, rate=0.008)    # 0.2% -> 0.8%
        self.assertFalse(protocol.wear_hint(days, TODAY))

    def test_a_small_relative_rise_does_not_even_when_absolutely_big(self):
        days = steady(30, 7, ticks=5000, rate=0.20) + steady(6, 0, ticks=5000, rate=0.25)      # 20% -> 25%
        self.assertFalse(protocol.wear_hint(days, TODAY))

    def test_improvement_does_not(self):
        self.assertFalse(protocol.wear_hint(steady(30, 7, rate=0.08) + steady(6, 0, rate=0.02), TODAY))

    def test_not_enough_data_never_shows_it(self):
        self.assertFalse(protocol.wear_hint([], TODAY))
        # a new install: only this week
        self.assertFalse(protocol.wear_hint(steady(6, 0, rate=0.5), TODAY))
        # a baseline of a couple of days
        self.assertFalse(protocol.wear_hint(steady(9, 8, ticks=5000, rate=0.01) + steady(6, 0, rate=0.2), TODAY))
        # plenty of baseline, but hardly any recent ticks
        self.assertFalse(protocol.wear_hint(steady(30, 7) + [day(0, 400, 200)], TODAY))
        # a single busy day this week
        self.assertFalse(protocol.wear_hint(steady(30, 7) + [day(1, 3000, 900)], TODAY))
        # a thin baseline: lots of days but few ticks
        self.assertFalse(protocol.wear_hint(steady(30, 7, ticks=50, rate=0.02) + steady(6, 0, rate=0.2), TODAY))

    def test_boundaries(self):
        base = steady(30, 7, rate=0.02)
        self.assertTrue(protocol.wear_hint(base + steady(6, 0, ticks=1000, rate=0.08), TODAY))
        # recent ticks just under the minimum (7 x 142 = 994)
        self.assertFalse(protocol.wear_hint(base + steady(6, 0, ticks=142, rate=0.5), TODAY))
        # days older than the 28-day baseline window do not count as baseline
        self.assertFalse(protocol.wear_hint(steady(90, 40, rate=0.02) + steady(6, 0, rate=0.08), TODAY))

    def test_future_dates_are_ignored(self):
        days = steady(30, 7, rate=0.02) + [day(-1, 10 ** 6, 10 ** 6)]
        self.assertFalse(protocol.wear_hint(days, TODAY))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class Backend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "wg.sock")
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(1)
        self.srv.settimeout(5)
        self.received, self.conn = [], None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()
        self.client = backend.DaemonClient(self.path)

    def tearDown(self):
        self.client._retry.stop()
        self.client._sock.abort()
        self.srv.close()
        if self.conn is not None:
            self.conn.close()
        self.thread.join(timeout=1)
        self.dir.cleanup()

    def _serve(self):
        try:
            self.conn, _ = self.srv.accept()
        except OSError:
            return
        buf = b""
        while True:
            try:
                data = self.conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line)
                self.received.append(msg)
                for out in self.reply(msg):
                    self.conn.sendall(json.dumps(out).encode() + b"\n")

    def reply(self, msg):
        if msg["cmd"] == "subscribe":
            return [{"type": "status", "connected": True, "device": "D", "config": {}, "stats": {}}]
        if msg["cmd"] == "history":
            t = datetime.date.today()
            return [{"type": "history", "days": [
                {"date": (t - datetime.timedelta(days=n)).isoformat(), "ticks": 1000, "dropped": 20 if n > 6 else 90}
                for n in range(0, 30)]}]
        return []

    def pump(self, cond, timeout=3.0):
        loop, poll, deadline = QEventLoop(), QTimer(), QTimer()
        poll.setInterval(10)
        deadline.setSingleShot(True)
        poll.timeout.connect(lambda: loop.quit() if cond() else None)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(int(timeout * 1000))
        loop.exec()
        poll.stop()
        deadline.stop()
        return cond()

    def test_history_flow(self):
        self.client.start()
        self.assertTrue(self.pump(lambda: self.client.state == "running"))
        self.assertFalse(self.client.wearHint)
        self.assertEqual(len(self.client.historySeries(7)), 7)
        self.assertFalse(self.client.hasHistory())
        self.client.refreshHistory()
        self.assertTrue(self.pump(lambda: self.client.hasHistory()))
        self.assertTrue(self.client.wearHint)                          # 2% before, 9% this week
        series = self.client.historySeries(30)
        self.assertEqual(len(series), 30)
        self.assertEqual(series[-1]["rate"], 9.0)
        self.assertEqual(len(self.client.historySeries(1000)), 90)      # clamped


CHART_SCRIPT = r"""
import json, sys
from pathlib import Path
from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
app = QGuiApplication([])
engine = QQmlEngine()
engine.addImportPath(sys.argv[1])
comp = QQmlComponent(engine)
comp.setData(b"import QtQuick\nimport Wg 1.0\nHistoryChart { width: 400; height: 170 }", QUrl("file:///test/chart.qml"))
obj = comp.create()
assert not comp.errors(), comp.errors()
series = [{"date": "2026-11-07", "ticks": 200, "dropped": 10, "rate": 5.0},
          {"date": "2026-11-08", "ticks": 0, "dropped": 0, "rate": None},
          {"date": "2026-11-09", "ticks": 0, "dropped": 0, "rate": -1.0},
          {"date": "2026-11-10", "ticks": 0, "dropped": 0}]
obj.setProperty("series", series)
def call(name, arg):
    return QMetaObject.invokeMethod(obj, name, Qt.ConnectionType.DirectConnection,
                                    Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", arg))
print(json.dumps({"max": obj.property("maxRate"), "has": [call("hasRate", d) for d in series],
                  "pct": call("pct", None)}))
"""


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class ChartRates(unittest.TestCase):
    """Python's None reaches QML as undefined, never as null: the chart must not turn it into NaN bars.
    (A subprocess: it needs a QGuiApplication, and other tests in this process made a QCoreApplication.)"""

    def test_days_without_ticks_are_not_bars(self):
        import subprocess
        import sys
        from pathlib import Path
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        qml = Path(backend.__file__).parent / "qml"
        out = subprocess.run([sys.executable, "-c", CHART_SCRIPT, str(qml)], env=env, capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        got = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertEqual(got["max"], 5.0)
        self.assertEqual(got["has"], [True, False, False, False])
        self.assertEqual(got["pct"], "–")


if __name__ == "__main__":
    unittest.main()
