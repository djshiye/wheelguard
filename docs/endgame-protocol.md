# Endgame Gear XM2w 4K v2 Configuration Tool v1.02 - HID protocol (static RE)

Method: static analysis only (pefile + objdump + hand reading of the x86 disassembly). The .exe was never executed.
Nothing here was verified against hardware. Each item carries a confidence tag:

- **CONFIRMED** = read directly from code (address given), no inference needed.
- **INFERRED** = assembled from several code facts (e.g. UI control IDs -> struct offsets); reasonable but not proven on a device.
- **UNKNOWN** = present in the protocol, meaning not determined.

All addresses are virtual addresses in the original .exe (image base 0x400000) unless prefixed `dll:`.
Annotated disassembly is in `decompiled/exe_disasm_annotated.txt` (grep the address). Helper scripts in `decompiled/tools/`.

---------------------------------------------------------------------------------------------------------

## 0. What the binary is (corrections to the brief)

| Item | Finding | Conf. |
|---|---|---|
| Tool type | **Native 32-bit MFC application** (VS2010, statically linked MFC "Afx*100"). **Not .NET**: PE data dir 14 (CLR) = 0; `mscoree.dll` appears only as a CRT string. There is no managed code, so there is no ILSpy/dnfile decompilation; the "source tree" is the annotated disassembly + extracted resources. | CONFIRMED |
| Code base | PDB path `G:\project\W26\Config\W25_096_config_V0.91_\Release\Endgame Gear OP1w 4k Configuration Tool.pdb` - the XM2w tool is a re-skin of the OP1w 4k tool. | CONFIRMED |
| Version | VS_VERSION_INFO: "Endgame Gear XM2w 4k v2 Configuration Tool", 1.0.2.0 | CONFIRMED |
| Embedded DLL | RCDATA resource `USBDATA`/165 (file offset 0x180d1c, 0x16000 bytes) is a PE DLL `makalu_67_dll.dll` (saved as `decompiled/makalu_67_dll.dll`). It is a **different product's** library: it opens **VID 0x22D4 PID 0x1503** (and 0x1502 bootloader), usage page 0xFF02, and exports `Update_firmware`, `Set_button_response_time`, `Add_profile`, ... The exe **never loads it**: the UTF-16 string `USBDATA` occurs exactly once in the whole file, at file offset 0x17eaee, inside .rsrc (the resource type-name directory; .rsrc starts at 0x17da00); there is no ASCII copy and no pointer to such a literal in .text/.rdata/.data, and a string-named resource type cannot be passed to `FindResourceW` without that literal. The only `FindResourceW`/`LockResource` sites (0x40bf2d/0x40bf98/0x40bfdb, 0x420e2e, 0x422dba, 0x423079, 0x423257, 0x423302, 0x4391b6) are MFC internals using numeric types. It is dead payload, irrelevant to the 0x3367 devices. | CONFIRMED (not loaded) / INFERRED (dead) |
| HID layer used by the app | The exe's **own** code: `HidD_SetFeature`/`HidD_GetFeature` (0x403740 / 0x403810) + SetupDi enumeration (0x4034e0). A hidapi copy is also linked (names at file 0x177a18) but only its `hid_read`-style reader is used, for the input-report listener (0x416ad0). | CONFIRMED |
| Firmware update | **Not in this exe.** On start it does `FindWindowW("Endgame Gear 4k Dongle Firmware Updater")` and, if that window exists, shows "The 4K dongle is being updated..." and quits (0x4170b1..0x4170d2). The updater is a separate program that was not provided. | CONFIRMED |

## 1. Devices / VID:PID

| VID | PID | Role in tool | Evidence | Conf. |
|---|---|---|---|---|
| 0x3367 | **0x1970** | 4K **dongle** (wireless path). Internal mode byte (0x57fc78) = **0x22**, UI string "Wireless". This is the user's `Endgame Gear HS Dongle`. | table `0x587304..` init at 0x41720c..0x41722a; 0x409a2c, 0x4099a4 | CONFIRMED |
| 0x3367 | **0x1982** | The **mouse itself, wired** (USB cable). Mode byte = **0x11**, UI string "Wired". Also the PID the dongle reports for the paired mouse (see cmd 0x0E). | 0x4198f0, 0x417444 (cmp 0x1982), 0x4173e4 | CONFIRMED |
| 0x3367 | 0x1981 | In the table, **never used** by any code path (probably another product / receiver variant). | 0x587308 | CONFIRMED (unused) |
| 0x3367 | 0x1969 | In the table, never used. | 0x587310 | CONFIRMED (unused) |

Selection logic (0x4198f0): try open 3367:1982 -> mode 0x11 (wired). Otherwise try 3367:1970 -> mode 0x22 (dongle). Wired wins if both present.
If the dongle reports a mouse PID other than 0x1982 (cmd 0x0E below) the tool treats the mouse as "not connected/unsupported" (0x4173e4..0x41740e). So **this tool is only validated for a mouse whose PID is 0x1982**. If the user's mouse is another Endgame model the protocol may differ; read cmd 0x0E first (read-only) and compare.

