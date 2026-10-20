import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import Wg

// Quick view opened from the tray icon. Wayland clients can't position windows, so this is a
// transparent frameless window shown maximized (it covers the work area, which excludes the panel)
// with the card drawn in the bottom-right corner. The window's input mask is cut down to the card
// (shell.setPopupMask), so clicks anywhere else go to the windows underneath; losing activation hides it.
Window {
    id: pop
    property bool preview: false        // screenshot harness: a small plain window on a fake desktop
    readonly property int edge: preview ? 24 : 12
    property bool armed: false          // has been active at least once since it was shown

    color: "transparent"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    title: "Wheelguard"
    width: preview ? card.width + 2 * edge : 640
    height: preview ? card.height + 2 * edge : 480
    visible: false

    function pushMask() {
        if (visible && !preview) shell.setPopupMask(card.x, card.y, card.width, card.height)
    }
    onVisibleChanged: {
        if (visible) {
            armed = false
            noFocus.restart()
            intro.restart()
            pushMask()
        } else {
            noFocus.stop()
        }
    }
    onActiveChanged: {
        if (active) armed = true
        else if (armed && visible && !preview) shell.hidePopup()
    }
    // If the compositor never gives us focus, losing it can't hide the card: don't leave it up forever.
    Timer { id: noFocus; interval: 2500; onTriggered: if (!pop.armed && !pop.preview) shell.hidePopup() }
    Shortcut { sequence: "Escape"; onActivated: shell.hidePopup() }

    Rectangle {
        anchors.fill: parent
        visible: pop.preview
        color: Theme.dark ? "#1A1B21" : "#CBCED9"
    }

    Item {
        id: card
        width: 340
        height: col.implicitHeight + 32
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: pop.edge
        onXChanged: pop.pushMask()
        onYChanged: pop.pushMask()
        onWidthChanged: pop.pushMask()
        onHeightChanged: pop.pushMask()

        transform: Translate { id: slide }
        ParallelAnimation {
            id: intro
            NumberAnimation { target: card; property: "opacity"; from: 0; to: 1; duration: 170; easing.type: Easing.OutCubic }
            NumberAnimation { target: slide; property: "y"; from: 18; to: 0; duration: 200; easing.type: Easing.OutCubic }
        }

        Repeater {      // shadow, as in Main.qml
            model: 10
            delegate: Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -index * 1.4
                anchors.topMargin: -index * 1.4 + 3
                radius: 18 + index * 1.4
                color: "black"
                opacity: Theme.dark ? 0.050 : 0.022
            }
        }
        Rectangle {
            anchors.fill: parent
            radius: 18
            color: Theme.chrome
            border.width: 1
            border.color: Theme.borderHi
            Behavior on color { ColorAnimation { duration: Theme.normal } }
        }

        ColumnLayout {
            id: col
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 16
            spacing: 10

            // ---------------------------------------------------- header
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Image {
                    Layout.preferredWidth: 28
                    Layout.preferredHeight: 28
                    source: iconUrl
                    sourceSize: Qt.size(56, 56)
                    smooth: true
                    mipmap: true
                }
                Text {
                    text: "Wheelguard"
                    font.pixelSize: Theme.fsBody + 1
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Item { Layout.fillWidth: true }
                StatusPill {
                    implicitHeight: 28
                    compact: true
                    daemonState: daemon.state
                    protecting: daemon.enabled
                }
                Rectangle {
                    Layout.preferredWidth: 30
                    Layout.preferredHeight: 30
                    radius: 9
                    color: openMa.pressed ? Theme.border : (openMa.containsMouse ? Theme.hover : "transparent")
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    WgIcon { anchors.centerIn: parent; name: "external"; size: 16; color: Theme.textMuted }
                    MouseArea {
                        id: openMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: shell.showMain()
                    }
                }
            }

            // ---------------------------------------------------- battery
            WgCard {
                Layout.fillWidth: true
                pad: 16
                vpad: 14
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Item {
                        Layout.preferredWidth: 42
                        Layout.preferredHeight: 20
                        BatteryGlyph {
                            anchors.verticalCenter: parent.verticalCenter
                            scale: 1.35
                            transformOrigin: Item.Left
                            percent: mouse.online ? mouse.battery : -1
                            charging: mouse.online && mouse.charging
                        }
                    }
                    Text {
                        text: mouse.online && mouse.battery >= 0 ? mouse.battery + "%" : "—"
                        font.pixelSize: Theme.fsTitle
                        font.weight: Font.DemiBold
                        color: mouse.online && mouse.battery >= 0 ? Theme.text : Theme.textFaint
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: !mouse.online ? "Mouse not connected" : (mouse.charging ? "Charging" : "Battery")
                        font.pixelSize: Theme.fsSmall
                        color: mouse.online && mouse.charging ? Theme.good : Theme.textMuted
                    }
                }
            }

            // ---------------------------------------------------- scroll protection
            WgCard {
                Layout.fillWidth: true
                pad: 16
                vpad: 14
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: "Scroll protection"
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            font.pixelSize: Theme.fsSmall
                            color: Theme.textMuted
                            text: {
                                if (daemon.state === "stopped" || daemon.state === "permission") return "Service not running"
                                var s = daemon.enabled ? daemon.presetName : "Paused · " + daemon.presetName
                                return daemon.dropped > 0 ? s + " · " + daemon.dropped + " dropped" : s
                            }
                        }
                    }
                    WgSwitch {
                        id: protect
                        enabled: daemon.state === "running" || daemon.state === "waiting"
                        opacity: enabled ? 1 : 0.5
                        onToggled: daemon.setEnabled(checked)
                        Binding { target: protect; property: "checked"; value: daemon.enabled }
                    }
                }
            }

            // ---------------------------------------------------- DPI
            WgCard {
                Layout.fillWidth: true
                pad: 16
                vpad: 14
                spacing: 10
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: "DPI"
                        font.pixelSize: Theme.fsBody
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Text {
                        visible: mouse.activeStage >= 0 && mouse.activeStage < mouse.stageLabels.length
                        text: visible ? mouse.stageLabels[mouse.activeStage] + " DPI" : ""
                        font.pixelSize: Theme.fsBody
                        font.weight: Font.DemiBold
                        color: Theme.dark ? "#B79DFF" : Theme.accentA
                    }
                }
                WgSegmented {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 40
                    visible: mouse.stageCount > 0
                    model: mouse.stageLabels
                    currentIndex: mouse.activeStage
                    enabled: mouse.stagesEnabled
                    opacity: enabled ? 1 : 0.5
                    onActivated: (index) => mouse.setActiveStage(index)
                    WheelHandler {      // scroll over the control to step through the stages
                        enabled: mouse.stagesEnabled
                        onWheel: (ev) => {
                            var n = mouse.activeStage + (ev.angleDelta.y > 0 ? 1 : -1)
                            if (n >= 0 && n < mouse.stageCount) mouse.setActiveStage(n)
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: mouse.stageReason !== ""
                    text: mouse.stageReason
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
        }
    }
}
