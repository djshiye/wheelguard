import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as C

// Opens under the status pill: which mouse Wheelguard protects, access to mice it may not read yet, and Pause.
C.Popup {
    id: root
    readonly property var rows: daemon.mouseRows
    readonly property bool reachable: daemon.state !== "stopped" && daemon.state !== "connecting"
    readonly property var chosen: {
        for (let i = 0; i < rows.length; i++)
            if (rows[i].selected)
                return rows[i]
        return null
    }

    width: Theme.px(400)
    padding: 8
    modal: false
    focus: true
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutsideParent

    enter: Transition {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast }
    }

    background: Rectangle {
        radius: Theme.radius
        color: Theme.card
        border.width: 1
        border.color: Theme.borderHi
        Rectangle {     // soft shadow
            z: -1
            anchors.fill: parent
            anchors.margins: -1
            anchors.topMargin: 3
            anchors.bottomMargin: -5
            radius: parent.radius + 2
            color: "black"
            opacity: Theme.dark ? 0.35 : 0.10
        }
    }

    contentItem: ColumnLayout {
        spacing: 2

        Text {
            Layout.leftMargin: 12
            Layout.topMargin: 6
            Layout.bottomMargin: 4
            text: "MOUSE TO PROTECT"
            font.pixelSize: Theme.fsSmall - 1
            font.weight: Font.DemiBold
            font.letterSpacing: 0.8
            color: Theme.textFaint
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            Layout.bottomMargin: 6
            visible: !root.reachable
            text: "Start the Wheelguard service to choose a mouse."
            font.pixelSize: Theme.fsSmall
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: root.reachable ? root.rows : []
            delegate: Rectangle {
                id: opt
                required property var modelData
                readonly property bool needsAccess: modelData.note === "no access"
                readonly property bool usable: !needsAccess || mouseAccess.available
                Layout.fillWidth: true
                implicitHeight: Math.max(Theme.px(52), optRow.implicitHeight + 16)
                radius: Theme.radiusSm
                color: optMa.containsMouse || activeFocus ? Theme.hover : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                activeFocusOnTab: true
                Accessible.role: Accessible.RadioButton
                Accessible.name: modelData.name + (modelData.note !== "" ? ", " + modelData.note : "")
                Accessible.checked: modelData.selected
                Accessible.onPressAction: pick()
                Keys.onSpacePressed: pick()
                Keys.onReturnPressed: pick()
                Keys.onEnterPressed: pick()
                WgFocusRing {}

                function pick() {
                    if (opt.needsAccess) {
                        if (mouseAccess.available && !mouseAccess.busy)
                            mouseAccess.allow(opt.modelData.id)      // Main.qml switches to it once allowed
                        return
                    }
                    if (!opt.modelData.selected)
                        daemon.setMouse(opt.modelData.id)
                    root.close()
                }

                MouseArea {
                    id: optMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: opt.usable ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: opt.pick()
                }
                RowLayout {
                    id: optRow
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 10
                    spacing: 12

                    Rectangle {     // radio mark
                        Layout.preferredWidth: 18
                        Layout.preferredHeight: 18
                        radius: 9
                        color: "transparent"
                        border.width: opt.modelData.selected ? 5 : 1.5
                        border.color: opt.modelData.selected ? Theme.accentB : Theme.textFaint
                        Behavior on border.width { NumberAnimation { duration: Theme.fast } }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            Layout.fillWidth: true
                            text: opt.modelData.name
                            elide: Text.ElideRight
                            font.pixelSize: Theme.fsBody
                            font.weight: opt.modelData.selected ? Font.DemiBold : Font.Medium
                            color: opt.usable ? Theme.text : Theme.textMuted
                        }
                        Text {
                            Layout.fillWidth: true
                            visible: text !== ""
                            text: {
                                const parts = []
                                if (opt.modelData.detail !== "") parts.push(opt.modelData.detail)
                                if (opt.modelData.active) parts.push("protected now")
                                if (opt.modelData.note === "not tested") parts.push("not tested")
                                if (opt.modelData.note === "not connected") parts.push("not connected")
                                if (opt.needsAccess) parts.push(mouseAccess.available ? "needs your permission" : "no access")
                                return parts.join(" · ")
                            }
                            elide: Text.ElideRight
                            font.pixelSize: Theme.fsSmall
                            color: opt.modelData.active ? Theme.good : Theme.textMuted
                        }
                    }
                    WgButton {
                        visible: opt.needsAccess && mouseAccess.available
                        text: mouseAccess.busy ? "Waiting…" : "Allow"
                        iconName: "lock"
                        enabled: !mouseAccess.busy
                        onClicked: opt.pick()
                    }
                }
            }
        }

        // What the current choice means, in one line each.
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            Layout.topMargin: 6
            visible: root.reachable && root.chosen !== null && root.chosen.note === "not tested"
            text: "We haven't tested this mouse. Scroll protection should work. If the wheel misbehaves, switch back to Automatic."
            font.pixelSize: Theme.fsSmall
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 10
            Layout.topMargin: 6
            visible: root.reachable && root.chosen !== null && root.chosen.allowed && mouseAccess.available
            spacing: 10
            Text {
                Layout.fillWidth: true
                text: "You allowed Wheelguard to read this mouse. Other programs you run can read its movement and clicks too."
                font.pixelSize: Theme.fsSmall
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
            WgButton {
                variant: "ghost"
                text: mouseAccess.busy ? "Waiting…" : "Remove access"
                enabled: !mouseAccess.busy
                onClicked: mouseAccess.remove(root.chosen.id)
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 12
            Layout.topMargin: 6
            visible: mouseAccess.error !== ""
            text: mouseAccess.error
            font.pixelSize: Theme.fsSmall
            color: Theme.coral
            wrapMode: Text.WordWrap
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: 8
            Layout.bottomMargin: 4
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            Layout.preferredHeight: 1
            color: Theme.border
        }

        // Pause: the service lets go of the mouse completely until resumed.
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 12
            Layout.rightMargin: 10
            Layout.bottomMargin: 6
            spacing: 12
            enabled: daemon.configKnown
            opacity: enabled ? 1 : 0.5
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    text: daemon.paused ? "Wheelguard is paused" : "Pause Wheelguard"
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.Medium
                    color: Theme.text
                }
                Text {
                    Layout.fillWidth: true
                    text: daemon.paused ? "It has let go of your mouse. Nothing is filtered."
                                        : "Lets go of your mouse completely until you resume."
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            WgButton {
                variant: daemon.paused ? "primary" : "secondary"
                text: daemon.paused ? "Resume" : "Pause"
                iconName: daemon.paused ? "play" : "power"
                onClicked: daemon.setPaused(!daemon.paused)
            }
        }
    }
}
