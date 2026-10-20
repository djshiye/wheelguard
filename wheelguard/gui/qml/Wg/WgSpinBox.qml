import QtQuick
import QtQuick.Controls.Basic as C

C.SpinBox {
    id: control
    property string suffix: " ms"

    implicitWidth: 124
    implicitHeight: 36
    editable: true
    hoverEnabled: true
    leftPadding: 32
    rightPadding: 32
    textFromValue: function(v) { return v + control.suffix }
    valueFromText: function(t) {
        var n = parseInt(t)
        return isNaN(n) ? control.value : n
    }
    validator: IntValidator { bottom: control.from; top: control.to }

    background: Rectangle {
        radius: Theme.radiusSm - 2
        color: Theme.inset
        border.width: 1
        border.color: control.activeFocus ? Theme.accentB : (control.hovered ? Theme.borderHi : Theme.border)
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }

    contentItem: TextInput {
        text: control.displayText
        font.pixelSize: Theme.fsBody
        font.weight: Font.Medium
        color: Theme.text
        selectionColor: Theme.accentA
        selectedTextColor: "white"
        horizontalAlignment: Qt.AlignHCenter
        verticalAlignment: Qt.AlignVCenter
        readOnly: !control.editable
        validator: control.validator
        inputMethodHints: Qt.ImhDigitsOnly
    }

    down.indicator: Item {
        x: 0
        width: 32
        height: parent.height
        Rectangle {
            anchors.fill: parent
            anchors.margins: 3
            radius: Theme.radiusSm - 5
            color: Theme.hover
            opacity: control.down.pressed ? 1 : (control.down.hovered ? 0.7 : 0)
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
        WgIcon {
            anchors.centerIn: parent
            name: "minimize"
            size: 14
            color: control.value > control.from ? Theme.text : Theme.textFaint
        }
    }

    up.indicator: Item {
        x: parent.width - width
        width: 32
        height: parent.height
        Rectangle {
            anchors.fill: parent
            anchors.margins: 3
            radius: Theme.radiusSm - 5
            color: Theme.hover
            opacity: control.up.pressed ? 1 : (control.up.hovered ? 0.7 : 0)
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
        WgIcon {
            anchors.centerIn: parent
            name: "plus"
            size: 14
            color: control.value < control.to ? Theme.text : Theme.textFaint
        }
    }
}
