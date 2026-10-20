"""App window plumbing and the fallback tray icon.

With the GNOME Shell extension active the panel icon and popup belong to the extension (see service.py)
and the QSystemTrayIcon below stays hidden. Without it, QSystemTrayIcon talks StatusNotifierItem, so
GNOME's AppIndicator extension (or another tray host) renders its menu; a left click opens the window.
"""
from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, Slot
from PySide6.QtGui import QAction, QActionGroup, QWindow
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import traymodel


def tray_available() -> bool:
    return QSystemTrayIcon.isSystemTrayAvailable()


class AppShell(QObject):
    """Window plumbing shared by the QML (as `shell`), the tray icon and the D-Bus service."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._main = None
        self._ext = None            # ExtensionWatcher: is the panel extension active?
        self._sni = False           # a StatusNotifierItem icon was set up

    def attach(self, main, ext, sni: bool):
        self._main, self._ext, self._sni = main, ext, sni

    def panel_ok(self) -> bool:
        """Live check: closing may hide the window only while something in the panel can bring it back."""
        return bool(self._ext is not None and self._ext.active) or (self._sni and tray_available())

    # ---- main window
    @Slot()
    def showMain(self):
        w = self._main
        if w is None:
            return
        if w.visibility() == QWindow.Visibility.Minimized:
            w.showNormal()
        else:
            w.show()
        w.raise_()
        w.requestActivate()

    @Slot()
    def closeMainWindow(self):
        """The X button: hide to the panel, or really quit when there is nothing to bring it back from."""
        if self.panel_ok() and self._main is not None:
            self._main.hide()
        else:
            QCoreApplication.quit()

    @Slot()
    def quit(self):
        QCoreApplication.quit()


class TrayIcon(QObject):
    """The icon, its tooltip and the context menu, kept in sync with the daemon and the mouse."""

    def __init__(self, shell: AppShell, daemon, mouse, icon, suppressed=False, parent=None):
        super().__init__(parent)
        self._shell, self._daemon, self._mouse = shell, daemon, mouse
        self._suppressed = suppressed
        self._rows = None
        self._menu = QMenu()
        self._group: Optional[QActionGroup] = None
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._menu.aboutToShow.connect(self._on_menu_open)
        for sig in (daemon.stateChanged, daemon.configChanged, mouse.trayChanged):
            sig.connect(self.update)
        self.update()
        if not suppressed:
            self._tray.show()

    def hide(self):
        self._tray.hide()

    def setSuppressed(self, on: bool):
        """The panel extension took over: never show two icons."""
        if on == self._suppressed:
            return
        self._suppressed = on
        if on:
            self._tray.hide()
        else:
            self._tray.show()

    def state(self) -> traymodel.TrayState:
        return traymodel.snapshot(self._daemon, self._mouse)

    @Slot()
    def update(self):
        s = self.state()
        self._tray.setToolTip(traymodel.tooltip(s))
        rows = traymodel.menu_rows(s)
        if rows != self._rows:
            self._rows = rows
            self._build(rows)

    def _build(self, rows):
        for act in self._menu.actions():        # not clear(): this may run inside one of these actions' signals
            self._menu.removeAction(act)
            act.deleteLater()
        self._group = QActionGroup(self._menu)
        self._group.setExclusive(True)
        for r in rows:
            if r.kind == "separator":
                self._menu.addSeparator()
                continue
            act = QAction(r.text, self._menu)
            act.setEnabled(r.enabled)
            if r.kind in ("toggle", "stage"):
                act.setCheckable(True)
                act.setChecked(r.checked)
            if r.kind == "stage":
                self._group.addAction(act)
            if r.kind == "toggle":
                act.toggled.connect(self._daemon.setEnabled)
            elif r.kind == "stage":
                act.triggered.connect(lambda _c=False, i=r.index: self._mouse.setActiveStage(i))
            elif r.kind == "action":
                act.triggered.connect(self._action(r.key))
            self._menu.addAction(act)

    def _action(self, key):
        return {"open": self._shell.showMain, "quit": self._shell.quit}[key]

    def _on_menu_open(self):
        self._mouse.refreshBattery()
        self.update()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._shell.showMain()