### 1.1 Which HID interface (confirmed against the user's hardware)

The tool opens the HID collection with **UsagePage 0xFF01, Usage 0x02** (0x403665..0x403681 checks `caps.UsagePage==0xFF01 && caps.Usage==2`).
On the user's dongle (`/sys/bus/hid/devices/0003:3367:1970.000C`, hidraw1, USB interface 1) the report descriptor contains exactly that collection:

```
06 01 FF  09 02  A1 01        UsagePage(FF01) Usage(2) Collection(Application)
  85 A1  75 08 95 3F  15 00 25 01  09 21  B1 03     ReportID A1: Feature, 63 bytes   (=64 with ID)
  85 A0  75 80 95 41  15 00 25 01  09 22  B1 03     ReportID A0: Feature, 128 bits x 65 = 1040 bytes (=1041 with ID)
C0
```

These sizes match the tool's buffers exactly (SetFeature/GetFeature with 0x40 bytes for A1; GetFeature with 0x411 bytes for A0 at 0x403dbd/0x403dc8). **CONFIRMED.**
Other collections on that interface: keyboard (ID 2), consumer (ID 6), vendor FF02/1 input ID 3 (7 bytes), vendor FF02/2 input ID 8 (63 bytes).
Interface 0 (hidraw0, `...000B`) is the plain mouse; the config channel is **not** there.
Saved descriptors: `decompiled/device_report_descriptors/`.

Linux mapping: `HIDIOCSFEATURE(len)` / `HIDIOCGFEATURE(len)` on the hidraw node of interface 1 with `buf[0] = report ID` and `len` = 64 (A1) or 1041 (A0) are byte-for-byte what `HidD_SetFeature/HidD_GetFeature` do (buffer includes the report ID byte).

## 2. Framing

All commands are **Feature reports with ID 0xA1, 64 bytes** (`SetFeature`, tool function 0x403740):

```
byte 0    0xA1 (report ID)
byte 1    command
byte 2    sub-command / parameter (0x0F for the write commands; 0x01 or 0x0F for the probe; 0 otherwise)
byte 3    fixed per-command field: 0x1C for cmd 0x14 and 0x16, 0x0A for cmd 0x15 (cmd 0x15 actually fills 11 payload bytes, so it is not a reliable length); 0 for all other commands
byte 4-5  0
byte 6    page number (cmd 0x16 only: 1 then 2), else 0
byte 7-15 0
byte 16.. payload (cmd 0x14/0x15/0x16)
rest      0
```
No checksum or CRC anywhere. **CONFIRMED** (every builder zero-fills 0x40 bytes then sets these fields; e.g. 0x404720, 0x404860, 0x404b80).

Reply = `GetFeature` of report **0xA1** (64 bytes; for cmd 0x12 report **0xA0**, 1041 bytes) after a delay:

```
byte 0    report ID echo (the tool pre-sets it to 0xA1 / 0xA0 before calling GetFeature)
byte 1    status: 1 = OK, 3 = busy (retry), anything else = failure. The tool itself returns 2 when SetFeature/GetFeature fail.
byte 2-15 (unused by the tool)
byte 16.. response data
```
**CONFIRMED** (0x403810: if `resp[1]==3` it sleeps `delay+200ms, +400ms ...` and re-reads up to 3 times until `resp[1]==1`).

Transport behaviour:

- `SetFeature` (0x403740): on failure with Win32 error 0x15/0x17/0x1D/0x57/0x65B it retries up to 3 more times with `Sleep(50)`. (Linux: retry on EIO/EPIPE similarly.) CONFIRMED.
- A global critical section (0x57fc80) serialises every command+reply pair. One command in flight at a time. CONFIRMED.
- Between `SetFeature` and the `GetFeature` the tool always `Sleep()`s: 300 ms (0x70/0x71/0x0F probes), 350 ms (0x72), 150 ms (0x0D), 350 ms (0x0E, dongle) / 150 ms (0x0E, wired), 350 ms/300 ms (0xB4), 400 ms (0x12 dongle) / 110 ms (0x12 wired), **500 ms (0x14/0x15/0x16, wired) / 880 ms (0x14/0x15/0x16, dongle)** (0x418e7f / 0x418f71). 100 ms between the two pages of cmd 0x16. Use at least these. CONFIRMED.
- Dongle mode (0x22) prefixes nearly every operation with the "0x0F probe" pair (below): `0x0F/01`; if its status != 1 then `0x0F/0F`; abort if that also fails. Wired mode does not. CONFIRMED (0x418f37..0x418f48, 0x4172fa..0x417303).

## 3. Commands used by the tool

### 3.1 Read-only commands (safe)

