import QtQuick

// Segmented control with a sliding gradient highlight. currentIndex -1 hides the highlight.
// Keyboard: Tab focuses it, Left/Right (or Up/Down) pick the neighbouring option. Screen readers see a
// group named `accessibleName` holding one radio button per option.
Item {
    id: root
    property var model: []
    property var subLabels: []
    property int currentIndex: -1
    property string accessibleName: ""
    property bool muted: false          // still usable, but the setting it belongs to is off: neutral pill
    signal activated(int index)

    activeFocusOnTab: enabled
    Accessible.role: Accessible.Grouping
    Accessible.name: accessibleName
    function step(d) {
        const n = model.length
        if (n === 0)
            return
        const i = currentIndex < 0 ? (d > 0 ? 0 : n - 1) : Math.max(0, Math.min(n - 1, currentIndex + d))
        if (i !== currentIndex)
            activated(i)
    }
    Keys.onLeftPressed: step(-1)
    Keys.onRightPressed: step(1)
    Keys.onUpPressed: step(-1)
    Keys.onDownPressed: step(1)

    readonly property int pad: 4
    readonly property real cellW: (width - pad * 2) / Math.max(1, model.length)
    readonly property bool hasSub: subLabels.length > 0

    implicitHeight: Theme.px(hasSub ? 60 : 44)
    implicitWidth: 320

    Rectangle {
        id: track
        anchors.fill: parent
        radius: Theme.radiusSm + 2
        color: Theme.inset
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: Theme.normal } }
        WgFocusRing { target: root; radiusExtra: Theme.radiusSm }
    }

    Rectangle {
        id: pill
        y: root.pad
        height: root.height - root.pad * 2
        width: root.cellW
        x: root.pad + Math.max(0, root.currentIndex) * root.cellW
        radius: Theme.radiusSm
        opacity: root.currentIndex >= 0 ? 1 : 0
        // Switched off: a plain neutral pill, so the choice still reads clearly but doesn't look active.
        color: Theme.track
        gradient: root.enabled && !root.muted ? accent : null
        Gradient {
            id: accent
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
            readonly property bool lit: cell.selected && root.enabled && !root.muted
            Accessible.role: Accessible.RadioButton
            Accessible.name: cell.modelData + (root.hasSub && root.subLabels[cell.index] ? ", " + root.subLabels[cell.index] : "")
            Accessible.checkable: true
            Accessible.checked: cell.selected
            Accessible.onPressAction: root.activated(cell.index)
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
                    color: cell.lit ? "white" : Theme.text
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
                Text {
                    visible: root.hasSub
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: root.hasSub ? (root.subLabels[cell.index] || "") : ""
                    font.pixelSize: Theme.fsSmall - 1
                    color: cell.lit ? Qt.rgba(1, 1, 1, 0.85) : Theme.textMuted
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
