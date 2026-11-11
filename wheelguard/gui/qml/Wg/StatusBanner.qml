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
    property bool chosenDenied: false   // a mouse picked by hand (not a supported model) that we may not read
    property string otherMouse: ""      // Automatic found no supported mouse, but this other wheel mouse is connected
    property bool otherMouseAccessible: false
    property bool canAllow: false       // "Allow this mouse" is available (the installed package's helper)
    property bool allowBusy: false      // the administrator password dialog is open
    property string allowError: ""
    signal startRequested()
    signal useOtherMouseRequested()
    signal automaticRequested()
    signal allowRequested()
    signal retryRequested()

    property string ss: "stopped"   // last non-running state, kept while collapsing
    onDaemonStateChanged: if (daemonState === "stopped" || daemonState === "permission" || daemonState === "waiting") ss = daemonState
    Component.onCompleted: if (shown) ss = daemonState
    readonly property bool shown: !suppressed && (daemonState === "stopped" || daemonState === "permission" || daemonState === "waiting")
    readonly property bool unrecognised: ss === "waiting" && otherMouse !== ""
    readonly property color tone: ss === "waiting" ? Theme.amber : Theme.coral
    readonly property string icon: ss === "stopped" ? "power" : (ss === "permission" ? "lock" : (unrecognised ? "mouse" : "clock"))
    readonly property bool denied: ss === "permission" && chosenDenied
    readonly property bool allowable: canAllow && (denied || (unrecognised && !otherMouseAccessible))
    readonly property string title: ss === "stopped" ? "The Wheelguard service isn't running"
        : (ss === "permission" ? (denied ? "Wheelguard can't read the mouse you chose" : "Wheelguard can't access your mouse")
           : (unrecognised ? "Your mouse isn't a supported model" : "Waiting for mouse…"))
    readonly property string body: ss === "stopped"
        ? "Scroll protection works through a small background service. Start it to protect your wheel; settings below apply once it is running."
        : (ss === "permission"
           ? (denied
              ? (canAllow
                 ? "Wheelguard isn't allowed to read this mouse yet. Allow it once with your administrator password, or pick another mouse below."
                 : "Wheelguard isn't allowed to read this mouse. Only supported mice get access automatically. Switch back to Automatic.")
              : "The device-access (udev) rule is missing, so the service isn't allowed to read the mouse. Reinstall the wheelguard package (sudo dnf reinstall wheelguard), then log out and back in.")
           : (!unrecognised
              ? "The service is running but can't see the mouse. Wake it with a click or a scroll, and check that the dongle is plugged in."
              : (otherMouseAccessible
                 ? "Wheelguard sees your " + otherMouse + ", but Automatic only takes over supported mice. Use it anyway to protect its wheel too."
                 : (canAllow
                    ? "Wheelguard sees your " + otherMouse + ", but isn't allowed to read it yet. Allow it once with your administrator password and Wheelguard protects its wheel too."
                    : "Wheelguard sees your " + otherMouse + ", but only supported mice get device access, so it can't protect this one yet. Supported: Endgame Gear XM2w 4k v2, Logitech G Pro X Superlight (first generation) and Razer DeathAdder V4 Pro."))))

    implicitHeight: shown ? card.implicitHeight + 16 : 0
    height: implicitHeight
    clip: true
    opacity: shown ? 1 : 0
    enabled: shown                  // a hidden banner's buttons stay out of the Tab order
    visible: opacity > 0
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
                        running: root.ss === "waiting" && !root.unrecognised && root.shown && Theme.motion
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
                    visible: root.ss === "permission" && !root.denied
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
                        text: "sudo dnf reinstall wheelguard"
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
                    visible: root.allowError !== "" && root.allowable
                    Layout.fillWidth: true
                    text: root.allowError
                    font.pixelSize: Theme.fsSmall
                    color: Theme.coral
                    wrapMode: Text.WordWrap
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
                visible: root.ss === "permission" && !root.denied
                text: "Check again"
                iconName: "reset"
                onClicked: root.retryRequested()
            }
            WgButton {
                Layout.alignment: Qt.AlignVCenter
                visible: root.unrecognised && root.otherMouseAccessible
                variant: "primary"
                text: "Use this mouse"
                onClicked: root.useOtherMouseRequested()
            }
            WgButton {
                Layout.alignment: Qt.AlignVCenter
                visible: root.allowable
                variant: "primary"
                iconName: "lock"
                text: root.allowBusy ? "Waiting for password…" : "Allow this mouse"
                enabled: !root.allowBusy
                onClicked: root.allowRequested()
            }
            WgButton {
                Layout.alignment: Qt.AlignVCenter
                visible: root.denied && !root.allowable
                variant: "primary"
                text: "Use Automatic"
                onClicked: root.automaticRequested()
            }
        }
    }
}
