import QtQuick
import QtQuick.Layouts

// Hardware settings of the Endgame Gear XM2w 4k v2, stored on the mouse itself. Edits are staged
// locally and only sent when the user presses Apply (see the action bar at the bottom).
Item {
    id: root

    readonly property string st: mouse.state
    readonly property bool shown: st === "ready" || st === "applying"
    readonly property bool editable: st === "ready" && mouse.writable
    readonly property bool barShown: shown && (mouse.changeCount > 0 || st === "applying")
    readonly property var v: mouse.values
    readonly property var edited: mouse.editedKeys
    readonly property var polling: ["125 Hz", "1000 Hz", "1000 Hz", "2000 Hz", "4000 Hz"]
    readonly property var pollingSub: ["Office mode", "Power saving", "No saving", "No saving", "No saving"]
    readonly property color valueTint: Theme.dark ? "#B79DFF" : Theme.accentA

    // The editor's stage selection is view state only; the unlink toggles are too.
    property int editStage: 0
    property var unlinked: ({})
    property bool confirmRestore: false

    function isEdited(key) { return edited.indexOf(key) >= 0 }
    function anyEdited(prefix) {
        for (var i = 0; i < edited.length; ++i) if (edited[i].indexOf(prefix) === 0) return true
        return false
    }
    function stageLinked(i) {
        return unlinked[i] !== undefined ? !unlinked[i] : v["dpi_x" + i] === v["dpi_y" + i]
    }
    function stageLabel(i) {
        var x = v["dpi_x" + i], y = v["dpi_y" + i]
        return x === y ? "" + x : x + " × " + y
    }
    function dpiFromPos(p) { return Math.round((mouse.dpiMin + (mouse.dpiMax - mouse.dpiMin) * p * p) / 50) * 50 }
    function posFromDpi(d) { return Math.sqrt(Math.max(0, d - mouse.dpiMin) / (mouse.dpiMax - mouse.dpiMin)) }
    function setUnlinked(i, un) { var u = Object.assign({}, unlinked); u[i] = un; unlinked = u }
    function lodModel() {
        var out = [], src = v.glass ? mouse.lodGlassMm : mouse.lodNormalMm
        for (var i = 0; i < src.length; ++i) out.push(src[i].toFixed(1))
        return out
    }
    function lodIndex() { return v.glass ? mouse.lodGlassCodes.indexOf(v.lod) : (v.lod >= 0 && v.lod <= 10 ? v.lod : -1) }
    function lodMm() { var i = lodIndex(); return i < 0 ? "?" : lodModel()[i] + " mm" }
    function filterMode(r) {
        var f = v["filter" + r]
        return f === mouse.gxSpeed ? 1 : (f === mouse.gxSafe ? 2 : 0)
    }

    onShownChanged: if (!shown) confirmRestore = false

    // ------------------------------------------------------------------ reusable bits
    component Heading: RowLayout {
        property string title
        property string badge: ""
        Layout.fillWidth: true
        Text {
            Layout.fillWidth: true
            text: parent.title
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            visible: parent.badge !== ""
            text: parent.badge
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: root.valueTint
        }
    }
    component Hint: Text {
        Layout.fillWidth: true
        font.pixelSize: Theme.fsBody - 1
        color: Theme.textMuted
        wrapMode: Text.WordWrap
        lineHeight: 1.1
    }
    component Divider: Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
    component Chip: Rectangle {
        id: chip
        property string icon: ""
        property string text: ""
        default property alias extra: chipRow.data
        implicitHeight: 28
        implicitWidth: chipRow.implicitWidth + 22
        radius: 14
        color: Theme.inset
        border.width: 1
        border.color: Theme.border
        Row {
            id: chipRow
            anchors.centerIn: parent
            spacing: 7
            WgIcon {
                visible: chip.icon !== ""
                anchors.verticalCenter: parent.verticalCenter
                name: chip.icon
                size: 15
                color: Theme.textMuted
            }
            Text {
                visible: chip.text !== ""
                anchors.verticalCenter: parent.verticalCenter
                text: chip.text
                font.pixelSize: Theme.fsSmall
                font.weight: Font.Medium
                color: Theme.text
            }
        }
    }
    // Coloured notice with an icon, a title, text and optional buttons.
    component Notice: Rectangle {
        id: nt
        property color tone: Theme.amber
        property string icon: "alert"
        property string title: ""
        property string body: ""
        property string detail: ""
        default property alias buttons: btnRow.data
        implicitHeight: lay.implicitHeight + 32
        radius: Theme.radius
        color: Theme.alpha(tone, Theme.dark ? 0.09 : 0.10)
        border.width: 1
        border.color: Theme.alpha(tone, 0.32)
        RowLayout {
            id: lay
            anchors.fill: parent
            anchors.margins: 16
            spacing: 16
            Rectangle {
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 40
                Layout.preferredHeight: 40
                radius: 12
                color: Theme.alpha(nt.tone, 0.16)
                WgIcon { anchors.centerIn: parent; name: nt.icon; size: 20; color: nt.tone }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    Layout.fillWidth: true
                    text: nt.title
                    font.pixelSize: Theme.fsBody + 1
                    font.weight: Font.DemiBold
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: nt.body !== ""
                    text: nt.body
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    lineHeight: 1.15
                }
                Text {
                    Layout.fillWidth: true
                    visible: nt.detail !== ""
                    text: nt.detail
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textFaint
                    wrapMode: Text.WrapAnywhere
                }
            }
            RowLayout {
                id: btnRow
                Layout.alignment: Qt.AlignVCenter
                spacing: 8
            }
        }
    }
    component StageChip: Rectangle {
        id: sc
        property int idx: 0
        property bool inUse: true
        property bool selected: false
        property bool active: false
        property bool edited: false
        property string label: ""
        signal clicked()
        radius: Theme.radiusSm
        opacity: inUse ? 1 : 0.38
        color: selected ? "transparent" : (scMa.containsMouse && inUse ? Theme.cardHi : Theme.inset)
        border.width: 1
        border.color: selected ? Theme.accentA : (scMa.containsMouse && inUse ? Theme.borderHi : Theme.border)
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            opacity: sc.selected ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.alpha(Theme.accentA, Theme.dark ? 0.30 : 0.18) }
                GradientStop { position: 1; color: Theme.alpha(Theme.accentB, Theme.dark ? 0.16 : 0.14) }
            }
        }
        ColumnLayout {
            anchors.fill: parent
            anchors.leftMargin: 14
            anchors.rightMargin: 12
            anchors.topMargin: 10
            anchors.bottomMargin: 10
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                Text {
                    text: "STAGE " + (sc.idx + 1)
                    font.pixelSize: Theme.fsSmall - 1
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.8
                    color: sc.selected ? Theme.text : Theme.textMuted
                }
                Item { Layout.fillWidth: true }
                Rectangle {
                    visible: sc.edited
                    Layout.preferredWidth: 6
                    Layout.preferredHeight: 6
                    radius: 3
                    color: Theme.accentB
                }
                Rectangle {
                    visible: sc.active
                    Layout.preferredWidth: activeRow.implicitWidth + 14
                    Layout.preferredHeight: 18
                    radius: 9
                    color: Theme.alpha(Theme.accentB, 0.18)
                    Row {
                        id: activeRow
                        anchors.centerIn: parent
                        spacing: 5
                        Rectangle { width: 6; height: 6; radius: 3; color: Theme.accentB; anchors.verticalCenter: parent.verticalCenter }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: "Active"
                            font.pixelSize: Theme.fsSmall - 2
                            font.weight: Font.DemiBold
                            color: Theme.accentB
                        }
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: sc.label
                elide: Text.ElideRight
                font.pixelSize: sc.label.length > 6 ? Theme.fsBody + 1 : Theme.fsH2 + 3
                font.weight: Font.DemiBold
                color: Theme.text
            }
        }
        MouseArea {
            id: scMa
            anchors.fill: parent
            hoverEnabled: true
            enabled: sc.inUse
            cursorShape: Qt.PointingHandCursor
            onClicked: sc.clicked()
        }
    }
    // One axis of a DPI stage: slider (square-root scale, so 400-3200 is easy to hit) + exact field.
    component DpiRow: RowLayout {
        id: dr
        property string axis: ""
        property string key: ""
        property bool linked: true
        Layout.fillWidth: true
        spacing: 14
        Text {
            visible: !dr.linked
            Layout.preferredWidth: 14
            text: dr.axis
            font.pixelSize: Theme.fsBody
            font.weight: Font.DemiBold
            color: Theme.textMuted
        }
        WgSlider {
            Layout.fillWidth: true
            enabled: root.editable
            from: 0
            to: 1
            stepSize: 0
            value: root.posFromDpi(root.v[dr.key])
            valueText: "" + root.dpiFromPos(value)
            onMoved: {
                var d = root.dpiFromPos(value)
                var patch = {}
                patch[dr.key] = d
                if (dr.linked) patch[dr.key.replace("dpi_x", "dpi_y")] = d
                mouse.setValues(patch)
            }
        }
        WgSpinBox {
            Layout.preferredWidth: 138
            enabled: root.editable
            suffix: " DPI"
            from: mouse.dpiMin
            to: mouse.dpiMax
            stepSize: 50
            value: root.v[dr.key]
            onValueModified: {
                var patch = {}
                patch[dr.key] = value
                if (dr.linked) patch[dr.key.replace("dpi_x", "dpi_y")] = value
                mouse.setValues(patch)
            }
        }
    }

    // ------------------------------------------------------------------ page
    WgScrollPage {
        id: sp
        anchors.fill: parent

        Column {
            width: parent.width
            spacing: 6
            Text {
                text: "Mouse"
                font.pixelSize: Theme.fsTitle
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                width: parent.width
                text: "These settings live on the mouse itself, so they follow it to any computer. Changes are staged here and only sent when you press Apply."
                font.pixelSize: Theme.fsBody
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }

        // ---------------------------------------------------------------- device header
        WgCard {
            width: parent.width
            pad: 20
            RowLayout {
                Layout.fillWidth: true
                spacing: 18
                Rectangle {
                    Layout.preferredWidth: 56
                    Layout.preferredHeight: 56
                    Layout.alignment: Qt.AlignTop
                    radius: 18
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: Theme.accentA }
                        GradientStop { position: 1.0; color: Theme.accentB }
                    }
                    WgIcon { anchors.centerIn: parent; name: "mouse"; size: 28; color: "white"; strokeWidth: 1.9 }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: "Endgame Gear XM2w 4k v2"
                            font.pixelSize: Theme.fsH2 + 3
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        WgButton {
                            text: "Refresh"
                            iconName: "reset"
                            enabled: !mouse.busy
                            onClicked: mouse.refresh()
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        Chip {
                            visible: mouse.connection !== ""
                            icon: mouse.connection === "wired" ? "plug" : "wireless"
                            text: mouse.connection === "wired" ? "Wired" : "Wireless via dongle"
                        }
                        Chip {
                            visible: mouse.battery >= 0
                            BatteryGlyph {
                                anchors.verticalCenter: parent.verticalCenter
                                percent: mouse.battery
                                charging: mouse.charging
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: mouse.charging ? mouse.battery + "% · Charging" : mouse.battery + "%"
                                font.pixelSize: Theme.fsSmall
                                font.weight: Font.Medium
                                color: Theme.text
                            }
                        }
                        Chip { visible: mouse.mouseFw !== ""; text: "Mouse FW " + mouse.mouseFw }
                        Chip { visible: mouse.dongleFw !== ""; text: "Dongle FW " + mouse.dongleFw }
                    }
                }
            }
        }

        // ---------------------------------------------------------------- state notices
        Rectangle {   // reading / applying progress
            id: busyCard
            width: parent.width
            visible: mouse.busy || mouse.state === "idle"
            implicitHeight: 76
            height: implicitHeight
            radius: Theme.radius
            color: Theme.card
            border.width: 1
            border.color: Theme.border
            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 20
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    Text {
                        Layout.fillWidth: true
                        text: mouse.state === "applying" ? "Saving to the mouse…" : "Reading the mouse…"
                        font.pixelSize: Theme.fsBody
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Text {
                        text: mouse.progressText
                        font.pixelSize: Theme.fsSmall
                        color: Theme.textMuted
                    }
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 6
                    radius: 3
                    color: Theme.track
                    Rectangle {
                        width: Math.max(parent.height, parent.width * Math.min(1, mouse.progress))
                        height: parent.height
                        radius: 3
                        Behavior on width { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0; color: Theme.accentA }
                            GradientStop { position: 1; color: Theme.accentB }
                        }
                    }
                }
            }
        }

        Notice {
            width: parent.width
            visible: mouse.state === "asleep"
            tone: Theme.amber
            icon: "clock"
            title: "Wake the mouse"
            body: mouse.connection === "wired"
                  ? "The mouse didn't answer. Check the cable, then press Refresh."
                  : "The dongle is plugged in, but the mouse isn't answering. It is probably asleep or switched off: click a button or move it, then press Refresh."
            WgButton { text: "Refresh"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "denied"
            tone: Theme.coral
            icon: "lock"
            title: "No permission to configure the mouse"
            body: "Wheelguard isn't allowed to open the dongle's configuration channel. Reinstall Wheelguard (it installs the device-access rule), then replug the dongle."
            WgButton { text: "Check again"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "missing"
            tone: Theme.amber
            icon: "plug"
            title: "Dongle not found"
            body: "Plug in the Endgame Gear dongle, or connect the mouse with its cable, then press Refresh."
            WgButton { text: "Refresh"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "unsupported"
            tone: Theme.amber
            icon: "alert"
            title: mouse.detail !== "" ? mouse.detail : "Unsupported mouse"
            body: "This page only knows the Endgame Gear XM2w 4k v2 (PID 0x1982). Other models may lay out their settings differently, so nothing is read or written and the page stays read-only."
            WgButton { text: "Refresh"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "error"
            tone: Theme.coral
            icon: "alert"
            title: "Couldn't talk to the mouse"
            body: mouse.message
            WgButton { text: "Try again"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {   // result of the last apply / restore (ready state only)
            width: parent.width
            visible: root.shown && mouse.message !== ""
            tone: mouse.messageKind === "ok" ? Theme.good : Theme.coral
            icon: mouse.messageKind === "ok" ? "check" : "alert"
            title: mouse.message
            WgButton { variant: "ghost"; text: "Dismiss"; onClicked: mouse.dismissMessage() }
        }
        Notice {
            width: parent.width
            visible: root.shown && !mouse.writable && mouse.problem !== ""
            tone: Theme.amber
            icon: "alert"
            title: "Settings are read-only"
            body: "The mouse returned an unexpected settings block (" + mouse.problem + "), so nothing will be written."
        }

        // ---------------------------------------------------------------- sensitivity
        WgCard {
            width: parent.width
            visible: root.shown
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            spacing: 14

            Heading { title: "Sensitivity" }
            Hint { text: "Up to four DPI stages. Click a stage to edit it; the mouse's DPI button cycles through them." }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                uniformCellSizes: true
                Repeater {
                    model: 4
                    delegate: StageChip {
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredHeight: 72
                        idx: index
                        inUse: index < root.v.stage_count
                        selected: index === root.editStage && inUse
                        active: index === root.v.active_stage && inUse
                        edited: root.isEdited("dpi_x" + index) || root.isEdited("dpi_y" + index)
                        label: root.stageLabel(index)
                        onClicked: root.editStage = index
                    }
                }
            }

            Rectangle {   // editor for the selected stage
                id: editor
                readonly property int s: Math.max(0, Math.min(root.editStage, root.v.stage_count - 1))
                readonly property bool linked: root.stageLinked(s)
                Layout.fillWidth: true
                implicitHeight: edCol.implicitHeight + 32
                radius: Theme.radius - 2
                color: Theme.inset
                border.width: 1
                border.color: Theme.border
                ColumnLayout {
                    id: edCol
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 14
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: "Stage " + (editor.s + 1)
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        WgButton {
                            implicitHeight: 32
                            variant: "ghost"
                            iconName: editor.linked ? "link" : "unlink"
                            text: editor.linked ? "X and Y linked" : "X and Y separate"
                            onClicked: {
                                var s = editor.s
                                if (editor.linked) {
                                    root.setUnlinked(s, true)
                                } else {
                                    root.setUnlinked(s, false)
                                    mouse.setValues({ ["dpi_y" + s]: root.v["dpi_x" + s] })
                                }
                            }
                        }
                        WgButton {
                            implicitHeight: 32
                            readonly property bool isActive: editor.s === root.v.active_stage
                            variant: isActive ? "ghost" : "secondary"
                            iconName: isActive ? "check" : ""
                            text: isActive ? "Active stage" : "Make active"
                            enabled: !isActive
                            opacity: 1
                            onClicked: mouse.setValue("active_stage", editor.s)
                        }
                    }
                    DpiRow {
                        key: "dpi_x" + editor.s
                        axis: "X"
                        linked: editor.linked
                    }
                    DpiRow {
                        visible: !editor.linked
                        key: "dpi_y" + editor.s
                        axis: "Y"
                        linked: false
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        WgIcon { name: "alert"; size: 14; color: Theme.amber }
                        Text {
                            Layout.fillWidth: true
                            text: "Range 50 to 30000 DPI. The top end hasn't been checked against this sensor's real limit."
                            font.pixelSize: Theme.fsSmall
                            color: Theme.textFaint
                            wrapMode: Text.WordWrap
                        }
                    }
                }
            }

            Divider {}
            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    RowLayout {
                        spacing: 8
                        Text { text: "Number of stages"; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
                        Rectangle { visible: root.isEdited("stage_count"); Layout.preferredWidth: 7; Layout.preferredHeight: 7; radius: 3.5; color: Theme.accentB }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "How many stages the DPI button cycles through."
                        font.pixelSize: Theme.fsSmall
                        color: Theme.textMuted
                        wrapMode: Text.WordWrap
                    }
                }
                WgSegmented {
                    Layout.preferredWidth: 220
                    Layout.preferredHeight: 40
                    model: ["1", "2", "3", "4"]
                    currentIndex: root.v.stage_count - 1
                    onActivated: (i) => mouse.setValue("stage_count", i + 1)
                }
            }
        }

        // ---------------------------------------------------------------- performance
        WgCard {
            width: parent.width
            visible: root.shown
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            spacing: 14

            Heading { title: "Performance"; badge: root.polling[mouse.pollingCodes.indexOf(root.v.polling)] || "" }
            Hint { text: "Polling rate: how often the mouse reports its position. Higher is smoother but uses more battery." }
            WgSegmented {
                Layout.fillWidth: true
                model: root.polling
                subLabels: root.pollingSub
                currentIndex: mouse.pollingCodes.indexOf(root.v.polling)
                onActivated: (i) => mouse.setValue("polling", mouse.pollingCodes[i])
            }
            Divider {}
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                WgToggleRow {
                    title: "Motion Sync"
                    description: "Aligns sensor reports with the polling interval for steadier tracking."
                    checked: root.v.motion_sync
                    edited: root.isEdited("motion_sync")
                    onToggled: (on) => mouse.setValue("motion_sync", on)
                }
                Divider {}
                WgToggleRow {
                    title: "Angle snapping"
                    description: "Straightens small wobbles when you move in a nearly straight line."
                    checked: root.v.angle_snapping
                    edited: root.isEdited("angle_snapping")
                    onToggled: (on) => mouse.setValue("angle_snapping", on)
                }
                Divider {}
                WgToggleRow {
                    title: "Ripple control"
                    description: "Smooths out sensor jitter at higher DPI."
                    checked: root.v.ripple
                    edited: root.isEdited("ripple")
                    onToggled: (on) => mouse.setValue("ripple", on)
                }
                Divider {}
                WgToggleRow {
                    title: "Glass mode"
                    description: "For glass and other very smooth surfaces. Lift-off then offers only 1.0 and 2.0 mm."
                    checked: root.v.glass
                    edited: root.isEdited("glass")
                    onToggled: (on) => mouse.setValue("glass", on)
                }
            }
        }

        // ---------------------------------------------------------------- lift-off
        WgCard {
            width: parent.width
            visible: root.shown
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            spacing: 12

            Heading { title: "Lift-off distance"; badge: root.lodMm() }
            Hint { text: "How high you can lift the mouse before it stops tracking. A lower value stops tracking sooner, which helps when you reposition on the pad." }
            WgSegmented {
                Layout.fillWidth: true
                model: root.lodModel()
                currentIndex: root.lodIndex()
                onActivated: (i) => mouse.setValue("lod", root.v.glass ? mouse.lodGlassCodes[i] : i)
            }
            Text {
                Layout.alignment: Qt.AlignRight
                text: "millimetres"
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
            }
        }

        // ---------------------------------------------------------------- buttons
        WgCard {
            width: parent.width
            visible: root.shown
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            spacing: 14

            Heading { title: "Buttons" }
            Text {
                text: "Multiclick filter"
                font.pixelSize: Theme.fsBody
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Hint { text: "Ignores extra clicks that arrive too soon after a press. It isn't a latency debounce: low values add no click delay. Default 8. The left and right buttons can use GX Speed or GX Safe instead." }

            Repeater {
                model: 5
                delegate: ColumnLayout {
                    id: fr
                    required property int index
                    readonly property int mode: root.filterMode(index)
                    readonly property bool hasGx: index < 2
                    Layout.fillWidth: true
                    spacing: 8
                    RowLayout {
                        visible: fr.hasGx
                        Layout.fillWidth: true
                        spacing: 12
                        RowLayout {
                            spacing: 8
                            Text {
                                text: mouse.buttonNames[fr.index]
                                font.pixelSize: Theme.fsBody
                                color: Theme.text
                            }
                            Rectangle {
                                visible: root.isEdited("filter" + fr.index)
                                Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3
                                color: Theme.accentB
                            }
                        }
                        Item { Layout.fillWidth: true }
                        WgSegmented {
                            Layout.preferredWidth: 270
                            Layout.preferredHeight: 36
                            model: ["Filter", "GX Speed", "GX Safe"]
                            currentIndex: fr.mode
                            onActivated: (i) => {
                                if (i === fr.mode) return
                                var key = "filter" + fr.index
                                mouse.setValue(key, i === 1 ? mouse.gxSpeed : (i === 2 ? mouse.gxSafe
                                                   : (mouse.baseline[key] <= mouse.filterMax ? mouse.baseline[key] : mouse.filterDefault)))
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 14
                        Text {
                            Layout.preferredWidth: 130
                            text: fr.hasGx ? "" : mouse.buttonNames[fr.index]
                            font.pixelSize: Theme.fsBody
                            color: Theme.text
                        }
                        WgSlider {
                            Layout.fillWidth: true
                            enabled: fr.mode === 0
                            opacity: enabled ? 1 : 0.4
                            from: 0
                            to: mouse.filterMax
                            stepSize: 1
                            value: fr.mode === 0 ? root.v["filter" + fr.index] : mouse.filterDefault
                            valueText: "" + Math.round(value)
                            onMoved: mouse.setValue("filter" + fr.index, Math.round(value))
                        }
                        Text {
                            Layout.preferredWidth: 72
                            horizontalAlignment: Text.AlignRight
                            text: fr.mode === 1 ? "GX Speed" : (fr.mode === 2 ? "GX Safe" : "" + root.v["filter" + fr.index])
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: root.valueTint
                        }
                    }
                }
            }

            Divider {}
            Text {
                text: "Button actions"
                font.pixelSize: Theme.fsBody
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Hint { text: "Pick what each button does. Only mouse buttons and wheel actions are supported; keyboard keys and macros can't be set here." }
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 28
                rowSpacing: 10
                Repeater {
                    model: 5
                    delegate: RowLayout {
                        id: br
                        required property int index
                        readonly property int idx: mouse.actionCodes.indexOf(root.v["button" + index])
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 10
                        Text {
                            Layout.fillWidth: true
                            text: mouse.buttonNames[br.index]
                            font.pixelSize: Theme.fsBody
                            color: Theme.text
                            elide: Text.ElideRight
                        }
                        WgSelect {
                            Layout.preferredWidth: 170
                            model: mouse.actionNames
                            currentIndex: br.idx
                            placeholder: "Custom (unchanged)"
                            enabled: br.idx >= 0
                            edited: root.isEdited("button" + br.index)
                            onActivated: (i) => mouse.setValue("button" + br.index, mouse.actionCodes[i])
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                visible: mouse.editProblem !== ""
                spacing: 8
                WgIcon { name: "alert"; size: 16; color: Theme.coral }
                Text {
                    Layout.fillWidth: true
                    text: mouse.editProblem
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.coral
                    wrapMode: Text.WordWrap
                }
            }
        }

        // ---------------------------------------------------------------- power
        WgCard {
            width: parent.width
            visible: root.shown
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            vpad: 14
            spacing: 0

            Heading { title: "Power"; Layout.topMargin: 10; Layout.bottomMargin: 4 }
            WgToggleRow {
                title: "Sleep"
                description: "Drop to a low-power state after this long without use."
                checked: root.v.sleep_on
                edited: root.anyEdited("sleep_")
                onToggled: (on) => mouse.setValue("sleep_on", on)
                WgSpinBox {
                    enabled: root.v.sleep_on
                    opacity: enabled ? 1 : 0.45
                    suffix: " min"
                    from: 1
                    to: 120
                    value: Math.max(1, root.v.sleep_min)
                    onValueModified: mouse.setValue("sleep_min", value)
                }
            }
            Divider {}
            WgToggleRow {
                title: "Deep sleep"
                description: "Switch off almost completely after this long. A click wakes it, with a short delay."
                checked: root.v.deep_on
                edited: root.anyEdited("deep_")
                onToggled: (on) => mouse.setValue("deep_on", on)
                WgSpinBox {
                    enabled: root.v.deep_on
                    opacity: enabled ? 1 : 0.45
                    suffix: " min"
                    from: 1
                    to: 120
                    value: Math.max(1, root.v.deep_min)
                    onValueModified: mouse.setValue("deep_min", value)
                }
            }
            Divider {}
            WgToggleRow {
                title: "Turn LED off on lift-off"
                description: "Switches the sensor LED off while the mouse is in the air."
                checked: root.v.led_off
                edited: root.isEdited("led_off")
                onToggled: (on) => mouse.setValue("led_off", on)
            }
        }

        // ---------------------------------------------------------------- backup
        WgCard {
            width: parent.width
            visible: root.shown
            spacing: 10
            Heading { title: "Backup" }
            Hint {
                text: mouse.hasBackup
                      ? "Wheelguard saved your mouse's original settings before its first change:"
                      : "Before the first change, Wheelguard saves a copy of the mouse's current settings to ~/.config/wheelguard/backups."
            }
            Text {
                visible: mouse.hasBackup
                Layout.fillWidth: true
                text: mouse.backupPath
                elide: Text.ElideMiddle
                font.family: "monospace"
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
            }
            RowLayout {
                visible: mouse.hasBackup
                Layout.fillWidth: true
                spacing: 8
                WgButton {
                    visible: !root.confirmRestore
                    text: "Restore original settings…"
                    iconName: "reset"
                    enabled: root.editable
                    onClicked: root.confirmRestore = true
                }
                Text {
                    visible: root.confirmRestore
                    Layout.fillWidth: true
                    text: "This overwrites the mouse's current settings with the saved copy."
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
                WgButton {
                    visible: root.confirmRestore
                    text: "Cancel"
                    onClicked: root.confirmRestore = false
                }
                WgButton {
                    visible: root.confirmRestore
                    variant: "primary"
                    text: "Restore"
                    enabled: root.editable
                    onClicked: { root.confirmRestore = false; mouse.restoreOriginal() }
                }
                Item { visible: !root.confirmRestore; Layout.fillWidth: true }
            }
        }

        Text {
            width: parent.width
            visible: root.shown
            text: "Wheelguard talks to the mouse with a protocol reverse-engineered from the vendor's Windows tool. It hasn't been tested on every firmware, and it isn't affiliated with or endorsed by Endgame Gear."
            font.pixelSize: Theme.fsSmall
            color: Theme.textFaint
            wrapMode: Text.WordWrap
            lineHeight: 1.1
        }

        Item {   // room for the action bar
            width: 1
            height: root.barShown ? 80 : 0
            Behavior on height { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
        }
    }

    // ------------------------------------------------------------------ sticky action bar
    Item {
        id: bar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: 28
        anchors.rightMargin: 28
        height: 64
        y: root.barShown ? parent.height - height - 16 : parent.height + 20
        opacity: root.barShown ? 1 : 0
        visible: opacity > 0
        Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

        Repeater {   // soft shadow
            model: 6
            delegate: Rectangle {
                required property int index
                anchors.fill: parent
                anchors.margins: -index * 1.6
                anchors.topMargin: -index * 1.6 + 3
                radius: Theme.radius + index * 1.6
                color: "black"
                opacity: Theme.dark ? 0.09 : 0.035
            }
        }
        Rectangle {
            anchors.fill: parent
            radius: Theme.radius
            color: Theme.card
            border.width: 1
            border.color: Theme.borderHi
            clip: true
            Rectangle {   // top accent hairline
                x: 24; y: 0; width: parent.width - 48; height: 1
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.3; color: Theme.accentA }
                    GradientStop { position: 0.7; color: Theme.accentB }
                    GradientStop { position: 1.0; color: "transparent" }
                }
            }
            Rectangle {   // progress while applying
                visible: mouse.state === "applying"
                anchors.bottom: parent.bottom
                height: 3
                width: parent.width * Math.min(1, mouse.progress)
                Behavior on width { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Theme.accentA }
                    GradientStop { position: 1; color: Theme.accentB }
                }
            }
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 20
                anchors.rightMargin: 14
                spacing: 14
                Rectangle {
                    Layout.preferredWidth: 10
                    Layout.preferredHeight: 10
                    radius: 5
                    color: mouse.editProblem !== "" ? Theme.coral : Theme.accentB
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: mouse.state === "applying" ? "Saving to the mouse…"
                              : mouse.changeCount + (mouse.changeCount === 1 ? " change" : " changes")
                        font.pixelSize: Theme.fsBody
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Text {
                        Layout.fillWidth: true
                        text: mouse.state === "applying" ? mouse.progressText
                              : (mouse.editProblem !== "" ? mouse.editProblem
                                 : mouse.changeSummary + " · stored on the mouse")
                        elide: Text.ElideRight
                        font.pixelSize: Theme.fsSmall
                        color: mouse.editProblem !== "" ? Theme.coral : Theme.textMuted
                    }
                }
                WgButton {
                    text: "Discard"
                    enabled: mouse.state === "ready"
                    onClicked: mouse.discard()
                }
                WgButton {
                    variant: "primary"
                    iconName: "save"
                    text: mouse.state === "applying" ? "Applying…" : "Apply"
                    enabled: mouse.state === "ready" && mouse.editProblem === ""
                    onClicked: mouse.apply()
                }
            }
        }
    }
}
