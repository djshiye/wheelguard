"""Wheelguard settings application: Qt Quick window, backends and a screenshot harness."""
import argparse
import os
import signal
import sys
from pathlib import Path

APP_ID = "io.github.djshiye.Wheelguard"
HERE = Path(__file__).resolve().parent
QML_DIR = HERE / "qml"
ICON_NAME = APP_ID + ".svg"


def find_icon() -> Path:
    """The SVG lives in build-aux/ next to the package (also checked next to the gui files)."""
    for base in (HERE.parent.parent / "build-aux", HERE, Path.home() / ".local/share/wheelguard/build-aux"):
        p = base / ICON_NAME
        if p.is_file():
            return p
    return HERE.parent.parent / "build-aux" / ICON_NAME


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="wheelguard-settings", description="Wheelguard advanced mouse settings")
    ap.add_argument("--socket", help="daemon socket path (default $XDG_RUNTIME_DIR/wheelguard.sock)")
    shot = ap.add_argument_group("screenshots (development)")
    shot.add_argument("--screenshot", metavar="PNG", help="render the window to PNG after a delay, then quit")
    shot.add_argument("--page", choices=("scroll", "pointer", "about"), default="scroll")
    shot.add_argument("--advanced", action="store_true", help="open the Advanced expander")
    shot.add_argument("--theme", choices=("dark", "light"), help="force a theme instead of following GNOME")
    shot.add_argument("--size", default="1008x708", help="window size WxH including shadow margin")
    shot.add_argument("--delay-ms", type=int, default=1500)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        from PySide6.QtCore import QTimer, QUrl
        from PySide6.QtGui import QFont, QGuiApplication, QIcon
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuickControls2 import QQuickStyle
    except ImportError:
        print("wheelguard-settings needs PySide6: sudo dnf install python3-pyside6", file=sys.stderr)
        return 1

    from .backend import Appearance, DaemonClient, PointerSettings

    QQuickStyle.setStyle("Basic")
    QGuiApplication.setApplicationName("Wheelguard")
    QGuiApplication.setApplicationDisplayName("Wheelguard")
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QGuiApplication(sys.argv[:1])
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        # Headless runs have no platform theme: use the GNOME UI font so screenshots match the desktop.
        from PySide6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        for name in ("Adwaita Sans", "Cantarell"):
            if name in families:
                app.setFont(QFont(name, 11))
                break

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

    engine.addImportPath(str(QML_DIR))
    ctx = engine.rootContext()
    ctx.setContextProperty("daemon", daemon)
    ctx.setContextProperty("pointer", pointer)
    ctx.setContextProperty("appearance", appearance)
    ctx.setContextProperty("iconUrl", QUrl.fromLocalFile(str(icon_path)))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    if not engine.rootObjects():
        return 1
    win = engine.rootObjects()[0]

    w, h = (int(v) for v in args.size.lower().split("x"))
    win.setWidth(w)
    win.setHeight(h)
    win.setProperty("pageIndex", {"scroll": 0, "pointer": 1, "about": 2}[args.page])
    if args.advanced:
        win.setProperty("advancedOpen", True)

    pointer.refresh()
    appearance.watch()
    daemon.start()

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app.aboutToQuit.connect(pointer.flush)
    app.aboutToQuit.connect(appearance.shutdown)

    if args.screenshot:
        def grab():
            img = win.grabWindow()
            ok = img.save(args.screenshot)
            print(f"screenshot {'saved' if ok else 'FAILED'}: {args.screenshot} {img.width()}x{img.height()}", flush=True)
            app.exit(0 if ok else 2)
        QTimer.singleShot(args.delay_ms, grab)

    return app.exec()
