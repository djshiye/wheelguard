"""Hardware access to the mouse's vendor HID channel. Pure Python, no Qt.

See docs/endgame-protocol.md. Nothing here has been validated on hardware yet, so every layer is
defensive: an allow-list of commands (endgame.py), a write gate on the mouse PID, diff-only writes
(codec.py) and a one-time backup of the mouse's settings before the first write (session.py).
"""
