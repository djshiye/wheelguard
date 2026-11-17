# Glorious mouse protocols

What Wheelguard sends to Glorious mice, and where it comes from. Glorious doesn't publish this. Both
protocols were decoded from the Glorious CORE 2.1.23 app (`out/main/index.js`, read as text, never run);
the line numbers below refer to that file. **None of it has been checked on a physical mouse by this
project yet.** Every Glorious model is listed with `confirmed=False` (`hw/models.py`), so Wheelguard reads
and never writes.

Neither protocol has a command that reads the DPI stages, the polling rate or the lift-off distance
back: the app only ever writes them. A setting Wheelguard can't read it can't show, and a write it can't
read back it can't check, so those commands are not on the allow-list at all. What the Mouse page shows:

| Family | Shown | Protocol |
|---|---|---|
| Model O3 / D3 (wired and wireless) | battery, charging, firmware, sleep timer | "v3" |
| Sinowealth wireless mice | battery, charging, firmware | "v2pro" |

These mice get the `status_only` feature: the page has no Sensitivity or Performance section for them.

## Model O3 / D3 ("v3", `ModelV3DeviceHandler`, line 61499)

| Product | Cable | Dongle |
|---|---|---|
| Model O3 Wireless | `3794:A312` | `3794:A300` (shared) |
| Model D3 Wireless | `3794:A322` | `3794:A300` (shared) |
| Model O3 (wired) | `3794:A310` | |
| Model D3 (wired) | `3794:A320` | |

Not matched: the bootloaders (A311, A313, A321, A323, and A301 for the dongle) and Bluetooth (A316, A326).

The O3 and D3 share the dongle. As the app does, Wheelguard asks the mouse its name (command `0x99`,
`MOUSE_O3_WIRELESS` / `MOUSE_D3_WIRELESS`) and picks the model from it.

### Transport

