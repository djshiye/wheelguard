import QtQuick
import QtQuick.Layouts

WgScrollPage {
    id: page
    readonly property var accelValues: ["default", "flat", "adaptive"]
    readonly property var accelNotes: [
        "Follows whatever GNOME decides for your pointer.",
        "Off (raw, recommended for games): the pointer moves exactly as far as your hand does.",
        "On: the pointer travels further the faster you move."
    ]

    // Title
    Column {
        width: parent.width
        spacing: 6
        Text {
            text: "Pointer"
            font.pixelSize: Theme.fsTitle
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            width: parent.width
            text: "These are GNOME's own mouse settings, so they apply to every mouse, not just this one."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
    }

    WgCard {
        width: parent.width
        visible: !pointer.available
        RowLayout {
            spacing: 12
            WgIcon { name: "alert"; size: 20; color: Theme.amber }
            Text {
                Layout.fillWidth: true
                text: "The gsettings tool or the GNOME mouse schema isn't available here, so these options can't be read or changed."
                font.pixelSize: Theme.fsBody
                color: Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }
    }

    // Speed
    WgCard {
        width: parent.width
        visible: pointer.available
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: "Pointer speed"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                text: Math.round((pointer.speed + 1) * 50) + "%"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.dark ? "#B79DFF" : Theme.accentA
            }
        }
        WgSlider {
            Layout.fillWidth: true
            from: -1
            to: 1
            stepSize: 0.01
            value: pointer.speed
            valueText: Math.round((value + 1) * 50) + "%"
            onMoved: pointer.speed = value
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            Text { text: "Slower"; font.pixelSize: Theme.fsSmall; color: Theme.textFaint }
            Item { Layout.fillWidth: true }
            Text { text: "Faster"; font.pixelSize: Theme.fsSmall; color: Theme.textFaint }
        }
    }

    // Acceleration
    WgCard {
        width: parent.width
        visible: pointer.available
        spacing: 14

        Text {
            text: "Acceleration"
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: Theme.text
        }
        WgSegmented {
            Layout.fillWidth: true
            model: ["System default", "Off", "On"]
            subLabels: ["GNOME's choice", "raw, best for games", "speeds up with motion"]
            currentIndex: page.accelValues.indexOf(pointer.accelProfile)
            onActivated: (i) => pointer.accelProfile = page.accelValues[i]
        }
        Text {
            Layout.fillWidth: true
            text: page.accelNotes[Math.max(0, page.accelValues.indexOf(pointer.accelProfile))]
            font.pixelSize: Theme.fsBody - 1
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
    }

    // Switches
    WgCard {
        width: parent.width
        visible: pointer.available
        vpad: 14
        spacing: 0

        component ToggleRow: RowLayout {
            id: tr
            property string title
            property string description
            property bool checked
            signal toggled(bool value)
            Layout.fillWidth: true
            Layout.topMargin: 10
            Layout.bottomMargin: 10
            spacing: 16
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { text: tr.title; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
                Text {
                    Layout.fillWidth: true
                    text: tr.description
                    font.pixelSize: Theme.fsSmall
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            WgSwitch {
                checked: tr.checked
                onToggled: tr.toggled(checked)
            }
        }

        ToggleRow {
            title: "Natural scrolling"
            description: "Content follows your finger: scrolling the wheel down moves the page up."
            checked: pointer.naturalScroll
            onToggled: (v) => pointer.naturalScroll = v
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
        ToggleRow {
            title: "Left-handed"
            description: "Swaps the left and right buttons."
            checked: pointer.leftHanded
            onToggled: (v) => pointer.leftHanded = v
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
        ToggleRow {
            title: "Middle-click emulation"
            description: "Press the left and right buttons together to middle-click."
            checked: pointer.middleClickEmulation
            onToggled: (v) => pointer.middleClickEmulation = v
        }
    }

    // Double-click
    WgCard {
        id: dcCard
        width: parent.width
        visible: pointer.available
        spacing: 12

        property double lastClick: 0
        property string feedback: "Double-click here to test"
        property real lit: 0

        SequentialAnimation {
            id: flash
            NumberAnimation { target: dcCard; property: "lit"; to: 1; duration: Theme.fast; easing.type: Easing.OutCubic }
            PauseAnimation { duration: 350 }
            NumberAnimation { target: dcCard; property: "lit"; to: 0; duration: 520; easing.type: Easing.InOutQuad }
        }
        Timer {
            id: reset
            interval: 1600
            onTriggered: dcCard.feedback = "Double-click here to test"
        }

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: "Double-click speed"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                text: pointer.doubleClick + " ms"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.dark ? "#B79DFF" : Theme.accentA
            }
        }
        Text {
            Layout.fillWidth: true
            text: "The longest gap between two clicks that still counts as a double-click."
            font.pixelSize: Theme.fsBody - 1
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        WgSlider {
            Layout.fillWidth: true
            from: 100
            to: 1000
            stepSize: 10
            value: pointer.doubleClick
            valueText: Math.round(value) + " ms"
            onMoved: pointer.doubleClick = Math.round(value)
        }

        Rectangle {
            id: target
            Layout.fillWidth: true
            Layout.preferredHeight: 76
            radius: Theme.radius - 2
            color: Theme.inset
            border.width: 1
            border.color: Qt.tint(Theme.border, Theme.alpha(Theme.accentB, dcCard.lit * 0.9))
            Behavior on color { ColorAnimation { duration: Theme.normal } }

            Rectangle {
                anchors.fill: parent
                radius: parent.radius
                opacity: dcCard.lit
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Theme.alpha(Theme.accentA, 0.45) }
                    GradientStop { position: 1; color: Theme.alpha(Theme.accentB, 0.35) }
                }
            }
            Row {
                anchors.centerIn: parent
                spacing: 12
                WgIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    name: dcCard.lit > 0.5 ? "check" : "cursor"
                    size: 20
                    color: dcCard.lit > 0.5 ? Theme.text : Theme.textMuted
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: dcCard.feedback
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.Medium
                    color: dcCard.lit > 0.5 ? Theme.text : Theme.textMuted
                }
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    var now = Date.now()
                    var gap = now - dcCard.lastClick
                    if (dcCard.lastClick > 0 && gap <= pointer.doubleClick) {
                        dcCard.feedback = "Double-click registered · " + Math.round(gap) + " ms"
                        dcCard.lastClick = 0
                        flash.restart()
                    } else {
                        dcCard.lastClick = now
                        dcCard.feedback = "Now click again…"
                    }
                    reset.restart()
                }
            }
        }
    }

    // Drag threshold
    WgCard {
        width: parent.width
        visible: pointer.available
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: "Drag threshold"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Text {
                text: pointer.dragThreshold + " px"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.dark ? "#B79DFF" : Theme.accentA
            }
        }
        Text {
            Layout.fillWidth: true
            text: "How far the pointer has to move with a button held before a click turns into a drag. Raise it if clicks sometimes become accidental drags."
            font.pixelSize: Theme.fsBody - 1
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        WgSlider {
            Layout.fillWidth: true
            from: 1
            to: 64
            stepSize: 1
            value: pointer.dragThreshold
            valueText: Math.round(value) + " px"
            onMoved: pointer.dragThreshold = Math.round(value)
        }
    }
}