| Cmd (byte1,2,3) | Name (inferred) | Request | Reply data | Wait | Evidence | Conf. |
|---|---|---|---|---|---|---|
| `A1 0D 00 00` | dongle info | zeros | `resp[0x11] | resp[0x12]<<8` = dongle FW version, hex digits read as decimal / 100 (0x0123 -> "1.23"); formatted `%.2f`; the tool only displays it when the raw word is < 0xC8 (0x41735d). 14 data bytes are copied from `resp[0x10..0x1D]`. | 150 ms | fn 0x405340, display 0x41732a..0x417395 | CONFIRMED layout / INFERRED version encoding |
| `A1 0E 00 00` | mouse info (works through the dongle) | zeros | `resp[0x12] | resp[0x13]<<8` = **mouse PID** (must equal 0x1982); `resp[0x17] | resp[0x18]<<8` = **mouse FW version** (same hex-as-decimal /100). 14 bytes copied from `resp[0x10..]`. | 350 ms dongle / 150 ms wired | fn 0x405200; 0x4173d2..0x4173e9, 0x417444..0x417466 | CONFIRMED |
| `A1 B4 00 00` | battery | zeros | `resp[0x10]` = battery percent (tool clamps >100 to 100). Wired mode shows "Charging" when value <100 and "100%" otherwise. | 350 / 300 ms | fn 0x4050c0 (0x4051ae), UI 0x4174da..0x417503, 0x4176fc..0x41772c | CONFIRMED |
| `A1 12 00 00` | **read full config blob** | zeros | **Report 0xA0** (1041 bytes) via `GetFeature`, `resp[1]` status, **blob = `resp[0x10 .. 0x40F]` (1024 bytes)** | 400 ms dongle / 110 ms wired, then GetFeature(0xA0) | fn 0x403d10 (copy `rep movs` 0x100 dwords from buf+0x10 at 0x403df3..0x403e08), unpack 0x4041e0 | CONFIRMED |
| `A1 72 00 00` | read pairing data | zeros | `resp[0x10..0x1F]` (16 bytes) | 350 ms | fn 0x403bd0 (0x403cc0..0x403cd9), caller 0x409b31 ("Get pair data") | CONFIRMED (read-only, but see 3.3) |

### 3.2 Write commands (the three "group" writes used by APPLY)

APPLY (0x418dd0) diff-compares the working struct against the last-synced struct and sends only the groups that changed, always in this order, each only if the previous succeeded:
**0x14 -> 0x15 -> 0x16**. No separate "save/commit" command exists anywhere in the tool: after the three writes it just copies its working struct to the snapshot (0x41902d -> 0x403e50). Whether the firmware writes flash on each command is unknown; therefore do **not** send writes while dragging sliders - send only on explicit apply. INFERRED.

Notation: `B[n]` = byte n of the 1024-byte config blob returned by cmd 0x12 (see section 4). The payload tables below show which blob byte each payload byte is copied from, so **a Linux implementation can build every write from a fresh blob read** (read blob -> edit the fields you own -> re-derive the 3 payloads -> send). Unknown bytes ride along unchanged.

#### `A1 14 0F 1C` - "sensor / DPI group", 28-byte payload at packet offset 0x10 (the tool actually copies 32 bytes; last 4 are 0)

Header bytes: `A1 14 0F 1C 00 00 00 00 ...`. Builder 0x404720 (pack fn 0x404ce0). Delay 500 ms wired / 880 ms dongle. Status in `resp[1]`.

| payload idx | from blob | meaning | Conf. |
|---|---|---|---|
| 0 | B[0x07] | S0c, unknown (default 0) | UNKNOWN |
| 1 | B[0x08] | **"LED on at lift-off"**: 1 = "Disable LED on Lift-Off" checkbox UNchecked, 0 = checked (4.3) | CONFIRMED (code), INFERRED (name) |
| 2 | B[0x09] | **LOD code** | CONFIRMED |
| 3 | B[0x0A] | **Angle snapping** (0/1) | CONFIRMED |
| 4 | B[0x0B] | **Ripple control** (0/1) | CONFIRMED |
| 5 | B[0x01] | Sensor angle tuning (signed byte) | INFERRED |
| 6 | B[0x0E] | number of CPI stages (1..4) | CONFIRMED |
| 7 | B[0x0D] | active CPI stage index (0..3) | CONFIRMED |
| 8..27 | B[0x23 .. 0x36] | 4 DPI stage records, 5 bytes each (see 4.2) | CONFIRMED |

#### `A1 15 0F 0A` - "advanced group", payload at offset 0x10 (**11 bytes are filled although the length byte says 0x0A**; replicate exactly)

Builder 0x404b80. Delay 500 / 880 ms.

