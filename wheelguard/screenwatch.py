"""Notice the screen locking, from the daemon, so a running Click-Lock never carries on behind the lock
screen even when the app isn't running.

The daemon has no D-Bus library (and should not grow one), so it reads the output of `gdbus monitor`
(part of GLib, present on every GNOME system) watching org.gnome.ScreenSaver on the session bus. The child
only ever sends us text; one line matters: "... ActiveChanged (true,)". Where gdbus or the session bus is
missing this is a no-op, and the app's own watcher (gui/screenlock.py) still covers that case.
"""
import logging
import os
import re
import subprocess
import time

log = logging.getLogger("wheelguard")
COMMAND = ("gdbus", "monitor", "--session", "--dest", "org.gnome.ScreenSaver",
           "--object-path", "/org/gnome/ScreenSaver")
LOCKED = re.compile(rb"org\.gnome\.ScreenSaver\.ActiveChanged \(true,?\)")
RETRY_S = 30.0          # a watcher that ended (session bus restarted) is started again this much later at the earliest
MAX_PENDING = 64 * 1024


class ScreenWatch:
    def __init__(self, command=COMMAND, clock=time.monotonic):
        self._command, self._clock = tuple(command), clock
        self._proc = None
        self._buf = b""
        self._retry_at = 0.0
        self.start()

    def start(self):
        """Start the watcher unless it runs or was tried too recently. Never raises."""
        if self._proc is not None or self._clock() < self._retry_at:
            return
        self._retry_at = self._clock() + RETRY_S
        try:
            self._proc = subprocess.Popen(self._command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, env=dict(os.environ, LC_ALL="C"))
        except OSError as ex:
            log.info("not watching the screen lock (%s)", ex.strerror or ex)
            self._retry_at = float("inf")           # gdbus isn't there; it won't appear by waiting
            return
        os.set_blocking(self._proc.stdout.fileno(), False)
        self._buf = b""

    def fileno(self):
        """The fd to select() on, or None while no watcher runs."""
        return None if self._proc is None else self._proc.stdout.fileno()

    def drain(self):
        """Read what the watcher said; True if the screen locked."""
        if self._proc is None:
            return False
        locked = False
        try:
            while True:
                data = os.read(self._proc.stdout.fileno(), 4096)
                if not data:                        # the watcher ended (no session bus, bus restarted)
                    self.close()
                    break
                self._buf += data
                *lines, self._buf = self._buf.split(b"\n")
                locked = locked or any(LOCKED.search(line) for line in lines)
                if len(self._buf) > MAX_PENDING:
                    self._buf = b""
        except BlockingIOError:
            pass
        except OSError:
            self.close()
        return locked

    def close(self):
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdout.close()
            proc.terminate()
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
