import QtQuick
import QtQuick.Controls.Basic as C

C.Slider {
    id: control
    property string valueText: ""
    property color tint: Theme.accentA

    implicitHeight: 32
    implicitWidth: 200
    padding: 0
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Item {
        x: control.leftPadding
        y: control.topPadding
        width: control.availableWidth
        height: control.availableHeight

        Rectangle {
            id: groove
            width: parent.width
            height: 6
            radius: 3
            anchors.verticalCenter: parent.verticalCenter
            color: Theme.track
            Behavior on color { ColorAnimation { duration: Theme.normal } }

            Rectangle {
                width: Math.max(height, control.handle.x + control.handle.width / 2)
                height: parent.height
                radius: 3
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Theme.accentA }
                    GradientStop { position: 1; color: Theme.accentB }
                }
            }
        }
    }

    handle: Item {
        id: handleItem
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + (control.availableHeight - height) / 2
        width: 20
        height: 20

        Rectangle {  // halo
            anchors.centerIn: parent
            width: 20 + 14
            height: width
            radius: width / 2
            color: Theme.accentB
            opacity: control.pressed ? 0.22 : (control.hovered ? 0.12 : 0)
            scale: control.pressed ? 1.0 : 0.8
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
        }
        Rectangle {
            anchors.fill: parent
            radius: 10
            color: "white"
            border.width: 1
            border.color: Qt.rgba(0, 0, 0, 0.18)
            scale: control.pressed ? 1.08 : 1
            Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
        }

        Rectangle {  // value bubble
            visible: control.valueText !== "" && opacity > 0
            opacity: control.pressed ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            y: -height - 6 - (control.pressed ? 0 : -4)
            Behavior on y { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
            anchors.horizontalCenter: parent.horizontalCenter
            width: bubbleText.implicitWidth + 20
            height: 26
            radius: 8
            color: Theme.dark ? "#2A2E3B" : "#20232D"
            Text {
                id: bubbleText
                anchors.centerIn: parent
                text: control.valueText
                color: "white"
                font.pixelSize: Theme.fsSmall
                font.weight: Font.Medium
            }
        }
    }
}