| payload idx | from blob | meaning | Conf. |
|---|---|---|---|
| 0 | B[0x0C] | **Motion Sync** (0/1) | CONFIRMED |
| 1 | B[0x05] | **Polling-rate / mode code** (4.1) | CONFIRMED |
| 2 | B[0x06] | flag byte (bit0 Slamclick filter, bit5 "I understand" ack, bit6 force-max-sensor-fps) | INFERRED |
| 3 | B[0x04] | **Power-saving timeout** (minutes 1..120, bit7 = disabled) | CONFIRMED |
| 4 | B[0x3D] | **Left button Multiclick filter / SPDT mode** | CONFIRMED |
| 5 | B[0x44] | **Right button** Multiclick filter / SPDT mode | CONFIRMED |
| 6 | B[0x4B] | **Middle button** Multiclick filter | CONFIRMED |
| 7 | B[0x52] | **record 3 (rear side button, default code 0x08 = "Back")** Multiclick filter | CONFIRMED (label caveat 4.4) |
| 8 | B[0x59] | **record 4 (front side button, default code 0x10 = "Forward")** Multiclick filter | CONFIRMED |
| 9 | B[0x03] | **Deep-sleep timeout** (minutes 1..120, bit7 = disabled) | CONFIRMED |
| 10 | B[0x6F] | **Sensor Glass Mode** (0/1; changes which LOD list is valid) | CONFIRMED |

#### `A1 16 0F 1C` + page byte - "button map", **sent twice**

Builder 0x404860 (pack fn 0x4049f0). Header `A1 16 0F 1C 00 00 <page> ...` with `page` = **1** then **2** (byte 6); payload = 28 bytes at offset 0x10; 100 ms between pages; delay 500/880 ms after each. Together the 56 payload bytes are exactly **`B[0x37 .. 0x6E]`** (page 1 = `B[0x37..0x52]`, page 2 = `B[0x53..0x6E]`). CONFIRMED (the mapping p[i] = B[0x37+i] was derived mechanically from 0x4041e0 vs 0x4049f0).

Note: the tool sends **all 8 button records** (including the 4 filter bytes that are also in cmd 0x15). Sending 0x15 without 0x16 changes only filters; sending 0x16 rewrites everything in the records, so always start from a fresh blob.

### 3.3 Probe commands (sent before dongle-mode operations)

| Cmd | Request | Reply | Notes | Conf. |
|---|---|---|---|---|
| `A1 0F 01 00 ...` | zeros | status in `resp[1]` (1 = ok) | fn 0x405480, 300 ms. Tool tries this first. Meaning (link/ready check) is a guess. | CONFIRMED bytes / UNKNOWN meaning |
| `A1 0F 0F 00 ...` | zeros | status | fn 0x4055a0, 300 ms. Tried when the previous returned != 1. The same bytes `A1 0F 0F` also open the cmd-0x14/0x15/0x16 headers, so this may be a "select target = mouse" style call. Treat as required in dongle mode but side effects unknown. | CONFIRMED bytes / UNKNOWN meaning |

### 3.4 Device -> host events (Input report ID 3, vendor page 0xFF02 usage 1)

The tool starts a thread (0x416ad0) that reads 8 bytes (`03 t d1 d2 d3 d4 d5 d6`) from the dongle/mouse and calls handler 0x418440 with `t,d1..`. Handled event types (jump table 0x4188f4 / 0x4188dc): CONFIRMED mapping, INFERRED semantics:

| t | handler | meaning |
|---|---|---|
| 0x02 | 0x4184e0 -> 0x410f60 | DPI stage changed on the mouse: `d1` = active stage index 0..3 (stored to S0a/B[0x0D]) |
| 0x06 | 0x41864c | polling rate changed on the mouse: `d1` = polling code (same encoding as B[0x05]), `d2` flag |
| 0x0E | 0x41857d | identity: `d2 | d3<<8` = mouse PID (bytes at 0x587222/0x587223); != 0x1982 triggers a reconnect/refresh |
| 0xB1 | 0x4185b0 | link state: `d1` = 0xF1 connected, 0xF0 disconnected (dongle only) |
| 0xB4 | 0x4184f3 | battery percent in `d1` (clamped to 100) |

On Linux this is the dongle's `hidraw` interface 1 report ID 3 (7 data bytes). Usable for live updates; not required.

## 4. Configuration blob (cmd 0x12) and field meanings

Blob offsets are relative to `resp[0x10]` of the 0xA0 reply. The tool unpacks it into a 0x82-byte struct S (0x4041e0) and packs it back with 0x403e50; `S` offsets are given only for traceability with the disassembly (S base 0x5866b0).

