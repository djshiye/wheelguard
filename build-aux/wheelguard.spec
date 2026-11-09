%global app_id io.github.djshiye.Wheelguard
%global shell_ext wheelguard@djshiye.github.io
# One package for every Fedora: the code lives in %{_datadir}/%{name} (not in a
# python3.X site-packages) and runs on the system's /usr/bin/python3, so nothing
# may pin a Python version. Nothing should generate one; this is the safety net.
%global __requires_exclude ^python\\(abi\\)
%global __brp_python_bytecompile %{nil}

Name:           wheelguard
Version:        1.6.0
# CI appends .<commit count> so every build from main is a newer release.
Release:        1%{?release_suffix}%{?dist}
Summary:        Scroll bounce filter and advanced mouse settings (Endgame Gear, Logitech, Razer)

License:        MIT
URL:            https://github.com/djshiye/wheelguard
Source0:        %{name}-%{version}.tar.gz
# The public package signing key; CI exports it from the signing secret.
Source1:        RPM-GPG-KEY-djshiye
BuildArch:      noarch

BuildRequires:  python3
# for the import check in %%check
BuildRequires:  python3-evdev
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
BuildRequires:  systemd-rpm-macros

Requires:       python3-pyside6
Requires:       python3-evdev
Requires:       hicolor-icon-theme

%description
A small per-user daemon grabs a supported mouse (Endgame Gear XM2w 4k v2,
Logitech G Pro X Superlight, Razer DeathAdder V4 Pro), drops the bouncing
wrong-direction scroll-wheel ticks of a faulty encoder and passes everything
else through untouched. It also offers Click-Lock: hold the left button and
Wheelguard keeps clicking, or keeps it pressed, until the next click. The
Wheelguard app (Qt Quick) tunes the filter with a live view of the wheel, edits
the mouse's on-board settings (DPI, polling rate and more, per model) and the
GNOME pointer settings. A GNOME Shell extension adds a panel icon with a quick popup.
Not affiliated with Endgame Gear, Logitech or Razer.

%prep
%autosetup -n %{name}-%{version}

%build

%install
# The whole package tree, including gui/qml (with its qmldir) and any hw/ modules.
install -d %{buildroot}%{_datadir}/%{name}
cp -r wheelguard %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name} -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot}%{_datadir}/%{name} -type d -exec chmod 0755 {} +
find %{buildroot}%{_datadir}/%{name} -type f -exec chmod 0644 {} +

install -d %{buildroot}%{_bindir} %{buildroot}%{_libexecdir}
printf '#!/usr/bin/python3 -sP\nimport sys\nsys.path.insert(0, "%{_datadir}/%{name}")\nfrom wheelguard.gui.app import main\nsys.exit(main())\n' > %{buildroot}%{_bindir}/wheelguard-settings
printf '#!/usr/bin/python3 -sP\nimport sys\nsys.path.insert(0, "%{_datadir}/%{name}")\nfrom wheelguard.daemon import main\nsys.exit(main())\n' > %{buildroot}%{_libexecdir}/wheelguard-daemon
chmod 0755 %{buildroot}%{_bindir}/wheelguard-settings %{buildroot}%{_libexecdir}/wheelguard-daemon

