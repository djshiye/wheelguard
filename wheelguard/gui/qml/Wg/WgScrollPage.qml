import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic as C

// Scrolling page with a slim custom scrollbar. Children go into `content` (a Column of max width).
Flickable {
    id: root
    default property alias content: col.data
    property int maxContentWidth: 760
    property alias spacing: col.spacing

    contentWidth: width
    contentHeight: col.implicitHeight + 40
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    flickableDirection: Flickable.VerticalFlick

    // Keyboard navigation: scroll the control that just got focus into view.
    Connections {
        target: root.Window.window
        function onActiveFocusItemChanged() {
            const it = root.Window.window ? root.Window.window.activeFocusItem : null
            if (!it || !root.visible)
                return
            let p = it.parent
            while (p && p !== col)
                p = p.parent
            if (!p)
                return                                  // not on this page
            const r = it.mapToItem(col, 0, 0)
            const top = col.y + r.y - 16
            const bottom = col.y + r.y + it.height + 16
            if (top < root.contentY)
                root.contentY = Math.max(0, top)
            else if (bottom > root.contentY + root.height)
                root.contentY = Math.min(root.contentHeight - root.height, bottom - root.height)
        }
    }

    C.ScrollBar.vertical: C.ScrollBar {
        id: bar
        policy: C.ScrollBar.AsNeeded
        implicitWidth: 10
        contentItem: Rectangle {
            implicitWidth: 4
            radius: 2
            color: Theme.textFaint
            opacity: bar.pressed ? 0.8 : (bar.hovered || bar.active ? 0.5 : 0.0)
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        }
        background: Item {}
    }

    Column {
        id: col
        x: Math.max(28, (root.width - width) / 2)
        y: 20
        width: Math.min(root.maxContentWidth, root.width - 56)
        spacing: 14
    }
}