| Blob | S | Field | Encoding | Written by | Conf. |
|---|---|---|---|---|---|
| B[0x00] | 00 | unknown, default 0 | - | never | UNKNOWN |
| B[0x01] | 11 | Sensor Angle Tuning | **signed int8** (UI slider; load code compares against 0x81/0x7F) | 0x14 p5 | INFERRED |
| B[0x02] | - | not read by tool | - | never | UNKNOWN |
| B[0x03] | 06 | Deep sleep | bits0-6 = minutes (1..120); **bit7 set = disabled** | 0x15 p9 | CONFIRMED |
| B[0x04] | 05 | Power saving (sleep) | bits0-6 = minutes (1..120); bit7 set = disabled | 0x15 p3 | CONFIRMED |
| B[0x05] | 04 | Polling rate (4.1) | code | 0x15 p1 | CONFIRMED |
| B[0x06] | 07 | flags: 0x01 Slamclick filter, 0x20 "I understand" ack checkbox, 0x40 force max sensor FPS | bitfield | 0x15 p2 | INFERRED |
| B[0x07] | 0c | unknown | - | 0x14 p0 | UNKNOWN |
| B[0x08] | 14 | inverse of "Disable LED on Lift-Off" (4.3) | 0/1 | 0x14 p1 | CONFIRMED (code) |
| B[0x09] | 15 | LOD code (4.5) | 0..10 | 0x14 p2 | CONFIRMED |
| B[0x0A] | 0d | Angle snapping | 0/1 | 0x14 p3 | CONFIRMED |
| B[0x0B] | 0e | Ripple control | 0/1 | 0x14 p4 | CONFIRMED |
| B[0x0C] | 0f | Motion sync | 0/1 | 0x15 p0 | CONFIRMED |
| B[0x0D] | 0a | Active CPI stage | 0..3 | 0x14 p7 | CONFIRMED |
| B[0x0E] | 0b | Number of CPI stages | 1..4 | 0x14 p6 | CONFIRMED |
| B[0x0F..0x22] | 6e..81 | 20 bytes never touched by any UI handler (LED page is dead code; only LED dialog 137 exists, no tab, no command) | - | **no command in this tool writes them** | UNKNOWN |
| B[0x23..0x36] | 16..2d | 4 DPI stage records | 4.2 | 0x14 p8-27 | CONFIRMED |
| B[0x37..0x6E] | 2e..6c | 8 button records, 7 bytes each | 4.4 | 0x16 | CONFIRMED |
| B[0x6F] | 10 | Sensor Glass Mode | 0/1 | 0x15 p10 | CONFIRMED |
| B[0x70..0x3FF] | - | not read | - | - | UNKNOWN |

Factory defaults the tool loads into S (function 0x418ce0, used before the first read and after a factory reset): S04=0x02, S05=0x01, S06=0x03, S07=0x01, S0a=1, S0b=4, S15=3, DPI stages 400/800/1600/3200 (X=Y), each button record filter byte = 8; button codes below. These tell you the intended defaults; they are the tool's local defaults, not necessarily the device's.

### 4.1 Polling rate (B[0x05]) - CONFIRMED (both directions)

UI combo `Polling Rate` (control 0x425, items from DLGINIT 140) <-> byte (set table 0x4130dc, get table 0x41376c):

| combo idx | UI text | B[0x05] |
|---|---|---|
| 0 | 4000Hz (no Power Saving) | **0x02** |
| 1 | 2000Hz (no Power Saving) | **0x04** |
| 2 | 1000Hz (no Power Saving) | **0x08** |
| 3 | 1000Hz (Power Saving) | **0x80** |
| 4 | 125Hz (Office Mode) | **0x40** |

No 8000 Hz (this is a 4K product), no 250/500 Hz. Other values are not produced by the UI; the load path ignores unknown values. Caveat: a hidden (invisible) combo "Sensor Power Mode" (control 0x431, items "Corded Gaming Mode / High Performance Mode / Low Power Mode") can OR 0x10/0x20/0x40 into the same byte (0x40a450); it is not visible in the shipped dialog (style lacks WS_VISIBLE), so ignore. Motion sync is a separate bit (B[0x0C]).

### 4.2 DPI stages (CPI) - CONFIRMED

Four records, 5 bytes each, starting at B[0x23], stride 5: `[flag][X lo][X hi][Y lo][Y hi]` little-endian.
Record k at `B[0x23+5k]`: flag, X = `B[0x24+5k]|B[0x25+5k]<<8`, Y = `B[0x26+5k]|B[0x27+5k]<<8`.
- X and Y are the **raw DPI numbers** (defaults 0x190, 0x320, 0x640, 0xC80 = 400, 800, 1600, 3200).
- `flag` = 1 if X != Y ("X/Y settings"), else 0 (UI sets `flag = (X != Y)`, 0x4115c2). INFERRED.
- UI edit clamp (0x40fc60): 10..30000, rounded to a multiple of 10 (or 50 above 10000). The hardware limit for this sensor is not known from the tool - check against the product spec.
- Number of enabled stages = B[0x0E] (1..4); active stage = B[0x0D] (0-based).
- **No stage colours / LED colour** exist in this tool.

### 4.3 Byte B[0x08] (S14) - resolved

Two collectors write S14, but APPLY (0x418dd0) runs them in a fixed order: the Advanced-page collector 0x412eb0 first (it sets S14 from the Sensor-Glass-Mode checkbox, 0x412ef9/0x412f0b) and then the Sensor-page collector **0x410460 (called at 0x418e1d) overwrites it**: `S14 = (checkbox "Disable LED on Lift-Off" (control 0x42a) == 0)` (0x410530..0x41053b, same expression as handler 0x411250). So on the wire **B[0x08] = 1 when "Disable LED on Lift-Off" is unchecked, 0 when checked** (factory default 1); the glass-mode write is dead. Glass mode itself travels in B[0x6F]. When building a write, copy B[0x08] from the fresh blob unless the user toggles that option.

