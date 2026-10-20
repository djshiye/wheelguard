import QtQuick

// Battery drawn from rectangles: body, nub and a fill scaled by `percent` (-1 = unknown).
Item {
    id: root
    property int percent: -1
    property bool charging: false
    readonly property color tone: percent < 0 ? Theme.textFaint
        : (percent <= 15 ? Theme.coral : (percent <= 30 ? Theme.amber : Theme.good))

    implicitWidth: 30
    implicitHeight: 14

    Rectangle {
        id: body
        width: parent.width - 3
        height: parent.height
        radius: 4
        color: "transparent"
        border.width: 1.5
        border.color: root.tone
        Rectangle {
            x: 3; y: 3
            height: parent.height - 6
            width: Math.max(root.percent > 0 ? 3 : 0, (parent.width - 6) * Math.max(0, root.percent) / 100)
            radius: 2
            color: root.tone
            Behavior on width { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
        }
    }
    Rectangle {   // nub
        x: body.width + 0.5
        anchors.verticalCenter: parent.verticalCenter
        width: 2.5
        height: 6
        radius: 1
        color: root.tone
    }
    WgIcon {
        visible: root.charging
        anchors.centerIn: body
        name: "bolt"
        size: 12
        filled: true
        strokeWidth: 1
        color: Theme.dark ? "#0A0B0E" : "white"
    }
}
