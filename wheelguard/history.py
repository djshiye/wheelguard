"""Wheel health over time: daily totals of wheel ticks and blocked (dropped) ticks.

Kept in ~/.local/share/wheelguard/history.json (XDG_DATA_HOME), 90 days of `{date, ticks, dropped}`:

    {"version": 1, "days": [{"date": "2026-11-09", "ticks": 5120, "dropped": 61}, ...]}

Written like config.save (temp file in the same directory, then rename), at most every few minutes
while ticks keep coming, when the mouse goes away, and on exit. A missing, unreadable or corrupt file
just means starting over; it never stops the daemon. Dates are local dates.
"""
import datetime
import json
import logging
import math
import os
import tempfile
import time
from pathlib import Path

log = logging.getLogger("wheelguard")

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "wheelguard"
HISTORY_PATH = DATA_DIR / "history.json"
KEEP_DAYS = 90
SAVE_EVERY_S = 300
MAX_COUNT = 10 ** 12            # anything bigger in a file is not a tick count


def _count(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
        return None
    return min(int(v), MAX_COUNT)


def _date(v):
    try:
        return datetime.date.fromisoformat(v) if isinstance(v, str) and len(v) == 10 else None
    except ValueError:
        return None


class History:
    def __init__(self, path=None, clock=time.time, save_every=SAVE_EVERY_S, keep_days=KEEP_DAYS):
        self.path = Path(path or HISTORY_PATH)
        self.clock = clock
        self.save_every = save_every
        self.keep_days = keep_days
        self.days = {}                  # "YYYY-MM-DD" -> [ticks, dropped]
        self.dirty = False
        self._last_save = clock()
        self.load()

    # ---------- time
    def today(self):
        return datetime.date.fromtimestamp(self.clock())

    def _prune(self):
        today = self.today()
        oldest = today - datetime.timedelta(days=self.keep_days - 1)
        newest = today + datetime.timedelta(days=1)         # a clock set wrongly must not pile up future days
        for key in list(self.days):
            d = _date(key)
            if d is None or not oldest <= d <= newest:
                del self.days[key]
                self.dirty = True

    # ---------- counting
    def add(self, ticks, dropped):
        """Count ticks (and how many of them were blocked) for today."""
        if ticks <= 0 and dropped <= 0:
            return
        day = self.days.setdefault(self.today().isoformat(), [0, 0])
        day[0] += max(0, ticks)
        day[1] += max(0, dropped)
        self.dirty = True

    def to_list(self):
        """The days oldest first as [{"date", "ticks", "dropped"}], for the IPC reply."""
        return [{"date": k, "ticks": v[0], "dropped": min(v[1], v[0])} for k, v in sorted(self.days.items())]

    # ---------- file
    def load(self):
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as ex:                 # unreadable, not JSON, not UTF-8
            self._set_aside(ex)
            return
        rows = data.get("days") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            self._set_aside("not a history file")
            return
        for row in rows:
            if not isinstance(row, dict):
                continue
            d, ticks, dropped = _date(row.get("date")), _count(row.get("ticks")), _count(row.get("dropped"))
            if d is None or ticks is None or dropped is None:
                continue
            day = self.days.setdefault(d.isoformat(), [0, 0])
            day[0] += ticks
            day[1] += min(dropped, ticks)
        self._prune()

    def _set_aside(self, why):
        """Keep a corrupt file for inspection (best effort) and start fresh."""
        log.warning("ignoring history file %s: %s", self.path, why)
        try:
            os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
        except OSError:
            pass

    def save(self, durable=False):
        """Write the file now (atomically). Returns False, and keeps the data, when that fails.

        `durable` also fsyncs before the rename: wanted on exit only. The periodic saves run on the
        input thread, where a disk flush could stall the mouse for a moment, and a crash that loses a
        few minutes of counts is no harm (the rename keeps the old file whole)."""
        if not self.dirty:
            return True
        self._last_save = self.clock()
        self._prune()
        path = self.path
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "days": self.to_list()}, f, indent=1)
                f.write("\n")
                f.flush()
                if durable:
                    os.fsync(f.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except OSError as ex:
            log.warning("cannot write %s: %s", path, ex)
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            return False
        self.dirty = False
        return True

    def maybe_save(self):
        """Call often: saves when there is something new and the last save is a few minutes old."""
        if self.dirty and self.clock() - self._last_save >= self.save_every:
            return self.save()
        return True