### 4.4 Button records `B[0x37 + 7*r]`, r = 0..7 - layout CONFIRMED, semantics partly INFERRED

`[type][code][w2 lo][w2 hi][w3 lo][w3 hi][filter]`

| r | physical (inferred from defaults) | default (type,code) | filter byte | Conf. |
|---|---|---|---|---|
| 0 | left | 00 01 | B[0x3D] = "Left Multiclick Filter / SPDT" | CONFIRMED |
| 1 | right | 00 02 | B[0x44] | CONFIRMED |
| 2 | middle (wheel click) | 00 04 | B[0x4B] | CONFIRMED |
| 3 | side button, code 0x08 (XBUTTON1 = "Back") | 00 08 | B[0x52] | CONFIRMED |
| 4 | side button, code 0x10 (XBUTTON2 = "Forward") | 00 10 | B[0x59] | CONFIRMED |
| 5 | not exposed in the Button dialog; default `09 F1` (DPI-cycle style code) | 09 F1 | B[0x60] (no UI) | INFERRED |
| 6 | **wheel up** | 01 01 | B[0x67] (no UI) | INFERRED |
| 7 | **wheel down** | 01 FF | B[0x6E] (no UI) | INFERRED |

Label caveat: the Button-Mapping dialog calls record 3 "FORWARD" and record 4 "BACK" (handlers 0x408cb0 -> `[this+0x380]=3`, 0x408d50 -> 4), while the Advanced dialog's sliders call record 4 "Forward" and record 3 "Back". The two dialogs disagree; rely on the numeric code (0x08 / 0x10) not the label.
Left-handed mode: records 0 and 1 swap (the "RIGHT CLICK" control addresses record 0 when left-handed is checked; 0x408b86).

Action encodings seen in the remap switch (0x407090): `(0,0x01)` left, `(0,0x02)` right, `(0,0x04)` middle, `(0,0x08)` back, `(0,0x10)` forward, `(1,0x01)` / `(1,0xFF)` wheel +1/-1, `(9,0xF1)`, `(0x0C,0x00)`, `(0x02,keycode,...)` keyboard key with modifier mask in a following byte, `(0x20,0xCD)` (consumer 0xCD play/pause). Keyboard/macro/media encodings (w2/w3 words) were **not** fully decoded. Button remapping should be considered out of scope for a first implementation.

### 4.5 Lift-off distance (B[0x09]) - CONFIRMED mapping, INFERRED unit labels

Combo `LOD` (control 0x400, items added at runtime 0x410740 from the wide strings at 0x55e070..0x55e0f4). Normal (B[0x6F]=0): items 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7 mm -> `B[0x09] = index` (0..10): 0.7mm=0 ... 1.0mm=3 ... 1.7mm=10. Glass mode (B[0x6F]=1): only two items, index0 -> code **1**, index1 -> code **2** (labels 1.0mm and 2.0mm in the string list; the normal-mode meaning of code 1 is 0.8 mm, so the code meaning differs in glass mode). Default code 3 (= 1.0 mm). Handler 0x410e10 (jump table 0x410ed0).

### 4.6 Multiclick filter (the only "debounce-like" setting) - CONFIRMED

Advanced tab "Left/Right/Middle/Forward/Back Button Multiclick Filter" (+ for Left/Right a combo "SPDT:" = OFF / GX Speed Mode / GX Safe Mode). Byte value:

| byte | meaning |
|---|---|
| 0 .. 25 (0x19) | filter level set by the slider (trackbar position, clamp 0x19: 0x4142bd). UI text: "not a traditional debounce slider, lower values don't affect click latency". Default 8. Unit presumably milliseconds - INFERRED |
| **0xF1** | SPDT "GX Speed Mode" (Left and Right only) - slider disabled |
| **0xF0** | SPDT "GX Safe Mode" (Left and Right only) - slider disabled |

(0x412cc0 / 0x412d75 / 0x412d99 for left; 0x412db0 for right, same values.) Middle, Back, Forward have only the slider (no SPDT combo): bytes B[0x4B], B[0x52], B[0x59].

### 4.7 Other settings

| Setting | Where | Conf. |
|---|---|---|
| Motion Sync | B[0x0C] (0/1) | CONFIRMED |
| Angle Snapping | B[0x0A] | CONFIRMED |
| Ripple Control | B[0x0B] | CONFIRMED |
| Sleep / Power saving | B[0x04], deep sleep B[0x03] (minutes 1-120, bit7 disables) | CONFIRMED |
| Slamclick Filter / Force max sensor fps | bits 0x01 / 0x40 of B[0x06] | INFERRED |
| "Motion Jitter Filter" checkbox (0x406), "Surface Calibration" button (0x425) | UI handlers point to a bare `ret` (0x418cd0): **no command is sent in this version** | CONFIRMED |
| LED / colours | no command; LED dialog is unused | CONFIRMED |
| Profiles | none (single settings set). The profile functions belong to the unused DLL | CONFIRMED |

