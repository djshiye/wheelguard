"""What the tray icon shows, as plain data: menu rows, tooltip and the DPI-stage rules. No Qt."""
from dataclasses import dataclass
from typing import Dict, List, Tuple

ONLINE_STATES = ("ready", "applying", "reading")       # mouse states in which a mouse has been seen
REASON_OFFLINE = "Mouse not connected"
REASON_EDITS = "Unsaved changes on the Mouse page"
REASON_READONLY = "This mouse's settings are read-only"
REASON_READING = "Reading the mouse…"


@dataclass(frozen=True)
class TrayState:
    daemon_state: str = "connecting"
    protecting: bool = True
    online: bool = False            # a mouse was read and is reachable
    battery: int = -1
    charging: bool = False
    stages: Tuple[str, ...] = ()    # one label per configured DPI stage ("800", "800 × 400")
    active: int = -1
    stage_reason: str = ""          # why the stage rows are disabled; "" = switchable


@dataclass(frozen=True)
class Row:
    kind: str                       # "info" | "toggle" | "stage" | "action" | "separator"
    text: str = ""
    key: str = ""                   # what a click does: "protect" | "stage" | "popup" | "open" | "quit"
    index: int = -1
    checked: bool = False
    enabled: bool = True


def stage_labels(flat: Dict[str, object]) -> List[str]:
    """DPI label per configured stage from a codec.flat() dict; X != Y shows both axes."""
    out = []
    for k in range(int(flat.get("stage_count", 0))):
        x, y = flat.get(f"dpi_x{k}"), flat.get(f"dpi_y{k}")
        out.append(f"{x}" if x == y else f"{x} × {y}")
    return out


def is_online(state: str, have_blob: bool) -> bool:
    return have_blob and state in ONLINE_STATES


def stage_reason(state: str, have_blob: bool, writable: bool, staged: int) -> str:
    """Why DPI stages can't be switched right now ("" when they can). Staged edits are never sent from the tray."""
    if not is_online(state, have_blob):
        return REASON_READING if state in ("idle", "reading") else REASON_OFFLINE
    if not writable:
        return REASON_READONLY
    if staged:
        return REASON_EDITS
    return ""


def battery_text(online: bool, battery: int, charging: bool) -> str:
    if not online:
        return REASON_OFFLINE
    if battery < 0:
        return "Battery unknown"
    return f"Battery {battery}%" + (" · Charging" if charging else "")


def protection_word(daemon_state: str, protecting: bool) -> str:
    if daemon_state == "running":
        return "protection on" if protecting else "protection off"
    if daemon_state == "waiting":
        return "waiting for mouse"
    if daemon_state in ("stopped", "permission"):
        return "service not running"
    return "connecting"


def tooltip(s: TrayState) -> str:
    text = "Wheelguard — " + protection_word(s.daemon_state, s.protecting)
    if s.online and s.battery >= 0:
        text += f" · {s.battery}%"
    return text


def menu_rows(s: TrayState) -> List[Row]:
    """The right-click menu. Stage rows are radio rows; a blocked or absent mouse leaves them disabled."""
    rows = [Row("info", battery_text(s.online, s.battery, s.charging), enabled=False),
            Row("toggle", "Scroll protection", key="protect", checked=s.protecting,
                enabled=s.daemon_state in ("running", "waiting")),
            Row("separator")]
    if s.stages:
        for i, label in enumerate(s.stages):
            rows.append(Row("stage", f"{label} DPI", key="stage", index=i, checked=i == s.active,
                            enabled=not s.stage_reason))
        if s.stage_reason:
            rows.append(Row("info", s.stage_reason, enabled=False))
    else:
        rows.append(Row("info", "DPI: " + (s.stage_reason or REASON_OFFLINE), enabled=False))
    rows += [Row("separator"),
             Row("action", "Quick view", key="popup"),
             Row("action", "Open Wheelguard", key="open"),
             Row("action", "Quit", key="quit")]
    return rows
