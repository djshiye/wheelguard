import QtQuick
import QtQuick.Layouts

// Compact daemon status in the header. Colour and text follow the daemon state.
Rectangle {
    id: root
    property string daemonState: "connecting"
    property string device: ""
    property bool protecting: true
    property bool compact: false        // shorter labels for the tray popup
    property bool unrecognised: false   // waiting, and the mouse that is connected isn't a supported model
    property bool interactive: false    // a button that opens the mouse picker
    property bool open: false           // the picker is showing
    signal clicked()

    activeFocusOnTab: interactive
    Accessible.role: interactive ? Accessible.Button : Accessible.StaticText
    Accessible.name: label + (detail !== "" ? ", " + detail : "") + (interactive ? ". Choose the mouse to protect, or pause." : "")
    Accessible.onPressAction: if (interactive) clicked()
    Keys.onSpacePressed: if (interactive) clicked()
    Keys.onReturnPressed: if (interactive) clicked()
    Keys.onEnterPressed: if (interactive) clicked()
    WgFocusRing {}

    readonly property color tone: {
        if (daemonState === "running") return protecting ? Theme.good : Theme.amber
        if (daemonState === "waiting" || daemonState === "paused") return Theme.amber
        if (daemonState === "permission" || daemonState === "stopped") return Theme.coral
        return Theme.textMuted
    }
    readonly property string label: {
        if (daemonState === "running") return protecting ? "Protecting" : (compact ? "Off" : "Protection off")
        if (daemonState === "paused") return "Paused"
        if (daemonState === "waiting")
            return unrecognised ? (compact ? "Unsupported" : "Mouse not supported") : (compact ? "No mouse" : "Waiting for mouse…")
        if (daemonState === "permission") return compact ? "No access" : "No device access"
        if (daemonState === "stopped") return compact ? "Stopped" : "Service not running"
        return "Connecting…"
    }
    // At Large Text the connection suffix ("· dongle") goes, so the model name still fits.
    readonly property string detail: {
        if (daemonState !== "running")
            return ""
        return Theme.textScale > 1.2 ? device.split(" · ")[0] : device
    }

    implicitHeight: Theme.px(34)
    implicitWidth: row.implicitWidth + 28
    radius: height / 2
    color: Theme.alpha(tone, (Theme.dark ? 0.12 : 0.14) + (pillMa.containsMouse || open ? 0.08 : 0))
    border.width: 1
    border.color: Theme.alpha(tone, open ? 0.7 : 0.38)
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
            Layout.maximumWidth: Theme.px(240)
            text: root.detail
            elide: Text.ElideRight
            font.pixelSize: Theme.fsSmall
            color: Theme.textMuted
        }
        WgIcon {
            visible: root.interactive
            name: "chevron"
            size: 12
            rotation: root.open ? -90 : 90
            color: Theme.textMuted
            Behavior on rotation { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
        }
    }
    MouseArea {
        id: pillMa
        anchors.fill: parent
        enabled: root.interactive
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
