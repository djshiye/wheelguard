#!/usr/bin/env bash
# Remove wheelguard. Keeps ~/.config/wheelguard/config.json unless --purge is given.
# Installed packages (python3-pyside6, python3-evdev) are left alone.
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "Run ./uninstall.sh as your normal user, not root (it uses sudo itself where needed)." >&2
    exit 1
fi

purge=0
case "${1:-}" in
    "") ;;
    --purge) purge=1 ;;
    *) echo "usage: $0 [--purge]" >&2; exit 2 ;;
esac

app_id=io.github.djshiye.Wheelguard
rule_dst=/etc/udev/rules.d/70-wheelguard.rules

if [ -t 1 ]; then
    b=$'\e[1m'; g=$'\e[32m'; c=$'\e[36m'; r=$'\e[0m'
else
    b=""; g=""; c=""; r=""
fi
step() { printf '%s==>%s %s%s%s\n' "$c" "$r" "$b" "$*" "$r"; }

step "Stopping the service"
systemctl --user disable --now wheelguard.service >/dev/null 2>&1 || true

step "Removing files from your home directory"
rm -f "$HOME/.config/systemd/user/wheelguard.service" \
      "$HOME/.local/bin/wheelguard-daemon" \
      "$HOME/.local/bin/wheelguard-settings" \
      "$HOME/.local/share/applications/$app_id.desktop" \
      "$HOME/.local/share/icons/hicolor/scalable/apps/$app_id.svg"
rm -rf "$HOME/.local/share/wheelguard"
systemctl --user daemon-reload >/dev/null 2>&1 || true
update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
gtk-update-icon-cache -q -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

if [ -e "$rule_dst" ]; then
    step "Removing the udev rule (sudo password needed)"
    sudo sh -c '
        set -eu
        rm -f "$1"
        udevadm control --reload
        udevadm trigger --subsystem-match=input --action=change
        udevadm trigger --subsystem-match=misc --action=change
    ' wheelguard-uninstall "$rule_dst"
fi

if [ "$purge" -eq 1 ]; then
    rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/wheelguard"
    kept=""
else
    kept="  Your settings are kept in ~/.config/wheelguard (use --purge to delete them)."
fi

echo
printf '%sWheelguard is uninstalled.%s\n' "$g$b" "$r"
[ -z "$kept" ] || echo "$kept"
