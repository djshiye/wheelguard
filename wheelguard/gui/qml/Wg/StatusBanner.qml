import QtQuick
import QtQuick.Layouts

// Explains every non-running daemon state and offers the next step.
Item {
    id: root
    property string daemonState: "connecting"
    property string detail: ""
    property bool busy: false
    property string serviceError: ""
    property bool suppressed: false     // hidden on pages that don't involve the daemon
    signal startRequested()
    signal retryRequested()

    property string ss: "stopped"   // last non-running state, kept while collapsing
    onDaemonStateChanged: if (daemonState === "stopped" || daemonState === "permission" || daemonState === "waiting") ss = daemonState
    Component.onCompleted: if (shown) ss = daemonState
    readonly property bool shown: !suppressed && (daemonState === "stopped" || daemonState === "permission" || daemonState === "waiting")
    readonly property color tone: ss === "waiting" ? Theme.amber : Theme.coral
    readonly property string icon: ss === "stopped" ? "power" : (ss === "permission" ? "lock" : "clock")
    readonly property string title: ss === "stopped" ? "The Wheelguard service isn't running"
        : (ss === "permission" ? "Wheelguard can't access your mouse" : "Waiting for mouse…")
    readonly property string body: ss === "stopped"
        ? "Scroll protection works through a small background service. Start it to protect your wheel; settings below apply once it is running."
        : (ss === "permission"
           ? "The device-access (udev) rule is missing, so the service isn't allowed to read the mouse. Re-run ./install.sh from the Wheelguard folder and follow the printed steps, then replug the dongle."
           : "The service is running but can't see the mouse. Wake it with a click or a scroll, and check that the dongle is plugged in.")

    implicitHeight: shown ? card.implicitHeight + 16 : 0
    height: implicitHeight
    clip: true
    opacity: shown ? 1 : 0
    Behavior on implicitHeight { NumberAnimation { duration: Theme.slow; easing.type: Easing.InOutCubic } }
    Behavior on opacity { NumberAnimation { duration: Theme.normal } }

    Rectangle {
        id: card
        x: 28
        y: 16
        width: parent.width - 56
        implicitHeight: lay.implicitHeight + 32
        radius: Theme.radius
        color: Theme.alpha(root.tone, Theme.dark ? 0.09 : 0.10)
        border.width: 1
        border.color: Theme.alpha(root.tone, 0.32)
        Behavior on color { ColorAnimation { duration: Theme.normal } }
        Behavior on border.color { ColorAnimation { duration: Theme.normal } }

        RowLayout {
            id: lay
            anchors.fill: parent
            anchors.margins: 16
            spacing: 16

            Rectangle {
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40
                radius: 12
                color: Theme.alpha(root.tone, 0.16)
                WgIcon {
                    id: ico
                    anchors.centerIn: parent
                    name: root.icon
                    size: 20
                    color: root.tone
                    RotationAnimation on rotation {
                        running: root.ss === "waiting" && root.shown
                        from: 0; to: 360
                        duration: 2400
                        loops: Animation.Infinite
                        onRunningChanged: if (!running) ico.rotation = 0
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    Layout.fillWidth: true
                    text: root.title
                    font.pixelSize: Theme.fsBody + 1
                    font.weight: Font.DemiBold
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    text: root.body
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    lineHeight: 1.15
                }
                Rectangle {
                    visible: root.ss === "permission"
                    Layout.topMargin: 4
                    implicitWidth: cmd.implicitWidth + 20
                    implicitHeight: 28
                    radius: 8
                    color: Theme.inset
                    border.width: 1
                    border.color: Theme.border
                    Text {
                        id: cmd
                        anchors.centerIn: parent
                        text: "./install.sh"
                        font.family: "monospace"
                        font.pixelSize: Theme.fsSmall + 1
                        color: Theme.text
                    }
                }
                Text {
                    visible: root.ss === "permission" && root.detail !== ""
                    Layout.fillWidth: true
                    text: root.detail
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textFaint
                    wrapMode: Text.WrapAnywhere
                }
                Text {
                    visible: root.serviceError !== "" && root.ss === "stopped"
                    Layout.fillWidth: true
                    text: root.serviceError
                    font.pixelSize: Theme.fsSmall
                    color: Theme.coral
                    wrapMode: Text.WordWrap
                }
            }

            WgButton {
                Layout.alignment: Qt.AlignVCenter
                visible: root.ss === "stopped"
                variant: "primary"
                iconName: "play"
                text: root.busy ? "Starting…" : "Start service"
                enabled: !root.busy
                onClicked: root.startRequested()
            }
            WgButton {
                Layout.alignment: Qt.AlignVCenter
                visible: root.ss === "permission"
                text: "Check again"
                iconName: "reset"
                onClicked: root.retryRequested()
            }
        }
    }
}
