"""Hardware access to the supported mice's vendor HID channels. Pure Python, no Qt.

Endgame Gear XM2w 4k v2 (docs/endgame-protocol.md), Logitech G Pro X Superlight
(docs/logitech-protocol.md) and Razer DeathAdder V4 Pro (docs/razer-protocol.md). Nothing here has been
validated on hardware yet, so every layer is defensive: an allow-list of commands per transport
(endgame.py, hidpp.py, razer.py), a write gate on the identified mouse, diff-only writes and a
one-time backup of the mouse's settings before the first write (session.py).
"""
