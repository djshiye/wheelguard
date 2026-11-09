import QtQuick
import QtQuick.Layouts

// Auto-tune: listen to the wheel for about a minute, then suggest the lightest protection strength that
// would have caught the stray ticks. Self-contained: it needs the `autoTune` backend (gui/autotune.py,
// fed from daemon.tick in app.py) and `daemon`. Drop it into the Scroll protection page as `AutoTuneCard {}`.
WgCard {
    id: root
    readonly property bool reachable: daemon.state === "running"
    readonly property bool ready: autoTune.state === "ready"
    readonly property bool differs: ready && autoTune.recommendedIndex !== daemon.presetIndex
    spacing: 14

    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Text {
            Layout.fillWidth: true
            text: "Auto-tune"
            font.pixelSize: Theme.fsH2
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Rectangle {
            visible: root.ready
            Layout.preferredWidth: confText.implicitWidth + 16
            Layout.preferredHeight: Theme.px(22)
            radius: 11
            color: Theme.alpha(autoTune.confidence === "high" ? Theme.good : (autoTune.confidence === "medium" ? Theme.accentB : Theme.amber), 0.16)
            Text {
                id: confText
                anchors.centerIn: parent
                text: autoTune.confidence === "high" ? "Confident" : (autoTune.confidence === "medium" ? "Fairly sure" : "Not very sure yet")
                font.pixelSize: Theme.fsSmall
                font.weight: Font.DemiBold
                color: autoTune.confidence === "high" ? Theme.good : (autoTune.confidence === "medium" ? Theme.accentB : Theme.amber)
            }
        }
    }

    Text {
        Layout.fillWidth: true
        text: autoTune.state === "idle" ? "Scroll normally for about a minute and Wheelguard will suggest how strong protection needs to be. Nothing changes until you say so."
              : autoTune.message
        font.pixelSize: Theme.fsBody
        color: Theme.textMuted
        wrapMode: Text.WordWrap
        lineHeight: 1.15
    }

    Rectangle {     // progress while listening
        visible: autoTune.running && !root.ready
        Layout.fillWidth: true
        Layout.preferredHeight: 6
        radius: 3
        color: Theme.track
        Rectangle {
            width: Math.max(parent.height, parent.width * Math.min(1, autoTune.progress))
            height: parent.height
            radius: 3
            Behavior on width { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accentA }
                GradientStop { position: 1; color: Theme.accentB }
            }
        }
    }

    Rectangle {     // the suggestion
        visible: root.ready
        Layout.fillWidth: true
        implicitHeight: tile.implicitHeight + 28
        radius: Theme.radiusSm + 2
        color: Theme.alpha(Theme.accentB, Theme.dark ? 0.08 : 0.10)
        border.width: 1
        border.color: Theme.alpha(Theme.accentB, 0.32)
        RowLayout {
            id: tile
            anchors.fill: parent
            anchors.margins: 14
            spacing: 14
            Rectangle {
                Layout.preferredWidth: Theme.px(40)
                Layout.preferredHeight: Theme.px(40)
                radius: 12
                color: Theme.alpha(Theme.accentB, 0.16)
                WgIcon { anchors.centerIn: parent; name: "sparkle"; size: 20; color: Theme.accentB }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: "Suggested: " + autoTune.recommendedName
                    font.pixelSize: Theme.fsBody + 1
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    Layout.fillWidth: true
                    text: root.differs ? "You're on " + daemon.presetName + " now."
                                       : "That's what you're using now."
                    font.pixelSize: Theme.fsSmall + 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            WgButton {
                visible: root.differs
                variant: "primary"
                text: "Use " + autoTune.recommendedName
                enabled: root.reachable
                onClicked: daemon.applyPreset(autoTune.recommendedIndex)
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        WgButton {
            visible: !autoTune.running
            variant: root.ready ? "secondary" : "primary"
            iconName: root.ready ? "reset" : "play"
            text: root.ready ? "Listen again" : "Start listening"
            enabled: root.reachable
            onClicked: autoTune.start()
        }
        WgButton {
            visible: autoTune.running
            text: root.ready ? "Done" : "Stop"
            onClicked: autoTune.stop()
        }
        Text {
            visible: !root.reachable
            text: "Needs the service running with your mouse connected."
            font.pixelSize: Theme.fsSmall
            color: Theme.textFaint
        }
        Item { Layout.fillWidth: true }
        Text {
            visible: autoTune.running
            text: autoTune.ticks + " ticks · " + Math.round(autoTune.activeSeconds) + " s"
            font.pixelSize: Theme.fsSmall
            color: Theme.textFaint
        }
    }
}
