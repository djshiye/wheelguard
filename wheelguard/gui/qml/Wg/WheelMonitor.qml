import QtQuick

// Scrolling timeline of wheel ticks: up lane on top, down lane below, newest at the right edge.
// Pulses live in a plain JS array; the canvas repaints on vsync only while something is visible.
Item {
    id: root
    property bool live: true            // connected and receiving
    property int holdMs: 25
    property int windowMs: 5000         // history shown across the lane width
    readonly property int gutter: 78
    readonly property real upY: height * 0.30
    readonly property real downY: height * 0.70
    readonly property real nowX: width - 30
    property bool animating: false
    property bool hasPulses: false

    implicitHeight: 190
    implicitWidth: 400

    property var pulses: []

    function addTick(sign, kind, held, t) {
        var now = Date.now()
        var list = pulses
        if (held && kind !== "hold") {
            // Resolution of a held tick: update the existing amber pulse in place.
            for (var i = list.length - 1; i >= 0; i--) {
                var q = list[i]
                if (q.kind === "hold" && q.sign === sign) {
                    q.kind = kind
                    q.resolved = now
                    kick()
                    return
                }
            }
        }
        list.push({ born: now, sign: sign, kind: kind, resolved: 0 })
        if (list.length > 400)
            list.splice(0, list.length - 400)
        kick()
    }

    function kick() {
        hasPulses = true
        animating = true
        canvas.requestPaint()
    }

    function clearAll() {
        pulses = []
        hasPulses = false
        animating = false
        canvas.requestPaint()
    }

    function rgba(c, a) {
        return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255) + "," + Math.round(c.b * 255) + "," + a + ")"
    }

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSm + 2
        color: Theme.inset
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: Theme.normal } }
    }

    FrameAnimation {
        running: root.animating && root.visible
        onTriggered: canvas.requestPaint()
    }

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d")
            var W = width, H = height
            ctx.clearRect(0, 0, W, H)
            if (W < 10 || H < 10) return

            var now = Date.now()
            var gx = root.gutter, nx = root.nowX
            var ppm = (nx - gx) / root.windowMs
            var upY = root.upY, downY = root.downY
            var dark = Theme.dark

            // time grid, scrolling with the data
            var phase = now % 1000
            ctx.lineWidth = 1
            for (var k = 0; k * 1000 < root.windowMs + 1000; k++) {
                var gxp = nx - (phase + k * 1000) * ppm
                if (gxp < gx || gxp > nx) continue
                var fade = Math.min(1, (gxp - gx) / 60)
                ctx.strokeStyle = rgba(Theme.text, 0.045 * fade)
                ctx.beginPath(); ctx.moveTo(Math.round(gxp) + 0.5, 14); ctx.lineTo(Math.round(gxp) + 0.5, H - 14); ctx.stroke()
            }

            // lanes
            var laneColor = rgba(Theme.text, dark ? 0.085 : 0.12)
            ctx.strokeStyle = laneColor
            ctx.setLineDash([2, 5])
            ctx.beginPath(); ctx.moveTo(gx, upY + 0.5); ctx.lineTo(nx, upY + 0.5); ctx.stroke()
            ctx.beginPath(); ctx.moveTo(gx, downY + 0.5); ctx.lineTo(nx, downY + 0.5); ctx.stroke()
            ctx.setLineDash([])

            // "now" marker
            var g = ctx.createLinearGradient(0, 12, 0, H - 12)
            g.addColorStop(0, rgba(Theme.accentA, 0))
            g.addColorStop(0.5, rgba(Theme.accentB, root.live ? 0.55 : 0.2))
            g.addColorStop(1, rgba(Theme.accentA, 0))
            ctx.fillStyle = g
            ctx.fillRect(nx + 14, 12, 1.5, H - 24)

            // pulses (oldest first)
            var list = root.pulses
            var keep = []
            for (var i = 0; i < list.length; i++) {
                var p = list[i]
                var age = now - p.born
                if (age > root.windowMs + 200) continue
                if (p.kind === "hold" && age > 800) p.kind = "emit"   // never left dangling if a release was missed
                keep.push(p)
                var x = nx - age * ppm
                var y = p.sign > 0 ? upY : downY
                var edge = Math.max(0, Math.min(1, (x - gx) / 70))
                if (edge <= 0) continue
                var a = edge * (1 - 0.25 * age / root.windowMs)
                var birth = Math.min(1, age / 240)
                var ease = 1 - Math.pow(1 - birth, 3)
                var flare = 1 - ease
                var r = 4.6 * (0.45 + 0.55 * ease)
                var res = p.resolved ? Math.min(1, (now - p.resolved) / 260) : 1

                if (p.resolved && res < 1) {
                    // fading amber "held" glow that resolves into the final colour
                    drawGlow(ctx, x, y, 15 + 5 * (1 - res), Theme.amber, 0.5 * (1 - res) * a)
                }

                if (p.kind === "hold") {
                    var pulse = 1 + 0.12 * Math.sin(age / 55)
                    drawGlow(ctx, x, y, 16 * pulse, Theme.amber, 0.55 * a)
                    ctx.fillStyle = rgba(Theme.amber, a)
                    ctx.beginPath(); ctx.arc(x, y, r * pulse, 0, 2 * Math.PI); ctx.fill()
                    // timer ring running down the hold window
                    var frac = Math.max(0, 1 - age / Math.max(1, root.holdMs * 1.6))
                    ctx.strokeStyle = rgba(Theme.amber, 0.9 * a)
                    ctx.lineWidth = 1.6
                    ctx.beginPath(); ctx.arc(x, y, 9, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * frac); ctx.stroke()
                } else if (p.kind === "drop") {
                    var ar = a * res
                    drawGlow(ctx, x, y, 16 + 10 * flare, Theme.coral, (0.42 + 0.4 * flare) * ar)
                    ctx.strokeStyle = rgba(Theme.coral, ar)
                    ctx.lineWidth = 1.8
                    ctx.beginPath(); ctx.arc(x, y, r + 1.4, 0, 2 * Math.PI); ctx.stroke()
                    var s = 2.6 * (0.5 + 0.5 * ease)
                    ctx.beginPath()
                    ctx.moveTo(x - s, y - s); ctx.lineTo(x + s, y + s)
                    ctx.moveTo(x + s, y - s); ctx.lineTo(x - s, y + s)
                    ctx.stroke()
                } else {
                    var ap = a * res
                    drawGlow(ctx, x, y, 13 + 12 * flare, Theme.accentA, (0.30 + 0.5 * flare) * ap)
                    var bw = 3.4 * (0.5 + 0.5 * ease)
                    var bh = 24 * (0.35 + 0.65 * ease)
                    var cg = ctx.createLinearGradient(0, y - bh / 2, 0, y + bh / 2)
                    cg.addColorStop(0, rgba(p.sign > 0 ? Theme.accentB : Theme.accentA, ap))
                    cg.addColorStop(1, rgba(p.sign > 0 ? Theme.accentA : Theme.accentB, ap))
                    ctx.strokeStyle = cg
                    ctx.lineWidth = bw
                    ctx.lineCap = "round"
                    ctx.beginPath(); ctx.moveTo(x, y - bh / 2 + bw / 2); ctx.lineTo(x, y + bh / 2 - bw / 2); ctx.stroke()
                    ctx.lineCap = "butt"
                    if (flare > 0.05) {
                        ctx.strokeStyle = rgba(Theme.accentB, 0.5 * flare * ap)
                        ctx.lineWidth = 1.2
                        ctx.beginPath(); ctx.arc(x, y, 6 + 10 * flare, 0, 2 * Math.PI); ctx.stroke()
                    }
                }
            }
            if (keep.length !== list.length) root.pulses = keep
            if (keep.length === 0) {
                root.hasPulses = false
                root.animating = false
            }
        }

        function drawGlow(ctx, x, y, radius, color, alpha) {
            if (alpha <= 0.01) return
            var gr = ctx.createRadialGradient(x, y, 0, x, y, radius)
            gr.addColorStop(0, rgba(color, alpha))
            gr.addColorStop(1, rgba(color, 0))
            ctx.fillStyle = gr
            ctx.beginPath(); ctx.arc(x, y, radius, 0, 2 * Math.PI); ctx.fill()
        }

        function rgba(c, a) { return root.rgba(c, a) }
    }

    // Lane labels
    Repeater {
        model: [{ label: "UP", icon: "arrowUp", up: true }, { label: "DOWN", icon: "arrowDown", up: false }]
        delegate: Row {
            required property var modelData
            x: 18
            y: (modelData.up ? root.upY : root.downY) - height / 2
            spacing: 4
            WgIcon {
                anchors.verticalCenter: parent.verticalCenter
                name: parent.modelData.icon
                size: 14
                color: Theme.textFaint
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: parent.modelData.label
                font.pixelSize: Theme.fsSmall - 1
                font.weight: Font.DemiBold
                font.letterSpacing: 0.8
                color: Theme.textFaint
            }
        }
    }

    Text {
        anchors.centerIn: parent
        anchors.horizontalCenterOffset: root.gutter / 2 - 14
        text: root.live ? "Scroll the wheel to see ticks appear here" : "Waiting for the mouse…"
        font.pixelSize: Theme.fsBody - 1
        color: Theme.textFaint
        opacity: root.hasPulses ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: Theme.slow } }
    }
}
