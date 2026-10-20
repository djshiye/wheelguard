import QtQuick
import QtQuick.Layouts

// Title + description on the left, a switch (and optional extra controls) on the right.
RowLayout {
    id: root
    property string title
    property string description
    property bool checked
    property bool edited: false
    property bool switchVisible: true
    default property alias extra: extraRow.data
    signal toggled(bool value)

    Layout.fillWidth: true
    Layout.topMargin: 10
    Layout.bottomMargin: 10
    spacing: 16

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 2
        RowLayout {
            spacing: 8
            Text { text: root.title; font.pixelSize: Theme.fsBody; font.weight: Font.DemiBold; color: Theme.text }
            Rectangle {   // "changed" dot
                visible: root.edited
                Layout.preferredWidth: 7
                Layout.preferredHeight: 7
                radius: 3.5
                color: Theme.accentB
            }
        }
        Text {
            Layout.fillWidth: true
            text: root.description
            font.pixelSize: Theme.fsSmall
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
    }
    RowLayout {
        id: extraRow
        spacing: 12
    }
    WgSwitch {
        visible: root.switchVisible
        checked: root.checked
        onToggled: root.toggled(checked)
    }
}
