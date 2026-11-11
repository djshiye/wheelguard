import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import QtQuick.Controls.Basic as C

// First-run welcome: three short steps (service and mouse, permissions, protection strength).
// Shown over the window until the user presses Done or Skip (appSettings.welcomeSeen), and again from
// About > "Show welcome again". Every problem it shows comes with the same fix button the pages use.
Item {
    id: root
    property int step: 0
    readonly property int lastStep: 2
    readonly property bool serviceUp: daemon.configKnown || daemon.state === "permission"
    property int cornerRadius: 18
    // The same advice everywhere the welcome mentions a missing device-access rule.
    readonly property string fixCommand: "sudo dnf reinstall wheelguard"
    readonly property string fixText: "Reinstall the wheelguard package (" + fixCommand + "), then log out and back in."

    visible: opacity > 0
    opacity: appSettings.welcomeSeen ? 0 : 1
    Behavior on opacity { NumberAnimation { duration: Theme.normal } }
    enabled: opacity > 0.5
    focus: visible
    onVisibleChanged: if (visible) { forceActiveFocus(); mouse.ensureLoaded() }
    Component.onCompleted: if (visible) mouse.ensureLoaded()
    Keys.onEscapePressed: root.finish()
    Keys.onTabPressed: (event) => { root.cycle(true); event.accepted = true }
    Keys.onBacktabPressed: (event) => { root.cycle(false); event.accepted = true }

    // Keyboard focus never leaves the open overlay: Tab wraps from its last control to its first.
    function inside(item) {
        for (var p = item; p; p = p.parent)
            if (p === root)
                return true
        return false
    }
    function cycle(forward) {
        const win = root.Window.window
        const cur = win ? win.activeFocusItem : null
        let next = cur ? cur.nextItemInFocusChain(forward) : null
        if (next && inside(next) && next !== cur) {
            next.forceActiveFocus(forward ? Qt.TabFocusReason : Qt.BacktabFocusReason)
            return
        }
        // wrap: the first control after the overlay itself, or the last one that is still inside it
        let first = root.nextItemInFocusChain(true)
        if (!first || !inside(first))
            return
        let target = first
        if (!forward) {
            let x = first.nextItemInFocusChain(true), guard = 0
            while (x && inside(x) && x !== first && guard++ < 200) {
                target = x
                x = x.nextItemInFocusChain(true)
            }
        }
        target.forceActiveFocus(forward ? Qt.TabFocusReason : Qt.BacktabFocusReason)
    }

    function finish() { appSettings.finishWelcome(); step = 0 }

    // ------------------------------------------------------------ one status line with an optional fix button
    component CheckRow: Rectangle {
        id: cr
        property color tone: Theme.good
        property string icon: "check"
        property string title: ""
        property string body: ""
        property string command: ""
        default property alias buttons: btns.data
        Layout.fillWidth: true
        implicitHeight: lay.implicitHeight + 28
        radius: Theme.radiusSm + 2
        color: Theme.alpha(tone, Theme.dark ? 0.08 : 0.09)
        border.width: 1
        border.color: Theme.alpha(tone, 0.30)
        Behavior on color { ColorAnimation { duration: Theme.normal } }
        RowLayout {
            id: lay
            anchors.fill: parent
            anchors.margins: 14
            spacing: 14
            Rectangle {
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: Theme.px(36)
                Layout.preferredHeight: Theme.px(36)
                radius: 11
                color: Theme.alpha(cr.tone, 0.16)
                WgIcon { anchors.centerIn: parent; name: cr.icon; size: 18; color: cr.tone }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 3
                Text {
                    Layout.fillWidth: true
                    text: cr.title
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.DemiBold
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: cr.body !== ""
                    text: cr.body
                    font.pixelSize: Theme.fsSmall + 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    lineHeight: 1.1
                }
                Rectangle {
                    visible: cr.command !== ""
                    Layout.topMargin: 2
                    implicitWidth: cmdText.implicitWidth + 20
                    implicitHeight: Theme.px(26)
                    radius: 8
                    color: Theme.inset
                    border.width: 1
                    border.color: Theme.border
                    Text {
                        id: cmdText
                        anchors.centerIn: parent
                        text: cr.command
                        font.family: "monospace"
                        font.pixelSize: Theme.fsSmall
                        color: Theme.text
                    }
                }
            }
            RowLayout {
                id: btns
                Layout.alignment: Qt.AlignVCenter
                spacing: 8
            }
        }
    }

    // ------------------------------------------------------------ scrim
    Item {
        anchors.fill: parent
        clip: true
        Rectangle {     // rounded at the bottom only: the header above stays reachable
            y: -root.cornerRadius * 2
            width: parent.width
            height: parent.height + root.cornerRadius * 2
            radius: root.cornerRadius
            color: Theme.dark ? Qt.rgba(0.02, 0.02, 0.04, 0.72) : Qt.rgba(0.85, 0.86, 0.9, 0.72)
        }
    }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: (w) => w.accepted = true }

    // ------------------------------------------------------------ card
    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(580, parent.width - 48)
        height: Math.min(parent.height - 32, body.implicitHeight + 56)
        radius: Theme.radius + 4
        color: Theme.card
        border.width: 1
        border.color: Theme.borderHi
        scale: root.visible && !appSettings.welcomeSeen ? 1 : 0.97
        Behavior on scale { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }

        ColumnLayout {
            id: body
            anchors.fill: parent
            anchors.margins: 28
            spacing: 18

            // Everything above the buttons scrolls when it doesn't fit (Large Text, a small window),
            // so Back / Next stay reachable.
            Flickable {
                id: scroller
                Layout.fillWidth: true
                Layout.fillHeight: true
                implicitHeight: content.implicitHeight
                contentWidth: width
                contentHeight: content.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                interactive: contentHeight > height
                C.ScrollBar.vertical: C.ScrollBar { policy: scroller.interactive ? C.ScrollBar.AsNeeded : C.ScrollBar.AlwaysOff }

                ColumnLayout {
                    id: content
                    width: scroller.width - (scroller.interactive ? 12 : 0)
                    spacing: 18

                    // heading
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 14
                        Image {
                            Layout.preferredWidth: Theme.px(44)
                            Layout.preferredHeight: Theme.px(44)
                            source: iconUrl
                            sourceSize: Qt.size(88, 88)
                            smooth: true
                            mipmap: true
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: root.step === 0 ? "Welcome to Wheelguard" : (root.step === 1 ? "Is everything allowed?" : "How strong should protection be?")
                                font.pixelSize: Theme.fsH2 + 3
                                font.weight: Font.DemiBold
                                color: Theme.text
                            }
                            Text {
                                text: "Step " + (root.step + 1) + " of 3"
                                font.pixelSize: Theme.fsSmall
                                color: Theme.textFaint
                            }
                        }
                    }

                    // ---- step 1: service and mouse
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: root.step === 0
                        spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: "Wheelguard stops a worn scroll wheel from jumping backwards. First, a quick check that it can see your mouse."
                            font.pixelSize: Theme.fsBody
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                            lineHeight: 1.15
                        }
                        CheckRow {
                            tone: root.serviceUp ? Theme.good : (daemon.state === "connecting" ? Theme.textFaint : Theme.coral)
                            icon: root.serviceUp ? "check" : (daemon.state === "connecting" ? "clock" : "power")
                            title: root.serviceUp ? "The Wheelguard service is running"
                                   : (daemon.state === "connecting" ? "Connecting to the service…" : "The Wheelguard service isn't running")
                            body: root.serviceUp ? "" : (daemon.state === "connecting" ? "" : "Scroll protection works through a small background service.")
                            WgButton {
                                visible: daemon.state === "stopped"
                                variant: "primary"
                                iconName: "play"
                                text: daemon.serviceBusy ? "Starting…" : "Start service"
                                enabled: !daemon.serviceBusy
                                onClicked: daemon.startService()
                            }
                        }
                        CheckRow {
                            id: mouseFound
                            readonly property string ms: mouse.state
                            tone: ms === "ready" || ms === "applying" ? Theme.good
                                  : (ms === "idle" || ms === "reading" ? Theme.textFaint : Theme.amber)
                            icon: ms === "ready" || ms === "applying" ? "check" : (ms === "idle" || ms === "reading" ? "clock" : "mouse")
                            title: ms === "ready" || ms === "applying" ? "Found your " + mouse.modelName
                                   : (ms === "idle" || ms === "reading" ? "Looking for your mouse…"
                                      : (ms === "asleep" ? "Your mouse is asleep" : (ms === "missing" ? "No supported mouse found" : "Couldn't read your mouse")))
                            body: ms === "ready" || ms === "applying" ? (mouse.battery >= 0 ? "Battery " + mouse.battery + "%. The Mouse page can change its DPI, polling rate and more." : "The Mouse page can change its DPI, polling rate and more.")
                                  : (ms === "asleep" ? "Click a button or move it, then check again."
                                     : (ms === "missing" ? "Plug in the receiver or the cable, then check again. Wheelguard works with the " + mouse.supportedNames.join(", ").replace(/, ([^,]*)$/, " and $1") + "."
                                        : (ms === "idle" || ms === "reading" ? "" : "The next step shows what to fix.")))
                            WgButton {
                                visible: ["asleep", "missing", "unsupported", "denied", "error"].indexOf(mouseFound.ms) >= 0
                                text: "Check again"
                                iconName: "reset"
                                onClicked: mouse.refresh()
                            }
                        }
                    }

                    // ---- step 2: permissions
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: root.step === 1
                        spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: "Wheelguard needs two permissions, both set up by the installer."
                            font.pixelSize: Theme.fsBody
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        CheckRow {
                            tone: daemon.state === "permission" ? Theme.coral : (daemon.configKnown ? Theme.good : Theme.textFaint)
                            icon: daemon.state === "permission" ? "lock" : (daemon.configKnown ? "check" : "clock")
                            title: daemon.state === "permission" ? "Wheelguard can't read your mouse's scroll wheel"
                                   : (daemon.configKnown ? "Scroll wheel access is fine" : "Scroll wheel access: start the service first")
                            body: daemon.state === "permission" ? "The device-access (udev) rule is missing. " + root.fixText
                                  : (daemon.state === "stopped" ? "Go back and press Start service." : "")
                            command: daemon.state === "permission" ? root.fixCommand : ""
                            WgButton {
                                visible: daemon.state === "permission"
                                text: "Check again"
                                iconName: "reset"
                                onClicked: daemon.refreshStatus()
                            }
                        }
                        CheckRow {
                            id: mouseAccess
                            readonly property string ms: mouse.state
                            tone: ms === "denied" ? Theme.coral : (ms === "ready" || ms === "applying" ? Theme.good : Theme.textFaint)
                            icon: ms === "denied" ? "lock" : (ms === "ready" || ms === "applying" ? "check" : "clock")
                            title: ms === "denied" ? "Wheelguard can't open the mouse's settings channel"
                                   : (ms === "ready" || ms === "applying" ? "Mouse settings access is fine" : "Mouse settings access: checked once the mouse is found")
                            body: ms === "denied" ? "The device-access (udev) rule is missing. " + root.fixText : ""
                            WgButton {
                                visible: mouseAccess.ms === "denied"
                                text: "Check again"
                                iconName: "reset"
                                onClicked: mouse.refresh()
                            }
                        }
                    }

                    // ---- step 3: strength
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: root.step === 2
                        spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: daemon.state === "stopped" || daemon.state === "connecting"
                                  ? "Start the service in step 1 to choose a strength. Balanced is a good start."
                                  : "Pick how firmly stray scrolls are caught. You can change this any time on the Scroll page."
                            font.pixelSize: Theme.fsBody
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                        GridLayout {
                            id: presetGrid
                            Layout.fillWidth: true
                                columns: 2
                            columnSpacing: 10
                            rowSpacing: 10
                            enabled: daemon.state !== "stopped" && daemon.state !== "connecting"
                            opacity: enabled ? 1 : 0.5
                            Repeater {
                                model: daemon.presetList()
                                delegate: Rectangle {
                                    id: pc
                                    required property int index
                                    required property var modelData
                                    readonly property bool selected: daemon.presetIndex === index
                                    // two equal columns, worked out from the grid so the text can't widen them
                                    Layout.preferredWidth: Math.floor((content.width - presetGrid.columnSpacing) / 2)
                                    Layout.minimumWidth: Layout.preferredWidth
                                    Layout.maximumWidth: Layout.preferredWidth
                                    implicitHeight: pcol.implicitHeight + 28
                                    radius: Theme.radiusSm + 2
                                    color: selected ? "transparent" : (pma.containsMouse ? Theme.cardHi : Theme.inset)
                                    border.width: 1
                                    border.color: selected ? Theme.accentA : (pma.containsMouse ? Theme.borderHi : Theme.border)
                                    activeFocusOnTab: true
                                    Accessible.role: Accessible.RadioButton
                                    Accessible.name: modelData.name + ". " + modelData.summary
                                    Accessible.checked: selected
                                    Accessible.onPressAction: daemon.applyPreset(index)
                                    Keys.onSpacePressed: daemon.applyPreset(index)
                                    Keys.onReturnPressed: daemon.applyPreset(index)
                                    Behavior on border.color { ColorAnimation { duration: Theme.fast } }
                                    Rectangle {
                                        anchors.fill: parent
                                        radius: parent.radius
                                        opacity: pc.selected ? 1 : 0
                                        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                                        gradient: Gradient {
                                            orientation: Gradient.Horizontal
                                            GradientStop { position: 0; color: Theme.alpha(Theme.accentA, Theme.dark ? 0.28 : 0.16) }
                                            GradientStop { position: 1; color: Theme.alpha(Theme.accentB, Theme.dark ? 0.14 : 0.12) }
                                        }
                                    }
                                    Rectangle {     // keyboard focus ring
                                        anchors.fill: parent
                                        anchors.margins: -2
                                        radius: parent.radius + 2
                                        color: "transparent"
                                        border.width: 2
                                        border.color: Theme.accentB
                                        visible: pc.activeFocus
                                    }
                                    ColumnLayout {
                                        id: pcol
                                        anchors.fill: parent
                                        anchors.margins: 14
                                        spacing: 4
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text {
                                                Layout.fillWidth: true
                                                text: pc.modelData.name
                                                font.pixelSize: Theme.fsBody + 1
                                                font.weight: Font.DemiBold
                                                color: Theme.text
                                            }
                                            Rectangle {
                                                visible: pc.index === 1
                                                Layout.preferredWidth: recText.implicitWidth + 14
                                                Layout.preferredHeight: Theme.px(18)
                                                radius: 9
                                                color: Theme.alpha(Theme.accentB, 0.18)
                                                Text {
                                                    id: recText
                                                    anchors.centerIn: parent
                                                    text: "Recommended"
                                                    font.pixelSize: Theme.fsSmall - 1
                                                    font.weight: Font.DemiBold
                                                    color: Theme.accentB
                                                }
                                            }
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: pc.index === 0 ? "Catches only the quickest bounces."
                                                  : (pc.index === 1 ? "Catches nearly every stray. The best start for most wheels."
                                                     : (pc.index === 2 ? "Also catches slow, stubborn strays."
                                                        : "Most aggressive. Can feel a little sluggish when you reverse."))
                                            font.pixelSize: Theme.fsSmall + 1
                                            color: Theme.textMuted
                                            wrapMode: Text.WordWrap
                                            lineHeight: 1.1
                                        }
                                    }
                                    MouseArea {
                                        id: pma
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: daemon.applyPreset(pc.index)
                                    }
                                }
                            }
                        }
                    }

                }
            }

            // ---- footer
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 10
                WgButton {
                    variant: "ghost"
                    text: "Skip"
                    onClicked: root.finish()
                }
                Item { Layout.fillWidth: true }
                Row {
                    spacing: 7
                    Repeater {
                        model: 3
                        delegate: Rectangle {
                            required property int index
                            width: root.step === index ? 20 : 7
                            height: 7
                            radius: 3.5
                            color: root.step === index ? Theme.accentB : Theme.track
                            Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
                        }
                    }
                }
                Item { Layout.fillWidth: true }
                WgButton {
                    visible: root.step > 0
                    text: "Back"
                    onClicked: root.step -= 1
                }
                WgButton {
                    variant: "primary"
                    text: root.step === root.lastStep ? "Done" : "Next"
                    onClicked: root.step === root.lastStep ? root.finish() : root.step += 1
                }
            }
        }
    }
}
