import QtQuick
import QtQuick.Layouts

WgScrollPage {
    id: page
    property alias advancedOpen: adv.expanded
    readonly property bool reachable: daemon.state !== "stopped" && daemon.state !== "connecting"
    readonly property var presetModel: ["Light", "Balanced", "Strong", "Maximum"]

    Connections {
        target: daemon
        function onTick(sign, kind, held, t) { monitor.addTick(sign, kind, held, t) }
    }

    // ---------------------------------------------------------------- hero
    Rectangle {
        id: hero
        width: parent.width
        height: Math.max(92, heroRow.implicitHeight + 24)
        radius: Theme.radius + 2
        color: Theme.card
        border.width: 1
        border.color: Theme.border
        opacity: page.reachable ? 1 : 0.55
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

        Rectangle {  // accent wash
            anchors.fill: parent
            radius: parent.radius
            opacity: daemon.enabled ? 1 : 0.25
            Behavior on opacity { NumberAnimation { duration: Theme.slow } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: Theme.alpha(Theme.accentA, Theme.dark ? 0.30 : 0.20) }
                GradientStop { position: 1.0; color: Theme.alpha(Theme.accentB, Theme.dark ? 0.10 : 0.12) }
            }
        }
        Rectangle {  // top highlight hairline
            x: 24; y: 0; width: parent.width - 48; height: 1
            opacity: daemon.enabled ? 1 : 0.2
            Behavior on opacity { NumberAnimation { duration: Theme.slow } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.3; color: Theme.accentA }
                GradientStop { position: 0.7; color: Theme.accentB }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }

        RowLayout {
            id: heroRow
            anchors.fill: parent
            anchors.leftMargin: 24
            anchors.rightMargin: 28
            anchors.topMargin: 12
            anchors.bottomMargin: 12
            spacing: 20

            Rectangle {
                Layout.preferredWidth: 56
                Layout.preferredHeight: 56
                radius: 18
                opacity: daemon.enabled ? 1 : 0.45
                Behavior on opacity { NumberAnimation { duration: Theme.slow } }
                gradient: Gradient {
                    GradientStop { position: 0.0; color: Theme.accentA }
                    GradientStop { position: 1.0; color: Theme.accentB }
                }
                WgIcon { anchors.centerIn: parent; name: "shield"; size: 28; color: "white"; strokeWidth: 1.9 }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 6
                Text {
                    text: "Scroll protection"
                    font.pixelSize: Theme.fsTitle
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    Layout.fillWidth: true
                    text: !page.reachable
                          ? "The service isn't running, so nothing is being filtered right now."
                          : (daemon.enabled
                             ? "Stray reverse ticks are blocked before they reach your games."
                             : "Protection is off. Every wheel tick passes through untouched.")
                    font.pixelSize: Theme.fsBody
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }

            ColumnLayout {
                Layout.alignment: Qt.AlignVCenter
                spacing: 6
                WgSwitch {
                    Layout.alignment: Qt.AlignHCenter
                    trackHeight: 36
                    enabled: page.reachable
                    checked: daemon.enabled
                    onToggled: daemon.setEnabled(checked)
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: !page.reachable ? "OFFLINE" : (daemon.enabled ? "ON" : "OFF")
                    font.pixelSize: Theme.fsSmall - 1
                    font.weight: Font.DemiBold
                    font.letterSpacing: 1
                    color: page.reachable && daemon.enabled ? Theme.accentB : Theme.textFaint
                    Behavior on color { ColorAnimation { duration: Theme.normal } }
                }
            }
        }
    }

    // ---------------------------------------------------------------- strength
    WgCard {
        width: parent.width
        pad: 20
        spacing: 10
        opacity: page.reachable ? (daemon.enabled ? 1 : 0.6) : 0.5
        enabled: page.reachable
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

        RowLayout {
            Layout.fillWidth: true
            Text {
                Layout.fillWidth: true
                text: "Protection strength"
                font.pixelSize: Theme.fsH2
                font.weight: Font.DemiBold
                color: Theme.text
            }
            Rectangle {
                implicitWidth: badge.implicitWidth + 20
                implicitHeight: 26
                radius: 13
                color: Theme.alpha(daemon.presetIndex < 0 ? Theme.amber : Theme.accentA, Theme.dark ? 0.18 : 0.14)
                Behavior on color { ColorAnimation { duration: Theme.normal } }
                Text {
                    id: badge
                    anchors.centerIn: parent
                    text: daemon.presetName
                    font.pixelSize: Theme.fsSmall
                    font.weight: Font.DemiBold
                    color: daemon.presetIndex < 0 ? Theme.amber : (Theme.dark ? "#B79DFF" : Theme.accentA)
                    Behavior on color { ColorAnimation { duration: Theme.normal } }
                }
            }
        }

        WgSegmented {
            Layout.fillWidth: true
            model: page.presetModel
            currentIndex: daemon.presetIndex
            onActivated: (i) => daemon.applyPreset(i)
        }

        Text {
            id: summary
            Layout.fillWidth: true
            text: daemon.presetSummary
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            onTextChanged: fade.restart()
            SequentialAnimation {
                id: fade
                NumberAnimation { target: summary; property: "opacity"; from: 0.2; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }
            }
        }

        Rectangle {   // inline error from the daemon (the control has already snapped back)
            Layout.fillWidth: true
            id: errBox
            property string shownError: ""
            property real open: daemon.editError !== "" ? 1 : 0
            Behavior on open { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            Connections { target: daemon; function onEditErrorChanged() { if (daemon.editError !== "") errBox.shownError = daemon.editError } }
            Layout.preferredHeight: (errRow.implicitHeight + 20) * open
            visible: open > 0.01
            clip: true
            radius: Theme.radiusSm
            color: Theme.alpha(Theme.coral, 0.12)
            border.width: 1
            border.color: Theme.alpha(Theme.coral, 0.4)
            opacity: open
            RowLayout {
                id: errRow
                x: 12; y: 10
                width: parent.width - 24
                spacing: 10
                WgIcon { name: "alert"; size: 18; color: Theme.coral }
                Text {
                    Layout.fillWidth: true
                    text: "The service rejected that change and it was reverted: " + errBox.shownError
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.text
                    wrapMode: Text.WordWrap
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }

        WgExpander {
            id: adv
            Layout.fillWidth: true
            title: "Advanced"
            subtitle: "Fine-tune the three timings yourself"

            Repeater {
                model: [
                    { key: "pre_ms", title: "Reverse window", from: 0, to: 300,
                      help: "A reversed tick arriving this soon after your last tick is dropped outright. Higher catches more strays, but a genuine quick reverse is lost too." },
                    { key: "hold_ms", title: "Hold time", from: 5, to: 150,
                      help: "Other reversals are held this long to see whether they were bounces. It is also the delay a deliberate reverse scroll sees." },
                    { key: "lock_ms", title: "Direction lock", from: 100, to: 1000,
                      help: "How long without scrolling before the wheel forgets its direction. Must stay longer than the hold time." }
                ]
                delegate: ColumnLayout {
                    id: adRow
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 6
                    readonly property int current: modelData.key === "pre_ms" ? daemon.preMs
                                                   : (modelData.key === "hold_ms" ? daemon.holdMs : daemon.lockMs)

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 16
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            Text {
                                text: adRow.modelData.title
                                font.pixelSize: Theme.fsBody
                                font.weight: Font.DemiBold
                                color: Theme.text
                            }
                            Text {
                                Layout.fillWidth: true
                                text: adRow.modelData.help
                                font.pixelSize: Theme.fsSmall
                                color: Theme.textMuted
                                wrapMode: Text.WordWrap
                                lineHeight: 1.1
                            }
                        }
                        WgSpinBox {
                            Layout.alignment: Qt.AlignTop
                            from: adRow.modelData.from
                            to: adRow.modelData.to
                            value: adRow.current
                            onValueModified: daemon.setMs(adRow.modelData.key, value)
                        }
                    }
                    WgSlider {
                        Layout.fillWidth: true
                        from: adRow.modelData.from
                        to: adRow.modelData.to
                        stepSize: 1
                        value: adRow.current
                        valueText: Math.round(value) + " ms"
                        onMoved: daemon.setMs(adRow.modelData.key, Math.round(value))
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- monitor
    WgCard {
        width: parent.width
        pad: 20
        spacing: 14
        opacity: page.reachable ? 1 : 0.55
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }

        RowLayout {
            Layout.fillWidth: true
            spacing: 20
            Item {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                implicitHeight: titleCol.implicitHeight
                Column {
                spacing: 6
                id: titleCol
                Text {
                    text: "Live wheel monitor"
                    font.pixelSize: Theme.fsH2
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Row {
                    spacing: 14
                    Repeater {
                        model: [{ l: "Passed", c: "a" }, { l: "Blocked", c: "c" }, { l: "Held", c: "h" }]
                        delegate: Row {
                            required property var modelData
                            spacing: 6
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 8; height: 8; radius: 4
                                color: parent.modelData.c === "a" ? Theme.accentA : (parent.modelData.c === "c" ? Theme.coral : Theme.amber)
                            }
                            Text {
                                text: parent.modelData.l
                                font.pixelSize: Theme.fsSmall
                                color: Theme.textMuted
                            }
                        }
                    }
                }
                }
            }

            Repeater {
                model: [
                    { label: "Ticks", kind: "ticks" },
                    { label: "Blocked", kind: "blocked" },
                    { label: "Block rate", kind: "rate" }
                ]
                delegate: ColumnLayout {
                    id: stat
                    required property var modelData
                    Layout.alignment: Qt.AlignVCenter
                    Layout.minimumWidth: 64
                    spacing: 0
                    Text {
                        text: stat.modelData.label
                        font.pixelSize: Theme.fsSmall
                        color: Theme.textMuted
                    }
                    Text {
                        text: stat.modelData.kind === "ticks" ? daemon.ticks
                              : (stat.modelData.kind === "blocked" ? daemon.dropped
                                 : daemon.blockRate.toFixed(1) + "%")
                        font.pixelSize: 22
                        font.weight: Font.DemiBold
                        color: stat.modelData.kind === "ticks" ? Theme.text
                               : (daemon.dropped > 0 ? Theme.coral : Theme.text)
                    }
                }
            }

            WgButton {
                Layout.alignment: Qt.AlignVCenter
                variant: "ghost"
                iconName: "reset"
                text: "Reset"
                enabled: daemon.state === "running" || daemon.state === "waiting"
                onClicked: { daemon.resetStats(); monitor.clearAll() }
            }
        }

        WheelMonitor {
            id: monitor
            Layout.fillWidth: true
            Layout.preferredHeight: 136
            live: daemon.state === "running"
            holdMs: daemon.holdMs
        }
    }
}
