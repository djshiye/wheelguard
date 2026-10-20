import QtQuick

// Segmented control with a sliding gradient highlight. currentIndex -1 hides the highlight.
Item {
    id: root
    property var model: []
    property var subLabels: []
    property int currentIndex: -1
    signal activated(int index)

    readonly property int pad: 4
    readonly property real cellW: (width - pad * 2) / Math.max(1, model.length)
    readonly property bool hasSub: subLabels.length > 0

    implicitHeight: hasSub ? 60 : 44
    implicitWidth: 320

    Rectangle {
        id: track
        anchors.fill: parent
        radius: Theme.radiusSm + 2
        color: Theme.inset
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: Theme.normal } }
    }

    Rectangle {
        id: pill
        y: root.pad
        height: root.height - root.pad * 2
        width: root.cellW
        x: root.pad + Math.max(0, root.currentIndex) * root.cellW
        radius: Theme.radiusSm
        opacity: root.currentIndex >= 0 ? 1 : 0
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Theme.accentA }
            GradientStop { position: 1; color: Theme.accentB }
        }
        Behavior on x { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
        Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    }

    Repeater {
        model: root.model
        delegate: Item {
            id: cell
            required property int index
            required property var modelData
            readonly property bool selected: index === root.currentIndex
            x: root.pad + index * root.cellW
            y: root.pad
            width: root.cellW
            height: root.height - root.pad * 2

            Rectangle {
                anchors.fill: parent
                anchors.margins: 0
                radius: Theme.radiusSm
                color: Theme.hover
                opacity: ma.containsMouse && !cell.selected ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.fast } }
            }

            Column {
                anchors.centerIn: parent
                spacing: 2
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: cell.modelData
                    font.pixelSize: Theme.fsBody
                    font.weight: cell.selected ? Font.DemiBold : Font.Medium
                    color: cell.selected ? "white" : Theme.text
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
                Text {
                    visible: root.hasSub
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.hasSub ? (root.subLabels[cell.index] || "") : ""
                    font.pixelSize: Theme.fsSmall - 1
                    color: cell.selected ? Qt.rgba(1, 1, 1, 0.85) : Theme.textMuted
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
            }

            MouseArea {
                id: ma
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: root.activated(cell.index)
            }
        }
    }
}
