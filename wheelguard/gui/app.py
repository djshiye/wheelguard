"""Wheelguard settings application: Qt Quick window, tray icon, backends and a screenshot harness."""
import argparse
import os
import signal
import sys
from pathlib import Path

APP_ID = "io.github.djshiye.Wheelguard"
HERE = Path(__file__).resolve().parent
QML_DIR = HERE / "qml"
ICON_NAME = APP_ID + ".svg"
PAGES = {"scroll": 0, "mouse": 1, "pointer": 2, "about": 3}     # sidebar order


def find_icon() -> Path:
    """The SVG lives in build-aux/ next to the package (also checked next to the gui files, and in the
    system icon theme where the RPM installs it)."""
    for base in (HERE.parent.parent / "build-aux", HERE, Path.home() / ".local/share/wheelguard/build-aux",
                 Path("/usr/share/icons/hicolor/scalable/apps")):
        p = base / ICON_NAME
        if p.is_file():
            return p
    return HERE.parent.parent / "build-aux" / ICON_NAME


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="wheelguard-settings", description="Wheelguard advanced mouse settings")
    ap.add_argument("--socket", help="daemon socket path (default $XDG_RUNTIME_DIR/wheelguard.sock)")
    ap.add_argument("--tray", action="store_true",
                    help="start hidden in the system tray (does nothing if there is no tray)")
    shot = ap.add_argument_group("screenshots (development)")
    shot.add_argument("--screenshot", metavar="PNG", help="render the window to PNG after a delay, then quit")
    shot.add_argument("--page", choices=("scroll", "mouse", "pointer", "about"), default="scroll")
    shot.add_argument("--advanced", action="store_true", help="open the Advanced expander")
    shot.add_argument("--theme", choices=("dark", "light"), help="force a theme instead of following GNOME")
    shot.add_argument("--size", default="1008x708", help="window size WxH including shadow margin")
    shot.add_argument("--popup", action="store_true", help="with --screenshot: render the tray quick view")
    shot.add_argument("--stage-demo", action="store_true",
                      help="on the Mouse page, stage two edits once the mouse has been read")
    shot.add_argument("--delay-ms", type=int, default=1500)
    return ap.parse_args(argv)


def wait_for_tray(QEventLoop, QTimer, stop, seconds=15) -> bool:
    """At login the shell's tray host can start a moment after us: give it a little time to appear.
    `stop()` ends the wait early (someone launched the app by hand meanwhile)."""
    from .tray import tray_available
    loop = QEventLoop()
    for _ in range(seconds):
        if tray_available():
            return True
        if stop():
            break
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
    return tray_available()


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QFont, QGuiApplication, QIcon
        from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
        from PySide6.QtQuickControls2 import QQuickStyle
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("wheelguard-settings needs PySide6: sudo dnf install python3-pyside6", file=sys.stderr)
        return 1

    from . import single, tray
    from .backend import Appearance, Autostart, DaemonClient, PointerSettings
    from .mouse import MouseController

    QQuickStyle.setStyle("Basic")
    QGuiApplication.setApplicationName("Wheelguard")
    QGuiApplication.setApplicationDisplayName("Wheelguard")
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])        # QtWidgets for QSystemTrayIcon; the UI itself is Qt Quick
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # Headless runs have no platform theme: use the GNOME UI font so screenshots match the desktop.
        from PySide6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        for name in ("Adwaita Sans", "Cantarell"):
            if name in families:
                app.setFont(QFont(name, 11))
                break

    shot = bool(args.screenshot)
    server, early = None, []
    if not shot:
        if single.send("tray" if args.tray else "show"):       # already running: it shows its window
            return 0
        server = single.InstanceServer(parent=app)
        if server.listen():
            server.messageReceived.connect(early.append)       # held until the window exists
            app.aboutToQuit.connect(server.close)
        if args.tray and not wait_for_tray(QEventLoop, QTimer, lambda: "show" in early) and "show" not in early:
            return 0                                           # no tray: don't pop a window up at login
    tray_ok = not shot and tray.tray_available()
    if tray_ok:
        app.setQuitOnLastWindowClosed(False)

    icon_path = find_icon()
    icon = QIcon(str(icon_path))
    if icon.isNull():
        icon = QIcon.fromTheme(APP_ID)
    app.setWindowIcon(icon)

    engine = QQmlApplicationEngine()
    # Backends are children of the engine so they outlive the QML objects bound to them at exit.
    override = None if args.theme is None else args.theme == "dark"
    appearance = Appearance(override, engine)
    daemon = DaemonClient(args.socket, engine)
    pointer = PointerSettings(engine)
    mouse = MouseController(parent=engine)
    shell = tray.AppShell(mouse, engine)

    engine.addImportPath(str(QML_DIR))
    ctx = engine.rootContext()
    ctx.setContextProperty("daemon", daemon)
    ctx.setContextProperty("pointer", pointer)
    ctx.setContextProperty("mouse", mouse)
    ctx.setContextProperty("appearance", appearance)
    ctx.setContextProperty("shell", shell)
    ctx.setContextProperty("autostart", Autostart(engine))
    ctx.setContextProperty("iconUrl", QUrl.fromLocalFile(str(icon_path)))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        return 1
    win = engine.rootObjects()[0]

    popup = None
    if tray_ok or args.popup:
        comp = QQmlComponent(engine, QUrl.fromLocalFile(str(QML_DIR / "TrayPopup.qml")))
        popup = comp.create()
        if popup is None:
            print(comp.errorString(), file=sys.stderr)
            return 1
        if args.popup:
            popup.setProperty("preview", True)
            shell.preview = True
    shell.attach(win, popup, tray_ok)
    tray_icon = tray.TrayIcon(shell, daemon, mouse, icon, engine) if tray_ok else None

    w, h = (int(v) for v in args.size.lower().split("x"))
    win.setWidth(w)
    win.setHeight(h)
    win.setProperty("pageIndex", PAGES[args.page])
    if args.advanced:
        win.setProperty("advancedOpen", True)

    if args.stage_demo:
        def stage_once():
            if mouse.state == "ready":
                mouse.stateChanged.disconnect(stage_once)
                mouse.stageDemoEdits()
        mouse.stateChanged.connect(stage_once)

    pointer.refresh()
    appearance.watch()
    daemon.start()
    if not shot or args.popup:
        mouse.ensureLoaded()            # the tray needs the stages and battery before the Mouse page is opened
    if tray_ok:
        mouse.startBackground()
    if server is not None:
        server.messageReceived.disconnect(early.append)
        server.messageReceived.connect(lambda msg: shell.showMain() if msg == "show" else None)
    if "show" in early or not ((args.tray and tray_ok) or (shot and args.popup)):
        win.show()

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.aboutToQuit.connect(pointer.flush)
    app.aboutToQuit.connect(appearance.shutdown)
    app.aboutToQuit.connect(mouse.shutdown)
    if tray_icon is not None:
        app.aboutToQuit.connect(tray_icon.hide)

    if shot:
        def grab():
            img = (popup if args.popup else win).grabWindow()
            ok = img.save(args.screenshot)
            print(f"screenshot {'saved' if ok else 'FAILED'}: {args.screenshot} {img.width()}x{img.height()}", flush=True)
            app.exit(0 if ok else 2)
        if args.popup:
            QTimer.singleShot(args.delay_ms, popup.show)
            QTimer.singleShot(args.delay_ms + 600, grab)        # after the fade-in
        else:
            QTimer.singleShot(args.delay_ms, grab)

    return app.exec()
