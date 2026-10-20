import QtQuick
import QtQuick.Layouts

WgScrollPage {
    id: page
    maxContentWidth: 640

    Item {
        width: parent.width
        height: 132

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
                    font.pixelSize: 32
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    text: "Advanced mouse settings"
                    font.pixelSize: Theme.fsH2
                    color: Theme.textMuted
                }
                Text {
                    text: daemon.version !== "" ? "Service version " + daemon.version : "Service not connected"
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textFaint
                }
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
        spacing: 12
        Text {
            text: "How it works"
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            Layout.fillWidth: true
            text: "Some scroll-wheel encoders occasionally report a scroll-down as a scroll-up. A small background service takes over your mouse, drops those bouncing ticks, and passes everything else through with no added latency. This app only changes the service's settings and shows what it is doing."
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
            text: "DPI stages, polling rate, lift-off distance, click filters, button actions and sleep timers are stored on the mouse itself. This app reads them over the dongle (or the cable) and writes them only when you press Apply, after saving a backup of the original settings. Wheelguard reads the mouse when it starts and now and then (battery and DPI stage, for the panel icon), and writes only when you press Apply or switch the DPI stage from the panel icon."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            lineHeight: 1.15
        }
        Text {
            Layout.fillWidth: true
            text: "Endgame Gear doesn't publish this protocol. It was reverse-engineered from the vendor's Windows configuration tool by reading its code, and hasn't been verified on every mouse and firmware. Wheelguard is an independent project and isn't affiliated with or endorsed by Endgame Gear."
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
