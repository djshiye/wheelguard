import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import Wg

Window {
    id: win
    property int pageIndex: 0
    property bool advancedOpen: false       // screenshots: open the Advanced expanders
    property bool pickerOpen: false         // screenshots: open the mouse picker
    onAdvancedOpenChanged: { scrollPage.advancedOpen = advancedOpen; clicksPage.advancedOpen = advancedOpen }
    onPickerOpenChanged: pickerOpen ? picker.open() : picker.close()
    property alias welcomeStep: welcome.step

    readonly property bool maximized: visibility === Window.Maximized || visibility === Window.FullScreen
    readonly property int margin: maximized ? 0 : 14
    readonly property int cornerRadius: maximized ? 0 : 18

    width: 1008
    height: 708
    minimumWidth: 860
    minimumHeight: 600
    color: "transparent"
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "Wheelguard"

    Binding { target: Theme; property: "dark"; value: appearance.dark }
    Binding { target: Theme; property: "textScale"; value: appearance.textScale }
    Binding { target: Theme; property: "motion"; value: appearance.motion }
    Binding { target: Theme; property: "glass"; value: liquidGlass }

    onActiveChanged: if (active) pointer.refresh()
    // The app loads the mouse once at startup (for the tray); this covers the screenshot harness.
    // X hides to the tray (or quits when there is none); only the tray's Quit and Ctrl+Q exit.
    onClosing: (close) => { close.accepted = false; shell.closeMainWindow() }
    onPageIndexChanged: if (pageIndex === 2) mouse.ensureLoaded()
    Component.onCompleted: if (pageIndex === 2) mouse.ensureLoaded()

    Timer {   // keeps the mouse list current where it shows (the service rescans only now and then)
        running: win.visible && (picker.opened || win.pageIndex === 0) && daemon.state !== "stopped" && daemon.state !== "connecting"
        repeat: true
        triggeredOnStart: true
        interval: 5000
        onTriggered: daemon.refreshMice()
    }

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
                    id: pill
                    daemonState: daemon.state
                    device: daemon.deviceLabel
                    protecting: daemon.enabled
                    unrecognised: daemon.otherMouse !== ""
                    interactive: true
                    open: picker.opened
                    onClicked: picker.opened ? picker.close() : picker.open()

                    MousePicker {
                        id: picker
                        x: pill.width - width
                        y: pill.height + 8
                    }
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
                            activeFocusOnTab: true
                            Accessible.role: Accessible.Button
                            Accessible.name: wb.modelData.close ? "Close" : "Minimize"
                            function press() { wb.modelData.close ? win.close() : win.showMinimized() }
                            Accessible.onPressAction: press()
                            Keys.onSpacePressed: press()
                            Keys.onReturnPressed: press()
                            WgFocusRing {}
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
            width: Math.round(204 + (Theme.textScale - 1) * 170)     // wider with Large Text so labels keep their margin
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
                    { label: "Scroll", icon: "shield" },
                    { label: "Clicks", icon: "hand" },
                    { label: "Mouse", icon: "mouse" },
                    { label: "Pointer", icon: "cursor" },
                    { label: "Profiles", icon: "apps" },
                    { label: "Settings", icon: "sliders" }
                ]
                delegate: Item {
                    id: nav
                    required property int index
                    required property var modelData
                    readonly property bool current: win.pageIndex === index
                    y: 4 + index * 52
                    width: sidebar.width
                    height: 44
                    property real radius: Theme.radiusSm      // read by the focus ring
                    activeFocusOnTab: true
                    Accessible.role: Accessible.PageTab
                    Accessible.name: nav.modelData.label
                    Accessible.selected: nav.current
                    Accessible.onPressAction: win.pageIndex = nav.index
                    Keys.onSpacePressed: win.pageIndex = nav.index
                    Keys.onReturnPressed: win.pageIndex = nav.index
                    Keys.onUpPressed: if (nav.index > 0) { win.pageIndex = nav.index - 1; nav.nextItemInFocusChain(false).forceActiveFocus() }
                    Keys.onDownPressed: if (nav.index < 5) { win.pageIndex = nav.index + 1; nav.nextItemInFocusChain(true).forceActiveFocus() }
                    WgFocusRing {}

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
                suppressed: win.pageIndex === 2     // the Mouse page doesn't involve the service
                daemonState: daemon.state
                detail: daemon.detail
                busy: daemon.serviceBusy
                serviceError: daemon.serviceError
                chosenDenied: daemon.mouseChoice !== "auto" && !daemon.mouseChoiceSupported
                otherMouse: daemon.otherMouse
                otherMouseAccessible: daemon.otherMouseAccessible
                canAllow: mouseAccess.available
                allowBusy: mouseAccess.busy
                allowError: mouseAccess.error
                onAllowRequested: mouseAccess.allow(daemon.mouseChoice !== "auto" ? daemon.mouseChoice : daemon.otherMouseId)
                onAutomaticRequested: daemon.setMouse("auto")
                onUseOtherMouseRequested: daemon.setMouse(daemon.otherMouseId)
                onStartRequested: daemon.startService()
                onRetryRequested: daemon.refreshStatus()
                onResumeRequested: daemon.setPaused(false)
            }

            // "Allow this mouse" worked: protect that mouse now. Access taken back: return to Automatic.
            Connections {
                target: mouseAccess
                function onFinished(action, ident, ok) {
                    if (!ok)
                        return
                    if (action === "add") {
                        daemon.setMouse(ident)
                        allowRefresh.restart()
                    } else if (daemon.mouseChoice === ident) {
                        daemon.setMouse("auto")
                    }
                }
            }
            Timer {
                id: allowRefresh
                interval: 1500
                onTriggered: { daemon.refreshMice(); daemon.refreshStatus() }
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
                    enabled: visible
                    transform: Translate { y: win.pageIndex === 0 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                ClicksPage {
                    id: clicksPage
                    anchors.fill: parent
                    opacity: win.pageIndex === 1 ? 1 : 0
                    visible: opacity > 0
                    enabled: visible
                    transform: Translate { y: win.pageIndex === 1 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                MousePage {
                    objectName: "mousePage"
                    anchors.fill: parent
                    opacity: win.pageIndex === 2 ? 1 : 0
                    visible: opacity > 0
                    enabled: visible
                    transform: Translate { y: win.pageIndex === 2 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                PointerPage {
                    anchors.fill: parent
                    opacity: win.pageIndex === 3 ? 1 : 0
                    visible: opacity > 0
                    enabled: visible
                    transform: Translate { y: win.pageIndex === 3 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                ProfilesPage {
                    anchors.fill: parent
                    opacity: win.pageIndex === 4 ? 1 : 0
                    visible: opacity > 0
                    enabled: visible
                    transform: Translate { y: win.pageIndex === 4 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
                SettingsPage {
                    anchors.fill: parent
                    opacity: win.pageIndex === 5 ? 1 : 0
                    visible: opacity > 0
                    enabled: visible
                    transform: Translate { y: win.pageIndex === 5 ? 0 : 12; Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } } }
                    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                }
            }
        }
    }

    // ------------------------------------------------------------ first-run welcome (over everything but the header)
    WelcomeOverlay {
        id: welcome
        x: win.margin
        y: win.margin + header.height
        width: win.width - 2 * win.margin
        height: win.height - 2 * win.margin - header.height
        cornerRadius: win.cornerRadius
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
