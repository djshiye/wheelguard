""""Allow this mouse": ask for administrator rights once and give the logged-in user access to a mouse
Wheelguard doesn't know, so the service can protect it. QML sees this as `mouseAccess`.

The work is done by /usr/libexec/wheelguard-allow-mouse (build-aux/wheelguard-allow-mouse), started through
pkexec, which shows the system's own password dialog. This side only passes the action and the mouse's
"vvvv:pppp" id, and reads the rules file (world-readable) to know which mice were allowed.
"""
import os
import re
import shutil
from typing import Callable, List, Optional

from PySide6.QtCore import Property, QObject, QProcess, Signal, Slot

from .. import mice

HELPER = "/usr/libexec/wheelguard-allow-mouse"
RULES_PATH = mice.ALLOWED_RULES
ID_RE = re.compile(r"[0-9a-f]{4}:[0-9a-f]{4}")
PKEXEC_DISMISSED, PKEXEC_DENIED = 126, 127      # pkexec: dialog closed / not authorised
TIMEOUT_MS = 120000                             # the password dialog may sit there for a while

CANCELLED = ""                                  # closing the password dialog is not an error
DENIED = "Administrator rights are needed to allow a mouse."
NOT_INSTALLED = "This needs the installed Wheelguard package (the helper wasn't found)."
FAILED = "Couldn't allow this mouse."


def valid_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def allowed_ids(path: str = RULES_PATH) -> List[str]:
    """The mice the user allowed, from the rules file the helper keeps ([] when there is none)."""
    return mice.allowed_ids(path)


def result_text(code: int, stderr: str) -> str:
    """What to show for a finished helper run ("" = nothing to say: it worked, or the user cancelled)."""
    if code == 0 or code == PKEXEC_DISMISSED:
        return CANCELLED
    if code == PKEXEC_DENIED:
        return DENIED
    line = next((ln.strip() for ln in stderr.splitlines() if ln.strip()), "")
    return line[:300] if line else FAILED


class MouseAccess(QObject):
    changed = Signal()
    finished = Signal(str, str, bool)       # action ("add" / "remove"), mouse id, worked

    def __init__(self, parent=None, helper: str = HELPER, rules_path: str = RULES_PATH,
                 which: Callable[[str], Optional[str]] = shutil.which):
        super().__init__(parent)
        self._helper, self._rules, self._which = helper, rules_path, which
        self._proc: Optional[QProcess] = None
        self._busy = False
        self._error = ""
        self._allowed = allowed_ids(rules_path)

    def _available(self) -> bool:
        return os.access(self._helper, os.X_OK) and self._which("pkexec") is not None

    def _start(self, action: str, ident: str):
        if self._busy or not valid_id(ident):
            return
        if not self._available():
            self._error = NOT_INSTALLED
            self.changed.emit()
            return
        self._busy, self._error = True, ""
        self.changed.emit()
        proc = QProcess(self)
        proc.setProgram("pkexec")
        proc.setArguments([self._helper, action, ident])
        proc.finished.connect(lambda code, _status, p=proc: self._done(p, action, ident, code))
        proc.errorOccurred.connect(lambda _e, p=proc: self._done(p, action, ident, -1))
        self._proc = proc
        proc.start()

    def _done(self, proc: QProcess, action: str, ident: str, code: int):
        if proc is not self._proc:
            return
        self._proc = None
        err = bytes(proc.readAllStandardError()).decode(errors="replace")
        proc.deleteLater()
        self._busy = False
        self._error = FAILED if code == -1 else result_text(code, err)
        self._allowed = allowed_ids(self._rules)
        self.changed.emit()
        self.finished.emit(action, ident, code == 0)

    @Slot(str)
    def allow(self, ident: str):
        """Ask for the administrator password and let the user read this mouse."""
        self._start("add", ident)

    @Slot(str)
    def remove(self, ident: str):
        """Take the access back."""
        self._start("remove", ident)

    @Slot(str, result=bool)
    def isAllowed(self, ident: str) -> bool:
        return ident in self._allowed

    @Slot()
    def clearError(self):
        if self._error:
            self._error = ""
            self.changed.emit()

    available = Property(bool, lambda s: s._available(), constant=True)
    busy = Property(bool, lambda s: s._busy, notify=changed)
    error = Property(str, lambda s: s._error, notify=changed)
    allowed = Property("QStringList", lambda s: list(s._allowed), notify=changed)
