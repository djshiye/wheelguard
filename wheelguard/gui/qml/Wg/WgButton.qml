import QtQuick
import QtQuick.Controls.Basic as C

// variant: "primary" (accent gradient) | "secondary" | "ghost"
C.Button {
    id: control
    property string variant: "secondary"
    property string iconName: ""
    readonly property bool primary: variant === "primary"

    implicitHeight: 36
    implicitWidth: row.implicitWidth + 32
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    opacity: enabled ? 1 : 0.5
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }

    HoverHandler { cursorShape: Qt.PointingHandCursor }

    background: Rectangle {
        radius: Theme.radiusSm - 2
        color: control.primary ? "transparent"
               : (control.variant === "ghost" ? (control.down ? Theme.border : (control.hovered ? Theme.hover : "transparent"))
                  : (control.down ? Theme.borderHi : (control.hovered ? Theme.cardHi : Theme.card)))
        border.width: control.primary ? 0 : 1
        border.color: control.visualFocus ? Theme.accentB : (control.variant === "ghost" ? "transparent" : (control.hovered ? Theme.borderHi : Theme.border))
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }

        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            visible: control.primary
            opacity: control.down ? 0.8 : (control.hovered ? 1 : 0.92)
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accentA }
                GradientStop { position: 1; color: Theme.accentB }
            }
        }
    }

    contentItem: Item {
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 8
            WgIcon {
                visible: control.iconName !== ""
                anchors.verticalCenter: parent.verticalCenter
                name: control.iconName
                size: 16
                color: control.primary ? "white" : Theme.text
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: control.text
                font.pixelSize: Theme.fsBody
                font.weight: Font.Medium
                color: control.primary ? "white" : Theme.text
            }
        }
    }
}
