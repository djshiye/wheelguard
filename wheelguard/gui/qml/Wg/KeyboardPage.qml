import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic as C

// A Glorious GMMK Pro: response, profiles and layers, what each key does, and macros (gui/keyboard.py).
// Lighting lives in RGBeast.
WgScrollPage {
    id: page
    readonly property string kbState: keyboard.state
    readonly property bool ready: kbState === "ready"
    readonly property bool editable: keyboard.canEdit
    property string editorOpenFor: ""       // screenshots: open the key editor for this key
    property bool macroEditorOpen: false    // screenshots: open the macro editor
    onEditorOpenForChanged: if (editorOpenFor !== "") keyEditor.openFor(editorOpenFor)
    onMacroEditorOpenChanged: if (macroEditorOpen) macroEditor.openFor(0)

    component SectionTitle: Text {
        font.pixelSize: Theme.fsH2
        font.weight: Font.DemiBold
        color: Theme.text
    }
    component Note: Text {
        Layout.fillWidth: true
        font.pixelSize: Theme.fsBody - 1
        color: Theme.textMuted
        wrapMode: Text.WordWrap
    }

    // Title
    Column {
        width: parent.width
        spacing: 6
        Text {
            text: "Keyboard"
            font.pixelSize: Theme.fsTitle
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            width: parent.width
            text: "How fast your " + keyboard.name.replace("Glorious ", "") + " responds, its profiles, what each key does, and macros."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        Item { width: 1; height: 2 }
        WgScope {
            icon: "keyboard"
            text: "Saved on the keyboard itself, so it works on any computer. Lighting is in RGBeast"
        }
    }

    // Not usable right now
    WgCard {
        width: parent.width
        visible: !page.ready
        RowLayout {
            spacing: 14
            WgIcon {
                Layout.alignment: Qt.AlignTop
                name: page.kbState === "none" ? "plug" : "alert"
                size: 22
                color: page.kbState === "none" ? Theme.textMuted : Theme.amber
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    Layout.fillWidth: true
                    text: page.kbState === "none" ? "Plug in your GMMK Pro"
                          : page.kbState === "qmk" ? "This GMMK Pro runs QMK firmware"
                          : page.kbState === "noaccess" ? "Wheelguard can't reach the keyboard's settings yet"
                          : "This keyboard answered in a way Wheelguard doesn't expect"
                    font.pixelSize: Theme.fsBody + 1
                    font.weight: Font.DemiBold
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
                Note {
                    text: page.kbState === "none" ? "Its settings show up here as soon as it's connected with its USB cable."
                          : page.kbState === "qmk" ? "Wheelguard speaks the keyboard's original Glorious firmware. With QMK, use VIA or the QMK Configurator instead. Nothing is sent to it."
                          : page.kbState === "noaccess" ? "Unplug the keyboard and plug it back in. If that doesn't help, reinstalling Wheelguard puts its access rule back."
                          : "Its settings channel has an unexpected size, so Wheelguard won't send it anything. Your keyboard is unchanged."
                }
            }
        }
    }

    // The board
    WgCard {
        width: parent.width
        visible: keyboard.present && page.kbState !== "qmk"
        vpad: 18
        RowLayout {
            spacing: 14
            Rectangle {
                Layout.preferredWidth: 44
                Layout.preferredHeight: 44
                radius: 12
                color: Theme.inset
                border.width: 1
                border.color: Theme.border
                WgIcon { anchors.centerIn: parent; name: "keyboard"; size: 24; color: Theme.accentB }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: keyboard.name
                    font.pixelSize: Theme.fsH2
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    text: keyboard.layoutName + " layout" + (keyboard.firmware !== "" ? "  ·  firmware " + keyboard.firmware : "") + "  ·  USB"
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                }
            }
            Rectangle {
                visible: keyboard.busy
                Layout.preferredHeight: 26
                Layout.preferredWidth: savingRow.implicitWidth + 20
                radius: 13
                color: Theme.alpha(Theme.accentB, 0.12)
                Row {
                    id: savingRow
                    anchors.centerIn: parent
                    spacing: 6
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 7; height: 7; radius: 3.5
                        color: Theme.accentB
                        SequentialAnimation on opacity {
                            running: keyboard.busy && Theme.motion
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.3; duration: 500 }
                            NumberAnimation { to: 1; duration: 500 }
                        }
                    }
                    Text { text: "Saving to the keyboard"; font.pixelSize: Theme.fsSmall; color: Theme.accentB }
                }
            }
        }
        Rectangle {
            visible: keyboard.message !== ""
            Layout.fillWidth: true
            Layout.preferredHeight: msg.implicitHeight + 20
            radius: Theme.radiusSm
            color: Theme.alpha(keyboard.messageOk ? Theme.good : Theme.coral, 0.10)
            border.width: 1
            border.color: Theme.alpha(keyboard.messageOk ? Theme.good : Theme.coral, 0.35)
            RowLayout {
                anchors.fill: parent
                anchors.margins: 10
                spacing: 10
                WgIcon { name: keyboard.messageOk ? "check" : "alert"; size: 16; color: keyboard.messageOk ? Theme.good : Theme.coral }
                Text {
                    id: msg
                    Layout.fillWidth: true
                    text: keyboard.message
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
            }
        }
    }

    // Locked until the owner tries it
    WgCard {
        width: parent.width
        visible: page.ready && keyboard.locked
        border.color: Theme.alpha(Theme.amber, 0.45)
        RowLayout {
            spacing: 14
            WgIcon { Layout.alignment: Qt.AlignTop; name: "lock"; size: 22; color: Theme.amber }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                Text {
                    Layout.fillWidth: true
                    text: "Not tried on a real GMMK Pro yet"
                    font.pixelSize: Theme.fsBody + 1
                    font.weight: Font.DemiBold
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
                Note {
                    text: "Wheelguard sends the same settings Glorious CORE does, and never touches the keyboard's firmware. The Fn key and the knob always keep working, so the keyboard's own shortcuts stay within reach. Controls stay off until you turn them on."
                }
                WgButton {
                    Layout.topMargin: 4
                    variant: "primary"
                    iconName: "check"
                    text: "Turn on controls"
                    onClicked: keyboard.unlock()
                }
            }
        }
    }

    // Response
    WgCard {
        width: parent.width
        visible: page.ready
        enabled: page.editable
        spacing: 12
        SectionTitle { text: "Response" }
        Text { text: "Polling rate"; font.pixelSize: Theme.fsBody; font.weight: Font.Medium; color: Theme.text }
        WgSegmented {
            accessibleName: "Polling rate"
            Layout.fillWidth: true
            model: ["125 Hz", "250 Hz", "500 Hz", "1000 Hz"]
            subLabels: ["8 ms", "4 ms", "2 ms", "1 ms, fastest"]
            currentIndex: keyboard.pollingIndex
            onActivated: (i) => keyboard.setPolling(i)
        }
        Note { text: "How often the keyboard reports to the computer. 1000 Hz is the default and the quickest." }
        Item { Layout.preferredHeight: 4; Layout.fillWidth: true }
        Text { text: "Debounce"; font.pixelSize: Theme.fsBody; font.weight: Font.Medium; color: Theme.text }
        WgSegmented {
            accessibleName: "Debounce"
            Layout.fillWidth: true
            model: ["2 ms", "8 ms", "16 ms"]
            subLabels: ["quickest", "balanced", "steadiest, default"]
            currentIndex: keyboard.debounceIndex
            onActivated: (i) => keyboard.setDebounce(i)
        }
        Note {
            text: "How long a key must settle before it counts. Lower is quicker; go higher if a key types twice. "
                  + (keyboard.rgbeastLighting
                     ? "The keyboard keeps this with its lighting, which RGBeast supplies, so your colours stay as they are."
                     : "The keyboard keeps this with its lighting, so changing it puts the lighting back to Glorious mode until RGBeast is set up.")
        }
    }

    // Profiles and layers
    WgCard {
        width: parent.width
        visible: page.ready
        enabled: page.editable
        spacing: 12
        SectionTitle { text: "Profile and layer" }
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                spacing: 8
                Text { text: "Profile"; font.pixelSize: Theme.fsBody; font.weight: Font.Medium; color: Theme.text }
                WgSegmented {
                    accessibleName: "Profile"
                    Layout.fillWidth: true
                    model: ["1", "2", "3"]
                    currentIndex: keyboard.profile
                    onActivated: (i) => keyboard.setProfile(i)
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 1
                spacing: 8
                Text { text: "Layer"; font.pixelSize: Theme.fsBody; font.weight: Font.Medium; color: Theme.text }
                WgSegmented {
                    accessibleName: "Layer"
                    Layout.fillWidth: true
                    model: ["1", "2", "3"]
                    currentIndex: keyboard.layer
                    onActivated: (i) => keyboard.setLayer(i)
                }
            }
        }
        Note { text: "Each profile has three layers, and each layer its own keys. The keyboard remembers all nine, and the page follows when you switch on the keyboard itself." }
    }

    // Keys
    WgCard {
        width: parent.width
        visible: page.ready
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            SectionTitle { text: "Keys" }
            Text {
                Layout.leftMargin: 6
                text: "Profile " + (keyboard.profile + 1) + ", layer " + (keyboard.layer + 1)
                font.pixelSize: Theme.fsSmall
                color: Theme.textMuted
            }
            Item { Layout.fillWidth: true }
            WgButton {
                visible: keyboard.remappedCount > 0
                enabled: page.editable
                variant: "ghost"
                iconName: "reset"
                text: "Reset all keys"
                onClicked: keyboard.resetLayer()
            }
        }
        Note {
            text: keyboard.remappedCount > 0
                  ? keyboard.remappedCount + (keyboard.remappedCount === 1 ? " key does" : " keys do") + " something else on this layer. Click a key to change it."
                  : "Click a key to make it type something else, control media or the mouse, switch profiles, or play a macro."
        }

        Item {
            id: art
            Layout.fillWidth: true
            Layout.topMargin: 4
            readonly property real unit: width / 16
            implicitHeight: unit * 6 + 12
            enabled: page.editable

            Rectangle {     // the case
                anchors.fill: parent
                anchors.margins: -6
                radius: Theme.radiusSm
                color: Theme.inset
                border.width: 1
                border.color: Theme.border
            }
            Repeater {
                model: keyboard.keys
                delegate: Item {
                    id: key
                    required property var modelData
                    readonly property bool knob: modelData.id === "Knob"
                    readonly property bool lower: modelData.piece === "EnterLower"
                    readonly property bool hot: keyMa.containsMouse && !modelData.locked && art.enabled
                    x: modelData.x * art.unit + 2
                    y: modelData.y * art.unit + 2 + (modelData.y > 0 ? 6 : 0) - (lower ? 6 : 0)
                    width: modelData.w * art.unit - 4
                    height: modelData.h * art.unit - 4 + (lower ? 6 : 0)
                    activeFocusOnTab: art.enabled && !modelData.locked && !lower
                    Accessible.role: Accessible.Button
                    Accessible.name: modelData.name + (modelData.action !== "" ? ", does " + modelData.action : "")
                    Accessible.onPressAction: keyEditor.openFor(modelData.id)
                    Keys.onSpacePressed: keyEditor.openFor(modelData.id)
                    Keys.onReturnPressed: keyEditor.openFor(modelData.id)
                    property real radius: knob ? width / 2 : 6
                    WgFocusRing {}

                    Rectangle {
                        anchors.fill: parent
                        radius: key.radius
                        color: key.modelData.remapped ? Theme.alpha(Theme.accentA, Theme.dark ? 0.22 : 0.12)
                               : key.hot ? Theme.cardHi : Theme.card
                        border.width: 1
                        border.color: key.modelData.remapped ? Theme.alpha(Theme.accentB, 0.7)
                                      : key.hot ? Theme.borderHi : Theme.border
                        opacity: key.modelData.locked ? 0.55 : 1
                        Behavior on color { ColorAnimation { duration: Theme.fast } }
                        // ISO Enter: hide the seam between the two pieces
                        Rectangle {
                            visible: key.lower
                            x: 1; y: -2; width: parent.width - 2; height: 4
                            color: parent.color
                        }
                    }
                    Text {
                        anchors.fill: parent
                        anchors.margins: 3
                        visible: !key.lower
                        text: key.modelData.remapped ? key.modelData.action : key.modelData.label
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        wrapMode: key.modelData.remapped ? Text.Wrap : Text.NoWrap
                        maximumLineCount: 2
                        font.pixelSize: Math.max(8, Math.min(Theme.px(12), art.unit * (key.modelData.remapped ? 0.24 : 0.3)))
                        font.weight: key.modelData.remapped ? Font.DemiBold : Font.Medium
                        color: key.modelData.remapped ? (Theme.dark ? "#E5DBFF" : Theme.accentA) : Theme.textMuted
                    }
                    MouseArea {
                        id: keyMa
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: !key.modelData.locked
                        cursorShape: art.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: keyEditor.openFor(key.modelData.id)
                    }
                    C.ToolTip.visible: keyMa.containsMouse && key.modelData.locked
                    C.ToolTip.text: "The " + key.modelData.name + " always keeps its own job"
                    C.ToolTip.delay: 400
                }
            }
        }
    }

    // Macros
    WgCard {
        width: parent.width
        visible: page.ready
        spacing: 10
        RowLayout {
            Layout.fillWidth: true
            SectionTitle { text: "Macros" }
            Item { Layout.fillWidth: true }
            WgButton {
                enabled: page.editable && !keyboard.macrosFull
                iconName: "plus"
                text: "New macro"
                onClicked: macroEditor.openFor(0)
            }
        }
        Note {
            visible: keyboard.macros.length === 0
            text: "A macro plays a series of keys from one key press, right on the keyboard. Record one here, then give it to a key above."
        }
        Repeater {
            model: keyboard.macros
            delegate: Rectangle {
                id: mrow
                required property var modelData
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(56, Theme.px(56))
                radius: Theme.radiusSm
                color: Theme.inset
                border.width: 1
                border.color: Theme.border
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 8
                    spacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: mrow.modelData.name
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                            text: mrow.modelData.text + "  ·  " + (mrow.modelData.mode === "once" ? "plays once" : mrow.modelData.mode === "repeat" ? "repeats while held" : "press to start, press to stop")
                                  + (mrow.modelData.used ? "" : "  ·  not on a key yet")
                            font.pixelSize: Theme.fsSmall
                            color: Theme.textMuted
                        }
                    }
                    WgButton {
                        variant: "ghost"
                        text: "Edit"
                        enabled: page.editable
                        onClicked: macroEditor.openFor(mrow.modelData.slot)
                    }
                    WgButton {
                        variant: "ghost"
                        iconName: "trash"
                        text: "Delete"
                        enabled: page.editable
                        onClicked: keyboard.deleteMacro(mrow.modelData.slot)
                    }
                }
            }
        }
    }

    // Controls on: a quiet way back
    Text {
        width: parent.width
        visible: page.ready && !keyboard.locked && !keyboard.confirmed
        horizontalAlignment: Text.AlignHCenter
        text: "Controls are on for this keyboard.  <a href=\"lock\">Turn them off</a>"
        textFormat: Text.StyledText
        linkColor: Theme.accentB
        font.pixelSize: Theme.fsSmall
        color: Theme.textFaint
        onLinkActivated: keyboard.lock()
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }

    // ------------------------------------------------------------ key editor
    component Dialog: C.Popup {
        parent: C.Overlay.overlay
        anchors.centerIn: parent
        modal: true
        focus: true
        padding: 22
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        C.Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, Theme.dark ? 0.5 : 0.25) }
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
        background: Rectangle {
            radius: Theme.radius
            color: Theme.card
            border.width: 1
            border.color: Theme.borderHi
        }
    }

    component Chip: Rectangle {
        id: chip
        property string text
        property bool checked
        signal toggled()
        implicitWidth: chipText.implicitWidth + 24
        implicitHeight: Math.max(32, Theme.px(32))
        radius: height / 2
        color: checked ? Theme.alpha(Theme.accentA, 0.2) : (chipMa.containsMouse ? Theme.hover : Theme.inset)
        border.width: 1
        border.color: checked ? Theme.accentB : Theme.border
        activeFocusOnTab: true
        Accessible.role: Accessible.CheckBox
        Accessible.name: text
        Accessible.checked: checked
        Keys.onSpacePressed: toggled()
        WgFocusRing {}
        Text {
            id: chipText
            anchors.centerIn: parent
            text: chip.text
            font.pixelSize: Theme.fsBody - 1
            font.weight: chip.checked ? Font.DemiBold : Font.Medium
            color: chip.checked ? Theme.text : Theme.textMuted
        }
        MouseArea { id: chipMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: chip.toggled() }
    }

    // A short scrolling list (for long choices like every key).
    component ChoiceList: Rectangle {
        id: cl
        property var model: []
        property int code: -1
        signal picked(int code)
        implicitHeight: Theme.px(180)
        radius: Theme.radiusSm
        color: Theme.inset
        border.width: 1
        border.color: Theme.border
        clip: true
        ListView {
            id: lv
            anchors.fill: parent
            anchors.margins: 4
            model: cl.model
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded }
            delegate: Rectangle {
                id: row
                required property var modelData
                width: lv.width - 8
                height: Math.max(30, Theme.px(30))
                radius: 6
                color: row.modelData.code === cl.code ? Theme.alpha(Theme.accentA, 0.2) : (rowMa.containsMouse ? Theme.hover : "transparent")
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    x: 10
                    text: row.modelData.name
                    font.pixelSize: Theme.fsBody - 1
                    font.weight: row.modelData.code === cl.code ? Font.DemiBold : Font.Normal
                    color: Theme.text
                }
                MouseArea { id: rowMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: cl.picked(row.modelData.code) }
            }
        }
    }

    Dialog {
        id: keyEditor
        property string keyId: ""
        property string keyName: ""
        property int kindIndex: 0
        property int code: -1
        property var mods: []
        readonly property var kinds: ["default", "key", "media", "mouse", "function", "macro", "disabled"]
        readonly property var macroChoices: keyboard.macros.map(m => ({ code: m.slot, name: m.name }))
        width: Math.min(page.width - 40, Theme.px(520))

        function openFor(id) {
            const k = keyboard.keys.find(x => x.id === id)
            if (!k || k.locked)
                return
            keyId = id
            keyName = k.name
            const b = keyboard.binding(id)
            kindIndex = Math.max(0, kinds.indexOf(b.kind))
            code = b.kind === "macro" ? b.slot : (b.code !== undefined ? b.code : -1)
            mods = b.mods || []
            open()
        }
        function hasMod(m) { return mods.indexOf(m) >= 0 }
        function toggleMod(m) { mods = hasMod(m) ? mods.filter(x => x !== m) : mods.concat([m]) }
        readonly property string kind: kinds[kindIndex]
        readonly property bool complete: kind === "default" || kind === "disabled" || code >= 0
        function apply() {
            keyboard.bindKey(keyId, kind, Math.max(0, code), kind === "key" ? mods.join(",") : "")
            close()
        }

        contentItem: ColumnLayout {
            spacing: 14
            Text {
                text: keyEditor.keyName.length === 1 ? "Key " + keyEditor.keyName : keyEditor.keyName + " key"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            WgSegmented {
                Layout.fillWidth: true
                accessibleName: "What the key does"
                model: ["Normal", "Key", "Media", "Mouse", "Profile", "Macro", "Off"]
                currentIndex: keyEditor.kindIndex
                onActivated: (i) => { keyEditor.kindIndex = i; keyEditor.code = -1; keyEditor.mods = [] }
            }

            Note {
                visible: keyEditor.kind === "default" || keyEditor.kind === "disabled"
                text: keyEditor.kind === "default" ? "The key does what's printed on it." : "The key does nothing on this layer."
            }

            // Key: press it, or pick from the list
            ColumnLayout {
                visible: keyEditor.kind === "key"
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    id: capture
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(44, Theme.px(44))
                    radius: Theme.radiusSm
                    color: Theme.inset
                    border.width: 1
                    border.color: activeFocus ? Theme.accentB : Theme.border
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Button
                    Accessible.name: "Press the key it should type"
                    Keys.onPressed: (event) => {
                        const u = keyboard.usageFromScan(event.nativeScanCode)
                        if (u > 0) { keyEditor.code = u; event.accepted = true }
                    }
                    Text {
                        anchors.centerIn: parent
                        text: capture.activeFocus ? (keyEditor.code >= 0 ? "Types " + keyboard.usageName(keyEditor.code) + ". Press another key to change it" : "Press the key it should type…")
                              : (keyEditor.code >= 0 ? "Types " + keyboard.usageName(keyEditor.code) : "Click here and press a key, or pick one below")
                        font.pixelSize: Theme.fsBody - 1
                        font.weight: keyEditor.code >= 0 ? Font.DemiBold : Font.Normal
                        color: keyEditor.code >= 0 ? Theme.text : Theme.textMuted
                    }
                    MouseArea { anchors.fill: parent; onClicked: capture.forceActiveFocus() }
                }
                ChoiceList {
                    Layout.fillWidth: true
                    model: keyboard.pickerKeys
                    code: keyEditor.code
                    onPicked: (c) => keyEditor.code = c
                }
                RowLayout {
                    spacing: 8
                    Text { text: "Hold with it"; font.pixelSize: Theme.fsBody - 1; color: Theme.textMuted; Layout.rightMargin: 4 }
                    Repeater {
                        model: [["ctrl", "Ctrl"], ["shift", "Shift"], ["alt", "Alt"], ["super", "Super"]]
                        delegate: Chip {
                            required property var modelData
                            text: modelData[1]
                            checked: keyEditor.hasMod(modelData[0])
                            onToggled: keyEditor.toggleMod(modelData[0])
                        }
                    }
                }
            }

            ChoiceList {
                visible: keyEditor.kind === "media" || keyEditor.kind === "mouse" || keyEditor.kind === "function"
                Layout.fillWidth: true
                implicitHeight: Theme.px(keyEditor.kind === "function" ? 136 : 180)
                model: keyEditor.kind === "media" ? keyboard.mediaActions : keyEditor.kind === "mouse" ? keyboard.mouseActions : keyboard.functionActions
                code: keyEditor.code
                onPicked: (c) => keyEditor.code = c
            }

            ColumnLayout {
                visible: keyEditor.kind === "macro"
                Layout.fillWidth: true
                spacing: 8
                Note {
                    visible: keyEditor.macroChoices.length === 0
                    text: "There are no macros yet. Make one with New macro under Macros, then come back."
                }
                ChoiceList {
                    visible: keyEditor.macroChoices.length > 0
                    Layout.fillWidth: true
                    implicitHeight: Theme.px(Math.min(180, 12 + 30 * keyEditor.macroChoices.length))
                    model: keyEditor.macroChoices
                    code: keyEditor.code
                    onPicked: (c) => keyEditor.code = c
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 8
                Item { Layout.fillWidth: true }
                WgButton { text: "Cancel"; onClicked: keyEditor.close() }
                WgButton {
                    variant: "primary"
                    text: "Save to keyboard"
                    enabled: keyEditor.complete
                    onClicked: keyEditor.apply()
                }
            }
        }
    }

    // ------------------------------------------------------------ macro editor
    Dialog {
        id: macroEditor
        property int slot: 0
        property var events: []
        property double lastTime: 0
        property int modeIndex: 0
        property bool recording: false
        readonly property var modes: ["once", "repeat", "toggle"]
        width: Math.min(page.width - 40, Theme.px(520))
        onClosed: recording = false

        function openFor(s) {
            slot = s
            const m = s > 0 ? keyboard.macro(s) : null
            nameField.text = m ? m.name : ""
            events = m ? m.events : []
            modeIndex = m ? Math.max(0, modes.indexOf(m.mode)) : 0
            recording = false
            open()
        }
        function record(type, scan) {
            const u = keyboard.usageFromScan(scan)
            if (u <= 0 || events.length >= 82)
                return
            const now = Date.now()
            const ev = events.slice()
            if (ev.length > 0)
                ev[ev.length - 1].delay = Math.max(1, Math.min(32767, Math.round(now - lastTime)))
            ev.push({ type: type, code: u, delay: 20 })
            lastTime = now
            events = ev
        }
        readonly property var presses: events.filter(e => e.type === "press")

        contentItem: ColumnLayout {
            spacing: 14
            Text {
                text: macroEditor.slot > 0 ? "Edit macro" : "New macro"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            WgTextField {
                id: nameField
                Layout.fillWidth: true
                placeholder: "Name, for example Copy and paste"
                maximumLength: 40
            }
            Rectangle {
                id: rec
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(96, Theme.px(96))
                radius: Theme.radiusSm
                color: Theme.inset
                border.width: 1
                border.color: macroEditor.recording ? Theme.coral : Theme.border
                focus: macroEditor.recording
                Keys.onPressed: (event) => { if (macroEditor.recording && !event.isAutoRepeat) { macroEditor.record("press", event.nativeScanCode); event.accepted = true } }
                Keys.onReleased: (event) => { if (macroEditor.recording && !event.isAutoRepeat) { macroEditor.record("release", event.nativeScanCode); event.accepted = true } }
                Flow {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 6
                    visible: macroEditor.presses.length > 0
                    Repeater {
                        model: macroEditor.presses
                        delegate: Rectangle {
                            required property var modelData
                            width: keyCap.implicitWidth + 16
                            height: Theme.px(26)
                            radius: 6
                            color: Theme.card
                            border.width: 1
                            border.color: Theme.borderHi
                            Text { id: keyCap; anchors.centerIn: parent; text: keyboard.usageName(parent.modelData.code); font.pixelSize: Theme.fsSmall; font.weight: Font.Medium; color: Theme.text }
                        }
                    }
                }
                Text {
                    anchors.centerIn: parent
                    visible: macroEditor.presses.length === 0
                    text: macroEditor.recording ? "Type the keys now…" : "Press Record, then type the keys"
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.textMuted
                }
            }
            RowLayout {
                spacing: 8
                WgButton {
                    text: macroEditor.recording ? "Stop" : (macroEditor.events.length > 0 ? "Record again" : "Record")
                    iconName: macroEditor.recording ? "block" : "play"
                    onClicked: {
                        if (!macroEditor.recording) { macroEditor.events = []; macroEditor.lastTime = Date.now() }
                        macroEditor.recording = !macroEditor.recording
                        if (macroEditor.recording) rec.forceActiveFocus()
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: macroEditor.presses.length + (macroEditor.presses.length === 1 ? " key" : " keys") + ", with your timing"
                    visible: macroEditor.events.length > 0
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                }
            }
            Text { text: "When its key is pressed"; font.pixelSize: Theme.fsBody; font.weight: Font.Medium; color: Theme.text }
            WgSegmented {
                Layout.fillWidth: true
                accessibleName: "When its key is pressed"
                model: ["Play once", "Repeat while held", "Start and stop"]
                currentIndex: macroEditor.modeIndex
                onActivated: (i) => macroEditor.modeIndex = i
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                spacing: 8
                Item { Layout.fillWidth: true }
                WgButton { text: "Cancel"; onClicked: macroEditor.close() }
                WgButton {
                    variant: "primary"
                    text: "Save macro"
                    enabled: macroEditor.events.length > 0 && !macroEditor.recording
                    onClicked: {
                        // A key held at the end gets its release, so the board never leaves it down.
                        const ev = macroEditor.events.slice()
                        const down = {}
                        ev.forEach(e => { down[e.code] = e.type === "press" })
                        Object.keys(down).forEach(c => { if (down[c]) ev.push({ type: "release", code: Number(c), delay: 20 }) })
                        if (keyboard.saveMacro(macroEditor.slot, nameField.text, macroEditor.modes[macroEditor.modeIndex], JSON.stringify(ev.slice(0, 82))) > 0)
                            macroEditor.close()
                    }
                }
            }
        }
    }
}