The settings collection is usage page 0xFF00, usage 1. Requests are Feature report 3, 64 bytes:
`03 BB cmd len data…` (`0xBB` is the app's "mouse device id"). Wheelguard sends the request
(`HIDIOCSFEATURE(64)`), waits 50 ms (wired) or 100 ms (dongle), then reads Feature report 3
(`HIDIOCGFEATURE(64)`).

**Open question: reply offsets.** Through the dongle the app drops two bytes after the report id
(`unwrapWirelessReceiverResponse`, 61210), and the handler then reads data from byte 3 on both paths.
Wheelguard does the same (`V3_PAYLOAD`). A capture of one wired and one dongle reply would settle it. If
the offsets are wrong, the numbers shown are wrong too; a battery level over 100 or a charging flag other
than 0 / 1 is refused as not a battery reply.

### Commands Wheelguard uses (the complete allow-list, `hw/glorious.py`)

| cmd | len | Use | Data / reply |
|---|---|---|---|
| 0x80 | 0 | firmware version | reply `major minor patch` at 3-5; also the write gate |
| 0x82 | 0 | battery | reply 7 = charging (1), 8 = level % |
| 0x84 | 0 | read power saving | reply sleep ms (u32 BE) at 3, low-power ms (u32 BE) at 7 |
| 0x99 | 1 | device name | data `01`; reply ASCII from 3 (only on the shared dongle) |
| 0x04 | 8 | set power saving | sleep ms u32 BE (0 or 1-60 whole minutes), low-power ms u32 BE (always 60000, as the app sends) |

The wired O3 and D3 have no battery: Wheelguard doesn't ask. The write is checked before it is sent
(range, whole minutes, the fixed low-power value) and refused until the mouse has answered a firmware
read, and the driver refuses it while the model is unconfirmed. Not on the allow-list (decoded, not used):
DPI stages 0x0F (it also carries each stage's LED colour, which can't be read back), active stage 0x10,
lift-off 0x09, polling 0x0D / 0x0E, debounce 0x06 / 0x07, motion sync 0x15, lighting 0x14 / 0x94, buttons
0x18, serial 0x81, the dock and dongle commands (`03 AA …`: E0-E2, EE). Firmware updates go through
vendor executables and aren't in the JS at all.

Wheelguard's record (never sent): `47 03`, sleep ms (u32 BE), low-power ms (u32 BE), zero to 16 bytes.

## The Sinowealth wireless mice ("v2pro", `MouseV2ProDeviceHandler`, line 57533)

Vendor `258A`.

| Product | Cable | Dongle |
|---|---|---|
| Model O Wireless | 2011 | 2022 |
| Model O- Wireless | 2013 | 2024 |
| Model O Pro Wireless | 2015 | 2027 |
| Model D Wireless | 2012 | 2023 |
| Model D- Wireless | (2014, not matched) | 2025 |
| Model D Pro Wireless | 2017 | 2029 |
| Series One Pro Wireless | 2018 | 2031 |
| Model O2 Pro Wireless | 2019 | 2033 |
| Model D2 Pro Wireless | 201A | 2034 |
| Model O2 Pro 4K/8K Wireless | 201B | 2035 |
| Model D2 Pro 4K/8K Wireless | 201C | 2036 |
| Series 2 Pro Wireless | 201D | 2039 |

Not matched:

- `258A:2014`. The app also gives it to the "Model D2 Minus Wired" with another protocol.
- The Model O Wired `320F:8888`. Its vendor and collections don't fit this protocol.
- The Model O Wireless Classic `3794:A100` / `A101`. A100 is also its bootloader's id.
- The Sinowealth bootloaders. The app lists them with empty ids.

`2036` is labelled "Model O Minus Wired" in a renderer comment, but the main process uses it for the D2 Pro
4K/8K dongle. Wheelguard follows the main process. `258A` is Sinowealth's vendor id, so other brands' mice
could reuse these product ids. Such a mouse would answer the two reads below, or not answer at all.

### Transport

The settings collection is usage page 0xFFFF, usage 0. Requests are Feature report 0, 65 bytes:
`00 00 00 02 len class id payload…`. The app sends `00` instead of `02` in byte 3 for a firmware read
through the dongle, and Wheelguard does the same. Wheelguard sends the request, waits 40 ms, then reads
Feature report 0 (65 bytes). The 64 bytes after the report id echo the request, with a status in
byte 0:

| Status | Meaning |
|---|---|
| A1 | OK |
| A0 | busy: ask for the reply again |
| A2 | failed: send the request again |
| A3 | unknown |
| A4 | the dongle can't find the mouse (shown as asleep) |

Wheelguard tries at most 10 times. A reply must echo the command id in byte 5. The app means to do this
check but never really does: `SetAndCheckStatus` reads the status off a Promise.

### Commands Wheelguard uses (the complete allow-list)

| class/id | len | Use | Reply (after the report id) |
|---|---|---|---|
| 00/81 | 3 | firmware version | 6-9 = major.minor.patch.rev; also the write gate |
| 00/83 | 2 | battery | 6 = charging (1), 7 = level % (the app shows 0 as 1) |

Nothing else this protocol stores can be read back, so there is no write on the allow-list. Decoded,
not used:

- DPI stages 01/01 and the active stage 01/02
- lift-off 01/08 or 01/0B. The app can't send 2 mm, so the encoding is uncertain.
- polling 01/00 or 01/0A
- motion sync 01/09
- sleep 00/07. It is never called by the app, and it is buggy.
- debounce 00/08 and profile 00/05
- DPI colours 02/01, lighting 02/00 and 02/02
- buttons 03/00 and macros 04/01-03

Firmware updates run vendor executables. There are no pairing commands.

Wheelguard's record (never sent): `47 02` and six zero bytes.

## Device access

`build-aux/70-wheelguard.rules` grants each model's event nodes only when udev sees a pure mouse
(`ID_INPUT_MOUSE`, not `ID_INPUT_KEYBOARD`). The hidraw nodes go through `wheelguard-glorious-check`,
which reads the report descriptor and sets `WHEELGUARD_GLORIOUS=1` only for a node that holds the settings
collection and nothing but vendor collections. A node that also carries the pointer, keys bound to buttons
or media keys is never granted. Discovery (`hidraw.discover_glorious`) applies the same test and matches
each protocol's own collection. If a mouse turns out to share the settings collection with an input
collection on one interface, Wheelguard can't reach it. That is deliberate.

## Testing on a real mouse

`WHEELGUARD_HW_MOCK=glorious-o3`, `glorious-d3`, `glorious-o3-wired`, `glorious-o2pro` and
`glorious-o2pro-asleep` run the app against fake devices that answer as described above.

On hardware:

1. Read the mouse (battery, firmware, and the sleep timer on an O3 / D3).
2. Compare the values with Glorious CORE.
3. Capture one reply of each command.

Then the model can be marked `confirmed=True`. Only the O3 / D3 sleep timer becomes writable.
