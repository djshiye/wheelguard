import QtQuick

// Keyboard focus ring for custom controls: drop it inside the focusable item. Mouse clicks don't move
// focus, so it shows only while someone navigates with Tab and the arrow keys.
Rectangle {
    property var target: parent          // any Item; Rectangles pass their radius on to the ring
    property real radiusExtra: 0
    anchors.fill: parent
    anchors.margins: -2
    radius: (target && target["radius"] !== undefined ? target["radius"] : Theme.radiusSm) + 2 + radiusExtra
    color: "transparent"
    border.width: 2
    border.color: Theme.focusRing
    visible: target !== null && target.activeFocus
    z: 100
}
