"""The app side of 1.7.0: the mouse picker's rows, the update prompt, the drag distance that follows the DPI,
which mouse the Mouse page talks to, and the paused state."""
import unittest
from types import SimpleNamespace

from wheelguard.gui import protocol, traymodel
from wheelguard.hw import session

MICE = [{"vendor": "3367", "product": "1970", "name": "Endgame Gear XM2w", "supported": True,
         "accessible": True, "connection": "USB receiver"},
        {"vendor": "046d", "product": "c08b", "name": "Logitech G502", "supported": False,
         "accessible": True, "connection": "USB"},
        {"vendor": "1234", "product": "abcd", "name": "Other Mouse", "supported": False,
         "accessible": False, "connection": "USB"}]


class MouseRowsTests(unittest.TestCase):
    def test_automatic_names_the_mouse_it_picked(self):
        rows = protocol.mouse_rows(MICE, "auto", "3367:1970")
        self.assertEqual([r["id"] for r in rows], ["auto", "3367:1970", "046d:c08b", "1234:abcd"])
        self.assertEqual((rows[0]["selected"], rows[0]["detail"]), (True, "Endgame Gear XM2w"))
        self.assertTrue(rows[1]["active"])
        self.assertEqual([r["note"] for r in rows], ["", "", "not tested", "no access"])

    def test_a_chosen_mouse_is_marked_and_allowed_ones_are_known(self):
        rows = protocol.mouse_rows(MICE, "046d:c08b", "046d:c08b", allowed={"046d:c08b"})
        self.assertEqual([r["selected"] for r in rows], [False, False, True, False])
        self.assertEqual([r["allowed"] for r in rows], [False, False, True, False])
        self.assertEqual(rows[0]["detail"], "Picks your supported mouse")

    def test_a_saved_mouse_that_is_unplugged_still_shows(self):
        rows = protocol.mouse_rows(MICE[:1], "dead:beef", "")
        self.assertEqual((rows[-1]["id"], rows[-1]["note"], rows[-1]["selected"]), ("dead:beef", "not connected", True))

    def test_junk_from_the_socket_is_dropped(self):
        rows = protocol.mouse_rows([{"vendor": "zz", "product": "1"}, "x", None], "auto", "nope")
        self.assertEqual(len(rows), 1)

    def test_status_ident(self):
        self.assertEqual(protocol.status_ident({"ident": "3367:1970"}), "3367:1970")
        for bad in (None, 7, "auto", "x" * 300, "3367"):
            self.assertEqual(protocol.status_ident({"ident": bad}), "")


class UpdateTests(unittest.TestCase):
    def test_same_version_everywhere_needs_nothing(self):
        self.assertEqual(protocol.update_state("1.7.0", "1.7.0", "1.7.0"), "")

    def test_an_older_service_wants_a_service_restart(self):
        self.assertEqual(protocol.update_state("1.7.0", "1.6.2", "1.7.0"), "service")

    def test_newer_files_on_disk_want_an_app_restart(self):
        self.assertEqual(protocol.update_state("1.6.2", "1.7.0", "1.7.0"), "app")
        self.assertEqual(protocol.update_state("1.6.2", "1.6.2", "1.7.0"), "app")

    def test_unknown_versions_never_nag(self):
        self.assertEqual(protocol.update_state("", "1.7.0", "1.7.0"), "")
        self.assertEqual(protocol.update_state("1.7.0", "", ""), "")
        self.assertEqual(protocol.update_state("1.7.0", "garbage", "also garbage"), "")

    def test_version_tuple(self):
        self.assertEqual(protocol.version_tuple("1.7.0-mock"), (1, 7, 0))
        self.assertEqual(protocol.version_tuple(None), ())


