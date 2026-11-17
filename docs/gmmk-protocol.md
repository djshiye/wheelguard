# Glorious GMMK Pro and GMMK 2: settings protocol (stock firmware)

How Wheelguard talks to a Glorious GMMK Pro or GMMK 2 on its original firmware. Learned by reading the keyboard
code of Glorious CORE 2.1.23 (the Windows app) without running it; no Glorious code is used. Nothing
here touches the firmware: the bootloader IDs and firmware updates are out of scope.

## Devices

| Board | USB ID | Key map |
|---|---|---|
| GMMK Pro ANSI | 320F:5044 | ANSI |
| GMMK Pro ISO | 320F:5046 | ISO |
| GMMK Pro ANSI, later boards | 320F:5092 | ANSI with `/` and `\` swapped in the map |
| GMMK Pro ISO, later boards | 320F:5093 | ISO |
| GMMK 2 65% ANSI | 320F:5045 | 65% ANSI |
| GMMK 2 65% ISO | 320F:504A | 65% ISO |
| GMMK 2 96% ANSI | 320F:504B | 96% ANSI |
| GMMK 2 96% ISO | 320F:505A | 96% ISO |

Bootloaders (never opened): B007, B009, B00F, B010 (Pro); B008, B00A, B00B, B00C (GMMK 2).

**GMMK 2.** Glorious CORE drives it with the same code as the Pro, so every frame below is the same. The
differences are the key positions, its default key assignment (one for both 65% boards, one for both
96% boards), no knob, and that CORE sends no profile commit (0x01) after a key assignment. The key
positions come from the default assignment, which says what each position types. CORE's own position
tables agree except for `/` and `\`, which they name the other way round; Wheelguard follows the
default assignment. That is one of the things to check on a real board.

Two vendor HID interfaces matter:

- **Settings**: usage page 0xFF01, usage 0x01. Feature report ID 7. Glorious CORE builds 264-byte frames
  (64 for the profile switch); Wheelguard pads every frame to the Feature report size the descriptor
  declares and refuses to write when that size is odd.
- **Events**: usage page 0xFF00, usage 0xFF00. Input report 4: `04 F1 pp ll` when the profile or layer
  was switched on the keyboard (1-based).

A board flashed with QMK shows usage page 0xFF60 instead and is left alone.

The firmware version is the USB device's `bcdDevice` (0045 for revision 1 boards, 0008 for revision 2).
The board can't report its settings, so Wheelguard keeps what it sent in `~/.config/wheelguard/keyboard.json`.

## Frames

Byte 0 is the report ID (7), byte 1 the command. Profiles and layers are 1 to 3 on the wire.

| Cmd | Purpose | Layout |
|---|---|---|
| 0x01 | Switch to / commit profile and layer | `[2]` profile, `[3]` layer |
| 0x03 | Key assignment of one layer | `[2]` profile, `[3]` layer, `[8..127]` type per key, `[128..247]` code per key |
| 0x04 | Macro play modes of one layer | `[2]` profile, `[3]` layer, `[8 + 2i]` big-endian mode for key i: 1 once, 0xFFFF while held, 0 toggle |
| 0x05 | One macro | `[2]` slot, `[8..]` 3 bytes per event: delay high bits (bit 7 set = press), delay low byte, HID usage |
| 0x07 | Properties of one layer (shared with lighting) | `[2]` profile, `[3]` layer, `[4]` effect, `[5]` 0, `[6]` 1, `[7]` side lights (0 on), `[8]` brightness 0-20, `[10]` debounce ms (2, 8, 16) |
| 0x08 | Polling rate | `[8]` 0 = 1000 Hz, 1 = 500, 2 = 250, 3 = 125; `[9]` 1 |

Key types in the 0x03 frame: 1 key (code = HID usage; code 0 = off), 2 modifier, 3 media (0 player,
1 play/pause, 2 mute, 3 volume up, 4 volume down, 5 stop, 6 previous, 7 next), 5 macro (code = slot),
8 keyboard function (1 previous profile, 2 next, 3 previous layer, 4 next), 9 mouse (1 left, 2 right,
3 middle, 4 back, 5 forward, 6 scroll up, 7 scroll down), 0xE0 + modifier bits (1 Ctrl, 2 Shift,
4 Alt, 8 Super) for a key with modifiers. The full default map is in `wheelguard/hw/gmmk.py`.

Order and pauses, as Glorious CORE does them: key changes send 0x04 and each used 0x05 (150 ms apart),
then 0x03 (150 ms), then 0x01 twice (550 ms). A debounce change sends 0x07 for each of the profile's
three layers (550 ms). Polling sends 0x08 (550 ms).

## Sharing the board with RGBeast

Lighting is RGBeast's job, but the 0x07 frame carries both lighting and debounce, so only one
program writes it at a time:

- While RGBeast manages the board (installed, and lighting control turned on for it), Wheelguard
  hands it the debounce and the profile and layer it switched to, over the system D-Bus:
  `io.github.djshiye.RGBeast1.Manager.SetKeyboardOptions(id, '{"profile":1,"layer":1,"debounce_ms":8}')`
  (1-based). RGBeast writes the three 0x07 frames with the lighting it shows. Wheelguard never
  starts RGBeast just to ask, and tells it the current settings once when it first sees it.
- Otherwise Wheelguard writes 0x07 itself with the board's defaults (Glorious mode, full
  brightness, side lights on), and the Keyboard page says so.

## Safety

- The Fn key and the knob are never remapped, so the board's own shortcuts always work.
- The udev rule grants only the two vendor interfaces, and only when every top-level collection on
  that node is a vendor one (`build-aux/wheelguard-gmmk-check`): no node that carries key presses is
  ever opened, so Wheelguard can't read what is typed.
- New boards start locked: nothing is written until the owner turns the controls on.
