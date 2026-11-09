import QtQuick
import QtQuick.Controls.Basic as C

// Single-line text input in the app's look. `placeholder` shows while empty.
C.TextField {
    id: control
    property string placeholder: ""

    implicitHeight: 36
    implicitWidth: 220
    leftPadding: 12
    rightPadding: 12
    font.pixelSize: Theme.fsBody
    color: Theme.text
    selectionColor: Theme.alpha(Theme.accentA, 0.45)
    selectedTextColor: Theme.text
    placeholderText: placeholder
    placeholderTextColor: Theme.textFaint
    verticalAlignment: TextInput.AlignVCenter
    hoverEnabled: true
    selectByMouse: true
    Accessible.role: Accessible.EditableText
    Accessible.name: placeholder

    background: Rectangle {
        radius: Theme.radiusSm - 2
        color: Theme.inset
        border.width: control.activeFocus ? 2 : 1
        border.color: control.activeFocus ? Theme.accentB : (control.hovered ? Theme.borderHi : Theme.border)
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
}
