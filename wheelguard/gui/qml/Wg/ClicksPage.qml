import QtQuick
import QtQuick.Layouts

// Everything the Wheelguard service does with clicks on the mouse it protects.
WgScrollPage {
    id: page
    property alias advancedOpen: clAdv.expanded
    // Click-Lock works on the main button: the right one when GNOME is set to left-handed.
    readonly property string lockButton: daemon.clickLockButton === "right"
                                         ? "the right button (your main button, as GNOME is set to left-handed)"
                                         : "the left button"

    Column {
        width: parent.width
        spacing: 6
        Text {
            text: "Clicks"
            font.pixelSize: Theme.fsTitle
            font.weight: Font.DemiBold
            color: Theme.text
        }
        Text {
            width: parent.width
            text: "Let Wheelguard hold or repeat a click for you, and clean up the stray double-clicks of a worn button."
            font.pixelSize: Theme.fsBody
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        Item { width: 1; height: 2 }
        WgScope {
            icon: "shield"
            text: daemon.state === "running" && daemon.deviceLabel !== ""
                  ? "Applies to " + daemon.deviceLabel.split(" · ")[0] + ", the mouse Wheelguard protects"
                  : "Applies to the mouse Wheelguard protects"
        }
    }

    // Click-Lock (done by the Wheelguard service, so it works whether or not GNOME's settings are reachable)
    WgCard {
        id: clCard
        width: parent.width
        spacing: 14
        readonly property bool reachable: daemon.configKnown
        readonly property color valueColor: Theme.accentValue
        function seconds(ms) { return (ms / 1000).toLocaleString(Qt.locale(), "f", ms % 1000 ? 1 : 0) + " s" }

        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                RowLayout {
                    spacing: 10
                    Text {
                        text: "Click-Lock"
                        font.pixelSize: Theme.fsH2
                        font.weight: Font.DemiBold
                        color: Theme.text
                    }
                    Rectangle {   // live badge while the lock is clicking
                        id: liveBadge
                        visible: opacity > 0
                        opacity: daemon.clickLockActive ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
                        Layout.preferredHeight: Theme.px(22)
                        Layout.preferredWidth: liveRow.implicitWidth + 18
                        radius: 11
                        color: Theme.alpha(Theme.good, 0.16)
                        border.width: 1
                        border.color: Theme.alpha(Theme.good, 0.35)
                        Row {
                            id: liveRow
                            anchors.centerIn: parent
                            spacing: 6
                            Rectangle {
                                id: liveDot
                                anchors.verticalCenter: parent.verticalCenter
                                width: 7; height: 7; radius: 3.5
                                color: Theme.good
                                SequentialAnimation on opacity {
                                    running: daemon.clickLockActive && Theme.motion
                                    onRunningChanged: if (!running) liveDot.opacity = 1
                                    loops: Animation.Infinite
                                    NumberAnimation { to: 0.3; duration: 500 }
                                    NumberAnimation { to: 1; duration: 500 }
                                }
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: daemon.clickLockMode === "hold" ? "Holding · click to let go" : "Clicking · click to stop"
                                font.pixelSize: Theme.fsSmall
                                font.weight: Font.Medium
                                color: Theme.good
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: daemon.clickLockMode === "hold"
                          ? "Hold " + page.lockButton + " and Wheelguard keeps it pressed after you let go, handy for long drags. Click again to let go."
                          : "Hold " + page.lockButton + " and Wheelguard keeps clicking for you, even after you let go. Click again to stop."
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            WgSwitch {
                Layout.alignment: Qt.AlignTop
                Accessible.name: "Click-Lock"
                enabled: clCard.reachable
                opacity: enabled ? 1 : 0.6
                checked: daemon.clickLock
                onToggled: daemon.setClickLock(checked)
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            enabled: clCard.reachable

            WgSegmented {
                opacity: daemon.clickLock && clCard.reachable ? 1 : 0.6     // dim the control, never its label
                accessibleName: "Click-Lock mode"
                muted: !daemon.clickLock
                Layout.fillWidth: true
                model: ["Repeat clicks", "Hold down"]
                subLabels: ["keeps clicking for you", "keeps the button pressed"]
                currentIndex: daemon.clickLockMode === "hold" ? 1 : 0
                onActivated: (i) => daemon.setClickLockMode(i === 1 ? "hold" : "repeat")
            }
            Rectangle { Layout.fillWidth: true; Layout.topMargin: 4; Layout.preferredHeight: 1; color: Theme.border }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                Text {
                    Layout.fillWidth: true
                    text: "Hold time"
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    text: clCard.seconds(daemon.clickLockMs)
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.DemiBold
                    color: clCard.valueColor
                }
            }
            WgSlider {
                opacity: daemon.clickLock && clCard.reachable ? 1 : 0.6     // dim the control, never its label
                Accessible.name: "Click-Lock hold time"
                Layout.fillWidth: true
                from: 1000
                to: 15000
                stepSize: 500
                snapMode: WgSlider.SnapAlways
                value: daemon.clickLockMs
                valueText: clCard.seconds(value)
                onMoved: daemon.setClickLockMs(Math.round(value))
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 4
                Layout.rightMargin: 4
                Text { text: "1 s"; font.pixelSize: Theme.fsSmall; color: Theme.textFaint }
                Item { Layout.fillWidth: true }
                Text { text: "15 s"; font.pixelSize: Theme.fsSmall; color: Theme.textFaint }
            }

            WgExpander {
                id: clAdv
                Layout.fillWidth: true
                Layout.topMargin: 2
                title: "Advanced"
                subtitle: daemon.clickLockMode === "hold" ? "Safety timer" : "Click speed, drags and the safety timer"

                WgToggleRow {
                    visible: daemon.clickLockMode !== "hold"
                    Layout.topMargin: 10
                    Layout.bottomMargin: 6
                    title: "Ignore drags"
                    description: "Only start clicking when the pointer stays still while you hold. Dragging a window or selecting text won't start Click-Lock by accident."
                    checked: daemon.clickLockIgnoreDrag
                    onToggled: (v) => daemon.setClickLockIgnoreDrag(v)
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    visible: daemon.clickLockMode !== "hold"
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        Text {
                            Layout.fillWidth: true
                            text: "Click speed"
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            text: daemon.clickLockRate + (daemon.clickLockRate === 1 ? " click/s" : " clicks/s")
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: clCard.valueColor
                        }
                    }
                    WgSlider {
                        opacity: daemon.clickLock && clCard.reachable ? 1 : 0.6     // dim the control, never its label
                        Accessible.name: "Click-Lock click speed"
                        Layout.fillWidth: true
                        from: 1
                        to: 30
                        stepSize: 1
                        snapMode: WgSlider.SnapAlways
                        value: daemon.clickLockRate
                        valueText: Math.round(value) + "/s"
                        onMoved: daemon.setClickLockRate(Math.round(value))
                    }
                }
                RowLayout {   // safety timer
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 16
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: "Stop automatically after"
                            font.pixelSize: Theme.fsBody
                            font.weight: Font.DemiBold
                            color: Theme.text
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "A safety net: if you forget about a lock, it lets go by itself. It also stops when the computer sleeps or the screen locks."
                            font.pixelSize: Theme.fsSmall
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                        }
                    }
                    WgSelect {
                        opacity: daemon.clickLock && clCard.reachable ? 1 : 0.6     // dim the control, never its label
                        Layout.alignment: Qt.AlignTop
                        Layout.preferredWidth: 150
                        Accessible.name: "Stop Click-Lock automatically after"
                        readonly property var values: [0, 60, 300, 600, 1800]
                        model: ["Never", "1 minute", "5 minutes", "10 minutes", "30 minutes"]
                        currentIndex: values.indexOf(daemon.clickLockMaxS)
                        placeholder: daemon.clickLockMaxS >= 120 ? Math.round(daemon.clickLockMaxS / 60) + " minutes"
                                                                 : daemon.clickLockMaxS + " seconds"
                        onActivated: (i) => daemon.setClickLockMaxS(values[i])
                    }
                }
            }
            Text {
                Layout.fillWidth: true
                text: "Also in the Wheelguard menu in the top bar, for switching it on and off quickly."
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
                wrapMode: Text.WordWrap
            }
        }
    }

    // Double-click fix (done by the Wheelguard service: drops the second press of a worn switch's chatter)
    WgCard {
        id: dbCard
        width: parent.width
        spacing: 14
        readonly property bool reachable: daemon.configKnown
        readonly property color valueColor: Theme.accentValue

        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Double-click fix"
                    font.pixelSize: Theme.fsH2
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    Layout.fillWidth: true
                    text: "Worn switches can let go for a moment mid-click. Wheelguard waits that long before releasing, so a bounce can't turn a click into a double-click or break a drag."
                    font.pixelSize: Theme.fsBody - 1
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                }
            }
            WgSwitch {
                Layout.alignment: Qt.AlignTop
                Accessible.name: "Double-click fix"
                enabled: dbCard.reachable
                opacity: enabled ? 1 : 0.6
                checked: daemon.debounceOn
                onToggled: daemon.setDebounce(checked)
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            enabled: dbCard.reachable && daemon.debounceOn

            Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: Theme.border }
            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 4
                Text {
                    Layout.fillWidth: true
                    text: "Ignore a bounce within"
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.DemiBold
                    color: Theme.text
                }
                Text {
                    text: daemon.debounceOn ? daemon.debounceMs + " ms" : "Off"
                    font.pixelSize: Theme.fsBody
                    font.weight: Font.DemiBold
                    color: dbCard.valueColor
                }
            }
            WgSlider {
                opacity: daemon.debounceOn && dbCard.reachable ? 1 : 0.6     // dim the control, never its label
                Layout.fillWidth: true
                Accessible.name: "Ignore a bounce within, in milliseconds"
                from: 10
                to: 150
                stepSize: 5
                snapMode: WgSlider.SnapAlways
                value: daemon.debounceOn ? daemon.debounceMs : 40
                valueText: Math.round(value) + " ms"
                onMoved: daemon.setDebounceMs(Math.round(value))
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 4
                Layout.rightMargin: 4
                Text { text: "10 ms"; font.pixelSize: Theme.fsSmall; color: Theme.textFaint }
                Item { Layout.fillWidth: true }
                Text { text: "150 ms"; font.pixelSize: Theme.fsSmall; color: Theme.textFaint }
            }
            Text {
                Layout.fillWidth: true
                text: "Start around 40 ms. Raise it only if stray double-clicks continue. Letting go of a button is delayed by up to this long, and too high a value merges a genuinely fast double-click into one."
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
                wrapMode: Text.WordWrap
            }
        }

        RowLayout {
            Layout.fillWidth: true
            visible: dbCard.reachable && (daemon.debounceOn || daemon.debouncedClicks > 0)
            spacing: 10
            WgIcon { name: "check"; size: 16; color: daemon.debouncedClicks > 0 ? Theme.good : Theme.textFaint }
            Text {
                Layout.fillWidth: true
                text: daemon.debouncedClicks === 0
                      ? "No stray clicks caught yet"
                      : daemon.debouncedClicks.toLocaleString(Qt.locale(), "f", 0)
                        + (daemon.debouncedClicks === 1 ? " stray click fixed" : " stray clicks fixed")
                        + " since the counters were last reset"
                font.pixelSize: Theme.fsBody - 1
                font.weight: Font.Medium
                color: daemon.debouncedClicks > 0 ? Theme.text : Theme.textMuted
                wrapMode: Text.WordWrap
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.leftMargin: 26
            Layout.topMargin: -10
            visible: dbCard.reachable && daemon.debouncedDetail !== ""
            text: "By button: " + daemon.debouncedDetail
            font.pixelSize: Theme.fsSmall
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        Text {
            Layout.fillWidth: true
            text: "This is not GNOME's double-click speed (on the Pointer page), and it works on any protected mouse, unlike the Multiclick filter some mice have built in (on the Mouse page)."
            font.pixelSize: Theme.fsSmall
            color: Theme.textFaint
            wrapMode: Text.WordWrap
        }
    }
}
