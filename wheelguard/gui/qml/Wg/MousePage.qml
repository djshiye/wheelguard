import QtQuick
import QtQuick.Layouts

// Hardware settings stored on the mouse itself (Endgame Gear XM2w 4k v2, Logitech G Pro X Superlight,
// Razer DeathAdder V4 Pro). Cards and controls follow mouse.features, so each model only shows what it
// has. Edits are staged locally and only sent when the user presses Apply (see the action bar).
Item {
    id: root

    readonly property string st: mouse.state
    readonly property bool shown: st === "ready" || st === "applying"
    readonly property bool editable: st === "ready" && mouse.writable
    readonly property bool barShown: shown && (mouse.changeCount > 0 || st === "applying")
    readonly property var v: mouse.values
    readonly property var edited: mouse.editedKeys
    readonly property var polling: mouse.pollingLabels
    readonly property var pollingSub: mouse.pollingSubs
    readonly property bool hasSubs: pollingSub.some(function (t) { return t !== "" })
    readonly property var feat: mouse.features
    function has(f) { return feat.indexOf(f) >= 0 }
    readonly property var stageNumbers: {
        var out = []
        for (var i = 1; i <= mouse.maxStages; ++i) out.push("" + i)
        return out
    }
    readonly property string countWord: ["zero", "one", "two", "three", "four", "five", "six"][mouse.maxStages] || ("" + mouse.maxStages)
    readonly property color valueTint: Theme.accentValue

    // The editor's stage selection is view state only; the unlink toggles are too.
    property int editStage: 0
    property var unlinked: ({})
    property bool confirmRestore: false
    property int hotButton: -1         // button row under the pointer or focus (lights it on the drawing)
    function setHot(i, on) { if (on) hotButton = i; else if (hotButton === i) hotButton = -1 }
    property bool namingSetup: false
    property string setupError: ""

    function isEdited(key) { return edited.indexOf(key) >= 0 }
    function anyEdited(prefix) {
        for (var i = 0; i < edited.length; ++i) if (edited[i].indexOf(prefix) === 0) return true
        return false
    }
    function stageLinked(i) {
        if (!mouse.xyDpi) return true
        return unlinked[i] !== undefined ? !unlinked[i] : v["dpi_x" + i] === v["dpi_y" + i]
    }
    function stageLabel(i) {
        var x = v["dpi_x" + i], y = v["dpi_y" + i]
        if (!x) return "–"
        return x === y ? "" + x : x + " × " + y
    }
    function dpiFromPos(p) {
        var step = mouse.dpiStep
        return Math.max(mouse.dpiMin, Math.round((mouse.dpiMin + (mouse.dpiMax - mouse.dpiMin) * p * p) / step) * step)
    }
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

    onShownChanged: if (!shown) { confirmRestore = false; namingSetup = false }

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
        activeFocusOnTab: inUse
        Accessible.role: Accessible.RadioButton
        Accessible.name: "Stage " + (idx + 1) + ", " + label + " DPI" + (active ? ", active" : "")
        Accessible.checkable: true
        Accessible.checked: selected
        Accessible.onPressAction: if (inUse) clicked()
        Keys.onSpacePressed: if (inUse) clicked()
        Keys.onReturnPressed: if (inUse) clicked()
        Keys.onEnterPressed: if (inUse) clicked()
        WgFocusRing {}
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
            Accessible.name: (dr.axis ? dr.axis + " " : "") + "DPI"
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
            Accessible.name: (dr.axis ? dr.axis + " " : "") + "DPI"
            Layout.preferredWidth: 138
            enabled: root.editable
            suffix: " DPI"
            from: mouse.dpiMin
            to: mouse.dpiMax
            stepSize: mouse.dpiStep
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
                      + (mouse.modelName === "" ? " Works with the " + mouse.supportedNames.join(", ").replace(/, ([^,]*)$/, " and $1") + "." : "")
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
                MouseArt {
                    Layout.alignment: Qt.AlignTop
                    unit: 0.95
                    modelKey: mouse.modelKey
                    highlight: root.hotButton
                    dim: !root.shown
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: mouse.modelName !== "" ? mouse.modelName : "Your mouse"
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
                    RowLayout {   // battery and connection, big enough to read at a glance
                        Layout.fillWidth: true
                        spacing: 36
                        ColumnLayout {
                            visible: mouse.battery >= 0
                            Layout.alignment: Qt.AlignTop
                            spacing: 4
                            Text { text: "BATTERY"; font.pixelSize: Theme.fsSmall - 1; font.weight: Font.DemiBold; font.letterSpacing: 0.8; color: Theme.textMuted }
                            RowLayout {
                                spacing: 10
                                BatteryGlyph { Layout.preferredWidth: 38; Layout.preferredHeight: 18; percent: mouse.battery; charging: mouse.charging }
                                Text { text: mouse.battery + "%"; font.pixelSize: Theme.fsH2 + 7; font.weight: Font.DemiBold; color: Theme.text }
                                Text { visible: mouse.charging; text: "Charging"; font.pixelSize: Theme.fsSmall; color: Theme.good }
                            }
                        }
                        ColumnLayout {
                            visible: mouse.connection !== ""
                            Layout.alignment: Qt.AlignTop
                            spacing: 4
                            Text { text: "CONNECTION"; font.pixelSize: Theme.fsSmall - 1; font.weight: Font.DemiBold; font.letterSpacing: 0.8; color: Theme.textMuted }
                            RowLayout {
                                spacing: 10
                                WgIcon { name: mouse.connection === "wired" ? "plug" : "wireless"; size: 22; color: Theme.textMuted }
                                Text { text: mouse.connection === "wired" ? "Wired" : mouse.wirelessText; font.pixelSize: Theme.fsBody + 1; font.weight: Font.DemiBold; color: Theme.text }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
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
                  : "The receiver is plugged in, but the mouse isn't answering. It is probably asleep or switched off: click a button or move it, then press Refresh."
            WgButton { text: "Refresh"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "denied"
            tone: Theme.coral
            icon: "lock"
            title: "No permission to configure the mouse"
            body: "Wheelguard isn't allowed to open the mouse's configuration channel. Reinstall Wheelguard (it installs the device-access rule), then unplug the receiver or cable and plug it back in."
            WgButton { text: "Check again"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "missing"
            tone: Theme.amber
            icon: "plug"
            title: "No supported mouse found"
            body: "Plug in the receiver, or connect the mouse with its cable, then press Refresh. Wheelguard can configure the " + mouse.supportedNames.join(", ").replace(/, ([^,]*)$/, " and $1") + "."
            WgButton { text: "Refresh"; iconName: "reset"; onClicked: mouse.refresh() }
        }
        Notice {
            width: parent.width
            visible: mouse.state === "unsupported"
            tone: Theme.amber
            icon: "alert"
            title: mouse.detail !== "" ? mouse.detail : "Unsupported mouse"
            body: "This dongle is talking to a mouse this page doesn't know (only the Endgame Gear XM2w 4k v2, PID 0x1982). Other models may lay out their settings differently, so nothing is read or written and the page stays read-only."
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
            body: "Wheelguard won't change anything on this mouse: " + mouse.problem + "."
        }

        // ---------------------------------------------------------------- saved setups
        WgCard {
            width: parent.width
            visible: root.shown
            spacing: 12

            Heading { title: "Setups" }
            Hint { text: "Save the settings you see now under a name, like Gaming or Desktop, and switch back with one click. Applying a setup saves a backup first and checks what the mouse stored, like the Apply button." }

            Text {
                visible: mouse.setups.length === 0 && !root.namingSetup
                Layout.fillWidth: true
                text: "No setups yet."
                font.pixelSize: Theme.fsBody - 1
                color: Theme.textFaint
            }
            Repeater {
                model: mouse.setups
                delegate: Rectangle {
                    id: sr
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: 56
                    radius: Theme.radiusSm
                    color: sr.modelData.active ? Theme.alpha(Theme.accentB, Theme.dark ? 0.08 : 0.10) : Theme.inset
                    border.width: 1
                    border.color: sr.modelData.active ? Theme.alpha(Theme.accentB, 0.4) : Theme.border
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 16
                        anchors.rightMargin: 10
                        spacing: 10
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            RowLayout {
                                spacing: 8
                                Text {
                                    text: sr.modelData.name
                                    font.pixelSize: Theme.fsBody
                                    font.weight: Font.DemiBold
                                    color: Theme.text
                                    elide: Text.ElideRight
                                }
                                Rectangle {
                                    visible: sr.modelData.active
                                    Layout.preferredWidth: inUseText.implicitWidth + 14
                                    Layout.preferredHeight: 18
                                    radius: 9
                                    color: Theme.alpha(Theme.accentB, 0.18)
                                    Text {
                                        id: inUseText
                                        anchors.centerIn: parent
                                        text: "In use"
                                        font.pixelSize: Theme.fsSmall - 1
                                        font.weight: Font.DemiBold
                                        color: Theme.accentB
                                    }
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                text: sr.modelData.summary
                                font.pixelSize: Theme.fsSmall
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                        WgButton {
                            text: "Apply"
                            iconName: "check"
                            enabled: root.editable && !sr.modelData.active
                            onClicked: mouse.applySetup(sr.modelData.name)
                        }
                        WgButton {
                            variant: "ghost"
                            text: "Delete"
                            onClicked: root.setupError = mouse.deleteSetup(sr.modelData.name)
                        }
                    }
                }
            }

            RowLayout {
                visible: root.namingSetup
                Layout.fillWidth: true
                spacing: 8
                WgTextField {
                    id: setupName
                    Layout.fillWidth: true
                    placeholder: "Name, for example Gaming"
                    maximumLength: 30
                    onAccepted: saveButton.clicked()
                    Keys.onEscapePressed: root.namingSetup = false
                    onVisibleChanged: if (visible) { text = ""; forceActiveFocus() }
                }
                WgButton { text: "Cancel"; onClicked: root.namingSetup = false }
                WgButton {
                    id: saveButton
                    variant: "primary"
                    iconName: "save"
                    text: "Save"
                    enabled: setupName.text.trim() !== ""
                    onClicked: {
                        root.setupError = mouse.saveSetup(setupName.text)
                        if (root.setupError === "")
                            root.namingSetup = false
                    }
                }
            }
            RowLayout {
                visible: root.setupError !== ""
                Layout.fillWidth: true
                spacing: 8
                WgIcon { name: "alert"; size: 16; color: Theme.coral }
                Text {
                    Layout.fillWidth: true
                    text: root.setupError
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.coral
                    wrapMode: Text.WordWrap
                }
            }
            WgButton {
                visible: !root.namingSetup
                text: "Save as setup…"
                iconName: "plus"
                onClicked: { root.setupError = ""; root.namingSetup = true }
            }
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
            Hint {
                text: "Up to " + root.countWord + " DPI stages. Click a stage to edit it; the mouse's DPI button cycles through them."
                      + (mouse.modelKey === "logitech-pro-x-superlight" ? " Picking the active stage takes effect at once; after a restart the mouse begins in its default stage." : "")
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                uniformCellSizes: true
                Repeater {
                    model: mouse.maxStages
                    delegate: StageChip {
                        required property int index
                        Layout.fillWidth: true
                        Layout.preferredHeight: Theme.px(72)
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
                            visible: mouse.xyDpi
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
                        WgIcon { visible: mouse.modelKey === "endgame-xm2w-4k-v2"; name: "alert"; size: 14; color: Theme.amber }
                        Text {
                            Layout.fillWidth: true
                            text: mouse.modelKey === "endgame-xm2w-4k-v2"
                                  ? "Range 50 to 30000 DPI. The top end hasn't been checked against this sensor's real limit."
                                  : "Range " + mouse.dpiMin + " to " + mouse.dpiMax + " DPI, in steps of " + mouse.dpiStep + "."
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
                    accessibleName: "Number of stages"
                    Layout.preferredWidth: 55 * mouse.maxStages
                    Layout.preferredHeight: 40
                    model: root.stageNumbers
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
                accessibleName: "Polling rate"
                Layout.fillWidth: true
                model: root.polling
                subLabels: root.hasSubs ? root.pollingSub : []
                currentIndex: mouse.pollingCodes.indexOf(root.v.polling)
                onActivated: (i) => mouse.setValue("polling", mouse.pollingCodes[i])
            }
            Divider { visible: root.has("motion_sync") }
            ColumnLayout {
                visible: root.has("motion_sync")
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
            visible: root.shown && root.has("lod")
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            spacing: 12

            Heading { title: "Lift-off distance"; badge: root.lodMm() }
            Hint { text: "How high you can lift the mouse before it stops tracking. A lower value stops tracking sooner, which helps when you reposition on the pad." }
            WgSegmented {
                accessibleName: "Lift-off distance"
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
            visible: root.shown && root.has("buttons")
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
                    HoverHandler { onHoveredChanged: root.setHot(fr.index, hovered) }
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
                            accessibleName: "Click filter mode, " + mouse.buttonNames[fr.index]
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
                            Accessible.name: "Click filter, " + mouse.buttonNames[fr.index]
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
            RowLayout {     // the rows on the left, the mouse on the right: the row you touch lights its button
                Layout.fillWidth: true
                spacing: 28
            GridLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                columns: 1
                rowSpacing: 10
                Repeater {
                    model: 5
                    delegate: RowLayout {
                        id: br
                        required property int index
                        readonly property int idx: mouse.actionCodes.indexOf(root.v["button" + index])
                        Layout.fillWidth: true
                        spacing: 10
                        HoverHandler { onHoveredChanged: root.setHot(br.index, hovered) }
                        Text {
                            Layout.fillWidth: true
                            text: mouse.buttonNames[br.index]
                            font.pixelSize: Theme.fsBody
                            color: Theme.text
                            elide: Text.ElideRight
                        }
                        WgSelect {
                            accessibleName: "Action for " + mouse.buttonNames[br.index]
                            Layout.preferredWidth: 190
                            model: mouse.actionNames
                            onActiveFocusChanged: root.setHot(br.index, activeFocus)
                            currentIndex: br.idx
                            placeholder: "Custom (unchanged)"
                            enabled: br.idx >= 0
                            edited: root.isEdited("button" + br.index)
                            onActivated: (i) => mouse.setValue("button" + br.index, mouse.actionCodes[i])
                        }
                    }
                }
            }
            MouseArt {
                Layout.alignment: Qt.AlignVCenter
                unit: 0.9
                modelKey: mouse.modelKey
                highlight: root.hotButton
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
            visible: root.shown && (root.has("sleep_deep") || root.has("idle") || root.has("low_battery"))
            enabled: root.editable
            opacity: root.editable ? 1 : 0.6
            Behavior on opacity { NumberAnimation { duration: Theme.normal } }
            vpad: 14
            spacing: 0

            Heading { title: "Power"; Layout.topMargin: 10; Layout.bottomMargin: 4 }
            WgToggleRow {   // Razer: one sleep timer
                visible: root.has("idle")
                title: "Sleep after"
                description: "Go to sleep after this long without use. A click or a move wakes it."
                switchVisible: false
                edited: root.isEdited("idle_min")
                WgSpinBox {
                    Accessible.name: "Sleep after"
                    suffix: " min"
                    from: mouse.idleMin
                    to: mouse.idleMax
                    value: Math.max(mouse.idleMin, root.v.idle_min)
                    onValueModified: mouse.setValue("idle_min", value)
                }
            }
            Divider { visible: root.has("idle") && root.has("low_battery") }
            WgToggleRow {   // Razer: low-battery warning
                visible: root.has("low_battery")
                title: "Low-battery warning"
                description: "The mouse warns you, and saves power, when the battery drops to this level."
                switchVisible: false
                edited: root.isEdited("low_battery")
                WgSelect {
                    accessibleName: "Low-battery warning"
                    implicitWidth: 120
                    model: mouse.lowBatteryLabels
                    // The nearest preset, so a level set elsewhere (e.g. in Synapse) never shows a blank box.
                    currentIndex: {
                        const codes = mouse.lowBatteryCodes
                        let best = 0
                        for (let i = 1; i < codes.length; i++)
                            if (Math.abs(codes[i] - root.v.low_battery) < Math.abs(codes[best] - root.v.low_battery))
                                best = i
                        return best
                    }
                    edited: root.isEdited("low_battery")
                    onActivated: (i) => mouse.setValue("low_battery", mouse.lowBatteryCodes[i])
                }
            }
            WgToggleRow {
                visible: root.has("sleep_deep")
                title: "Sleep"
                description: "Drop to a low-power state after this long without use."
                checked: root.v.sleep_on
                edited: root.anyEdited("sleep_")
                onToggled: (on) => mouse.setValue("sleep_on", on)
                WgSpinBox {
                    Accessible.name: "Sleep after"
                    enabled: root.v.sleep_on
                    opacity: enabled ? 1 : 0.45
                    suffix: " min"
                    from: 1
                    to: 120
                    value: Math.max(1, root.v.sleep_min)
                    onValueModified: mouse.setValue("sleep_min", value)
                }
            }
            Divider { visible: root.has("sleep_deep") }
            WgToggleRow {
                visible: root.has("sleep_deep")
                title: "Deep sleep"
                description: "Switch off almost completely after this long. A click wakes it, with a short delay."
                checked: root.v.deep_on
                edited: root.anyEdited("deep_")
                onToggled: (on) => mouse.setValue("deep_on", on)
                WgSpinBox {
                    Accessible.name: "Deep sleep after"
                    enabled: root.v.deep_on
                    opacity: enabled ? 1 : 0.45
                    suffix: " min"
                    from: 1
                    to: 120
                    value: Math.max(1, root.v.deep_min)
                    onValueModified: mouse.setValue("deep_min", value)
                }
            }
            Divider { visible: root.has("sleep_deep") }
            WgToggleRow {
                visible: root.has("sleep_deep")
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
            text: mouse.modelKey === "logitech-pro-x-superlight"
                  ? "Wheelguard edits the mouse's onboard profile the way libratbag and Solaar do. It hasn't been tested on every firmware, and it isn't affiliated with or endorsed by Logitech."
                  : mouse.modelKey === "razer-deathadder-v4-pro"
                    ? "Wheelguard talks to the mouse with the protocol documented by the OpenRazer project. It hasn't been tested on every firmware, and it isn't affiliated with or endorsed by Razer."
                    : "Wheelguard talks to the mouse with a protocol reverse-engineered from the vendor's Windows tool. It hasn't been tested on every firmware, and it isn't affiliated with or endorsed by Endgame Gear."
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