## 5. DO NOT IMPLEMENT / avoid (give these to the Linux side as a refusal list)

Recommended policy: **allow-list** only `A1 {0D, 0E, B4, 12, 14, 15, 16}` (+ optionally the two 0x0F probes) and refuse everything else; only open a hidraw node whose VID is 0x3367 and whose descriptor has the `06 01 FF 09 02` collection. Never send a report ID other than 0xA1 / 0xA0.

| Command bytes (hex, 64-byte report) | What it does in the tool | Why avoid | Evidence |
|---|---|---|---|
| **`A1 13 00 00 ...`** | **Factory reset** ("Factory Reset" button 0x40f -> handler 0x419060 -> fn 0x4056c0; waits 200/800/1080 ms for the mouse, then reloads defaults into the UI) | Wipes the mouse settings (and possibly pairing). | 0x4190f5, fn 0x4056c0 (0x405739 `A1 13`) |
| **`A1 70 00 00 ...`** | "Pair Dongle" button (handler 0x409980 -> fn 0x4039d0, 300 ms) | Starts pairing; can drop the current mouse<->dongle link. | 0x403a34 |
| **`A1 71 00 00 ...`** | "Pair Default" button (0x409a10 -> fn 0x403ad0; retried up to 2x, with the 0x0F/0F probe in between) | Resets the pairing to a default address; can unpair the mouse. | 0x403b34 |
| `A1 72 00 00 ...` | "Get pair data" - read-only (16 bytes) | Safe to read, listed here because it sits in the pairing group; no value in implementing it. | 0x403c52 |
| *firmware update / bootloader* | **No such command exists in this exe.** The firmware updater is a separate program ("Endgame Gear 4k Dongle Firmware Updater") not provided. The embedded `makalu_67_dll.dll` exports `Update_firmware`, `Open_x2bl_device`, `Reset_factory_default`, `Write_profile_data`, ... but speaks to VID 0x22D4 PID 0x1503/0x1502 and is never called. | Do not try to guess DFU bytes. Anything not in the allow-list could be a bootloader/flash command in the unseen firmware. | dll: export table; `decompiled/embedded_makalu_67_dll_disasm_annotated.txt` |
| Writes to unmapped blob bytes (B[0x0F..0x22], B[0x00], B[0x02], B[0x07]...) | not written by this tool | Unknown meaning (possibly LED/flash calibration). Do not modify; echo them back unchanged. | table 4 |

## 6. Wireless vs wired

- Both work with the same A1/A0 report format. Wired (PID 0x1982) talks straight to the mouse; dongle (PID 0x1970) relays to the paired mouse - proven by cmd 0x0E returning the mouse's own PID/FW through the dongle (0x4173d2..0x4173e9) and by "Settings" being written/read in dongle mode with only longer delays (880 ms vs 500 ms) plus the 0x0F probes. CONFIRMED.
- So **all settings below can be changed through the dongle** on the user's setup, provided the mouse is awake and linked (event 0xB1 / cmd 0x0E PID == 0x1982). A sleeping mouse may time out (the tool simply reports failure).
- The user's dongle exposes the config collection on hidraw1 (`3367:1970`, USB interface 1).

## 7. Scroll wheel debounce / filter - answer for the user's problem

- **There is no scroll-wheel debounce, filter, or encoder setting in this tool.** The only filter-like controls are the five *button* "Multiclick Filter" sliders (left, right, middle-click, two side buttons) and the Slamclick filter. The wheel only has remapping (wheel up/down actions) in the Button dialog. CONFIRMED (every control ID in dialogs 135/139/140/161 was enumerated; no wheel filter control exists).
- The Multiclick filter (0..25) is documented by the vendor UI as *not* a latency debounce; it suppresses repeated button events. It will **not** fix reversed encoder ticks.
- Hidden possibility (**unverified, do not rely**): the wire format has an identical 7-byte record for the two wheel directions (records 6 and 7) whose last byte (`B[0x67]`, `B[0x6E]`) sits in the same "filter" slot as the buttons' multiclick byte and defaults to 8. The firmware *may* apply it to wheel events, but nothing in the tool exposes or sets it, and no function of the tool tells what it does. Writing it requires a cmd-0x16 resend of all records. (Weak corroboration only: APPLY's change detector 0x419a20 classifies B[0x60], B[0x67], B[0x6E] - the record-5/6/7 filter bytes - into the same group as the five button multiclick filters, i.e. the vendor code treats them as the same field type; still not exposed by any control and unproven on the firmware side.) Experiment only with a known-good backup of the blob (cmd 0x12) and the ability to restore; the factory-reset command is NOT a safe recovery plan for pairing.
- The 9 % reversed ticks are therefore better handled in software (a userspace/evdev filter), unless the vendor firmware changes.

## 8. Reference recipe for a Linux implementation

