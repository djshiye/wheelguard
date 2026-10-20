import QtQuick
import QtQuick.Controls.Basic as C

C.Switch {
    id: control
    property real trackHeight: 28
    readonly property real trackWidth: Math.round(trackHeight * 1.8)
    readonly property real thumbSize: trackHeight - 8

    implicitWidth: trackWidth
    implicitHeight: trackHeight
    padding: 0
    hoverEnabled: true
    focusPolicy: Qt.TabFocus

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    indicator: Rectangle {
        implicitWidth: control.trackWidth
        implicitHeight: control.trackHeight
        radius: height / 2
        color: Theme.track
        border.width: 1
        border.color: control.visualFocus ? Theme.accentB
                      : (control.hovered && !control.checked ? Theme.borderHi : Theme.border)
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }

        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            opacity: control.checked ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accentA }
                GradientStop { position: 1; color: Theme.accentB }
            }
        }

        Rectangle {
            id: thumb
            property real w: control.pressed ? control.thumbSize + 4 : control.thumbSize
            width: w
            height: control.thumbSize
            radius: height / 2
            y: 4
            x: control.checked ? parent.width - width - 4 : 4
            color: "white"
            Behavior on x { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            Behavior on w { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
            border.width: 1
            border.color: Qt.rgba(0, 0, 0, 0.12)
        }
    }

    contentItem: Item {}
}
