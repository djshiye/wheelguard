import QtQuick
import QtQuick.Layouts

WgScrollPage {
    id: page
    objectName: "profilesPage"

    // "list" shows the profiles, "pick" the installed apps to choose from, "edit" the three settings.
    property string mode: "list"
    property string editId: ""
    property string editName: ""
    property string editIcon: ""
    property int protIndex: 0           // 0 keep, then Light to Maximum
    property int lockIndex: 0           // 0 keep, 1 on, 2 off
    property int stageIndex: 0          // 0 keep, then stage 1 to n
    property string query: ""
    property var appList: []
    property string formError: ""

    readonly property var protModel: ["Keep", "Light", "Balanced", "Strong", "Maximum"]
    readonly property var lockModel: ["Keep", "On", "Off"]
    readonly property var lockWords: ["keep", "on", "off"]
    readonly property int stageCount: mouse.stageLabels.length > 0 ? mouse.stageLabels.length : 4
    readonly property var stageModel: {
        const m = ["Keep"]
        for (let i = 1; i <= stageCount; i++)
            m.push(String(i))
        return m
    }
    readonly property var stageSubs: {
        if (mouse.stageLabels.length === 0)
            return []
        const s = [""]
        for (let i = 0; i < mouse.stageLabels.length; i++)
            s.push(mouse.stageLabels[i] + " DPI")
        return s
    }
    readonly property bool anyChange: protIndex > 0 || lockIndex > 0 || stageIndex > 0

    function startPick() {
        profiles.refreshApps()
        page.query = ""
        page.appList = profiles.installedApps("")
        page.formError = ""
        page.mode = "pick"
    }
    function startEdit(appId, name, icon, prot, lock, stage) {
        page.editId = appId
        page.editName = name
        page.editIcon = icon
        page.protIndex = Math.max(0, page.protModel.indexOf(prot))
        page.lockIndex = Math.max(0, page.lockWords.indexOf(lock))
        page.stageIndex = stage >= 0 ? Math.min(stage + 1, page.stageCount) : 0
        page.formError = ""
        page.mode = "edit"
    }
    function save() {
        page.formError = profiles.saveProfile(page.editId, page.protIndex > 0 ? page.protModel[page.protIndex] : "keep",
                                              page.lockWords[page.lockIndex], page.stageIndex - 1)
        if (page.formError === "")
            page.mode = "list"
    }
    function cancel() {
        page.formError = ""
        page.mode = "list"
    }

    // An app's icon from the theme, or a generic glyph when it has none.
    component AppIcon: Item {
        id: ai
        property string icon: ""
        property real side: Theme.px(36)
        implicitWidth: side
        implicitHeight: side
        Rectangle {
            anchors.fill: parent
            radius: Theme.radiusSm - 2
            color: Theme.inset
            border.width: 1
            border.color: Theme.border
            visible: img.status !== Image.Ready
        }
        Image {
            id: img
            anchors.fill: parent
            source: ai.icon !== "" ? "image://appicon/" + encodeURIComponent(ai.icon) : ""
            sourceSize: Qt.size(128, 128)
            fillMode: Image.PreserveAspectFit
            smooth: true
            mipmap: true
            visible: status === Image.Ready
        }
        WgIcon {
            anchors.centerIn: parent
            visible: img.status !== Image.Ready
            name: "apps"
            size: ai.side * 0.5
            color: Theme.textMuted
        }
    }

    // A small icon-only button (delete); keyboard and screen-reader friendly.
    component IconAction: Rectangle {
        id: act
        property string iconName: ""
        property string label: ""
        signal triggered()
        implicitWidth: Theme.px(34)
        implicitHeight: Theme.px(34)
        radius: Theme.radiusSm - 2
        color: actMa.pressed ? Theme.borderHi : (actMa.containsMouse ? Theme.hover : "transparent")
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: act.label
        Accessible.onPressAction: act.triggered()
        Keys.onSpacePressed: act.triggered()
        Keys.onReturnPressed: act.triggered()
        WgFocusRing {}
        WgIcon {
            anchors.centerIn: parent
            name: act.iconName
            size: Theme.px(18)
            color: actMa.containsMouse ? Theme.coral : Theme.textMuted
            Behavior on color { ColorAnimation { duration: Theme.fast } }
        }
        MouseArea {
            id: actMa
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: act.triggered()
        }
    }

    // Title
    Column {
        width: parent.width
        spacing: 6
        Text {
            text: "Profiles"
            font.pixelSize: Theme.fsTitle
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            width: parent.width
            text: "Pick an app, like a game. While it is in front, Wheelguard switches to the settings you choose, then back to yours when you leave it."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
    }

    // Master switch, what is active, and the panel-extension note
    WgCard {
        width: parent.width
        spacing: 12

        WgToggleRow {
            Layout.topMargin: 0
            Layout.bottomMargin: 0
            title: "Per-game profiles"
            description: "Off by default. Wheelguard only learns which app is in front, never what is in its window, and nothing leaves your computer."
            checked: appSettings.profilesEnabled
            onToggled: (value) => appSettings.setProfilesEnabled(value)
        }
        RowLayout {
            visible: appSettings.profilesEnabled && profiles.activeId !== ""
            Layout.fillWidth: true
            spacing: 10
            Rectangle {
                Layout.preferredWidth: Theme.px(10)
                Layout.preferredHeight: Theme.px(10)
                radius: width / 2
                color: Theme.good
            }
            Text {
                Layout.fillWidth: true
                text: "Active now: " + profiles.activeName
                font.pixelSize: Theme.fsBody
                font.weight: Font.DemiBold
                color: Theme.text
                elide: Text.ElideRight
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
        }
        RowLayout {
            visible: appSettings.profilesEnabled && !profiles.panelActive && profiles.activeId === ""
            Layout.fillWidth: true
            spacing: 12
            WgIcon { name: "info"; size: 20; color: Theme.amber }
            Text {
                Layout.fillWidth: true
                text: "Profiles need the Wheelguard panel extension, which turns on after your next login."
                font.pixelSize: Theme.fsSmall
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }
    }

    // The profiles
    WgCard {
        width: parent.width
        visible: page.mode === "list"
        enabled: visible
        spacing: 14

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    Layout.fillWidth: true
                    text: "Your profiles"
                    font.pixelSize: Theme.fsH2
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    Layout.fillWidth: true
                    text: profiles.count + " of " + profiles.maxProfiles
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textFaint
                }
            }
            WgButton {
                variant: "primary"
                iconName: "plus"
                text: "Add profile"
                enabled: profiles.count < profiles.maxProfiles
                onClicked: page.startPick()
            }
        }

        Text {
            visible: profiles.count === 0
            Layout.fillWidth: true
            text: "No profiles yet. Add an app, then choose what changes while it is in front: scroll protection, Click-Lock or the DPI stage."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: profiles.items
            delegate: Rectangle {
                id: row
                required property var modelData
                readonly property bool engaged: appSettings.profilesEnabled && profiles.activeId === modelData.appId
                Layout.fillWidth: true
                implicitHeight: Theme.px(68)
                radius: Theme.radiusSm
                color: rowMa.containsMouse ? Theme.cardHi : Theme.inset
                border.width: 1
                border.color: row.engaged ? Theme.alpha(Theme.good, 0.6) : Theme.border
                Behavior on color { ColorAnimation { duration: Theme.fast } }

                function edit() {
                    page.startEdit(modelData.appId, modelData.name, modelData.icon, modelData.protection,
                                   modelData.clicklock, modelData.stage)
                }
                activeFocusOnTab: true
                Accessible.role: Accessible.Button
                Accessible.name: "Edit the profile for " + modelData.name
                Accessible.description: modelData.summary
                Accessible.onPressAction: row.edit()
                Keys.onSpacePressed: row.edit()
                Keys.onReturnPressed: row.edit()
                WgFocusRing {}

                MouseArea {
                    id: rowMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: row.edit()
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 14
                    anchors.rightMargin: 10
                    spacing: 12
                    AppIcon {
                        Layout.alignment: Qt.AlignVCenter
                        icon: modelData.icon
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignVCenter
                        spacing: 2
                        Item {
                            Layout.fillWidth: true
                            implicitHeight: nameText.implicitHeight
                            Text {
                                id: nameText
                                width: Math.min(implicitWidth, parent.width - (row.engaged ? activePill.width + 8 : 0))
                                text: modelData.name
                                font.pixelSize: Theme.fsBody
                                font.weight: Font.DemiBold
                                color: Theme.text
                                elide: Text.ElideRight
                            }
                            Rectangle {
                                id: activePill
                                visible: row.engaged
                                x: nameText.width + 8
                                anchors.verticalCenter: parent.verticalCenter
                                width: activeText.implicitWidth + 14
                                height: Theme.px(18)
                                radius: height / 2
                                color: Theme.alpha(Theme.good, 0.18)
                                Text {
                                    id: activeText
                                    anchors.centerIn: parent
                                    text: "Active"
                                    font.pixelSize: Theme.fsSmall
                                    font.weight: Font.DemiBold
                                    color: Theme.good
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: modelData.summary
                            font.pixelSize: Theme.fsSmall
                            color: Theme.textMuted
                            elide: Text.ElideRight
                        }
                    }
                    IconAction {
                        Layout.alignment: Qt.AlignVCenter
                        iconName: "trash"
                        label: "Delete the profile for " + modelData.name
                        onTriggered: profiles.removeProfile(modelData.appId)
                    }
                }
            }
        }

        Text {
            visible: profiles.error !== ""
            Layout.fillWidth: true
            text: profiles.error
            font.pixelSize: Theme.fsSmall
            color: Theme.coral
            wrapMode: Text.WordWrap
        }
    }

    // Step 1: choose the app
    WgCard {
        width: parent.width
        visible: page.mode === "pick"
        enabled: visible
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: "Choose an app"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            WgButton { text: "Cancel"; variant: "ghost"; onClicked: page.cancel() }
        }
        WgTextField {
            id: search
            Layout.fillWidth: true
            placeholder: "Search installed apps"
            maximumLength: 60
            onTextChanged: {
                page.query = text
                page.appList = profiles.installedApps(text)
            }
            Keys.onEscapePressed: page.cancel()
            onVisibleChanged: if (visible) { text = ""; forceActiveFocus() }
        }
        Text {
            visible: page.appList.length === 0
            Layout.fillWidth: true
            text: page.query === "" ? "No more apps to add." : "No installed app matches \"" + page.query + "\"."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        ListView {
            id: appsView
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, Theme.px(300))
            visible: page.appList.length > 0
            clip: true
            spacing: 4
            model: page.appList
            boundsBehavior: Flickable.StopAtBounds
            delegate: Rectangle {
                id: appRow
                required property var modelData
                width: ListView.view.width
                height: Theme.px(52)
                radius: Theme.radiusSm - 2
                color: appMa.containsMouse ? Theme.cardHi : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                function choose() {
                    page.startEdit(modelData.id, modelData.name, modelData.icon, "", "keep", -1)
                }
                activeFocusOnTab: true
                Accessible.role: Accessible.Button
                Accessible.name: modelData.name
                Accessible.onPressAction: appRow.choose()
                Keys.onSpacePressed: appRow.choose()
                Keys.onReturnPressed: appRow.choose()
                WgFocusRing {}
                MouseArea {
                    id: appMa
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: appRow.choose()
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 10
                    anchors.rightMargin: 10
                    spacing: 12
                    AppIcon {
                        side: Theme.px(32)
                        icon: modelData.icon
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData.name
                        font.pixelSize: Theme.fsBody
                        color: Theme.text
                        elide: Text.ElideRight
                    }
                    WgIcon { name: "chevron"; size: 16; color: Theme.textFaint }
                }
            }
        }
    }

    // Step 2: choose what changes
    WgCard {
        width: parent.width
        visible: page.mode === "edit"
        enabled: visible
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            AppIcon { icon: page.editIcon }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    Layout.fillWidth: true
                    text: page.editName
                    font.pixelSize: Theme.fsH2
                    font.weight: Font.DemiBold
                    color: Theme.text
                    elide: Text.ElideRight
                }
                Text {
                    Layout.fillWidth: true
                    text: "Choose what changes while this app is in front. Anything left on Keep stays as it is."
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            Text { text: "Scroll protection"; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
            WgSegmented {
                Layout.fillWidth: true
                accessibleName: "Scroll protection while this app is in front"
                model: page.protModel
                currentIndex: page.protIndex
                onActivated: (i) => page.protIndex = i
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            Text { text: "Click-Lock"; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
            WgSegmented {
                Layout.fillWidth: true
                accessibleName: "Click-Lock while this app is in front"
                model: page.lockModel
                currentIndex: page.lockIndex
                onActivated: (i) => page.lockIndex = i
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 8
            Text { text: "DPI stage"; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
            WgSegmented {
                Layout.fillWidth: true
                accessibleName: "DPI stage while this app is in front"
                model: page.stageModel
                subLabels: page.stageSubs
                currentIndex: page.stageIndex
                onActivated: (i) => page.stageIndex = i
            }
            Text {
                Layout.fillWidth: true
                text: "Only with a supported mouse connected. If it is asleep or busy, the switch is skipped."
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
                wrapMode: Text.WordWrap
            }
        }

        Text {
            visible: page.formError !== ""
            Layout.fillWidth: true
            text: page.formError
            font.pixelSize: Theme.fsSmall
            color: Theme.coral
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                Layout.fillWidth: true
                visible: !page.anyChange
                text: "Pick at least one change."
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
            }
            Item { Layout.fillWidth: true; visible: page.anyChange }
            WgButton { text: "Cancel"; onClicked: page.cancel() }
            WgButton {
                variant: "primary"
                iconName: "check"
                text: "Save profile"
                enabled: page.anyChange
                onClicked: page.save()
            }
        }
    }
}
