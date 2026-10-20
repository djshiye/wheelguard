%global app_id io.github.djshiye.Wheelguard

Name:           wheelguard
Version:        1.2.0
# tools/publish.sh appends .<commit count> so every build from main is a newer release.
Release:        1%{?release_suffix}%{?dist}
Summary:        Scroll bounce filter and advanced settings for the Endgame Gear XM2w 4k v2

License:        MIT
URL:            https://github.com/djshiye/wheelguard
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  python3-devel
# for the import check in %%check
BuildRequires:  python3-evdev
BuildRequires:  desktop-file-utils
BuildRequires:  appstream
BuildRequires:  systemd-rpm-macros

Requires:       python3-pyside6
Requires:       python3-evdev
Requires:       hicolor-icon-theme

%description
A small per-user daemon grabs the Endgame Gear wireless mouse (USB 3367:1970),
drops the bouncing wrong-direction scroll-wheel ticks of a faulty encoder and
passes everything else through untouched. The Wheelguard app (Qt Quick) tunes
the filter with a live view of the wheel, edits the mouse's on-board settings
(DPI, polling rate, lift-off distance, button filtering, sleep, battery) and
the GNOME pointer settings, and sits in the system tray for a quick look.
Not affiliated with Endgame Gear.

%prep
%autosetup -n %{name}-%{version}

%build

%install
# The whole package tree, including gui/qml (with its qmldir) and any hw/ modules.
install -d %{buildroot}%{python3_sitelib}
cp -r wheelguard %{buildroot}%{python3_sitelib}/
find %{buildroot}%{python3_sitelib}/wheelguard -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot}%{python3_sitelib}/wheelguard -type d -exec chmod 0755 {} +
find %{buildroot}%{python3_sitelib}/wheelguard -type f -exec chmod 0644 {} +

install -d %{buildroot}%{_bindir} %{buildroot}%{_libexecdir}
printf '#!%{python3}\nimport sys\nfrom wheelguard.gui.app import main\nsys.exit(main())\n' > %{buildroot}%{_bindir}/wheelguard-settings
printf '#!%{python3}\nimport sys\nfrom wheelguard.daemon import main\nsys.exit(main())\n' > %{buildroot}%{_libexecdir}/wheelguard-daemon
chmod 0755 %{buildroot}%{_bindir}/wheelguard-settings %{buildroot}%{_libexecdir}/wheelguard-daemon

install -Dpm 0644 build-aux/wheelguard.service %{buildroot}%{_userunitdir}/wheelguard.service
install -Dpm 0644 build-aux/80-wheelguard.preset %{buildroot}%{_userpresetdir}/80-wheelguard.preset
install -Dpm 0644 build-aux/70-wheelguard.rules %{buildroot}%{_udevrulesdir}/70-wheelguard.rules
install -Dpm 0644 build-aux/%{app_id}.desktop %{buildroot}%{_datadir}/applications/%{app_id}.desktop
install -Dpm 0644 build-aux/%{app_id}-autostart.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/%{app_id}.desktop
install -Dpm 0644 build-aux/%{app_id}.svg %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
install -Dpm 0644 build-aux/%{app_id}.metainfo.xml %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
# The app loads its header/About logo from next to its gui files; point that at the themed icon.
ln -s ../../../../../share/icons/hicolor/scalable/apps/%{app_id}.svg %{buildroot}%{python3_sitelib}/wheelguard/gui/%{app_id}.svg

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
desktop-file-validate %{buildroot}%{_sysconfdir}/xdg/autostart/%{app_id}.desktop
appstreamcli validate --no-net --explain %{buildroot}%{_metainfodir}/%{app_id}.metainfo.xml
PYTHONPATH=%{buildroot}%{python3_sitelib} %{python3} -c "import wheelguard.core, wheelguard.config, wheelguard.frames, wheelguard.control, wheelguard.ipc"

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
%{python3_sitelib}/wheelguard/
%{_bindir}/wheelguard-settings
%{_libexecdir}/wheelguard-daemon
%{_userunitdir}/wheelguard.service
%{_userpresetdir}/80-wheelguard.preset
%{_udevrulesdir}/70-wheelguard.rules
%{_datadir}/applications/%{app_id}.desktop
%config(noreplace) %{_sysconfdir}/xdg/autostart/%{app_id}.desktop
%{_datadir}/icons/hicolor/scalable/apps/%{app_id}.svg
%{_metainfodir}/%{app_id}.metainfo.xml

%changelog
* Tue Oct 20 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.2.0-1
- Tray icon with a quick-view popup (battery, scroll protection, DPI stage) and a menu
- Closing the window hides it to the tray; a second launch brings the running window forward
- Start in the tray at login (autostart entry, switch on the About page)

* Tue Oct 20 2026 Dino Klisura <115914552+djshiye@users.noreply.github.com> - 1.1.0-1
- First RPM: per-user service, udev rule (input, uinput, hidraw) and app installed under /usr
- Hardware mouse settings page for the Endgame Gear XM2w 4k v2
