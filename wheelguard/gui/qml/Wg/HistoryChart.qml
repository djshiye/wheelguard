import QtQuick

// Daily block-rate bars (percent of wheel ticks blocked), oldest day on the left, today on the right.
// series: [{ date: "2026-11-09", ticks, dropped, rate }]; rate is -1 (protocol.NO_RATE) for a day without ticks;
// null or undefined count as "no data" too, so a missing rate never turns into NaN bars.
Item {
    id: root
    property var series: []
    property int hovered: -1
    readonly property var hoveredDay: hovered >= 0 && hovered < series.length ? series[hovered] : null

    readonly property int axisW: 40
    readonly property int labelH: 22
    readonly property real plotW: width - axisW
    readonly property real plotH: height - labelH
    readonly property real slotW: series.length > 0 ? plotW / series.length : plotW

    // Tick-mark scale: the smallest "round" ceiling that fits every bar
    readonly property real maxRate: {
        var m = 0
        for (var i = 0; i < series.length; i++)
            if (hasRate(series[i]) && series[i].rate > m)
                m = series[i].rate
        return m
    }
    readonly property real ceiling: {
        var steps = [1, 2, 4, 10, 20, 40, 100]
        for (var i = 0; i < steps.length; i++)
            if (root.maxRate <= steps[i])
                return steps[i]
        return 100
    }
    readonly property real average: {
        var t = 0, d = 0
        for (var i = 0; i < series.length; i++) {
            t += series[i].ticks
            d += series[i].dropped
        }
        return t > 0 ? 100 * d / t : 0
    }

    function dayLabel(iso, withWeekday) {
        var p = iso.split("-")
        var d = new Date(parseInt(p[0]), parseInt(p[1]) - 1, parseInt(p[2]))
        return Qt.locale().toString(d, withWeekday ? "ddd d MMM" : "d MMM")
    }
    function hasRate(day) { return !!day && typeof day.rate === "number" && isFinite(day.rate) && day.rate >= 0 }
    function pct(v) {
        if (typeof v !== "number" || !isFinite(v))
            return "–"
        return v.toLocaleString(Qt.locale(), "f", v < 10 ? 1 : 0) + "%"
    }

    implicitHeight: 170
    implicitWidth: 400
    Accessible.role: Accessible.Graphic
    Accessible.name: series.length === 0 ? "Block rate chart, no data"
                     : "Block rate over the last " + series.length + " days, average " + pct(average)
                       + ", highest " + pct(maxRate)

    Repeater {   // gridlines and axis labels: 0, half, top
        model: [0, 0.5, 1]
        delegate: Item {
            required property real modelData
            readonly property real gy: root.plotH - modelData * root.plotH
            Rectangle {
                x: root.axisW
                y: parent.gy
                width: root.plotW
                height: 1
                color: modelData === 0 ? Theme.borderHi : Theme.border
            }
            Text {
                x: 0
                width: root.axisW - 8
                y: parent.gy - height / 2
                horizontalAlignment: Text.AlignRight
                text: (root.ceiling * modelData).toLocaleString(Qt.locale(), "f", (root.ceiling * modelData) % 1 === 0 ? 0 : 1) + "%"
                font.pixelSize: Theme.fsSmall
                color: Theme.textFaint
            }
        }
    }

    Repeater {
        model: root.series
        delegate: Item {
            id: slot
            required property int index
            required property var modelData
            readonly property bool has: root.hasRate(modelData)
            readonly property real bw: Math.max(3, Math.min(28, root.slotW * 0.62))
            readonly property real bh: has ? Math.max(2, modelData.rate / root.ceiling * root.plotH) : 0
            x: root.axisW + index * root.slotW
            width: root.slotW
            height: root.plotH

            Rectangle {   // the bar
                visible: slot.has
                x: (slot.width - slot.bw) / 2
                width: slot.bw
                y: slot.height - slot.bh
                height: slot.bh
                radius: Math.min(3, slot.bw / 2)
                color: root.hovered === slot.index ? Theme.accentB : Theme.accentA
                opacity: root.hovered < 0 || root.hovered === slot.index ? 1 : 0.7
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Behavior on height { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
                Behavior on y { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            }
            Rectangle {   // a day without data: a faint dot on the baseline
                visible: !slot.has
                x: (slot.width - 4) / 2
                y: slot.height - 4
                width: 4
                height: 4
                radius: 2
                color: Theme.track
            }
        }
    }

    Repeater {   // dates under the first, middle and last bar
        model: root.series.length > 1 ? [0, Math.floor((root.series.length - 1) / 2), root.series.length - 1] : []
        delegate: Text {
            required property int modelData
            readonly property real cx: root.axisW + (modelData + 0.5) * root.slotW
            text: root.dayLabel(root.series[modelData].date, false)
            font.pixelSize: Theme.fsSmall
            color: Theme.textFaint
            y: root.plotH + 6
            x: Math.max(root.axisW - 8, Math.min(root.width - width, cx - width / 2))
        }
    }

    MouseArea {
        x: root.axisW
        width: root.plotW
        height: root.plotH
        hoverEnabled: true
        onPositionChanged: (m) => root.hovered = Math.max(0, Math.min(root.series.length - 1, Math.floor(m.x / root.slotW)))
        onExited: root.hovered = -1
    }
}
