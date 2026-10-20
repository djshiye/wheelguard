import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import Wg

Window {
    id: win
    property int pageIndex: 0
    property alias advancedOpen: scrollPage.advancedOpen

    readonly property bool maximized: visibility === Window.Maximized || visibility === Window.FullScreen
    readonly property int margin: maximized ? 0 : 14
    readonly property int cornerRadius: maximized ? 0 : 18

    width: 1008
    height: 708
    minimumWidth: 860
    minimumHeight: 600
    visible: true
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "Wheelguard"

    Binding { target: Theme; property: "dark"; value: appearance.dark }

    onActiveChanged: if (active) pointer.refresh()

    Shortcut { sequences: [StandardKey.Quit]; onActivated: Qt.quit() }

    // ------------------------------------------------------------ shadow (cheap layered rounded rects)
    Item {
        anchors.fill: parent
        visible: !win.maximized
        Repeater {
            model: 10
            delegate: Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: win.margin - 1 - index * 1.4
                anchors.topMargin: win.margin - 1 - index * 1.4 + 3
                radius: win.cornerRadius + index * 1.4
                color: "black"
                opacity: (Theme.dark ? 0.050 : 0.022)
            }
        }
    }

    // ------------------------------------------------------------ window frame
    Rectangle {
        id: frame
        anchors.fill: parent
        anchors.margins: win.margin
        radius: win.cornerRadius
        color: Theme.chrome
        border.width: 1
        border.color: Theme.borderHi
        Behavior on color { ColorAnimation { duration: Theme.normal } }

        // ---------------------------------------------------- header
        Item {
            id: header
            width: parent.width
            height: 64

            DragHandler {
                target: null
                onActiveChanged: if (active) win.startSystemMove()
            }
            TapHandler {
                onDoubleTapped: win.visibility = win.maximized ? Window.Windowed : Window.Maximized
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 22
                anchors.rightMargin: 14
                spacing: 12

                Image {
                    Layout.preferredWidth: 34
                    Layout.preferredHeight: 34
                    source: iconUrl
                    sourceSize: Qt.size(68, 68)
                    smooth: true
                    mipmap: true
                }
                Column {
                    spacing: 1
                    Text {
                        text: "Wheelguard"
                        font.pixelSize: Theme.fsH2
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Text {
                        text: "Advanced mouse settings"
                        font.pixelSize: Theme.fsSmall
                        color: Theme.textMuted
                    }
                }
                Item { Layout.fillWidth: true }

                StatusPill {
                    daemonState: daemon.state
                    device: daemon.device
                    protecting: daemon.enabled
                }

                Row {
                    spacing: 4
                    Layout.leftMargin: 8
                    Repeater {
                        model: [{ icon: "minimize", close: false }, { icon: "close", close: true }]
                        delegate: Rectangle {
                            id: wb
                            required property var modelData
                            width: 34; height: 34; radius: 10
                            color: ma.pressed ? (modelData.close ? Theme.alpha(Theme.coral, 0.35) : Theme.border)
                                   : (ma.containsMouse ? (modelData.close ? Theme.alpha(Theme.coral, 0.2) : Theme.hover) : "transparent")
                            Behavior on color { ColorAnimation { duration: Theme.fast } }
                            WgIcon {
                                anchors.centerIn: parent
                                name: wb.modelData.icon
                                size: 16
                                color: ma.containsMouse && wb.modelData.close ? Theme.coral : Theme.textMuted
                                Behavior on color { ColorAnimation { duration: Theme.fast } }
                            }
                            MouseArea {
                                id: ma
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: wb.modelData.close ? win.close() : win.showMinimized()
                            }
                        }
                    }
                }
            }
        }

        // ---------------------------------------------------- sidebar
        Item {
            id: sidebar
            x: 12
            y: header.height
            width: 204
            height: parent.height - header.height - 12

            Rectangle {   // moving selection highlight
                x: 0
                y: 4 + win.pageIndex * 52
                width: parent.width
                height: 44
                radius: Theme.radiusSm
                color: Theme.card
                border.width: 1
                border.color: Theme.border
                Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
                Behavior on color { ColorAnimation { duration: Theme.normal } }
                Rectangle {
                    x: 0; y: 11; width: 3; height: 22; radius: 1.5
                    gradient: Gradient {
                        GradientStop { position: 0; color: Theme.accentA }
                        GradientStop { position: 1; color: Theme.accentB }
                    }
                }
            }

            Repeater {
                model: [
                    { label: "Scroll protection", icon: "shield" },
                    { label: "Pointer", icon: "cursor" },
                    { label: "About", icon: "info" }
                ]
                delegate: Item {
                    id: nav
                    required property int index
                    required property var modelData
                    readonly property bool current: win.pageIndex === index
                    y: 4 + index * 52
                    width: sidebar.width
                    height: 44

                    Rectangle {
                        anchors.fill: parent
                        radius: Theme.radiusSm
                        color: Theme.hover
                        opacity: navMa.containsMouse && !nav.current ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                    }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        x: 16
                        spacing: 12
                        WgIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            name: nav.modelData.icon
                            size: 20
                            color: nav.current ? Theme.accentB : Theme.textMuted
                            Behavior on color { ColorAnimation { duration: Theme.normal } }
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: nav.modelData.label
                            font.pixelSize: Theme.fsBody
                            font.weight: nav.current ? Font.DemiBold : Font.Medium
                            color: nav.current ? Theme.text : Theme.textMuted
                            Behavior on color { ColorAnimation { duration: Theme.normal } }
                        }
                    }
                    MouseArea {
                        id: navMa
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: win.pageIndex = nav.index
                    }
                }
            }

            Text {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.leftMargin: 14
                anchors.rightMargin: 10
                anchors.bottomMargin: 6
                text: "DPI, polling rate and button remapping aren't available: this mouse's protocol isn't public."
                font.pixelSize: Theme.fsSmall - 1
                color: Theme.textFaint
                wrapMode: Text.WordWrap
                lineHeight: 1.1
            }
        }

        // ---------------------------------------------------- content panel
        Rectangle {
            id: panel
            x: sidebar.x + sidebar.width + 8
            y: header.height
            width: parent.width - x - 12
            height: parent.height - y - 12
            radius: Theme.radius
            color: Theme.panel
            border.width: 1
            border.color: Theme.border
            Behavior on color { ColorAnimation { duration: Theme.normal } }

            StatusBanner {
                id: banner
                width: parent.width
                daemonState: daemon.state
                detail: daemon.detail
                busy: daemon.serviceBusy
                serviceError: daemon.serviceError
                onStartRequested: daemon.startService()
                onRetryRequested: daemon.refreshStatus()
            }

            Item {
                id: pages
                y: banner.height
                width: parent.width
                height: parent.height - y
                Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.InOutCubic } }

                ScrollProtectionPage {
                    id: scrollPage
                    anchors.fill: parent
                    opacity: win.pageIndex === 0 ? 1 : 0
                    visible: opacity > 0
                    transform: Translate { y: win.pageIndex === 0 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                PointerPage {
                    anchors.fill: parent
                    opacity: win.pageIndex === 1 ? 1 : 0
                    visible: opacity > 0
                    transform: Translate { y: win.pageIndex === 1 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                AboutPage {
                    anchors.fill: parent
                    opacity: win.pageIndex === 2 ? 1 : 0
                    visible: opacity > 0
                    transform: Translate { y: win.pageIndex === 2 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
            }
        }
    }

    // ------------------------------------------------------------ edge resize handles
    component EdgeHandle: MouseArea {
        required property int edges
        visible: !win.maximized
        hoverEnabled: true
        onPressed: win.startSystemResize(edges)
    }
    readonly property int grip: 10
    EdgeHandle { edges: Qt.TopEdge; x: win.grip; y: 0; width: win.width - 2 * win.grip; height: win.grip - 4; cursorShape: Qt.SizeVerCursor }
    EdgeHandle { edges: Qt.BottomEdge; x: win.grip; y: win.height - height; width: win.width - 2 * win.grip; height: win.grip - 4; cursorShape: Qt.SizeVerCursor }
    EdgeHandle { edges: Qt.LeftEdge; x: 0; y: win.grip; width: win.grip - 4; height: win.height - 2 * win.grip; cursorShape: Qt.SizeHorCursor }
    EdgeHandle { edges: Qt.RightEdge; x: win.width - width; y: win.grip; width: win.grip - 4; height: win.height - 2 * win.grip; cursorShape: Qt.SizeHorCursor }
    EdgeHandle { edges: Qt.TopEdge | Qt.LeftEdge; x: 0; y: 0; width: win.grip; height: win.grip; cursorShape: Qt.SizeFDiagCursor }
    EdgeHandle { edges: Qt.TopEdge | Qt.RightEdge; x: win.width - width; y: 0; width: win.grip; height: win.grip; cursorShape: Qt.SizeBDiagCursor }
    EdgeHandle { edges: Qt.BottomEdge | Qt.LeftEdge; x: 0; y: win.height - height; width: win.grip; height: win.grip; cursorShape: Qt.SizeBDiagCursor }
    EdgeHandle { edges: Qt.BottomEdge | Qt.RightEdge; x: win.width - width; y: win.height - height; width: win.grip; height: win.grip; cursorShape: Qt.SizeFDiagCursor }
}
