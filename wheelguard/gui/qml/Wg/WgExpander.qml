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
        height: Math.max(44, Theme.px(44))
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: root.title
        Accessible.description: root.subtitle
        Accessible.checkable: true
        Accessible.checked: root.expanded
        Accessible.onPressAction: root.expanded = !root.expanded
        Keys.onSpacePressed: root.expanded = !root.expanded
        Keys.onReturnPressed: root.expanded = !root.expanded
        Keys.onEnterPressed: root.expanded = !root.expanded
        WgFocusRing {}
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
            enabled: root.expanded          // collapsed controls stay out of the Tab order
            opacity: root.expanded ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        }
    }
}
