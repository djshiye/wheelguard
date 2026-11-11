"""A plain-text desktop notification through `gdbus call`. No Qt, so a keyboard-shortcut command can say what
it did without loading the app's toolkit."""
import shutil
import subprocess

APP_ID = "io.github.djshiye.Wheelguard"
NOTIFY_NAME = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"


def notification_args(text: str, summary: str = "Wheelguard") -> list:
    """`gdbus call` arguments for org.freedesktop.Notifications.Notify. gdbus fills in the exact wire types
    from the service's introspection (the replaces-id must be unsigned, which QtDBus cannot send from Python)."""
    return ["call", "--session", "--dest", NOTIFY_NAME, "--object-path", NOTIFY_PATH,
            "--method", NOTIFY_NAME + ".Notify", "Wheelguard", "0", APP_ID, summary, text, "[]",
            "{'desktop-entry': <'" + APP_ID + "'>}", "-1"]


def send(text: str, summary: str = "Wheelguard") -> bool:
    """Fire and forget. False when it could not be started."""
    exe = shutil.which("gdbus")
    if exe is None:
        return False
    try:
        subprocess.Popen([exe, *notification_args(text, summary)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    return True
