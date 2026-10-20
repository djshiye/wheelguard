# wheelguard

Drops bouncing (wrong-direction) scroll-wheel ticks from a faulty encoder, on GNOME/Wayland, and
comes with a small settings app. Written for the Endgame Gear wireless mouse (USB dongle
`3367:1970`), where about 9% of ticks come through reversed, which in Warframe means a stray heavy
attack in the middle of a melee scroll. A background daemon grabs the mouse, filters the wheel and
passes everything else through untouched.

## Install
```sh
gh repo clone djshiye/wheelguard && cd wheelguard && ./install.sh
```
It asks for your sudo password once (to install `python3-pyside6` and `python3-evdev` with dnf, and
the udev rule that lets your user open the mouse and `/dev/uinput`), then installs everything else
into your home directory and starts the service. The clone can be deleted afterwards. Re-run
`./install.sh` from a newer checkout to upgrade. Then open **Wheelguard** from the app launcher.

If the app says "permission denied" right after the first install, unplug and replug the dongle once.

## Uninstall
```sh
./uninstall.sh            # keeps ~/.config/wheelguard/config.json
./uninstall.sh --purge    # removes the config too
```

## What the app controls
- **Enabled**: turns the filter on or off live. Off means every tick passes through untouched; the
  mouse stays grabbed, nothing is re-opened.
- **lock / pre / hold** times (see below), applied live as you change them.
- A live view of the wheel (each tick as emitted, dropped, held or passed) and counters.
- Connection status, including why the mouse is unavailable (not found, permission denied).

The settings app talks to the daemon over a Unix socket (`$XDG_RUNTIME_DIR/wheelguard.sock`), see
`PROTOCOL.md`. The daemon works without the app.

## How the filter works
It keeps a *direction lock* (the direction you have been scrolling) and the time of the last tick
emitted in it.

1. No lock, or no ticks for `lock_ms`: the tick goes through and sets the lock.
2. Same direction as the lock: goes through at once (zero added latency). If a reversal was being
   held, it was a bounce and is dropped.
3. A reversal less than `pre_ms` after the last lock-direction tick: dropped.
4. Any other reversal is held for `hold_ms`. A lock-direction tick during the hold means it was a
   bounce (dropped). A second reversal during the hold confirms a real switch (both go through, the
   lock switches). If nothing happens, it goes through after `hold_ms` and the lock switches.

Trade-off: a genuine reversal within `pre_ms` (60 ms) of your last tick is dropped. A deliberate
direction change after a short pause (or a quick burst of two or more ticks) still works. The
horizontal wheel, pointer motion and buttons are forwarded immediately.

## Tuning
Use the app, or edit `~/.config/wheelguard/config.json` (the daemon notices within ~2 s):

| Key | Default | Meaning |
|---|---|---|
| `enabled` | true | filter on/off |
| `lock_ms` | 300 | direction lock expires after this long without ticks |
| `pre_ms` | 60 | reversals this soon after a lock-direction tick are dropped |
| `hold_ms` | 25 | other reversals are held this long before being let through |
| `vendor` / `product` | `"3367"` / `"1970"` | hex USB ids of the mouse to look for |
| `retry_s` | 2 | seconds between device searches |

Rules: `0 < hold_ms < lock_ms`, `0 <= pre_ms <= lock_ms`, all times at most 2000.

Tuning in-game: stop the service (`systemctl --user stop wheelguard`), run `wheelguard-daemon --log`
and scroll. Real strays show up as `DROP` lines; if intended reversals are being eaten, lower
`pre_ms`; if strays leak through after a pause, raise `hold_ms` or `lock_ms`. The daemon also
accepts `--lock-ms`, `--pre-ms`, `--hold-ms`, `--vendor`, `--product` and `--retry`, which override
the config file. Check the service with `systemctl --user status wheelguard` and
`journalctl --user -u wheelguard -f`.

## Not supported
DPI, polling rate and button remapping. The mouse's HID protocol for those is unknown, so
wheelguard only works on the standard input events.

## Files
- `wheelguard/core.py`: the pure filter (`WheelFilter`, timestamps + signs in, decisions out)
- `wheelguard/frames.py`: frame stripping / held-tick re-emission on plain `(type, code, value)` tuples
- `wheelguard/config.py`: config file, validation, atomic save
- `wheelguard/control.py`: live config, counters and status shared by the input loop and IPC
- `wheelguard/ipc.py`: the Unix socket server
- `wheelguard/daemon.py`: device discovery, grab, uinput, select loop, CLI
- `wheelguard/gui/`, `wheelguard-settings`: the settings app
- `build-aux/`: udev rule, systemd user unit, desktop entry and icon
- `install.sh`, `uninstall.sh`
- `tests/`: `python3 -m unittest discover -s tests -t .`

Installed locations: `~/.local/share/wheelguard/` (program), `~/.local/bin/wheelguard-{daemon,settings}`
(launchers), `~/.config/systemd/user/wheelguard.service`, the desktop entry and icon under
`~/.local/share/`, and `/etc/udev/rules.d/70-wheelguard.rules`.
