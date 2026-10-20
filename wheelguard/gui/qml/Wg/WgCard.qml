import QtQuick
import QtQuick.Layouts

Rectangle {
    id: root
    property int pad: 24
    property int vpad: pad
    property alias spacing: inner.spacing
    default property alias content: inner.data

    implicitHeight: inner.implicitHeight + vpad * 2
    radius: Theme.radius
    color: Theme.card
    border.width: 1
    border.color: Theme.border

    Behavior on color { ColorAnimation { duration: Theme.normal } }

    ColumnLayout {
        id: inner
        anchors.fill: parent
        anchors.leftMargin: root.pad
        anchors.rightMargin: root.pad
        anchors.topMargin: root.vpad
        anchors.bottomMargin: root.vpad
        spacing: 16
    }
}
