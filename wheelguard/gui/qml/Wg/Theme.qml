pragma Singleton
import QtQuick

QtObject {
    id: t

    property bool dark: true
    // Liquid Glass theme tokens (backend.LiquidGlass); null or inactive keeps Wheelguard's own look.
    property var glass: null
    readonly property bool glassy: glass !== null && glass.active

    function pick(role, fallback) {
        if (!glassy)
            return fallback
        const v = glass.color(dark, role, "")
        return v === "" ? fallback : v
    }

    // Surfaces
    readonly property color chrome:   pick("chrome", dark ? "#0A0B0E" : "#E6E8EF")   // window frame, header, sidebar
    readonly property color panel:    pick("panel", dark ? "#0F1116" : "#F4F5F9")   // main content panel
    readonly property color card:     pick("card", dark ? "#161820" : "#FFFFFF")
    readonly property color cardHi:   pick("cardHi", dark ? "#1C1F28" : "#F3F4F8")   // hovered / nested surfaces
    readonly property color inset:    pick("inset", dark ? "#0C0D11" : "#ECEEF4")   // wells, tracks, monitor background
    readonly property color track:    pick("track", dark ? "#272A35" : "#D9DCE6")
    readonly property color border:   pick("border", dark ? Qt.rgba(1, 1, 1, 0.075) : Qt.rgba(0, 0, 0, 0.08))
    readonly property color borderHi: pick("borderHi", dark ? Qt.rgba(1, 1, 1, 0.15)  : Qt.rgba(0, 0, 0, 0.16))
    readonly property color hover:    pick("hover", dark ? Qt.rgba(1, 1, 1, 0.06)  : Qt.rgba(0, 0, 0, 0.05))

    // Text
    readonly property color text:      pick("text", dark ? "#ECEEF5" : "#14161D")
    readonly property color textMuted: pick("textMuted", dark ? "#9097A8" : "#5F6575")
    readonly property color textFaint: pick("textFaint", dark ? "#666C7C" : "#8A90A0")

    // Accent: electric violet to cyan
    readonly property color accentA: pick("accentA", dark ? "#8B5CF6" : "#7C4DFF")
    readonly property color accentB: pick("accentB", dark ? "#22D3EE" : "#0BB5D6")
    readonly property color coral:   pick("coral", dark ? "#FF6B7A" : "#E8455A")
    readonly property color amber:   pick("amber", dark ? "#FFB547" : "#E39A1B")
    readonly property color good:    pick("good", dark ? "#3DDC97" : "#16A870")

    // Metrics (8 px grid)
    readonly property int radius: glassy ? glass.number(dark, "radius", 16) : 16
    readonly property int radiusSm: glassy ? glass.number(dark, "radius_small", 12) : 12
    readonly property int fast: 150
    readonly property int normal: 220
    readonly property int slow: 280

    // Typography
    readonly property int fsTitle: 26
    readonly property int fsH2: 17
    readonly property int fsBody: 14
    readonly property int fsSmall: 12

    readonly property var ease: Easing.OutCubic

    function alpha(c, a) { return Qt.rgba(c.r, c.g, c.b, a) }
}
