"""Per-game profiles, Qt side: listens to the focused app (reported by the panel extension through the
service), runs the ProfileEngine and applies what it decides: scroll protection and Click-Lock as temporary
settings in the service (never written to config.json, and gone by themselves if this app goes away), the
DPI stage through MouseController.setActiveStage (it lives on the mouse, so it keeps the crash journal).

QML sees it as `profiles`. The pure logic and the file formats live in profiles.py and desktopapps.py.
"""
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QIcon, QImage
from PySide6.QtQuick import QQuickImageProvider

from . import desktopapps, profiles, protocol
from .appsettings import config_dir

SERVICE_KEYS = ("protection", "clicklock")      # applied as temporary settings in the service
RECENT_MAX = 6
OWN_APP = "io.github.djshiye.Wheelguard"
PROVIDER_ID = "appicon"
ICON_MAX = 256


class AppIconProvider(QQuickImageProvider):
    """image://appicon/<icon name or absolute path>: an app's icon from the theme (or a file) for the list."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, name, size, requested):
        from urllib.parse import unquote
        key = unquote(name)[:200]
        side = max(16, min(ICON_MAX, requested.width() if requested.width() > 0 else 64))
        icon = QIcon(key) if key.startswith("/") else QIcon.fromTheme(key)
        if icon.isNull():
            return QImage()
        img = icon.pixmap(side, side).toImage()
        size.setWidth(img.width())
        size.setHeight(img.height())
        return img


def _usable_icon(icon: str) -> str:
    """The icon name if the theme (or the file) really has it, so the list never asks for a missing one."""
    if not icon:
        return ""
    if icon.startswith("/"):
        return icon if Path(icon).is_file() else ""
    return icon if QIcon.hasThemeIcon(icon) else ""


class ProfileController(QObject):
    changed = Signal()              # the list, the active profile or the switch changed
    activeChanged = Signal()

    def __init__(self, daemon, mouse, settings, ext=None, config_home: Optional[Path] = None, parent=None):
        super().__init__(parent)
        base = Path(config_home) / "wheelguard" if config_home else config_dir()
        self._store = profiles.ProfileStore(base / profiles.FILE)
        self._journal = base / profiles.JOURNAL_FILE
        self._daemon, self._mouse, self._settings, self._ext = daemon, mouse, settings, ext
        self._engine = profiles.ProfileEngine()
        self._items = []
        self._apps = None
        self._demo = False
        self._focus = ""
        self._error = ""
        self._reload()
        self._engine.seed(profiles.journal_load(self._journal))     # a previous run died with a profile engaged
        self._journal_last = self._engine.restore_values()
        self._recent: list = []                 # app ids that had the focus lately, newest first (never titles)
        if hasattr(daemon, "reconnected"):
            daemon.reconnected.connect(lambda: self._engine.expect(SERVICE_KEYS))
        daemon.configChanged.connect(self._tick)
        daemon.stateChanged.connect(self._tick)
        mouse.trayChanged.connect(self._tick)
        settings.changed.connect(self._on_switch)
        if ext is not None:
            ext.changed.connect(lambda _on: self.changed.emit())
        self._was_on = self.enabled
        self._tick()

    # ---- live settings
    def _current(self) -> Dict[str, object]:
        d, m = self._daemon, self._mouse
        cur: Dict[str, object] = {"protection": None, "clicklock": None, "stage": None}
        if d.state in protocol.CONFIG_STATES:
            cur["protection"] = (int(d.preMs), int(d.holdMs), int(d.lockMs))
            cur["clicklock"] = bool(d.clickLock)
        if m.stagesEnabled and m.activeStage >= 0:
            cur["stage"] = int(m.activeStage)
        return cur

    @staticmethod
    def _config(values: Dict[str, object]) -> Dict[str, object]:
        """Engine values ("protection", "clicklock") as service config keys."""
        out: Dict[str, object] = {}
        if "protection" in values:
            out.update(zip(("pre_ms", "hold_ms", "lock_ms"), (int(v) for v in values["protection"]), strict=True))
        if "clicklock" in values:
            out["clicklock"] = bool(values["clicklock"])        # a running lock ends when this goes false
        return out

    def _apply(self, plan: Dict[str, object], saved: bool = False):
        """Carry out what the engine decided. Scroll protection and Click-Lock are not taken from the plan:
        the service is simply told which temporary settings are in force now (`_sync_overlay`), and leaving
        a profile ends them. `saved=True` is only for values a run before 1.7.0 left written in config.json
        (the crash journal): those are written back for good."""
        skipped = []
        for key, value in plan.items():
            if key == "stage":
                if not self._mouse.stagesEnabled:
                    skipped.append(key)         # busy, asleep or unsaved edits: try again later, never force it
                elif 0 <= int(value) < len(self._mouse.stageLabels):
                    self._mouse.setActiveStage(int(value))
        if saved:
            patch = self._config({k: v for k, v in plan.items() if k in SERVICE_KEYS})
            if patch and self._daemon.state in protocol.CONFIG_STATES:
                self._daemon.setValues(patch)
        if skipped:
            self._engine.skipped(skipped)
        self._sync_overlay()

    def _sync_overlay(self):
        self._daemon.setOverride(self._config(self._engine.overlay(SERVICE_KEYS)))

    def _save_journal(self):
        """Keep the crash-recovery journal equal to what restore_values() says now."""
        # Only the DPI stage needs handing back after a crash: the service drops its temporary settings by itself.
        want = {k: v for k, v in self._engine.restore_values().items()
                if k not in SERVICE_KEYS or k in self._engine._seed}
        if want != self._journal_last:
            self._journal_last = want
            profiles.journal_save(self._journal, want)

    @Slot()
    def _tick(self):
        cur = self._current()
        self._engine.observe(cur)
        seeded = self._engine.seed_plan(cur)
        if seeded:
            self._apply(seeded, saved=True)
        if self._engine.has_retry():
            plan = self._engine.retry_plan(cur)
            if plan:
                self._apply(plan)
        self._sync_overlay()            # a setting changed by hand leaves the temporary set
        self._save_journal()

    def _on_switch(self):
        on = self.enabled
        if on == self._was_on:
            return
        self._was_on = on
        if on:
            self._engage_focus()
        else:
            self._apply(self._engine.release(self._current()))
            self._after_change()
        self.changed.emit()

    def _engage_focus(self):
        self._apply(self._engine.focus(self._focus, self._current()))
        self._after_change()

    def _after_change(self):
        self._save_journal()
        self.activeChanged.emit()
        self.changed.emit()

    # ---- focus from the panel extension (via the service)
    @Slot(str)
    def focusChanged(self, app_id: str):
        self._focus = profiles.clean_app_id(app_id)
        if self._focus and not self._demo:
            self._recent = ([self._focus] + [a for a in self._recent if a != self._focus])[:RECENT_MAX]
        if self.enabled:
            self._engage_focus()

    def shutdown(self):
        """The app is quitting: hand everything back. Anything that can't be done now is left in the journal."""
        self._apply(self._engine.release(self._current()))
        self._save_journal()        # cleared unless something couldn't be handed back (it stays for the next start)
        self._daemon.flushSocket()

    # ---- the list
    def _reload(self):
        found = self._store.load()
        self._engine.set_profiles({p.app_id: profiles.engine_values(p) for p in found})
        self._items_raw = found
        self._items = None
        self.changed.emit()

    def _apps_list(self):
        if self._apps is None:
            self._apps = [dict(a, icon=_usable_icon(a["icon"])) for a in desktopapps.list_apps()]
        return self._apps

    @Slot()
    def refreshApps(self):
        if not self._demo:
            self._apps = None
        self._items = None
        self.changed.emit()

    @Slot(str, result="QVariantList")
    def installedApps(self, query: str):
        have = {p.app_id for p in self._items_raw}
        return [a for a in self._apps_list() if a["id"] not in have and desktopapps.matches(a, query)][:200]

    @Slot(result="QVariantList")
    def recentApps(self):
        """Apps that had the focus lately and have no launcher entry: games started from Lutris, Heroic or
        Wine. The panel icon reports them by their window's app class (never a title), which is what a
        profile is matched by, so one of these can get a profile although the app list doesn't have it."""
        known = {a["id"] for a in self._apps_list()}
        have = {p.app_id for p in self._items_raw}
        return [{"id": a, "name": desktopapps.display_name(a, ()), "icon": ""}
                for a in self._recent if a not in known and a not in have and not a.startswith(OWN_APP)]

    def _build_items(self):
        apps = self._apps_list()
        by_id = {a["id"]: a for a in apps}
        out = []
        for p in self._items_raw:
            a = by_id.get(p.app_id)
            out.append({"appId": p.app_id, "name": desktopapps.display_name(p.app_id, apps),
                        "icon": a["icon"] if a else "", "summary": profiles.summary(p),
                        "protection": p.protection or "", "clicklock": "keep" if p.clicklock is None
                        else ("on" if p.clicklock else "off"),
                        "stage": -1 if p.stage is None else p.stage})
        return out

    @Slot(str, result=str)
    def nameFor(self, app_id: str) -> str:
        return desktopapps.display_name(app_id, self._apps_list())

    @Slot(str, result=str)
    def iconFor(self, app_id: str) -> str:
        return next((a["icon"] for a in self._apps_list() if a["id"] == app_id), "")

    @Slot(str, str, str, int, result=str)
    def saveProfile(self, app_id: str, protection: str, clicklock: str, stage: int) -> str:
        """protection: a preset name or "keep"; clicklock: "on", "off" or "keep"; stage: 0-based or -1 for keep."""
        p = profiles.make_profile(app_id, protection, clicklock, stage if stage >= 0 else None)
        if p is None:
            self._error = "Choose at least one change for this app."
        else:
            self._error = self._store.save(p)
        if not self._error:
            self._reload()
            self._apply(self._engine.refresh(self._current()))
            self._after_change()
        else:
            self.changed.emit()
        return self._error

    @Slot(str)
    def removeProfile(self, app_id: str):
        self._error = self._store.delete(app_id)
        self._reload()
        self._apply(self._engine.refresh(self._current()))      # deleting the active profile hands settings back
        self._after_change()

    @Slot()
    def clearError(self):
        if self._error:
            self._error = ""
            self.changed.emit()

    def demo(self):
        """Screenshot harness: a few example profiles, one of them engaged."""
        self._store.save(profiles.Profile("steam_app_730.desktop", "Strong", False, 1))
        self._store.save(profiles.Profile("org.gnome.Nautilus.desktop", None, True, None))
        self._store.save(profiles.Profile("com.valvesoftware.Steam.desktop", "Light", None, 0))
        self._reload()
        names = {"steam_app_730.desktop": "Counter-Strike 2", "org.gnome.Nautilus.desktop": "Files",
                 "com.valvesoftware.Steam.desktop": "Steam", "org.blender.Blender.desktop": "Blender",
                 "org.mozilla.firefox.desktop": "Firefox", "org.gimp.GIMP.desktop": "GIMP",
                 "com.obsproject.Studio.desktop": "OBS Studio", "org.kde.kdenlive.desktop": "Kdenlive"}
        self._demo = True
        self._apps = [{"id": k, "name": v, "icon": ""} for k, v in sorted(names.items(), key=lambda kv: kv[1])]
        self._focus = "steam_app_730.desktop"
        self._recent = ["heroic-hades.exe", "steam_app_730.desktop"]
        self._engage_focus()

    # ---- properties
    def _get_items(self):
        if self._items is None:
            self._items = self._build_items()
        return self._items

    enabled = Property(bool, lambda s: s._settings.profilesEnabled, notify=changed)
    items = Property("QVariantList", _get_items, notify=changed)
    count = Property(int, lambda s: len(s._items_raw), notify=changed)
    maxProfiles = Property(int, lambda s: profiles.MAX_PROFILES, constant=True)
    activeId = Property(str, lambda s: s._engine.active or "", notify=activeChanged)
    activeName = Property(str, lambda s: s.nameFor(s._engine.active) if s._engine.active else "", notify=activeChanged)
    panelActive = Property(bool, lambda s: bool(s._ext is not None and s._ext.active), notify=changed)
    error = Property(str, lambda s: s._error, notify=changed)
