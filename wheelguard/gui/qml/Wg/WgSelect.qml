import QtQuick
import QtQuick.Controls.Basic as C

// Drop-down chooser. currentIndex -1 shows `placeholder` (used for values the app doesn't know).
Item {
    id: root
    property var model: []
    property int currentIndex: -1
    property string placeholder: "Custom"
    property bool edited: false
    signal activated(int index)
    function open() { popup.open() }

    implicitWidth: 180
    implicitHeight: 36
    opacity: enabled ? 1 : 0.5
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    Rectangle {
        id: face
        anchors.fill: parent
        radius: Theme.radiusSm - 2
        color: Theme.inset
        border.width: 1
        border.color: popup.opened || root.activeFocus ? Theme.accentB : (ma.containsMouse ? Theme.borderHi : Theme.border)
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }

        Rectangle {
            visible: root.edited
            x: 8; anchors.verticalCenter: parent.verticalCenter
            width: 6; height: 6; radius: 3
            color: Theme.accentB
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            x: 18
            width: parent.width - 18 - 34
            elide: Text.ElideRight
            text: root.currentIndex >= 0 ? root.model[root.currentIndex] : root.placeholder
            font.pixelSize: Theme.fsBody
            font.weight: Font.Medium
            color: root.currentIndex >= 0 ? Theme.text : Theme.textMuted
        }
        WgIcon {
            anchors.verticalCenter: parent.verticalCenter
            x: parent.width - 28
            name: "chevron"
            size: 14
            rotation: popup.opened ? -90 : 90
            color: Theme.textMuted
            Behavior on rotation { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
        }
    }

    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: popup.opened ? popup.close() : popup.open()
    }

    C.Popup {
        id: popup
        y: root.height + 4
        width: root.width
        padding: 5
        modal: false
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside

        background: Rectangle {
            radius: Theme.radiusSm
            color: Theme.card
            border.width: 1
            border.color: Theme.borderHi
        }
        contentItem: Column {
            spacing: 2
            Repeater {
                model: root.model
                delegate: Rectangle {
                    id: opt
                    required property int index
                    required property var modelData
                    width: parent.width
                    height: 34
                    radius: Theme.radiusSm - 4
                    color: optMa.containsMouse ? Theme.hover : "transparent"
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        x: 12
                        text: opt.modelData
                        font.pixelSize: Theme.fsBody
                        font.weight: opt.index === root.currentIndex ? Font.DemiBold : Font.Normal
                        color: opt.index === root.currentIndex ? Theme.accentB : Theme.text
                    }
                    MouseArea {
                        id: optMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { popup.close(); root.activated(opt.index) }
                    }
                }
            }
        }
    }
}
