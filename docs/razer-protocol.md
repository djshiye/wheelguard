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

Polling codes: `01` 8000, `02` 4000, `04` 2000, `08` 1000, `10` 500, `40` 125 Hz. DPI 100-45000.

Every write is checked against these ranges before it is sent, and writes are refused until the
mouse has answered a firmware read. Not used: device mode (00:04), lighting, the dongle's indicator
LED, button remapping, and the lift-off / asymmetric cut-off settings (OpenRazer doesn't document
them for this mouse).

## The rest of the Razer family

`hw/models.py` (`RAZER_LINKS`) lists more Razer mice that the OpenRazer driver handles with the same
transaction id (0x1F) and the same DPI stage, battery, sleep and low-battery commands. Per USB product
id it records only what differs:

- the polling-rate command: "hyper" (00:C0 / 00:40 above) or "classic", 00:85 to read (reply arg 0)
  and 00:05 to set (one byte: `01` 1000, `02` 500, `08` 125 Hz). Wheelguard keeps the "hyper" code in
  its record either way and translates for classic mice. Each link only accepts its own command;
- how long to wait before reading the reply (31 ms, or 59.9 ms for the mice OpenRazer gives the Viper
  receiver timing);
- the DPI range and the polling rates the connection offers.

These models start **read-only** (`Model.confirmed = False`): Wheelguard reads settings and battery
but refuses every write, in the driver and in the session, until someone with that mouse confirms
the readings match Razer Synapse. Their input nodes are granted only where udev marks them as a pure
mouse (`ID_INPUT_MOUSE`, never `ID_INPUT_KEYBOARD`).

## What Wheelguard stores

The session works on a 32-byte record built from the reads above (layout in `hw/razer.py`). It is the
backup format (`~/.config/wheelguard/backups/razer-deathadder-v4-pro-<fw>-<time>.bin`) and is never
sent to the mouse as such.