install -Dpm 0644 build-aux/wheelguard.service %{buildroot}%{_userunitdir}/wheelguard.service
install -Dpm 0644 build-aux/80-wheelguard.preset %{buildroot}%{_userpresetdir}/80-wheelguard.preset
install -Dpm 0644 build-aux/70-wheelguard.rules %{buildroot}%{_udevrulesdir}/70-wheelguard.rules
install -Dpm 0644 build-aux/%{app_id}.desktop %{buildroot}%{_datadir}/applications/%{app_id}.desktop
install -Dpm 0644 build-aux/%{app_id}-autostart.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/%{app_id}.desktop
install -Dpm 0644 build-aux/%{app_id}.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
install -Dpm 0644 build-aux/%{app_id}.metainfo.xml %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
# The panel icon: a GNOME Shell extension (enabled for the user at the next login by `wheelguard-settings --tray`).
install -d %{buildroot}%{_datadir}/gnome-shell/extensions/%{shell_ext}/icons
install -pm 0644 build-aux/gnome-extension/{metadata.json,extension.js,stylesheet.css} %{buildroot}%{_datadir}/gnome-shell/extensions/%{shell_ext}/
install -pm 0644 build-aux/gnome-extension/icons/*.svg %{buildroot}%{_datadir}/gnome-shell/extensions/%{shell_ext}/icons/
# The app loads its header/About logo from next to its gui files; point that at the themed icon.
ln -s ../../../icons/hicolor/scalable/apps/%{app_id}.svg %{buildroot}%{_datadir}/%{name}/wheelguard/gui/%{app_id}.svg
# The DNF repository definition and its signing key, so the repository keeps itself configured.
install -Dpm 0644 build-aux/%{name}.repo %{buildroot}%{_sysconfdir}/yum.repos.d/%{name}.repo
install -Dpm 0644 %{SOURCE1} %{buildroot}%{_sysconfdir}/pki/rpm-gpg/RPM-GPG-KEY-djshiye-%{name}

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
desktop-file-validate %{buildroot}%{_sysconfdir}/xdg/autostart/%{app_id}.desktop
appstreamcli validate --no-net --explain %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
PYTHONPATH=%{buildroot}%{_datadir}/%{name} /usr/bin/python3 -c "import wheelguard.core, wheelguard.config, wheelguard.frames, wheelguard.control, wheelguard.ipc, wheelguard.clicklock, wheelguard.hotplug, wheelguard.hw.drivers"

%post
%systemd_user_post wheelguard.service
# Apply the udev rule now so the mouse, uinput and hidraw are usable without a reboot.
udevadm control --reload >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=input --subsystem-match=misc --subsystem-match=hidraw >/dev/null 2>&1 || :
udevadm settle --timeout=5 >/dev/null 2>&1 || :
# First install: start the service for everyone already logged in.
# (A personal package: the preset above enables it for future logins.)
if [ $1 -eq 1 ]; then
    for unit in $(systemctl list-units 'user@*.service' --state=running --no-legend --plain 2>/dev/null | cut -d' ' -f1); do
        uid=${unit#user@}; uid=${uid%%.service}
        user=$(id -nu "$uid" 2>/dev/null) || continue
        systemctl --user -M "$user@" start wheelguard.service >/dev/null 2>&1 || :
    done
fi

%preun
%systemd_user_preun wheelguard.service

%postun
%systemd_user_postun_with_restart wheelguard.service

%files
%license LICENSE
%doc README.md PROTOCOL.md
%{_datadir}/%{name}/
%{_bindir}/wheelguard-settings
%{_libexecdir}/wheelguard-daemon
%{_userunitdir}/wheelguard.service
%{_userpresetdir}/80-wheelguard.preset
%{_udevrulesdir}/70-wheelguard.rules
%{_datadir}/applications/%{app_id}.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/%{app_id}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_metainfodir}/%{app_id}.metainfo.xml
%{_datadir}/gnome-shell/extensions/%{shell_ext}/
# Plain %%config, not noreplace: an upgrade replaces an older hand-added copy
# (saved as .rpmorig/.rpmsave), which is how users of the old per-Fedora
# repository move to the signed one.
%config %{_sysconfdir}/yum.repos.d/%{name}.repo
%{_sysconfdir}/pki/rpm-gpg/RPM-GPG-KEY-djshiye-%{name}

%changelog
* Mon Nov 09 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.6.0-1
- Per-game profiles: pick an app, such as a game, and Wheelguard switches scroll protection, Click-Lock and the DPI stage while it is in front, then back to your settings
- Double-click fix: ignores the extra click a worn mouse button sends, for every mouse
- Scroll protection works with any mouse that has a wheel; pick it in the new Mouse box
- Wheel health: a 7 and 30 day chart of how often the wheel needed blocking, with a warning if it starts bouncing more
- Auto-tune: watches a minute of your scrolling and suggests the lightest protection strength that fixes it
- Click-Lock lets go by itself after 10 minutes (adjustable), and when the computer sleeps or the screen locks
- Click-Lock in Repeat mode no longer starts while you are dragging
- Low-battery alerts at 20% and 5%, with an amber dot on the panel icon
- Saved mouse setups: store your mouse settings under a name and switch in one click, from the app or the panel
- Keyboard shortcuts for Click-Lock and the next DPI stage, added from the About page
- A drawing of your mouse on the Mouse page that lights up the button you are editing
- A short welcome for new users that checks the mouse, permissions and protection strength
- The status pill shows your mouse's real name and how it is connected
- Calmer panel popup when the mouse is asleep; hover the icon to see the battery
- Copy diagnostics: one click on About copies a privacy-safe report for bug reports
- Works with screen readers and the keyboard, and follows GNOME's Large Text and Reduce Animation settings
- The mouse is picked up the moment it wakes or is plugged in
- The background service runs in a tight sandbox: read-only system and home folder, no network

* Mon Nov 09 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.5.1-1
- Fix: a click at the exact moment Wheelguard took over the mouse could still leave the button stuck
- Fix: clicks, scrolls and moves made while Wheelguard was starting could happen twice
- Fix: a broken config file or a bad request could stop the scroll filter service
- Fix: switching on the Endgame sleep timer could save only part of the changes to the mouse
- Fix: the Logitech mouse could be set to a DPI stage that doesn't exist
- Fix: the panel popup could show controls after the app had closed

* Mon Nov 09 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.5.0-1
- Click-Lock: hold the left button for a set time (default 5 s) and Wheelguard keeps clicking, or keeps
  the button pressed, until the next click
- Switch it on and off, pick the mode and set the hold time from the panel popup or the Pointer page
- New mice: Logitech G Pro X Superlight (first generation) and Razer DeathAdder V4 Pro, with the scroll
  filter and their on-board settings (DPI stages, polling rate, battery; sleep timer on the Razer)
- Fix: the left button could stay pressed after the mouse woke up or reconnected while it was held

* Wed Nov 04 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.4.0-1
- One package for every Fedora release: installed under /usr/share/wheelguard, runs on any Python 3
- Signed packages and repository metadata, published by GitHub Actions on every push
- The package installs its own repository file and key
- The panel extension works on GNOME 51 (Fedora 45) as well as GNOME 50

* Wed Nov 04 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.3.1-1
- Build for Fedora 45 (Python 3.15) as well as Fedora 44

* Wed Oct 21 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.3.0-1
- Panel icon as a GNOME Shell extension: one click opens an instant popup (battery, scroll protection, DPI stage)
- The app owns a D-Bus name for the popup and for single instance; the maximized overlay popup is gone
- The extension is switched on once by the tray autostart; the StatusNotifier tray stays as a fallback

* Tue Oct 20 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.2.1-1
- Keep the QML cache per version so an upgrade never loads stale components

* Tue Oct 20 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.2.0-1
- Tray icon with a quick-view popup (battery, scroll protection, DPI stage) and a menu
- Closing the window hides it to the tray; a second launch brings the running window forward
- Start in the tray at login (autostart entry, switch on the About page)

* Tue Oct 20 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.1.0-1
- First RPM: per-user service, udev rule (input, uinput, hidraw) and app installed under /usr
- Hardware mouse settings page for the Endgame Gear XM2w 4k v2
