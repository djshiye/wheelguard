"""Tray icon, its right-click menu and the quick-view popup window.

QSystemTrayIcon talks StatusNotifierItem, so GNOME's AppIndicator extension renders the menu. Wayland
clients can't position their own windows, so the popup is a transparent frameless window shown
maximized (it covers the work area, which excludes the panel); the QML draws the card in the bottom-right
corner and the window's input mask is shrunk to that card, so clicks anywhere else reach the windows below.
"""
import time
from typing import Optional

from PySide6.QtCore import QCoreApplication, QObject, QPoint, QRect, Slot
from PySide6.QtGui import QAction, QActionGroup, QRegion, QWindow
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from . import traymodel

REOPEN_GUARD_S = 0.35        # a tray click that just closed the popup (by taking focus) must not reopen it


def tray_available() -> bool:
    return QSystemTrayIcon.isSystemTrayAvailable()


class AppShell(QObject):
    """Window plumbing shared by the QML (as `shell`) and the tray icon."""

    def __init__(self, mouse, parent=None):
        super().__init__(parent)
        self._mouse = mouse
        self._main = None
        self._popup = None
        self._tray_enabled = False
        self._popup_hidden_at = 0.0
        self.preview = False        # screenshot harness: the popup is a plain window, not a maximized overlay

    def attach(self, main, popup, tray_enabled: bool):
        self._main, self._popup, self._tray_enabled = main, popup, tray_enabled

    def tray_ok(self) -> bool:
        """Live check: if the shell's tray host goes away the window must not hide into nothing."""
        return self._tray_enabled and tray_available()

    # ---- main window
    @Slot()
    def showMain(self):
        w = self._main
        if w is None:
            return
        self.hidePopup()
        if w.visibility() == QWindow.Visibility.Minimized:
            w.showNormal()
        else:
            w.show()
        w.raise_()
        w.requestActivate()

    @Slot()
    def closeMainWindow(self):
        """The X button: hide to the tray, or really quit when there is no tray to bring it back from."""
        if self.tray_ok() and self._main is not None:
            self._main.hide()
        else:
            QCoreApplication.quit()

    @Slot()
    def quit(self):
        QCoreApplication.quit()

    # ---- popup
    @Slot()
    def togglePopup(self):
        p = self._popup
        if p is None:
            return
        if p.isVisible():
            self.hidePopup()
        elif time.monotonic() - self._popup_hidden_at > REOPEN_GUARD_S:
            self._mouse.refreshQuietly()
            if self.preview:
                p.show()
            else:
                p.showMaximized()
            p.raise_()
            p.requestActivate()

    @Slot()
    def hidePopup(self):
        p = self._popup
        if p is not None and p.isVisible():
            p.hide()
            self._popup_hidden_at = time.monotonic()

    @Slot(float, float, float, float)
    def setPopupMask(self, x, y, w, h):
        """Only the card takes input; the rest of the maximized window is click-through."""
        if self._popup is not None:
            self._popup.setMask(QRegion(QRect(QPoint(round(x), round(y)), QPoint(round(x + w) - 1, round(y + h) - 1))))


class TrayIcon(QObject):
    """The icon, its tooltip and the context menu, kept in sync with the daemon and the mouse."""

    def __init__(self, shell: AppShell, daemon, mouse, icon, parent=None):
        super().__init__(parent)
        self._shell, self._daemon, self._mouse = shell, daemon, mouse
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
        self._tray.show()

    def hide(self):
        self._tray.hide()

    def state(self) -> traymodel.TrayState:
        d, m = self._daemon, self._mouse
        return traymodel.TrayState(d.state, d.enabled, m.online, m.battery, m.charging,
                                   tuple(m.stageLabels), m.activeStage, m.stageReason)

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
        return {"popup": self._shell.togglePopup, "open": self._shell.showMain,
                "quit": self._shell.quit}[key]

    def _on_menu_open(self):
        self._mouse.refreshBattery()
        self.update()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._shell.togglePopup()
