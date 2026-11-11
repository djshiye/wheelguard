<h1 align="center">
  <img src="build-aux/io.github.djshiye.Wheelguard.svg" width="96" alt=""><br>
  Wheelguard
</h1>

<p align="center">Stop stray scroll ticks and tune your gaming mouse, on GNOME.</p>

<p align="center"><img src="docs/screenshot.png" width="720" alt="Wheelguard settings"></p>

## Install

```sh
sudo dnf config-manager addrepo --from-repofile=https://djshiye.github.io/wheelguard/wheelguard.repo
sudo dnf install wheelguard
```

Then open **Wheelguard** from the app grid. **Update:** `sudo dnf upgrade`.
**Uninstall:** `sudo dnf remove wheelguard`. One package works on every current Fedora release.
Packages and repository metadata are signed; dnf asks once to trust the key
([RPM-GPG-KEY-djshiye](https://djshiye.github.io/wheelguard/RPM-GPG-KEY-djshiye)). The package
installs its own `/etc/yum.repos.d/wheelguard.repo`, so the repository stays configured.

## Features

- **Scroll bounce filter** that drops wrong-direction wheel ticks, with a live monitor of every tick
- **Hardware mouse settings**, stored on the mouse:
  - Endgame Gear XM2w 4k v2: DPI stages, polling rate, lift-off distance, debounce, button actions,
    sleep and battery
  - Logitech G Pro X Superlight (first generation): DPI stages (edits the mouse's onboard profile),
    polling rate up to 1000 Hz, battery
  - Razer DeathAdder V4 Pro: DPI stages with separate X/Y, polling rate up to 8000 Hz, sleep timer,
    low-battery warning, battery
  - More Razer mice, settings shown read-only until tested on each model: DeathAdder V3 Pro and
    V3 HyperSpeed, Viper V2 Pro, V3 Pro, V3 Pro SE, V3 HyperSpeed and Mini Signature Edition,
    Basilisk V3 Pro and V3 Pro 35K, Cobra Pro (battery, DPI stages, polling rate, sleep timer)
  - More Logitech G mice, settings shown read-only until tested on each model: G Pro Wireless,
    G Pro Hero, G305, G102 / G203, G303 Shroud Edition, G403 Hero, G502 Hero, Lightspeed, X,
    X Lightspeed and X Plus, G604, G703 Hero, G705, G903 Hero (battery, DPI stages, polling rate)
- **Scroll filter for any mouse with a wheel**: supported mice are picked up automatically. For any other
  mouse, press **Allow this mouse** (in the mouse list under the status pill at the top of the window) and
  enter your administrator password once. That adds a device rule for that one mouse (`/etc/udev/rules.d/71-wheelguard-allowed-mice.rules`),
  and from then on **Automatic** protects it like a supported mouse (a supported mouse still comes first when
  both are connected). **Remove access** takes it back. As with the supported mice, programs you run can then read that
  mouse's movement and clicks. A mouse that shares its device with a keyboard is never allowed.
- **Wheel health**: a 7 and 30 day chart of how often the wheel needed blocking, a warning
  if it starts bouncing more
- **Double-click fix** for any mouse: ignores the extra click a worn button sends
- **Per-game profiles**: switch scroll protection, Click-Lock and the DPI stage while a chosen app is in front
- **Works for games without a launcher entry** too (Lutris, Heroic, Wine): bring the game to the front
  once and it appears in the profile app list. A profile never changes your saved settings
- **Pause**: one shortcut (Super+Alt+P), or the Pause button under the status pill, makes Wheelguard let go
  of the mouse completely until you resume
- **Battery for any wireless mouse**: for a mouse without its own Wheelguard driver, the panel icon, tray and
  low-battery alerts show the level Linux already reports for it (through UPower, read-only: most Logitech
  mice and Bluetooth mice that report one)
- **Low-battery alerts** and keyboard shortcuts for Click-Lock, the DPI stage and Pause
- **Click-Lock**: hold the left button for a few seconds (5 s by default, adjustable) and Wheelguard keeps
  clicking for you, or keeps the button pressed for long drags, until your next click (any other mouse
  button ends it too). It lets go by itself after 10 minutes (adjustable) and when the screen locks or the
  computer sleeps. Switch it on and off from the panel popup or a keyboard shortcut
- **GNOME pointer settings** (speed, acceleration, scrolling) in the same app
- **Panel icon** with an instant popup (battery, scroll protection, DPI stage); closing the window keeps
  it running there, and it starts at login (switch on the Settings page). The icon is a GNOME Shell
  extension, enabled automatically after the next login. Without it a tray icon is used if available,
  otherwise closing the window quits.
- **Runs as a background service**: a user daemon starts with your session, the app is optional
- **What the service may do**: it runs sandboxed, with no network access, and can write only its own
  settings and history. To filter the wheel it reads your mouse and creates a virtual mouse in its place.
  Creating that virtual mouse needs access to `/dev/uinput`, which the package grants to the logged-in
  user, so, as on any system where a remapping tool is installed, programs you run could also create
  virtual input devices
- **Light and dark**, following GNOME

<details>
<summary>How the filter works</summary>

The daemon grabs the mouse and tracks a direction lock: the way you have been scrolling. Ticks in
that direction pass at once, with no added latency. A reversal right after a tick is dropped, and
any other reversal is held for a few milliseconds: if the lock direction returns, it was a bounce;
if a second reversal follows or nothing happens, it was real. Everything else is forwarded
untouched. Tuning keys are in `~/.config/wheelguard/config.json`, the app talks to the daemon over
a Unix socket ([PROTOCOL.md](PROTOCOL.md)).

</details>

<details>
<summary>Building / publishing</summary>

GitHub Actions (`.github/workflows/ci.yml`) builds and tests the RPM on every push and pull
request. Pushes to `main` also sign it (key in the `RPM_SIGNING_KEY` secret), rebuild the DNF
repository with the last three builds (`build-aux/dnf-repo.py`), commit it to the `gh-pages`
branch that GitHub Pages serves, and install it in clean Fedora 44 and 45 containers
(`build-aux/verify-install.sh`). The old local `tools/publish.sh` is retired: one publishing path
is less to maintain, and local builds could not be signed. Run the tests with `python3 -m unittest discover -s tests -t .`. The mice's
protocols are documented in [docs/endgame-protocol.md](docs/endgame-protocol.md),
[docs/logitech-protocol.md](docs/logitech-protocol.md) and [docs/razer-protocol.md](docs/razer-protocol.md).

</details>

---

MIT licensed. Wheelguard is an independent project and is not affiliated with or endorsed by
Endgame Gear, Logitech or Razer.