```
open /dev/hidrawN of interface with descriptor containing 06 01 FF 09 02 (VID 3367)
GET: send A1 12 00..(64B) via HIDIOCSFEATURE, sleep 0.4 s, HIDIOCGFEATURE(buf[0]=A0, len=1041)
     verify buf[1]==1 (retry on 3), blob = buf[0x10:0x410]
(dongle mode: first send the 0x0F probe: A1 0F 01 then, if status!=1, A1 0F 0F; wait 0.3 s each; the tool repeats this probe pair before EACH subsequent group write when an earlier group was sent in the same APPLY: 0x418fa1 before 0x15, 0x419001 before 0x16)
EDIT blob fields (section 4). Never touch bytes you do not own.
SET (only the groups that changed, in order 14, 15, 16):
  pkt14 = A1 14 0F 1C 00 00 00 .. [16:] = blob[07..0B] + blob[01] + blob[0E] + blob[0D] + blob[23..36]  (28 B)
  pkt15 = A1 15 0F 0A 00 00 00 .. [16:] = blob[0C],blob[05],blob[06],blob[04],blob[3D],blob[44],blob[4B],blob[52],blob[59],blob[03],blob[6F]  (11 B)
  pkt16 pages: A1 16 0F 1C 00 00 <1|2> .. [16:] = blob[37:53] / blob[53:6F]  (28 B each, 100 ms apart)
  after each SetFeature: sleep >= 0.5 s (wired) / 0.9 s (dongle), HIDIOCGFEATURE(A1, 64): require buf[1]==1 (retry if 3)
GET again to confirm.
```
Pseudocode only; none of it was executed against hardware.

## 9. Evidence index (addresses in the .exe)

| What | Address |
|---|---|
| SetFeature wrapper (64 B, retry) | 0x403740 |
| GetFeature wrapper (status==3 retry) | 0x403810 |
| Open device by VID/PID + usage check FF01/2 | 0x4034e0 (check 0x403665..0x403681) |
| Mode detect (wired 0x11 / dongle 0x22) | 0x4198f0; PID table init 0x41720c |
| Cmd 0x70 / 0x71 / 0x72 | 0x4039d0 / 0x403ad0 / 0x403bd0 |
| Cmd 0x12 (blob read) | 0x403d10; unpack 0x4041e0; pack 0x403e50 |
| Cmd 0x14 / 0x15 / 0x16 builders | 0x404720 (+0x404ce0) / 0x404b80 / 0x404860 (+0x4049f0) |
| Cmd 0xB4 / 0x0E / 0x0D / 0x13 / 0x0F probes | 0x4050c0 / 0x405200 / 0x405340 / 0x4056c0 / 0x405480 & 0x4055a0 |
| Connect/refresh sequence | 0x417260 |
| APPLY handler | 0x418dd0 (diff 0x419a20, snapshot 0x403e50) |
| Factory reset handler | 0x419060 |
| Event listener / handler | 0x416ad0 / 0x418440 |
| Polling combo handlers | 0x4146f0 (set), 0x4130dc/0x41376c (tables) |
| Multiclick handlers | 0x412cc0 (left SPDT), 0x412db0 (right), 0x414240.. (sliders), collector 0x412eb0 |
| LOD | 0x410e10, list builder 0x410740 |
| DPI stage handlers | 0x4110d0..0x411220 (values), 0x4114b0..0x411d30 (stage select) |
| Power/sleep | 0x40a180..0x40a570 |
| Group classification used by APPLY's change detector (`0x419a20`, arrays built by `0x404db0` in blob coordinates): bit0 -> cmd 0x14 = {B01, B07-B0B, B0D-B36}; bit1 -> cmd 0x15 = {B03-B06, B0C, B3D, B44, B4B, B52, B59, B60, B67, B6E, B6F}; bit2 -> cmd 0x16 = button records minus filter bytes. Independently corroborates the payload tables. | 0x419a20, 0x404db0 |
| Dialogs / DLGINIT | `decompiled/dialogs_dump.txt`, `decompiled/pe_resources/` |
| Message maps | dialog 135 @0x55dd20, 140 @0x55e328, 139 @0x55cea0, 161 @0x55d620, 159 @0x55d3c0, main 102 @0x55eea0 |

## 10. Open issues / blockers

1. Nothing validated on hardware. First live step should be read-only (`A1 0E`, `A1 B4`, `A1 12`) and compare against this document.
2. Button-record action encodings for keyboard/macro/media are not decoded; only mouse-button codes and wheel codes are.
3. Meaning of `A1 0F 01` / `A1 0F 0F` probes, of B[0x07], B[0x08] (ambiguous), B[0x0F..0x22], B[0x00], B[0x02].
4. Whether each write goes to flash immediately (no commit command exists) - assume yes and avoid write loops.
5. The mouse on the user's dongle must report PID 0x1982 for this tool's assumptions to hold; unverified.
6. No Ghidra/IDA available; analysis was done on raw disassembly, so field semantics derived from UI control mapping carry the INFERRED tag where noted.
