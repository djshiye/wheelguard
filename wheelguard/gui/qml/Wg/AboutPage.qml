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
            text: "DPI, polling rate and button remapping aren't available: this mouse's protocol isn't public, so there is no way to read or change them. Pointer options in this app are GNOME's own settings."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            lineHeight: 1.15
        }
    }
}
