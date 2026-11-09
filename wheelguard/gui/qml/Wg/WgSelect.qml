import QtQuick
import QtQuick.Controls.Basic as C

// Drop-down chooser. currentIndex -1 shows `placeholder` (used for values the app doesn't know).
Item {
    id: root
    property var model: []
    property int currentIndex: -1
    property string placeholder: "Custom"
    property bool edited: false
    property string accessibleName: ""
    signal activated(int index)
    function open() { popup.highlighted = Math.max(0, currentIndex); popup.open() }

    activeFocusOnTab: enabled
    Accessible.role: Accessible.ComboBox
    Accessible.name: accessibleName
    Accessible.description: currentIndex >= 0 ? model[currentIndex] : placeholder
    Accessible.onPressAction: open()
    function move(d) {
        if (popup.opened) {
            popup.highlighted = Math.max(0, Math.min(model.length - 1, popup.highlighted + d))
        } else if (model.length > 0) {
            const i = currentIndex < 0 ? 0 : Math.max(0, Math.min(model.length - 1, currentIndex + d))
            if (i !== currentIndex)
                activated(i)
        }
    }
    function confirm() {
        if (!popup.opened) {
            open()
        } else {
            popup.close()
            if (popup.highlighted >= 0 && popup.highlighted !== currentIndex)
                activated(popup.highlighted)
        }
    }
    Keys.onUpPressed: move(-1)
    Keys.onDownPressed: move(1)
    Keys.onSpacePressed: confirm()
    Keys.onReturnPressed: confirm()
    Keys.onEnterPressed: confirm()
    Keys.onEscapePressed: (event) => { event.accepted = popup.opened; popup.close() }

    implicitWidth: 180
    implicitHeight: Math.max(36, Theme.px(36))
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
        onClicked: popup.opened ? popup.close() : root.open()
    }

    C.Popup {
        id: popup
        property int highlighted: -1
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
                    height: Math.max(34, Theme.px(34))
                    radius: Theme.radiusSm - 4
                    color: optMa.containsMouse || opt.index === popup.highlighted ? Theme.hover : "transparent"
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
