# Razer DeathAdder V4 Pro protocol

What Wheelguard sends to the DeathAdder V4 Pro, and where it comes from. Razer doesn't publish this.
Everything here follows the [OpenRazer](https://github.com/openrazer/openrazer) kernel driver
(`driver/razermouse_driver.c`, `razerchromacommon.c`), which supports this mouse. **None of it has been
checked on the physical mouse by this project yet.**

## Device

| | USB ID | Notes |
|---|---|---|
| Wired | `1532:00BE` | |
| Wireless (HyperSpeed / HyperPolling dongle) | `1532:00BF` | |

Settings travel as a 90-byte Feature report (report ID 0) on **USB interface 0**: hidraw
`HIDIOCSFEATURE(91)` with a leading `0x00`, wait 31 ms (OpenRazer's wait for this mouse), then
`HIDIOCGFEATURE(91)`; the reply's 90 bytes start at offset 1.

## Report

| Offset | Field |
|---|---|
| 0 | status: request 0; reply 1 busy (treated as success, like OpenRazer), 2 ok, 3 failed, 4 timeout (wireless mouse unreachable), 5 not supported |
| 1 | transaction id, `0x1F` for this mouse |
| 2-3 | remaining packets (0) |
| 4 | protocol type (0) |
| 5 | argument length |
| 6, 7 | command class, command id (bit 7 of the id = read) |
| 8-87 | arguments |
| 88 | XOR of bytes 2-87 |
| 89 | 0 |

A reply must echo bytes 2-3, 6 and 7, else it is retried (5 attempts, 10 ms apart).

## Commands Wheelguard uses (the complete allow-list, `hw/razer.py`)

| Class:id | Len | Use | Arguments |
|---|---|---|---|
| 00:81 | 2 | firmware version | reply `major, minor` |
| 00:C0 | 1 | read polling rate | reply arg 1 = code |
| 00:40 | 2 | set polling rate | `00 code`, then again `01 code` (OpenRazer does both) |
| 04:86 | 0x26 | read DPI stages | `01` (stored); reply `01, active (1-based), count, count × [n, X hi, X lo, Y hi, Y lo, 0, 0]` |
| 04:06 | 0x26 | set DPI stages | `01, active (1-based), count, count × [n (0-based), X hi, X lo, Y hi, Y lo, 0, 0]` |
| 04:05 | 7 | set current DPI | `01, X hi, X lo, Y hi, Y lo, 0, 0` (sent after 04:06 so the active stage takes effect now) |
| 07:80 | 2 | battery | reply arg 1 = 0-255 |
| 07:84 | 2 | charging | reply arg 1 = 1 while charging |
| 07:83 / 07:03 | 2 | sleep timer | seconds, big-endian, 60-900 |
| 07:81 / 07:01 | 1 | low-battery threshold | 0x0C-0x3F (of 255) |
| 02:8C | 0x0A or 0x50 | read a button's action | `01 (profile), button, 00 (layer)`; reply `01, button, 00, function, size, 5 data bytes` |
| 02:0C | 0x0A or 0x50 | set a button's action | `01, button, 00, function, size, data` |

Polling codes: `01` 8000, `02` 4000, `04` 2000, `08` 1000, `10` 500, `40` 125 Hz. DPI 100-45000.

Every write is checked against these ranges before it is sent, and writes are refused until the
mouse has answered a firmware read. Not used: device mode (00:04), lighting, the dongle's indicator
LED, keys, macros and HyperShift on buttons, and the lift-off / asymmetric cut-off settings
(OpenRazer doesn't document them for this mouse).

## Button actions

OpenRazer doesn't send the button commands. Their layout comes from
[razerctl](https://github.com/Dromares/razerctl) (`docs/onboard-button-protocol.md`, Synapse captures,
a write verified on a Viper Mini SE) and the OpenMouse project's Naga V2 HyperSpeed report. **Neither
is this mouse**, so this is the least certain part of the Razer support.

* Buttons: 1 left, 2 right, 3 middle, 4 back, 5 forward. Profile 1 is the mouse's default profile;
  layer 0 is the normal layer (1 = HyperShift).
* Actions Wheelguard writes: function 1 (mouse button), size 1, data = the button (1-5); function 6
  (DPI), size 1, data 6 (cycle up). Anything else read from the mouse (keys, macros, DPI up/down...)
  is shown as custom and only replaced when the user picks a new action.
* The two captures disagree on the size byte (0x0A for the Naga, 0x50 for the Viper), so the reads try
  0x0A first, then 0x50, and the writes use whichever the mouse answered. If neither works, the page
  says the mouse didn't report its button actions and everything else stays editable.
* A change that would leave no button on left click is refused.

## DPI popup

No DPI-change event is documented for this mouse, so while the DPI popup is on Wheelguard reads the
stages (04:86) every 1.5 seconds and announces a new active stage. If the mouse can't be reached
(asleep), it waits 5, 15, then 60 seconds before trying again.

## What Wheelguard stores

The session works on a 72-byte record built from the reads above (layout in `hw/razer.py`; version 2,
with the five button assignments). It is the backup format
(`~/.config/wheelguard/backups/razer-deathadder-v4-pro-<fw>-<time>.bin`) and is never sent to the mouse
as such. Backups from Wheelguard 1.6 are the 32-byte version 1 record without buttons; restoring one
puts back everything it holds and leaves the buttons as they are.
