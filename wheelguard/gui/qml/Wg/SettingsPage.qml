import QtQuick
import QtQuick.Layouts

WgScrollPage {
    id: page

    property bool copied: false
    Timer { id: copiedTimer; interval: 2500; onTriggered: page.copied = false }

    // A title and a sentence on the left, one button on the right.
    component ActionRow: RowLayout {
        id: actionRow
        property string title
        property string description
        default property alias button: buttonSlot.data
        Layout.fillWidth: true
        spacing: 16
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            Text { text: actionRow.title; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
            Text {
                Layout.fillWidth: true
                text: actionRow.description
                font.pixelSize: Theme.fsSmall
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }
        RowLayout { id: buttonSlot }
    }

    Item {
        width: parent.width
        height: Theme.px(132)

        Row {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 24
            Image {
                width: 96
                height: 96
                source: iconUrl
                sourceSize: Qt.size(192, 192)
                smooth: true
                mipmap: true
            }
            Column {
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Text {
                    text: "Wheelguard"
                    font.pixelSize: Theme.px(32)
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    text: "Advanced mouse settings"
                    font.pixelSize: Theme.fsH2
                    color: Theme.textMuted
                }
                Text {
                    text: "Version " + daemon.appVersion
                          + (daemon.version === "" ? " · service not connected"
                             : daemon.version !== daemon.appVersion ? " · service " + daemon.version : "")
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textFaint
                }
            }
        }
    }

    // After a package update the panel app and the service keep running the old code until restarted.
    WgCard {
        width: parent.width
        visible: daemon.updateNeeded !== ""
        height: visible ? implicitHeight : 0
        color: Theme.alpha(Theme.accentB, Theme.dark ? 0.10 : 0.08)
        border.color: Theme.alpha(Theme.accentB, 0.35)
        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            WgIcon { name: "sparkle"; size: 20; color: Theme.accentB; Layout.alignment: Qt.AlignTop }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: "Wheelguard was updated"
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    Layout.fillWidth: true
                    text: daemon.updateNeeded === "app"
                          ? "This window is still the version from before the update. Restart Wheelguard to finish it. Your settings stay as they are."
                          : "The background service is still version " + daemon.version + ". Restart it to finish the update. Your mouse keeps working, and protection is back within a second."
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: daemon.serviceError !== ""
                    text: daemon.serviceError
                    font.pixelSize: Theme.fsSmall
                    color: Theme.coral
                    wrapMode: Text.WordWrap
                }
            }
            WgButton {
                text: daemon.updateNeeded === "app" ? "Restart Wheelguard" : "Restart service"
                iconName: "reset"
                variant: "primary"
                enabled: !daemon.serviceBusy
                onClicked: daemon.updateNeeded === "app" ? daemon.restartApp() : daemon.restartService()
            }
        }
    }

    WgCard {
        width: parent.width
        visible: autostart.available
        height: visible ? implicitHeight : 0
        WgToggleRow {
            Layout.topMargin: 0
            Layout.bottomMargin: 0
            title: "Start in tray at login"
            description: "Keep the panel icon running in the background. Closing the window hides it there."
            checked: autostart.enabled
            onToggled: (value) => autostart.setEnabled(value)
        }
    }

    WgCard {
        width: parent.width
        spacing: 14
        WgToggleRow {
            Layout.topMargin: 0
            Layout.bottomMargin: 0
            title: "Low-battery alerts"
            description: "Get a desktop notification when the mouse battery drops to 20% and again at 5%. The panel icon also shows an amber dot at 20% or below."
            checked: appSettings.batteryAlerts
            onToggled: (value) => appSettings.setBatteryAlerts(value)
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
        ActionRow {
            visible: shortcuts.available
            title: "Keyboard shortcuts"
            description: shortcuts.message !== "" ? shortcuts.message
                         : (shortcuts.installed || shortcuts.partly
                            ? shortcuts.keys[0] + " toggles Click-Lock, " + shortcuts.keys[1] + " switches to the next DPI stage"
                              + (shortcuts.partly ? ". New: a shortcut that pauses Wheelguard, so it lets go of your mouse at once."
                                 : " and " + shortcuts.keys[2] + " pauses Wheelguard. Change the keys in GNOME Settings > Keyboard > Custom Shortcuts.")
                            : "Adds three GNOME shortcuts: Super+Alt+C toggles Click-Lock, Super+Alt+D switches to the next DPI stage and Super+Alt+P pauses Wheelguard. Your own shortcuts are left alone.")
            WgButton {
                text: shortcuts.installed ? "Added" : (shortcuts.partly ? "Add the Pause shortcut" : "Add keyboard shortcuts")
                iconName: shortcuts.installed ? "check" : "keyboard"
                enabled: !shortcuts.installed
                onClicked: shortcuts.add()
            }
        }
        Rectangle { visible: shortcuts.available; Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
        ActionRow {
            title: "Welcome tour"
            description: "The short setup that appears the first time you open Wheelguard: service, permissions and protection strength."
            WgButton {
                text: "Show welcome again"
                iconName: "sparkle"
                onClicked: appSettings.resetWelcome()
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
        ActionRow {
            title: "Diagnostics"
            description: "Copies a short report for bug reports: versions, your mouse model and what the Mouse page says. It has no names, folders or serial numbers."
            WgButton {
                text: page.copied ? "Copied" : "Copy diagnostics"
                iconName: page.copied ? "check" : "copy"
                onClicked: {
                    if (diagnostics.copy()) {
                        page.copied = true
                        copiedTimer.restart()
                    }
                }
            }
        }
    }

    WgCard {
        width: parent.width
        spacing: 12
        Text {
            text: "How it works"
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            Layout.fillWidth: true
            text: "Some scroll-wheel encoders occasionally report a scroll-down as a scroll-up. A small background service takes over your mouse, drops those bouncing ticks, and passes everything else through with no added latency. This app only changes the service's settings and shows what it is doing. The service runs in a sandbox: it has no network access and can write only its own settings. It needs to read your mouse and to create a virtual one in its place."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            lineHeight: 1.15
        }
    }

    WgCard {
        width: parent.width
        spacing: 12
        Text {
            text: "The Mouse page"
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            Layout.fillWidth: true
            text: "DPI stages, polling rate and, depending on the mouse, lift-off distance, click filters, button actions and sleep timers are stored on the mouse itself. This app reads them over the receiver (or the cable) and writes them only when you press Apply, after saving a backup of the original settings. Wheelguard reads the mouse when it starts and now and then (battery and DPI stage, for the panel icon), and writes only when you press Apply or switch the DPI stage from the panel icon."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            lineHeight: 1.15
        }
        Text {
            Layout.fillWidth: true
            text: "None of the vendors publish these protocols. The Endgame Gear one was reverse-engineered from the vendor's Windows configuration tool; the Logitech and Razer ones follow the open-source libratbag, Solaar and OpenRazer projects. They haven't been verified on every mouse and firmware. Wheelguard is an independent project and isn't affiliated with or endorsed by Endgame Gear, Logitech or Razer."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            lineHeight: 1.15
        }
    }

    WgCard {
        width: parent.width
        spacing: 10
        RowLayout {
            spacing: 10
            WgIcon { name: "info"; size: 18; color: Theme.textMuted }
            Text {
                text: "What isn't here"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
        }
        Text {
            Layout.fillWidth: true
            text: "Keyboard and macro button actions, LED effects, profiles and a scroll-wheel filter on the mouse aren't supported, because the protocol for them isn't known. Pointer options in this app are GNOME's own settings."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            lineHeight: 1.15
        }
    }
}
