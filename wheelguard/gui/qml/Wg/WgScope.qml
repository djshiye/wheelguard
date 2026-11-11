import QtQuick

// One quiet line under a page title: what the settings on this page apply to.
Row {
    id: root
    property string icon: "mouse"
    property string text: ""
    spacing: 8
    Accessible.role: Accessible.StaticText
    Accessible.name: root.text

    WgIcon {
        anchors.verticalCenter: parent.verticalCenter
        name: root.icon
        size: 15
        color: Theme.accentB
    }
    Text {
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, root.parent ? root.parent.width - 23 : implicitWidth)
        text: root.text
        elide: Text.ElideRight
        font.pixelSize: Theme.fsSmall
        font.weight: Font.Medium
        color: Theme.textMuted
    }
}