class DragAndStatsTests(unittest.TestCase):
    def test_a_quarter_inch_at_any_dpi(self):
        self.assertEqual(protocol.drag_counts_for_dpi(1600), 400)
        self.assertEqual(protocol.drag_counts_for_dpi(400), 100)
        self.assertEqual(protocol.drag_counts_for_dpi(26000), 6500)
        self.assertEqual(protocol.drag_counts_for_dpi(100), 50)         # never below the service's minimum

    def test_a_dpi_we_cannot_use(self):
        for bad in (None, "x", 0, -5, 10 ** 9):
            self.assertEqual(protocol.drag_counts_for_dpi(bad), 0)

    def test_per_button_counts(self):
        st = {"stats": {"ticks": 1, "dropped": 0, "debounced": 37,
                        "debounced_by": {"left": 34, "right": 3, "middle": 0}}}
        self.assertEqual(protocol.status_stats(st)["debounced_by"], (34, 3, 0))
        self.assertEqual(protocol.debounced_text((34, 3, 0)), "Left 34 · Right 3")
        self.assertEqual(protocol.debounced_text((0, 0, 0)), "")
        bad = {"stats": {"ticks": 1, "dropped": 0, "debounced_by": {"left": "x", "right": True, "middle": -4}}}
        self.assertEqual(protocol.status_stats(bad)["debounced_by"], (0, 0, 0))


class PausedTests(unittest.TestCase):
    def test_paused_wins_over_everything_else(self):
        st = {"connected": True, "device": "Mouse", "paused": True}
        self.assertEqual(protocol.device_state(st).state, "paused")
        self.assertEqual(protocol.device_state({"connected": True, "device": "Mouse", "paused": "yes"}).state,
                         "running")

    def test_the_panel_says_paused(self):
        self.assertEqual(traymodel.status_pill("paused", True), ("Paused", "amber"))
        self.assertIn("paused", protocol.CONFIG_STATES)


class PickTests(unittest.TestCase):
    A = SimpleNamespace(vid=0x3367, pid=0x1970)
    B = SimpleNamespace(vid=0x1532, pid=0x00b6)
    C = SimpleNamespace(vid=0x1532, pid=0x00b7)

    def test_no_preference_takes_the_first(self):
        self.assertIs(session.pick([self.A, self.B], None), self.A)

    def test_the_protected_mouse_wins(self):
        self.assertIs(session.pick([self.A, self.B, self.C], (0x1532, 0x00b7)), self.C)

    def test_else_the_same_brand(self):
        self.assertIs(session.pick([self.A, self.B], (0x1532, 0x9999)), self.B)

    def test_an_unknown_preference_falls_back(self):
        self.assertIs(session.pick([self.A, self.B], (0x046d, 0xc08b)), self.A)


try:
    from PySide6.QtCore import QCoreApplication, QObject, Signal
    from wheelguard.gui import backend
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

if HAVE_QT:
    class RefusingDaemon(QObject):
        """A service that refuses the drag distance (an older one): every refusal reverts the config."""
        stateChanged = Signal()
        configChanged = Signal()

        def __init__(self):
            super().__init__()
            self.ident, self.configKnown, self.asked = "3367:1970", True, []

        def setClickLockDragCounts(self, counts):
            self.asked.append(counts)
            if len(self.asked) < 50:
                self.configChanged.emit()           # the revert after the error reply

    class DpiMouse(QObject):
        trayChanged = Signal()
        state, online, usbIdText, activeStage = "ready", True, "3367:1970", 0
        baseline = {"dpi_x0": 800, "dpi_x1": 3200}

        def refresh(self):
            pass


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class MouseFollowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_a_refused_drag_distance_is_not_asked_for_again(self):
        d, m = RefusingDaemon(), DpiMouse()
        follow = backend.MouseFollow(d, m, prefer=lambda ids: False)
        self.assertEqual(d.asked, [200])            # a quarter inch at 800 DPI, once
        m.activeStage = 1
        m.trayChanged.emit()
        self.assertEqual(d.asked, [200, 800])       # a new DPI is a new question
        d.ident = ""
        d.stateChanged.emit()
        d.ident = "3367:1970"
        d.stateChanged.emit()                       # the service came back (maybe updated): ask again
        self.assertEqual(d.asked, [200, 800, 800])
        del follow


if __name__ == "__main__":
    unittest.main()
