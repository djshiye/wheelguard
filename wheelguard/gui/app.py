"""Wheelguard settings application: Qt Quick window, panel/tray integration, backends and a screenshot harness."""
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
                    help="start hidden in the panel (does nothing if there is no panel icon or tray)")
    shot = ap.add_argument_group("screenshots (development)")
    shot.add_argument("--screenshot", metavar="PNG", help="render the window to PNG after a delay, then quit")
    shot.add_argument("--page", choices=("scroll", "mouse", "pointer", "about"), default="scroll")
    shot.add_argument("--advanced", action="store_true", help="open the Advanced expander")
    shot.add_argument("--theme", choices=("dark", "light"), help="force a theme instead of following GNOME")
    shot.add_argument("--size", default="1008x708", help="window size WxH including shadow margin")
    shot.add_argument("--stage-demo", action="store_true",
                      help="on the Mouse page, stage two edits once the mouse has been read")
    shot.add_argument("--delay-ms", type=int, default=1500)
    return ap.parse_args(argv)


def wait_for_panel(QEventLoop, QTimer, ext, stop, seconds=15) -> bool:
    """At login the shell's extension or tray host can start a moment after us: give it a little time to
    appear. `stop()` ends the wait early (someone launched the app by hand meanwhile)."""
    from .tray import tray_available
    loop = QEventLoop()
    ext.changed.connect(loop.quit)
    for _ in range(seconds):
        if ext.active or tray_available():
            return True
        if stop():
            break
        QTimer.singleShot(1000, loop.quit)
        loop.exec()
    return ext.active or tray_available()


def wait_for(QEventLoop, QTimer, signal, ms):
    """Spin the event loop until `signal` fires or `ms` pass."""
    loop = QEventLoop()
    signal.connect(loop.quit)
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    signal.disconnect(loop.quit)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    # Qt's QML disk cache trusts source mtimes, but RPM clamps every file to the build day, so two
    # releases built the same day look identical and stale bytecode gets loaded. Key it by version.
    from .. import __version__
    cache_root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    os.environ.setdefault("QML_DISK_CACHE_PATH", str(cache_root / "Wheelguard" / f"qmlcache-{__version__}"))

    try:
        from PySide6.QtCore import QEventLoop, QTimer, QUrl
        from PySide6.QtGui import QFont, QGuiApplication, QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("wheelguard-settings needs PySide6: sudo dnf install python3-pyside6", file=sys.stderr)
        return 1

    from . import service
    QGuiApplication.setApplicationName("Wheelguard")
    QGuiApplication.setApplicationDisplayName("Wheelguard")
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])        # QtWidgets for QSystemTrayIcon; the UI itself is Qt Quick

    # Owning the bus name is the single-instance lock. A second launch hands over to the first before any
    # of the heavy modules (QML, backends) are even imported.
    shot = bool(args.screenshot)
    svc = None
    if not shot:
        if not service.claim():
            if not args.tray:
                service.call_remote("ShowWindow")
            return 0
        svc = service.Service(parent=app)
        svc.register()

    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle
    from . import shellext, tray
    from .backend import Appearance, Autostart, DaemonClient, PointerSettings
    from .mouse import MouseController

    QQuickStyle.setStyle("Basic")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # Headless runs have no platform theme: use the GNOME UI font so screenshots match the desktop.
        from PySide6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        for name in ("Adwaita Sans", "Cantarell"):
            if name in families:
                app.setFont(QFont(name, 11))
                break

    ext = None
    if not shot:
        ext = shellext.ExtensionWatcher(parent=app)
        if args.tray:
            if not ext.answered:
                wait_for(QEventLoop, QTimer, ext.infoReceived, 2500)    # one round trip to the shell
            marker = shellext.marker_path()
            if shellext.should_enable(ext.info, marker.exists(), True):
                ext.enable()                                    # once: afterwards the user's choice stands
                shellext.write_marker(marker)
                wait_for(QEventLoop, QTimer, ext.changed, 3000)         # so no tray icon flashes up meanwhile
            elif ext.active:
                shellext.write_marker(marker)
            if not wait_for_panel(QEventLoop, QTimer, ext, lambda: svc.pending_show) and not svc.pending_show:
                return 0                                        # no panel icon: don't pop a window up at login
        app.setQuitOnLastWindowClosed(False)                    # closing hides or quits via AppShell

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
    shell = tray.AppShell(engine)

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

    sni = not shot and tray.tray_available()
    tray_icon = None
    if sni:
        # Hidden until the shell has said whether the extension (which replaces this icon) is active.
        tray_icon = tray.TrayIcon(shell, daemon, mouse, icon, suppressed=not ext.answered or ext.active, parent=engine)
        ext.infoReceived.connect(lambda _info: tray_icon.setSuppressed(ext.active))
    shell.attach(win, ext, sni)
    if svc is not None:
        svc.attach(daemon, mouse)
        svc.showRequested.connect(shell.showMain)

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
    if not shot:
        mouse.ensureLoaded()            # the panel needs the stages and battery before the Mouse page is opened
        mouse.startBackground()
    if (svc is not None and svc.pending_show) or not (args.tray and shell.panel_ok()):
        win.show()

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.aboutToQuit.connect(pointer.flush)
    app.aboutToQuit.connect(appearance.shutdown)
    app.aboutToQuit.connect(mouse.shutdown)
    if tray_icon is not None:
        app.aboutToQuit.connect(tray_icon.hide)

    if shot:
        def grab():
            img = win.grabWindow()
            ok = img.save(args.screenshot)
            print(f"screenshot {'saved' if ok else 'FAILED'}: {args.screenshot} {img.width()}x{img.height()}", flush=True)
            app.exit(0 if ok else 2)
        QTimer.singleShot(args.delay_ms, grab)

    return app.exec()
