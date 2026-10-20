#!/usr/bin/env bash
# Install wheelguard for the current user. Run as your normal user; it asks for sudo once (only if
# packages or the udev rule need installing). Re-running upgrades in place.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run ./install.sh as your normal user, not root (it uses sudo itself where needed)." >&2
    exit 1
fi

here=$(cd "$(dirname "$0")" && pwd)
data="$HOME/.local/share/wheelguard"
bin="$HOME/.local/bin"
apps="$HOME/.local/share/applications"
icons="$HOME/.local/share/icons/hicolor"
unit_dir="$HOME/.config/systemd/user"
app_id=io.github.djshiye.Wheelguard
rule_src="$here/build-aux/70-wheelguard.rules"
rule_dst=/etc/udev/rules.d/70-wheelguard.rules

if [ -t 1 ]; then
    b=$'\e[1m'; g=$'\e[32m'; y=$'\e[33m'; c=$'\e[36m'; r=$'\e[0m'
else
    b=""; g=""; y=""; c=""; r=""
fi
step() { printf '%s==>%s %s%s%s\n' "$c" "$r" "$b" "$*" "$r"; }
warn() { printf '%s!%s  %s\n' "$y" "$r" "$*" >&2; }

for f in wheelguard/__init__.py wheelguard-daemon build-aux/70-wheelguard.rules \
         build-aux/wheelguard.service build-aux/$app_id.desktop; do
    [ -e "$here/$f" ] || { echo "Missing $f: run this from a full checkout." >&2; exit 1; }
done

# ---- 1. system part: packages + udev rule, one sudo call ----
step "Checking system packages and the udev rule"
missing=""
for pkg in python3-pyside6 python3-evdev; do
    rpm -q "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done
missing=${missing# }
rule_ok=0
cmp -s "$rule_src" "$rule_dst" 2>/dev/null && rule_ok=1

if [ -n "$missing" ] || [ "$rule_ok" -eq 0 ]; then
    [ -z "$missing" ] || echo "   will install: $missing"
    [ "$rule_ok" -eq 1 ] || echo "   will install the udev rule (mouse + /dev/uinput access)"
    echo "   (sudo password needed once)"
    # shellcheck disable=SC2016  # the script is meant to be expanded by the root shell
    sudo sh -c '
        set -eu
        pkgs=$1; src=$2; dst=$3
        if [ -n "$pkgs" ]; then
            # shellcheck disable=SC2086
            dnf install -y $pkgs
        fi
        if ! cmp -s "$src" "$dst"; then
            install -m 0644 "$src" "$dst"
            udevadm control --reload
            udevadm trigger --subsystem-match=input --action=change
            udevadm trigger --subsystem-match=misc --action=change
        fi
    ' wheelguard-install "$missing" "$rule_src" "$rule_dst"
else
    echo "   nothing to do"
fi

# ---- 2. program files ----
step "Installing program files to ~/.local/share/wheelguard"
mkdir -p "$data" "$bin"
rm -rf "$data/wheelguard"
tar -C "$here" --exclude=__pycache__ -cf - wheelguard | tar -C "$data" -xf -
for script in wheelguard-daemon wheelguard-settings; do
    if [ -f "$here/$script" ]; then
        install -m 0755 "$here/$script" "$data/$script"
        printf '#!/bin/sh\nexec /usr/bin/python3 "%s/%s" "$@"\n' "$data" "$script" > "$bin/$script.new"
        chmod 0755 "$bin/$script.new"
        mv -f "$bin/$script.new" "$bin/$script"
    else
        warn "$script not found in the checkout, skipped"
    fi
done

# ---- 3. desktop entry + icon ----
step "Installing the Wheelguard app entry"
mkdir -p "$apps"
sed "s|^Exec=.*|Exec=$bin/wheelguard-settings|" "$here/build-aux/$app_id.desktop" > "$apps/$app_id.desktop"
if [ -f "$here/build-aux/$app_id.svg" ]; then
    mkdir -p "$icons/scalable/apps"
    install -m 0644 "$here/build-aux/$app_id.svg" "$icons/scalable/apps/$app_id.svg"
    # the app's header/About logo is looked up next to the package
    install -D -m 0644 "$here/build-aux/$app_id.svg" "$data/build-aux/$app_id.svg"
fi
update-desktop-database "$apps" >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t "$icons" >/dev/null 2>&1 || true

# ---- 4. user service ----
step "Starting the wheelguard service"
mkdir -p "$unit_dir"
install -m 0644 "$here/build-aux/wheelguard.service" "$unit_dir/wheelguard.service"
systemctl --user daemon-reload
systemctl --user enable wheelguard.service >/dev/null
systemctl --user restart wheelguard.service

# ---- 5. summary ----
echo
printf '%sWheelguard is installed and running.%s\n' "$g$b" "$r"
echo "  Open ${b}Wheelguard${r} from your app launcher (or run: wheelguard-settings)."
echo "  Status: systemctl --user status wheelguard    Logs: journalctl --user -u wheelguard -f"
case ":$PATH:" in
    *":$bin:"*) ;;
    *) warn "$bin is not on your PATH; log out and in, or add it, to run wheelguard-settings from a terminal." ;;
esac
if [ -n "$missing" ] || [ "$rule_ok" -eq 0 ]; then
    echo "  If the mouse shows as 'permission denied' in the app, unplug and replug the dongle once."
fi
