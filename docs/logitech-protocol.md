# Logitech G Pro X Superlight protocol

What Wheelguard sends to the first-generation G Pro X Superlight, and where it comes from. Logitech
documents HID++ 2.0 only partly; the onboard-profile details follow
[libratbag](https://github.com/libratbag/libratbag) (`src/hidpp20.c`, used by Piper) and
[Solaar](https://github.com/pwr-Solaar/Solaar) (`lib/logitech_receiver/hidpp20.py`). Both support this
mouse. **None of it has been checked on the physical mouse by this project yet.**

## Device

| | USB / HID ID | Node Wheelguard opens |
|---|---|---|
| Wireless | receiver `046D:C547`, mouse `046D:4093` | the HID device hid-logitech-dj creates for the mouse (`0003:046D:4093.*`) |
| Wired | `046D:C094` | the interface whose descriptor has the HID++ reports (usage page 0xFF00, report 0x11) |

Requests are HID++ long reports (`0x11`, 20 bytes) with device index `0xFF`, software id `0x0A`,
written to the hidraw node; replies are read back and matched on index, feature index, function and
software id. Pointer reports and notifications on the same node are skipped. Error replies:
`FF <feature index> <fn|sw> <code>` (HID++ 2.0) and `8F ...` (receiver: mouse unreachable).

## Features used (the complete allow-list, `hw/hidpp.py`)

| Feature | Functions | Use |
|---|---|---|
| 0x0000 Root | 0 getFeature, 1 ping | feature indexes, link check |
| 0x0003 Firmware | 0, 1 | firmware version |
| 0x1004 / 0x1000 / 0x1001 | status | battery, whichever the mouse has (the Gen 1 reports voltage, 0x1001; percent uses Solaar's curve) |
| 0x8100 Onboard profiles | 0 info, 2 get mode, 4 current profile, 5 memory read, 0xB current DPI index | reading |
| 0x8100 | 1 set mode (onboard only), 3 select profile, 6/7/8 memory write, 0xC set DPI index | writing |

## Onboard profiles

`0x8100` function 0 gives the memory layout: model, profile format, macro format, profile count, ROM
profile count, buttons, writable sector count, sector size (big-endian). Wheelguard only writes when
the memory model is 1 and the profile format is one libratbag parses (1-5).

* Sector `0x0000` is the profile directory: 4 bytes per profile (`sector hi, sector lo, enabled, 0`),
  ended by `FF FF 00 00`, padded with `FF`. Sectors `0x0001..` hold user profiles; `0x0101..` are
  read-only factory (ROM) profiles.
* Every sector ends with a CRC-16/CCITT (poly 0x1021, init 0xFFFF) of the rest, big-endian.
* Profile layout (the part Wheelguard edits): byte 0 report interval in ms (1, 2, 4, 8), byte 1 default
  DPI slot, byte 2 DPI-shift slot, bytes 3-12 five DPI values (little-endian, 0 = unused). Everything
  else (buttons, name, lighting, power settings) is copied unchanged.
* Reads are 16 bytes at a time; the last read is aligned to the end of the sector (the firmware
  refuses reads past it). A write is: address (sector, 0, size) -> 16-byte chunks -> end.

## The rest of the Logitech family

`hw/models.py` (`LOGI_LINKS`) lists more Logitech G mice that libratbag drives with the same HID++ 2.0
onboard profiles (product ids from libratbag's MIT-licensed device files): G Pro Wireless, G Pro Hero,
G305, G102 / G203, G303 Shroud Edition, G403 Hero, G502 Hero, G502 Lightspeed, G502 X, G502 X
Lightspeed, G502 X Plus, G604, G703 Hero, G705 and G903 Hero. They are reached exactly like the
Superlight: through the HID device hid-logitech-dj creates for the mouse behind its receiver, or over the
cable. Only mice whose fastest rate is 1000 Hz are listed, since faster ones keep the rate outside the
profile. Per model Wheelguard records the DPI range of its sensor; the profile layout checks above apply
unchanged, so a mouse that reports another memory model or profile format stays read-only.

These models start **read-only** (`Model.confirmed = False`): Wheelguard reads settings and battery but
refuses every write, in the driver, the HID++ transport and the session, until someone with that mouse
confirms the readings match G HUB. Their input nodes are granted only where udev marks them as a pure
mouse (`ID_INPUT_MOUSE`, never `ID_INPUT_KEYBOARD`).

## What Wheelguard does

* **Read**: mode, directory, current profile, that profile's sector, current DPI index.
* **Apply**: change the profile in a copy, recompute the CRC, write that one sector, read it back.
  Switching the active stage only sets the current DPI index (no flash write). If the mouse was put
  in host mode (by G HUB or Solaar), it is switched back to onboard mode first, or the profile
  wouldn't be used.
* **Factory mouse** (no valid directory, running a ROM profile): the first Apply writes the edited
  ROM profile to sector 1, writes a directory that enables it, and selects it, which is what
  libratbag does.
* **Refused**: a ROM profile selected while saved profiles exist, a profile with a bad CRC or values
  out of range, an unknown memory layout, a profile list that can't be read while a profile is saved, an
  unclear answer about which profile is active.

Backups are the profile sector plus the DPI-index byte, in
`~/.config/wheelguard/backups/logitech-pro-x-superlight-<fw>-p<sector>-<time>.bin`
(`p0001` for profile 1; a factory mouse's ROM profile is filed under `p0001` too, the sector its first write
fills).

Not used: host-mode settings (0x2201 DPI, 0x8060 report rate), button remapping, macros, lighting,
pairing and everything else.
