import QtQuick
import QtQuick.Layouts

Item {
    id: root
    property string title: ""
    property string subtitle: ""
    property bool expanded: false
    default property alias content: body.data

    implicitHeight: header.height + wrap.height
    implicitWidth: 300

    Rectangle {
        id: header
        width: parent.width
        height: 44
        radius: Theme.radiusSm
        color: ma.containsMouse ? Theme.hover : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.fast } }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 8
            anchors.rightMargin: 12
            spacing: 12
            WgIcon {
                name: "chevron"
                size: 16
                color: Theme.textMuted
                rotation: root.expanded ? 90 : 0
                Behavior on rotation { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            }
            Text {
                text: root.title
                font.pixelSize: Theme.fsBody
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                Layout.fillWidth: true
                text: root.subtitle
                elide: Text.ElideRight
                font.pixelSize: Theme.fsSmall
                color: Theme.textMuted
            }
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: root.expanded = !root.expanded
        }
    }

    Item {
        id: wrap
        y: header.height
        width: parent.width
        height: root.expanded ? body.implicitHeight + 16 : 0
        clip: true
        Behavior on height { NumberAnimation { duration: Theme.slow; easing.type: Easing.InOutCubic } }

        ColumnLayout {
            id: body
            x: 0
            y: 8
            width: parent.width
            spacing: 20
            opacity: root.expanded ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        }
    }
}
