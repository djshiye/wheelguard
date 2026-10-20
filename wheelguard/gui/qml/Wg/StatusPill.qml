import QtQuick
import QtQuick.Layouts

// Compact daemon status in the header. Colour and text follow the daemon state.
Rectangle {
    id: root
    property string daemonState: "connecting"
    property string device: ""
    property bool protecting: true

    readonly property color tone: {
        if (daemonState === "running") return protecting ? Theme.good : Theme.amber
        if (daemonState === "waiting") return Theme.amber
        if (daemonState === "permission" || daemonState === "stopped") return Theme.coral
        return Theme.textMuted
    }
    readonly property string label: {
        if (daemonState === "running") return protecting ? "Protecting" : "Protection off"
        if (daemonState === "waiting") return "Waiting for mouse…"
        if (daemonState === "permission") return "No device access"
        if (daemonState === "stopped") return "Service not running"
        return "Connecting…"
    }
    readonly property string detail: daemonState === "running" ? device : ""

    implicitHeight: 34
    implicitWidth: row.implicitWidth + 28
    radius: height / 2
    color: Theme.alpha(tone, Theme.dark ? 0.12 : 0.14)
    border.width: 1
    border.color: Theme.alpha(tone, 0.38)
    Behavior on color { ColorAnimation { duration: Theme.normal } }
    Behavior on border.color { ColorAnimation { duration: Theme.normal } }
    Behavior on implicitWidth { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }

    RowLayout {
        id: row
        anchors.centerIn: parent
        spacing: 9

        Item {
            Layout.preferredWidth: 14
            Layout.preferredHeight: 14
            Rectangle {   // soft glow
                anchors.centerIn: parent
                width: 14; height: 14; radius: 7
                color: root.tone
                opacity: 0.28
                Behavior on color { ColorAnimation { duration: Theme.normal } }
            }
            Rectangle {
                anchors.centerIn: parent
                width: 8; height: 8; radius: 4
                color: root.tone
                Behavior on color { ColorAnimation { duration: Theme.normal } }
            }
        }
        Text {
            text: root.label
            font.pixelSize: Theme.fsSmall + 1
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            visible: root.detail !== ""
            Layout.maximumWidth: 240
            text: root.detail
            elide: Text.ElideRight
            font.pixelSize: Theme.fsSmall
            color: Theme.textMuted
        }
    }
}
